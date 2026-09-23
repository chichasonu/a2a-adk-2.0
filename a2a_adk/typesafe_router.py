"""TypeSafe System One (Jev) routing client.

Jev does not generate text. It receives a small ``state`` plus typed questions
and returns calibrated probability distributions, so routing becomes a typed
decision the application code can branch on instead of a prompt the LLM has to
follow. Requests go through OpenRouter's Decisions API
(``POST {OPENROUTER_BASE_URL}/alpha/decisions``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    """Typed result of a routing decision."""

    agent: str
    confidence: float
    probabilities: dict[str, float]
    gates: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "gates": self.gates,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "model": self.model,
        }


class TypeSafeRouter:
    """Routes a user utterance to one of a fixed set of agents using Jev.

    Args:
        criteria: ``{agent_name: description}`` — the option set for the
            Choice question. Descriptions should read like the bullet points
            of a supervisor prompt.
        gates: Optional ``{gate_name: yes/no question}`` Noul questions that are
            evaluated in the same request (no extra latency) and exposed on
            :attr:`RouteDecision.gates` so callers can override the choice.
        fallback_agent: Agent returned when confidence is below
            ``confidence_floor``.
    """

    def __init__(
        self,
        criteria: dict[str, str],
        *,
        gates: dict[str, str] | None = None,
        fallback_agent: str = "fallback_agent",
        confidence_floor: float | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if len(criteria) < 2:
            raise ValueError("TypeSafeRouter needs at least two agents to choose from.")
        self.criteria = dict(criteria)
        self.gates = dict(gates or {})
        self.fallback_agent = fallback_agent
        self.confidence_floor = (
            settings.TYPESAFE_CONFIDENCE_FLOOR
            if confidence_floor is None
            else confidence_floor
        )
        self.model = model or settings.TYPESAFE_MODEL
        self._api_key = api_key or settings.OPENROUTER_API_KEY
        self._base_url = (base_url or settings.OPENROUTER_BASE_URL).rstrip("/")
        self._timeout = timeout or settings.TYPESAFE_TIMEOUT_SECONDS
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def build_request(self, message: str, context: str = "") -> dict[str, Any]:
        """Builds the Decisions API payload. Public so it can be inspected/tested."""
        questions: dict[str, Any] = {
            "agent": {
                "type": "choice",
                "instructions": (
                    "Which sub-agent of a banking assistant should handle the "
                    "user's request?"
                ),
                "criteria": self.criteria,
            }
        }
        for name, question in self.gates.items():
            questions[f"gate::{name}"] = {"type": "noul", "instructions": question}
        state: dict[str, Any] = {"request": message}
        if context:
            state["recent_context"] = context
        return {"model": self.model, "state": state, "questions": questions}

    def parse_response(self, data: dict[str, Any], latency_ms: float) -> RouteDecision:
        """Converts a Decisions API response into a :class:`RouteDecision`."""
        answers = data.get("answers", {})
        choice = answers.get("agent", {})
        probabilities = {
            k: float(v) for k, v in (choice.get("probabilities") or {}).items()
        }
        agent = choice.get("choice") or (
            max(probabilities, key=probabilities.get) if probabilities else self.fallback_agent
        )
        confidence = float(choice.get("confidence", 0.0))
        gates = {
            key.removeprefix("gate::"): float(ans.get("noul", 0.0))
            for key, ans in answers.items()
            if key.startswith("gate::")
        }
        if agent not in self.criteria or confidence < self.confidence_floor:
            logger.info(
                "TypeSafe route %s (confidence=%.2f) below floor %.2f -> %s",
                agent,
                confidence,
                self.confidence_floor,
                self.fallback_agent,
            )
            agent = self.fallback_agent
        usage = data.get("usage") or {}
        return RouteDecision(
            agent=agent,
            confidence=confidence,
            probabilities=probabilities,
            gates=gates,
            latency_ms=latency_ms,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cost_usd=float(usage.get("cost", 0.0) or 0.0),
            model=str(data.get("model", self.model)),
        )

    async def route(self, message: str, context: str = "") -> RouteDecision:
        """Asks Jev which agent should handle ``message``."""
        if not self.enabled:
            raise RuntimeError("OPENROUTER_API_KEY is required for TypeSafe routing.")
        payload = self.build_request(message, context)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/alpha/decisions"
        started = time.perf_counter()
        if self._client is not None:
            response = await self._client.post(
                url, json=payload, headers=headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        decision = self.parse_response(response.json(), latency_ms)
        logger.info(
            "TypeSafe routed to %s (confidence=%.2f, %.0f ms, %d tokens, $%.6f)",
            decision.agent,
            decision.confidence,
            decision.latency_ms,
            decision.input_tokens,
            decision.cost_usd,
        )
        return decision
