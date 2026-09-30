from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from setfit_router.router import FALLBACK, Candidate, Classification, RouteDecision, RouterInfo


class FixedRouter:
    """Routes by keyword so tests don't need a trained SetFit model."""

    def route(self, text: str) -> RouteDecision:
        lowered = text.lower()
        agent = "cards" if "card" in lowered else "accounts" if "address" in lowered else FALLBACK
        return RouteDecision(
            agent=agent,
            confidence=0.9,
            predicted_label=agent,
            intent=None,
            granularity="agent",
            low_confidence=False,
            latency_ms=1.0,
        )

    def classify(self, text: str, top_k: int = 1) -> Classification:
        decision = self.route(text)
        others = [a for a in ("cards", "transactions", "accounts", FALLBACK) if a != decision.agent]
        candidates = [Candidate(decision.agent, decision.agent, 0.9)]
        candidates += [Candidate(a, a, 0.1 / len(others)) for a in others]
        return Classification(
            decision=decision,
            candidates=tuple(candidates[:top_k]),
            info=RouterInfo(name="fixed", base_model="keyword", granularity="agent", num_labels=4),
        )


class ScriptedLlm(BaseLlm):
    """Calls the first requested tool once, then answers with the tool result."""

    model: str = "scripted"
    tool_to_call: str = "list_cards"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        last = llm_request.contents[-1] if llm_request.contents else None
        responses = [p.function_response for p in (last.parts if last else None) or [] if p.function_response]
        if responses:
            text = f"tool {responses[0].name} returned {responses[0].response}"
        elif self.tool_to_call in llm_request.tools_dict:
            call = types.FunctionCall(name=self.tool_to_call, args={"customer_id": "cust-001"})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
            return
        else:
            text = "general answer"
        if stream:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=text)]), partial=True
            )
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))
