from __future__ import annotations

from setfit_router.metrics import RoutingMetrics, main, read_log
from setfit_router.router import RouteDecision


def _decision(agent: str, low: bool = False) -> RouteDecision:
    return RouteDecision(
        agent=agent,
        confidence=0.3 if low else 0.9,
        predicted_label=agent,
        intent=None,
        granularity="agent",
        low_confidence=low,
        latency_ms=4.0,
    )


def test_record_writes_jsonl_and_summarizes(tmp_path, capsys):
    log = tmp_path / "routing.jsonl"
    m = RoutingMetrics(log)
    m.record(
        _decision("cards"), request_id="r1", session_id="s", user_id="u", text="secret", total_latency_ms=50
    )
    m.record(_decision("fallback", low=True), request_id="r2", session_id="s", user_id="u", text="x")
    records = read_log(log)
    assert [r["agent"] for r in records] == ["cards", "fallback"]
    assert "text" not in records[0]
    s = m.summary()
    assert s["requests"] == 2
    assert s["by_agent"] == {"cards": 1, "fallback": 1}
    assert s["low_confidence"] == 1
    assert main([str(log)]) == 0
    assert '"requests": 2' in capsys.readouterr().out
