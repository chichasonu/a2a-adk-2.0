import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("USE_FAKEREDIS", "true")
os.environ.setdefault("MCP_ENABLED", "false")

import httpx
import pytest

from a2a_adk.financial_agents import AGENT_CRITERIA, ROUTING_GATES, apply_gates
from a2a_adk.typesafe_router import RouteDecision, TypeSafeRouter


def _router(**kwargs) -> TypeSafeRouter:
    return TypeSafeRouter(
        AGENT_CRITERIA, gates=ROUTING_GATES, api_key="test-key", **kwargs
    )


def _response(agent: str, confidence: float, gates: dict[str, float] | None = None):
    answers = {
        "agent": {
            "choice": agent,
            "confidence": confidence,
            "probabilities": {agent: confidence},
        }
    }
    for name, p in (gates or {}).items():
        answers[f"gate::{name}"] = {"noul": p}
    return {
        "model": "typesafe/jev-1.13",
        "answers": answers,
        "usage": {"input_tokens": 700, "output_tokens": 100, "cost": 0.00003},
    }


def test_build_request_shape():
    payload = _router().build_request("lost my card", context="prev turn")
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"request": "lost my card", "recent_context": "prev turn"}
    assert payload["questions"]["agent"]["type"] == "choice"
    assert set(payload["questions"]["agent"]["criteria"]) == set(AGENT_CRITERIA)
    for gate in ROUTING_GATES:
        assert payload["questions"][f"gate::{gate}"]["type"] == "noul"


def test_parse_response_extracts_decision_and_usage():
    decision = _router(confidence_floor=0.5).parse_response(
        _response("card_management_agent", 0.93, {"wants_human": 0.02}), 210.0
    )
    assert decision.agent == "card_management_agent"
    assert decision.confidence == pytest.approx(0.93)
    assert decision.gates == {"wants_human": pytest.approx(0.02)}
    assert (decision.input_tokens, decision.output_tokens) == (700, 100)
    assert decision.cost_usd == pytest.approx(0.00003)
    assert decision.latency_ms == 210.0


def test_low_confidence_falls_back():
    decision = _router(confidence_floor=0.5).parse_response(
        _response("transaction_agent", 0.31), 1.0
    )
    assert decision.agent == "fallback_agent"


def test_unknown_agent_falls_back():
    decision = _router(confidence_floor=0.0).parse_response(_response("bogus", 0.99), 1.0)
    assert decision.agent == "fallback_agent"


def test_apply_gates_human_overrides_choice():
    d = RouteDecision("account_management_agent", 0.9, {}, gates={"wants_human": 0.95})
    assert apply_gates(d) == "transfer_to_human_agent"


def test_apply_gates_fee_dispute_reroutes_transaction():
    d = RouteDecision("transaction_agent", 0.9, {}, gates={"fee_dispute": 0.9})
    assert apply_gates(d) == "rates_fees_limits_management_agent"
    d = RouteDecision("card_management_agent", 0.9, {}, gates={"fee_dispute": 0.9})
    assert apply_gates(d) == "card_management_agent"


@pytest.mark.asyncio
async def test_route_posts_to_decisions_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=_response("spending_insights_agent", 0.88))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = _router(client=client, base_url="https://openrouter.ai/api")
        decision = await router.route("how much did I spend on food?")

    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["auth"] == "Bearer test-key"
    assert decision.agent == "spending_insights_agent"
    assert decision.latency_ms > 0


def test_requires_two_agents():
    with pytest.raises(ValueError):
        TypeSafeRouter({"only": "one"}, api_key="k")
