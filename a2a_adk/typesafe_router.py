"""System One routing clients: TypeSafe Jev and the open Contrastive LM.

System One models do not generate text. They receive a small ``state`` plus
typed questions and return calibrated probability distributions, so routing
becomes a typed decision the application code can branch on instead of a
prompt the LLM has to follow.

* :class:`TypeSafeRouter` — Jev via OpenRouter's Decisions API
  (``POST {OPENROUTER_BASE_URL}/alpha/decisions``).
* :class:`ContrastiveRouter` — CLM-8B served by ``clm-serve``
  (``POST {CLM_BASE_URL}/v1/systemone``). Same request/response wire format.
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

    name = "typesafe"
    endpoint_path = "/alpha/decisions"

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
        self.model = model or self._default_model()
        self._api_key = api_key or self._default_api_key()
        self._base_url = (base_url or self._default_base_url()).rstrip("/")
        self._timeout = timeout or settings.TYPESAFE_TIMEOUT_SECONDS
        self._client = client

    def _default_model(self) -> str:
        return settings.TYPESAFE_MODEL

    def _default_api_key(self) -> str:
        return settings.OPENROUTER_API_KEY

    def _default_base_url(self) -> str:
        return settings.OPENROUTER_BASE_URL

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

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
            raise RuntimeError(f"{self.name} router is not configured.")
        payload = self.build_request(message, context)
        headers = self._headers()
        url = f"{self._base_url}{self.endpoint_path}"
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
            "%s routed to %s (confidence=%.2f, %.0f ms, %d tokens, $%.6f)",
            self.name,
            decision.agent,
            decision.confidence,
            decision.latency_ms,
            decision.input_tokens,
            decision.cost_usd,
        )
        return decision


class ContrastiveRouter(TypeSafeRouter):
    """Routes with the open Contrastive Language Model (CLM-8B).

    CLM is a System One model trained with a contrastive (InfoNCE) objective:
    a state encoder and an action encoder score each option by embedding
    alignment. ``clm-serve`` exposes it behind the TypeSafe wire format, so the
    request built by :meth:`build_request` is sent unchanged to
    ``POST {CLM_BASE_URL}/v1/systemone``. It is self-hosted, so the API key is
    optional and the reported cost is always zero.
    """

    name = "contrastive"
    endpoint_path = "/v1/systemone"

    def _default_model(self) -> str:
        return settings.CLM_MODEL

    def _default_api_key(self) -> str:
        return settings.CLM_API_KEY

    def _default_base_url(self) -> str:
        return settings.CLM_BASE_URL

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)
