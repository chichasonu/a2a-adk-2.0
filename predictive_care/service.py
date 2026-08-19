"""Orchestration layer tying signals, memory, prediction and the agent together."""

from __future__ import annotations

import logging
import time
from typing import Any

from google.genai import types

from .agent import build_care_runner
from .cards import CardService
from .config import settings
from .memory import CareMemoryService
from .memory import create_memory_service
from .predictor import ACTION_LABELS
from .predictor import IssuePredictor
from .predictor import Prediction
from .session_store import RedisCareSessionService
from .signals import CARD_PAGES
from .signals import Signal
from .signals import SignalStore
from .signals import classify_issue_keywords
from .tools import ACTION_TOOLS
from .tools import bind_services

logger = logging.getLogger(__name__)

DISPUTE_REASONS = {
    "unauthorized": ("unauthorized", "unauthorised", "fraud", "not mine", "someone else"),
    "duplicate": ("duplicate", "twice", "double"),
    "not_received": ("not received", "never arrived", "did not arrive"),
    "wrong_amount": ("wrong amount", "overcharged", "too much"),
}

REPLACE_REASONS = {
    "stolen": ("stolen", "theft"),
    "lost": ("lost", "missing", "misplaced"),
    "damaged": ("damaged", "broken", "cracked", "worn"),
    "fraud": ("fraud", "compromised", "skimmed"),
}


def _is_card_context(signal: Signal) -> bool:
    """True when the signal happens somewhere the card offer is relevant."""
    if signal.type in ("transaction_declined", "call_ivr", "chat_open", "action_taken"):
        return True
    text = f"{signal.value} {signal.metadata.get('context', '')}".lower()
    if signal.type == "page_view":
        return signal.value in CARD_PAGES or "card" in text
    if signal.type == "search":
        return bool(classify_issue_keywords(text)) or "card" in text
    return False


def _match_keyword(text: str, table: dict[str, tuple[str, ...]], default: str) -> str:
    lowered = text.lower()
    for value, keywords in table.items():
        if any(keyword in lowered for keyword in keywords):
            return value
    return default


class CareService:
    """Application service for the predictive card-care POC."""

    def __init__(
        self,
        *,
        memory: CareMemoryService | None = None,
        signals: SignalStore | None = None,
        cards: CardService | None = None,
        session_service: RedisCareSessionService | None = None,
    ):
        self.memory = memory or create_memory_service()
        self.signals = signals or SignalStore()
        self.cards = cards or CardService()
        self.session_service = session_service or RedisCareSessionService()
        self.predictor = IssuePredictor(self.memory)
        bind_services(self.cards, self.memory)
        self._runner = None

    @property
    def llm_enabled(self) -> bool:
        return settings.llm_enabled

    def _get_runner(self):
        if self._runner is None:
            self._runner = build_care_runner(
                memory=self.memory, session_service=self.session_service
            )
        return self._runner

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.close()
            self._runner = None
        await self.memory.close()
        await self.signals.close()
        await self.cards.close()
        await self.session_service.close()

    # ------------------------------------------------------------------
    # Signals and prediction
    # ------------------------------------------------------------------

    async def ingest_signal(self, signal: Signal) -> dict[str, Any]:
        """Record a behavioural signal and re-evaluate the intervention."""
        await self.signals.record(signal)

        # Card-issue searches are worth remembering beyond the session window:
        # they are the strongest hint about what the customer struggles with.
        if signal.type == "search" and classify_issue_keywords(signal.value):
            await self.memory.remember(
                user_id=signal.user_id,
                text=f'Customer searched help for "{signal.value}"',
                author="app",
                kind="behaviour",
                metadata={"signal": "search"},
            )

        prediction = await self.get_prediction(signal.user_id)
        if prediction.should_intervene and not _is_card_context(signal):
            # Only interrupt while the customer is in a card context, even when
            # accumulated friction is high.
            prediction.should_intervene = False
            prediction.suppressed_by = "off_card_context"
        if prediction.should_intervene:
            await self._record_intervention(prediction)
        return {
            "signal": signal.model_dump(),
            "prediction": prediction.model_dump(),
        }

    async def get_prediction(
        self, user_id: str, respect_cooldown: bool = True
    ) -> Prediction:
        """Score the user's current friction and build the offer payload."""
        features = await self.signals.features(user_id)
        card = await self.cards.get_card(user_id)
        return await self.predictor.predict(
            user_id=user_id,
            features=features,
            card_status=card.status,
            respect_cooldown=respect_cooldown,
        )

    async def _record_intervention(self, prediction: Prediction) -> None:
        """Persist that the proactive prompt was shown (drives cooldown + memory)."""
        await self.memory.update_profile(
            user_id=prediction.user_id,
            updates={
                "last_intervention_at": time.time(),
                "last_predicted_issue": prediction.issue_type,
            },
        )
        issue_history = (await self.memory.get_profile(user_id=prediction.user_id)).get(
            "issue_history"
        )
        history = dict(issue_history) if isinstance(issue_history, dict) else {}
        history[prediction.issue_type] = int(history.get(prediction.issue_type, 0)) + 1
        await self.memory.update_profile(
            user_id=prediction.user_id, updates={"issue_history": history}
        )
        await self.memory.remember(
            user_id=prediction.user_id,
            text=(
                f"Proactively offered card help for a '{prediction.issue_type}' issue "
                f"at confidence {prediction.confidence:.2f}"
            ),
            author="predictor",
            kind="intervention",
            metadata={
                "issue_type": prediction.issue_type,
                "confidence": prediction.confidence,
                "reasons": [r.code for r in prediction.reasons],
            },
        )

    async def record_offer_response(
        self, user_id: str, accepted: bool, action: str | None = None
    ) -> dict[str, Any]:
        """Remember whether the customer engaged with the proactive prompt."""
        field = "accepted_offers" if accepted else "declined_offers"
        count = await self.memory.increment_profile_counter(user_id=user_id, field=field)
        await self.memory.remember(
            user_id=user_id,
            text=(
                f"Customer {'accepted' if accepted else 'dismissed'} the proactive card "
                f"help offer{f' and chose to {ACTION_LABELS.get(action, action)}' if action else ''}"
            ),
            author="app",
            kind="feedback",
            metadata={"accepted": accepted, "action": action},
        )
        return {field: count}

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def execute_action(
        self,
        *,
        user_id: str,
        action: str,
        transaction_id: str = "",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Run one resolution action directly (proactive prompt button click)."""
        tool = ACTION_TOOLS.get(action)
        if tool is None:
            raise ValueError(f"Unknown action: {action}")

        if action == "replace":
            result = await tool(user_id, reason=reason or "lost")
        elif action == "dispute":
            result = await tool(
                user_id, transaction_id=transaction_id, reason=reason or "unauthorized"
            )
        else:
            result = await tool(user_id)

        await self.record_offer_response(user_id, accepted=True, action=action)
        await self.signals.record(
            Signal(user_id=user_id, type="action_taken", value=action)
        )
        return result

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    async def chat(
        self, *, user_id: str, message: str, session_id: str | None = None
    ) -> dict[str, Any]:
        """Answer a customer message, with the ADK agent or the rule engine."""
        await self.signals.record(
            Signal(user_id=user_id, type="chat_open", value=message[:120])
        )
        if self.llm_enabled:
            return await self._chat_with_agent(
                user_id=user_id, message=message, session_id=session_id
            )
        return await self._chat_with_rules(
            user_id=user_id, message=message, session_id=session_id
        )

    async def _chat_with_agent(
        self, *, user_id: str, message: str, session_id: str | None
    ) -> dict[str, Any]:
        runner = self._get_runner()
        session_id = session_id or f"care_{user_id}"
        session = await self.session_service.get_session(
            app_name=settings.APP_NAME, user_id=user_id, session_id=session_id
        )
        if session is None:
            session = await self.session_service.create_session(
                app_name=settings.APP_NAME, user_id=user_id, session_id=session_id
            )

        prediction = await self.get_prediction(user_id, respect_cooldown=False)
        profile = await self.memory.get_profile(user_id=user_id)
        # The tools are per-customer, so the id and the current prediction are
        # given to the model as grounding for this turn.
        grounded = (
            f"[customer_id={user_id}] [predicted_issue={prediction.issue_type}] "
            f"[known_preference={profile.get('preferred_resolution', 'none')}]\n{message}"
        )
        content = types.Content(role="user", parts=[types.Part(text=grounded)])

        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.text and event.content.role == "model":
                    texts.append(part.text)
                if part.function_call:
                    tool_calls.append(
                        {
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args or {}),
                        }
                    )

        session = await self.session_service.get_session(
            app_name=settings.APP_NAME, user_id=user_id, session_id=session_id
        )
        if session is not None:
            await self.memory.add_session_to_memory(session)

        return {
            "response": "".join(texts).strip() or "I could not reach the assistant.",
            "session_id": session_id,
            "engine": "adk-agent",
            "tool_calls": tool_calls,
        }

    async def _chat_with_rules(
        self, *, user_id: str, message: str, session_id: str | None
    ) -> dict[str, Any]:
        """Deterministic fallback so the POC works without a Gemini API key."""
        lowered = message.lower()
        card = await self.cards.get_card(user_id)
        profile = await self.memory.get_profile(user_id=user_id)
        tool_calls: list[dict[str, Any]] = []

        action = None
        if any(k in lowered for k in ("replace", "new card", "reissue", "lost", "stolen")):
            action = "replace"
        elif any(k in lowered for k in ("activate", "activation")):
            action = "activate"
        elif any(k in lowered for k in ("dispute", "fraud", "unauthor", "refund", "charge")):
            action = "dispute"
        elif any(k in lowered for k in ("unlock", "unblock", "blocked", "locked")):
            action = "unlock"

        if action:
            kwargs: dict[str, Any] = {}
            if action == "replace":
                kwargs["reason"] = _match_keyword(message, REPLACE_REASONS, "lost")
            if action == "dispute":
                kwargs["reason"] = _match_keyword(message, DISPUTE_REASONS, "unauthorized")
            result = await self.execute_action(user_id=user_id, action=action, **kwargs)
            tool_calls.append({"name": f"{action}_card", "args": {"user_id": user_id}})
            response = result["message"]
            preferred = profile.get("preferred_resolution")
            if preferred == action:
                response += " Same fix as last time, so I applied it straight away."
        elif any(k in lowered for k in ("status", "what is wrong", "why", "help")):
            response = (
                f"Your card ending {card.last4} is {card.status}"
                f"{f' ({card.blocked_reason})' if card.blocked_reason else ''}. "
                "I can replace, activate, dispute a charge, or unlock it - which would you like?"
            )
        else:
            response = (
                "I can help with your debit card: replace it, activate it, "
                "dispute a charge, or unlock it. Which one do you need?"
            )

        return {
            "response": response,
            "session_id": session_id or f"care_{user_id}",
            "engine": "rule-engine",
            "tool_calls": tool_calls,
        }

    # ------------------------------------------------------------------
    # Inspection / demo helpers
    # ------------------------------------------------------------------

    async def memory_view(self, user_id: str, limit: int = 25) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "profile": await self.memory.get_profile(user_id=user_id),
            "records": await self.memory.list_records(user_id=user_id, limit=limit),
        }

    async def reset(self, user_id: str, forget_memory: bool = True) -> dict[str, Any]:
        """Reset the demo: always clear signals, optionally forget memory."""
        await self.signals.clear(user_id)
        await self.cards.reset(user_id)
        if forget_memory:
            await self.memory.clear(user_id=user_id)
            await self.session_service.delete_session(
                app_name=settings.APP_NAME, user_id=user_id, session_id=f"care_{user_id}"
            )
        return {
            "user_id": user_id,
            "signals_cleared": True,
            "memory_cleared": forget_memory,
            "card": await self.cards.snapshot(user_id),
        }
