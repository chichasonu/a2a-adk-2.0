"""Append-only JSONL routing metrics log plus in-process aggregates."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from setfit_router.router import RouteDecision


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def summarize(records: list[dict]) -> dict:
    router_ms = [r["router_latency_ms"] for r in records if r.get("router_latency_ms") is not None]
    total_ms = [r["total_latency_ms"] for r in records if r.get("total_latency_ms") is not None]
    return {
        "requests": len(records),
        "by_agent": dict(Counter(r["agent"] for r in records)),
        "low_confidence": sum(bool(r.get("low_confidence")) for r in records),
        "errors": sum(r.get("status") == "error" for r in records),
        "mean_confidence": (sum(r["confidence"] for r in records) / len(records)) if records else None,
        "router_latency_ms": {"p50": _percentile(router_ms, 0.5), "p95": _percentile(router_ms, 0.95)},
        "total_latency_ms": {"p50": _percentile(total_ms, 0.5), "p95": _percentile(total_ms, 0.95)},
    }


class RoutingMetrics:
    """Writes one JSON line per routed request and keeps the records for ``/metrics``."""

    def __init__(self, log_path: Path | None, log_text: bool = False, keep_last: int = 10_000) -> None:
        self.log_path = log_path
        self.log_text = log_text
        self.keep_last = keep_last
        self._records: list[dict] = []
        self._lock = threading.Lock()
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        decision: RouteDecision,
        *,
        request_id: str,
        session_id: str,
        user_id: str,
        text: str,
        status: str = "ok",
        total_latency_ms: float | None = None,
    ) -> dict:
        entry = {
            "ts": time.time(),
            "request_id": request_id,
            "session_id": session_id,
            "user_id": user_id,
            "agent": decision.agent,
            "predicted_label": decision.predicted_label,
            "intent": decision.intent,
            "granularity": decision.granularity,
            "confidence": round(decision.confidence, 4),
            "low_confidence": decision.low_confidence,
            "router_latency_ms": round(decision.latency_ms, 2),
            "total_latency_ms": None if total_latency_ms is None else round(total_latency_ms, 2),
            "status": status,
            "text_chars": len(text),
        }
        if self.log_text:
            entry["text"] = text
        with self._lock:
            self._records.append(entry)
            del self._records[: -self.keep_last]
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
        return entry

    def summary(self) -> dict:
        with self._lock:
            return summarize(list(self._records))


def read_log(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarize a routing metrics JSONL log.")
    p.add_argument("log", type=Path)
    args = p.parse_args(argv)
    print(json.dumps(summarize(read_log(args.log)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
