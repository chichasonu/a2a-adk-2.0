from __future__ import annotations

import os
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from setfit_router.app import create_app
from setfit_router.config import Settings
from setfit_router.metrics import RoutingMetrics
from setfit_router.schemas import AgentName, ClassifyResponse
from tests.conftest import FixedRouter, ScriptedLlm
from training.prepare_data import AGENT_LABELS

DEAD = "http://127.0.0.1:9/sse"


def _client(tmp_path, router) -> TestClient:
    settings = Settings(
        mcp_urls={"cards": DEAD, "transactions": DEAD, "accounts": DEAD},
        routing_metrics_log=tmp_path / "routing.jsonl",
    )
    app = create_app(
        settings, router=router, model=ScriptedLlm(), metrics=RoutingMetrics(tmp_path / "routing.jsonl")
    )
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with _client(tmp_path, FixedRouter()) as c:
        yield c


def test_agent_literal_matches_training_labels():
    assert set(get_args(AgentName)) == set(AGENT_LABELS)


def test_classify_returns_typed_response(client):
    r = client.post("/classify", json={"text": "  my card is broken  ", "top_k": 3})
    assert r.status_code == 200
    body = ClassifyResponse.model_validate(r.json())
    assert body.agent == "cards"
    assert body.model.name == "fixed"
    assert [c.agent for c in body.candidates][0] == "cards"
    assert len(body.candidates) == 3


def test_classify_defaults_to_top1(client):
    body = client.post("/classify", json={"text": "exchange rate?"}).json()
    assert body["agent"] == "fallback"
    assert len(body["candidates"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": 123},
        {"text": "hi", "top_k": "3"},
        {"text": "hi", "top_k": 0},
        {"text": "hi", "top_k": 11},
        {"text": "hi", "unexpected": True},
        {"text": "x" * 2001},
    ],
)
def test_classify_rejects_invalid_requests(client, payload):
    assert client.post("/classify", json=payload).status_code == 422


def test_openapi_exposes_classify_schema(client):
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/classify"]["post"]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("ClassifyResponse")
    agent = spec["components"]["schemas"]["ClassifyResponse"]["properties"]["agent"]
    assert set(agent["enum"]) == set(AGENT_LABELS)


MODEL_DIR = Path(os.environ.get("ROUTER_MODEL_DIR", "models/setfit-intent"))


@pytest.mark.skipif(not (MODEL_DIR / "router_meta.json").exists(), reason="trained router not available")
def test_classify_with_trained_setfit_router(tmp_path):
    from setfit_router.router import SetFitRouter

    with _client(tmp_path, SetFitRouter(MODEL_DIR)) as c:
        body = c.post("/classify", json={"text": "I lost my card yesterday", "top_k": 5}).json()
    assert body["agent"] == "cards"
    assert body["model"]["name"] == MODEL_DIR.name
    confs = [x["confidence"] for x in body["candidates"]]
    assert len(confs) == 5 and confs == sorted(confs, reverse=True)
    assert body["confidence"] == confs[0]
