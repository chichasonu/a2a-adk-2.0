"""Contract test against a running ``clm-serve`` (skipped unless CLM_BASE_URL is set).

Run ``clm-serve`` (with the real Qwen3-8B embedder or a stub) and::

    CLM_BASE_URL=http://127.0.0.1:8700 pytest tests/test_contrastive_live.py -q
"""

import os

import pytest

from a2a_adk.financial_agents import AGENT_CRITERIA, ROUTING_GATES, apply_gates
from a2a_adk.typesafe_router import ContrastiveRouter

CLM_BASE_URL = os.environ.get("CLM_BASE_URL", "")

pytestmark = pytest.mark.skipif(not CLM_BASE_URL, reason="CLM_BASE_URL not set")


@pytest.mark.asyncio
async def test_live_clm_returns_typed_decision():
    router = ContrastiveRouter(AGENT_CRITERIA, gates=ROUTING_GATES, base_url=CLM_BASE_URL)
    decision = await router.route("I lost my card")

    assert set(decision.probabilities) == set(AGENT_CRITERIA)
    assert abs(sum(decision.probabilities.values()) - 1.0) < 1e-3
    assert set(decision.gates) == set(ROUTING_GATES)
    assert all(0.0 <= p <= 1.0 for p in decision.gates.values())
    assert decision.output_tokens == 0 and decision.cost_usd == 0.0
    # clm-serve caches embeddings, so a repeated utterance may report 0 encoder tokens
    assert decision.input_tokens >= 0 and decision.latency_ms > 0
    assert decision.model
    assert apply_gates(decision) in AGENT_CRITERIA
