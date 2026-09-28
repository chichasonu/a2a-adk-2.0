from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from setfit_router.app import create_app
from setfit_router.config import Settings
from setfit_router.metrics import RoutingMetrics
from tests.conftest import FixedRouter, ScriptedLlm


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cards_mcp_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.cards_server"],
        env={**os.environ, "CARDS_MCP_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("cards MCP server did not start")
    yield f"http://127.0.0.1:{port}/sse"
    proc.terminate()
    proc.wait(timeout=10)


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def client(tmp_path, cards_mcp_url):
    settings = Settings(
        mcp_urls={
            "cards": cards_mcp_url,
            "transactions": "http://127.0.0.1:9/sse",
            "accounts": "http://127.0.0.1:9/sse",
        },
        routing_metrics_log=tmp_path / "routing.jsonl",
    )
    app = create_app(
        settings,
        router=FixedRouter(),
        model=ScriptedLlm(),
        metrics=RoutingMetrics(tmp_path / "routing.jsonl"),
    )
    with TestClient(app) as c:
        yield c


def test_route_endpoint(client):
    r = client.post("/route", json={"message": "my card is broken"})
    assert r.status_code == 200
    assert r.json()["agent"] == "cards"


def test_stream_routes_to_cards_agent_and_calls_mcp_tool_over_sse(client):
    with client.stream("POST", "/chat/stream", json={"message": "list my cards"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.read().decode())
    kinds = [k for k, _ in events]
    assert kinds[0] == "session" and kinds[-1] == "done"
    route = next(p for k, p in events if k == "route")
    assert route["agent"] == "cards"
    assert any(k == "tool_call" and p["name"] == "list_cards" for k, p in events)
    result = next(p for k, p in events if k == "tool_result")
    assert "card-001" in json.dumps(result["response"])
    final = [p for k, p in events if k == "message"]
    assert final and final[-1]["author"] == "cards_agent"
    assert events[-1][1]["status"] == "ok"

    summary = client.get("/metrics").json()
    assert summary["requests"] == 1
    assert summary["by_agent"] == {"cards": 1}


def test_stream_fallback_and_session_reuse(client):
    with client.stream("POST", "/chat/stream", json={"message": "what is the exchange rate"}) as r:
        events = _parse_sse(r.read().decode())
    session_id = events[0][1]["session_id"]
    assert next(p for k, p in events if k == "route")["agent"] == "fallback"
    assert any(k == "delta" for k, _ in events)
    assert [p for k, p in events if k == "message"][-1]["text"] == "general answer"

    with client.stream(
        "POST", "/chat/stream", json={"message": "and in euros?", "session_id": session_id}
    ) as r:
        again = _parse_sse(r.read().decode())
    assert again[0][1]["session_id"] == session_id

    assert client.post("/chat/stream", json={"message": "x", "session_id": "missing"}).status_code == 404
