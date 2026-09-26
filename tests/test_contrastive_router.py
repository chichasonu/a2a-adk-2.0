import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("USE_FAKEREDIS", "true")
os.environ.setdefault("MCP_ENABLED", "false")

import httpx
import pytest

from a2a_adk.financial_agents import AGENT_CRITERIA, ROUTING_GATES, apply_gates
from a2a_adk.typesafe_router import ContrastiveRouter, TypeSafeRouter


def _router(**kwargs) -> ContrastiveRouter:
    return ContrastiveRouter(
        AGENT_CRITERIA,
        gates=ROUTING_GATES,
        base_url="http://clm:8700",
        confidence_floor=0.5,
        **kwargs,
    )


def _clm_response(agent: str, confidence: float, gates: dict[str, float] | None = None):
    # Shape emitted by clm-serve's POST /v1/systemone (schema.answer_from_probs).
    answers = {
        "agent": {
            "type": "choice",
            "choice": agent,
            "confidence": confidence,
            "probabilities": {agent: confidence},
        }
    }
    for name, p in (gates or {}).items():
        answers[f"gate::{name}"] = {"type": "noul", "noul": p}
    return {
        "model": "clm-latest",
        "answers": answers,
        "usage": {"billing_units": 3, "input_tokens": 42, "output_tokens": 0},
    }


def test_disabled_without_base_url():
    router = ContrastiveRouter(AGENT_CRITERIA, base_url="")
    assert not router.enabled


def test_request_matches_typesafe_wire_format():
    clm = _router().build_request("lost my card", context="prev")
    jev = TypeSafeRouter(
        AGENT_CRITERIA, gates=ROUTING_GATES, api_key="k"
    ).build_request("lost my card", context="prev")
    assert clm["model"] == "clm-latest"
    assert clm["state"] == jev["state"]
    assert clm["questions"] == jev["questions"]


def test_parse_clm_response_reports_zero_cost():
    decision = _router().parse_response(
        _clm_response("account_management_agent", 0.9, {"fee_dispute": 0.1}), 17.0
    )
    assert decision.agent == "account_management_agent"
    assert decision.gates == {"fee_dispute": pytest.approx(0.1)}
    assert (decision.input_tokens, decision.output_tokens) == (42, 0)
    assert decision.cost_usd == 0.0
    assert decision.model == "clm-latest"
    assert apply_gates(decision) == "account_management_agent"


@pytest.mark.asyncio
async def test_route_posts_to_systemone_endpoint_without_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["has_auth"] = "Authorization" in request.headers
        return httpx.Response(200, json=_clm_response("transaction_agent", 0.8))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await _router(client=client).route("show my last 5 transactions")

    assert seen["url"] == "http://clm:8700/v1/systemone"
    assert seen["has_auth"] is False
    assert decision.agent == "transaction_agent"


@pytest.mark.asyncio
async def test_route_sends_bearer_when_api_key_set():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_clm_response("fallback_agent", 0.7))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _router(client=client, api_key="clm-secret").route("hello")

    assert seen["auth"] == "Bearer clm-secret"
