"""Predictive engine that decides when to proactively offer card help.

The score combines in-session behavioural friction (searches, repeated visits
to the card management page, declined transactions) with long-term memory
(previous issues, how the customer resolved them, whether they engaged with a
previous proactive offer). Every contribution is returned as an explainable
reason so the POC can show *why* the prompt fired.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel
from pydantic import Field

from .config import settings
from .memory import RedisMemoryService
from .signals import SignalFeatures

logger = logging.getLogger(__name__)

# Card issue taxonomy -> the self-service actions that can resolve it, best first.
ISSUE_ACTIONS: dict[str, tuple[str, ...]] = {
    "blocked": ("unlock", "replace", "dispute", "activate"),
    "lost_or_stolen": ("replace", "unlock", "dispute", "activate"),
    "not_activated": ("activate", "unlock", "replace", "dispute"),
    "disputed_charge": ("dispute", "unlock", "replace", "activate"),
    "unknown": ("replace", "activate", "dispute", "unlock"),
}

ACTION_LABELS: dict[str, str] = {
    "replace": "Replace card",
    "activate": "Activate card",
    "dispute": "Dispute a charge",
    "unlock": "Unlock card",
}

ISSUE_SUMMARIES: dict[str, str] = {
    "blocked": "your card is blocked",
    "lost_or_stolen": "your card may be lost or stolen",
    "not_activated": "your new card is not activated yet",
    "disputed_charge": "a charge on your card looks wrong",
    "unknown": "you are having trouble with your card",
}

# Card states already implied by the issue summary, so the prompt does not
# repeat them ("your card is blocked. Your card is currently blocked.").
IMPLIED_STATUS: dict[str, str] = {
    "blocked": "blocked",
    "not_activated": "inactive",
}

# Weight of each behavioural cue in the friction score.
WEIGHTS: dict[str, float] = {
    "card_issue_search": 0.30,
    "repeated_card_page": 0.35,
    "extra_card_page_view": 0.05,
    "declined_transaction": 0.25,
    "ivr_call": 0.15,
    "long_dwell": 0.10,
    "memory_recurring_issue": 0.15,
    "memory_offer_accepted": 0.05,
}


class Reason(BaseModel):
    """One explainable contribution to the friction score."""

    code: str
    detail: str
    weight: float
    source: str = "signals"


class SuggestedAction(BaseModel):
    """A self-service action offered in the proactive prompt."""

    action: str
    label: str
    primary: bool = False
    reason: str | None = None


class Prediction(BaseModel):
    """Outcome of the predictive engine for one user."""

    user_id: str
    should_intervene: bool
    confidence: float
    issue_type: str = "unknown"
    headline: str = "Having trouble with your card?"
    message: str = ""
    actions: list[SuggestedAction] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_by: str | None = None
    features: SignalFeatures | None = None
    predicted_at: float = Field(default_factory=time.time)


class IssuePredictor:
    """Scores friction and builds the proactive intervention payload."""

    def __init__(self, memory: RedisMemoryService):
        self.memory = memory

    async def predict(
        self,
        *,
        user_id: str,
        features: SignalFeatures,
        card_status: str | None = None,
        respect_cooldown: bool = True,
    ) -> Prediction:
        """Return an explainable prediction for the user's current session."""
        reasons: list[Reason] = []

        if features.card_issue_searches:
            reasons.append(
                Reason(
                    code="card_issue_search",
                    detail=(
                        f"{len(features.card_issue_searches)} card-issue search(es): "
                        + ", ".join(f'"{q}"' for q in features.card_issue_searches[:3])
                    ),
                    weight=WEIGHTS["card_issue_search"],
                )
            )
        if features.repeated_card_page:
            reasons.append(
                Reason(
                    code="repeated_card_page",
                    detail=(
                        f"Opened the card management page {features.card_page_views} "
                        f"times in {features.window_seconds // 60} minutes"
                    ),
                    weight=WEIGHTS["repeated_card_page"],
                )
            )
        extra_views = max(
            features.card_page_views - settings.REPEAT_VIEW_THRESHOLD, 0
        )
        if extra_views:
            reasons.append(
                Reason(
                    code="extra_card_page_view",
                    detail=f"{extra_views} further card page revisit(s)",
                    weight=min(extra_views * WEIGHTS["extra_card_page_view"], 0.15),
                )
            )
        if features.declined_transactions:
            reasons.append(
                Reason(
                    code="declined_transaction",
                    detail=f"{features.declined_transactions} declined transaction(s)",
                    weight=WEIGHTS["declined_transaction"],
                )
            )
        if features.ivr_calls:
            reasons.append(
                Reason(
                    code="ivr_call",
                    detail=f"{features.ivr_calls} call(s) to the card IVR",
                    weight=WEIGHTS["ivr_call"],
                )
            )
        if features.dwell_seconds >= 60:
            reasons.append(
                Reason(
                    code="long_dwell",
                    detail=f"{int(features.dwell_seconds)}s dwell on card pages",
                    weight=WEIGHTS["long_dwell"],
                )
            )

        issue_type = self._infer_issue(features, card_status)
        profile = await self.memory.get_profile(user_id=user_id)
        memory_hits = await self.memory.search_records(
            user_id=user_id,
            query=f"debit card {issue_type.replace('_', ' ')} block replace activate dispute unlock",
            top_k=3,
        )

        past_issues = profile.get("issue_history")
        if isinstance(past_issues, dict) and past_issues.get(issue_type):
            reasons.append(
                Reason(
                    code="memory_recurring_issue",
                    detail=(
                        f"Long-term memory: {past_issues[issue_type]} previous "
                        f"'{issue_type}' issue(s) for this customer"
                    ),
                    weight=WEIGHTS["memory_recurring_issue"],
                    source="memory",
                )
            )
        if profile.get("accepted_offers"):
            reasons.append(
                Reason(
                    code="memory_offer_accepted",
                    detail=(
                        f"Long-term memory: accepted {profile['accepted_offers']} "
                        "previous proactive offer(s)"
                    ),
                    weight=WEIGHTS["memory_offer_accepted"],
                    source="memory",
                )
            )

        confidence = round(min(sum(reason.weight for reason in reasons), 1.0), 3)
        should_intervene = confidence >= settings.INTERVENE_THRESHOLD
        suppressed_by = None

        if should_intervene and profile.get("declined_offers", 0) and confidence < 0.85:
            # The customer dismissed help before: raise the bar for re-prompting.
            should_intervene = False
            suppressed_by = "memory_previously_dismissed"
        if should_intervene and respect_cooldown:
            last = profile.get("last_intervention_at")
            if isinstance(last, (int, float)) and (
                time.time() - float(last) < settings.INTERVENTION_COOLDOWN_SECONDS
            ):
                should_intervene = False
                suppressed_by = "cooldown"

        prediction = Prediction(
            user_id=user_id,
            should_intervene=should_intervene,
            confidence=confidence,
            issue_type=issue_type,
            message=self._message(issue_type, profile, card_status),
            actions=self._actions(issue_type, profile),
            reasons=reasons,
            memory_hits=memory_hits,
            suppressed_by=suppressed_by,
            features=features,
        )
        logger.info(
            "Prediction user=%s issue=%s confidence=%.2f intervene=%s suppressed=%s",
            user_id,
            issue_type,
            confidence,
            should_intervene,
            suppressed_by,
        )
        return prediction

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_issue(
        self, features: SignalFeatures, card_status: str | None
    ) -> str:
        if features.keyword_hits:
            best = max(features.keyword_hits.items(), key=lambda item: item[1])
            if best[1] > 0:
                return best[0]
        if card_status == "blocked":
            return "blocked"
        if card_status == "inactive":
            return "not_activated"
        return "unknown"

    def _message(
        self, issue_type: str, profile: dict[str, Any], card_status: str | None
    ) -> str:
        summary = ISSUE_SUMMARIES.get(issue_type, ISSUE_SUMMARIES["unknown"])
        message = f"It looks like {summary}."
        if card_status and IMPLIED_STATUS.get(issue_type) != card_status:
            message += f" Your card is currently {card_status}."
        message += " I can help replace, activate, dispute or unlock it."
        preferred = profile.get("preferred_resolution")
        if isinstance(preferred, str) and preferred in ACTION_LABELS:
            message += (
                f" Last time you preferred to {ACTION_LABELS[preferred].lower()}, "
                "so I put that first."
            )
        return message

    def _actions(
        self, issue_type: str, profile: dict[str, Any]
    ) -> list[SuggestedAction]:
        order = list(ISSUE_ACTIONS.get(issue_type, ISSUE_ACTIONS["unknown"]))
        preferred = profile.get("preferred_resolution")
        if isinstance(preferred, str) and preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)

        actions = []
        for index, action in enumerate(order):
            reason = None
            if action == preferred:
                reason = "Recalled from long-term memory as your usual choice"
            elif index == 0:
                reason = f"Best match for a '{issue_type}' issue"
            actions.append(
                SuggestedAction(
                    action=action,
                    label=ACTION_LABELS[action],
                    primary=index == 0,
                    reason=reason,
                )
            )
        return actions
