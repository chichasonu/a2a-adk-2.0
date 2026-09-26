"""Benchmark: prompt-based LLM routing vs TypeSafe System One (Jev) routing.

Both routers see the same labelled utterances (``routing_dataset.json``) and
are measured on the *routing step only* — the part of the supervisor that
decides which sub-agent should handle the request:

* ``llm``      — the supervisor prompt is sent to a chat model through
  OpenRouter with a ``transfer_to_agent`` tool, exactly what the ADK
  ``LlmAgent`` supervisor does on every turn.
* ``typesafe`` — the same utterance is sent to Jev through the OpenRouter
  Decisions API as a typed ``Choice`` over the sub-agents.

Per call we record latency, input/output tokens and the cost reported by
OpenRouter (``usage.cost``), then aggregate accuracy, p50/p95 latency, tokens
and cost and print the relative reduction.

Usage::

    OPENROUTER_API_KEY=sk-or-... python -m benchmarks.route_benchmark \
        [--llm-model google/gemini-2.5-flash] [--runs 1] [--concurrency 4] \
        [--e2e http://localhost:8000] [--out benchmarks/results]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from a2a_adk.config import settings
from a2a_adk.financial_agents import (
    AGENT_CRITERIA,
    SUPERVISOR_INSTRUCTION,
    apply_gates,
    build_typesafe_router,
)

HERE = Path(__file__).resolve().parent
DATASET = HERE / "routing_dataset.json"

TRANSFER_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_agent",
        "description": "Transfer the conversation to another agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "enum": list(AGENT_CRITERIA)}
            },
            "required": ["agent_name"],
        },
    },
}


@dataclass
class Sample:
    router: str
    message: str
    expected: str
    predicted: str
    correct: bool
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    confidence: float | None = None
    error: str | None = None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }


async def route_with_llm(
    client: httpx.AsyncClient, model: str, message: str
) -> Sample:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SUPERVISOR_INSTRUCTION},
            {"role": "user", "content": message},
        ],
        "tools": [TRANSFER_TOOL],
        "tool_choice": "auto",
        "temperature": 0,
        "usage": {"include": True},
    }
    started = time.perf_counter()
    try:
        resp = await client.post(
            f"{settings.OPENROUTER_BASE_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
        )
        latency = (time.perf_counter() - started) * 1000
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return Sample("llm", message, "", "error", False, 0, 0, 0, 0.0, error=str(exc))

    predicted = "no_transfer"
    msg = data["choices"][0]["message"]
    for call in msg.get("tool_calls") or []:
        if call["function"]["name"] == "transfer_to_agent":
            try:
                predicted = json.loads(call["function"]["arguments"])["agent_name"]
            except (KeyError, ValueError):
                predicted = "invalid_arguments"
            break
    usage = data.get("usage") or {}
    return Sample(
        router="llm",
        message=message,
        expected="",
        predicted=predicted,
        correct=False,
        latency_ms=latency,
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        cost_usd=float(usage.get("cost", 0.0) or 0.0),
    )


async def route_with_typesafe(router, message: str) -> Sample:
    try:
        decision = await router.route(message)
    except Exception as exc:  # noqa: BLE001
        return Sample("typesafe", message, "", "error", False, 0, 0, 0, 0.0, error=str(exc))
    return Sample(
        router="typesafe",
        message=message,
        expected="",
        predicted=apply_gates(decision),
        correct=False,
        latency_ms=decision.latency_ms,
        input_tokens=decision.input_tokens,
        output_tokens=decision.output_tokens,
        cost_usd=decision.cost_usd,
        confidence=decision.confidence,
    )


async def e2e_call(client: httpx.AsyncClient, base: str, path: str, message: str) -> float:
    started = time.perf_counter()
    resp = await client.post(
        f"{base}{path}", json={"user_id": "bench", "message": message}, timeout=120
    )
    resp.raise_for_status()
    return (time.perf_counter() - started) * 1000


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def summarize(samples: list[Sample]) -> dict[str, Any]:
    ok = [s for s in samples if s.error is None]
    lat = [s.latency_ms for s in ok]
    return {
        "calls": len(samples),
        "errors": len(samples) - len(ok),
        "accuracy": (sum(s.correct for s in ok) / len(ok)) if ok else 0.0,
        "latency_ms_mean": statistics.fmean(lat) if lat else 0.0,
        "latency_ms_p50": _pct(lat, 50),
        "latency_ms_p95": _pct(lat, 95),
        "input_tokens_total": sum(s.input_tokens for s in ok),
        "output_tokens_total": sum(s.output_tokens for s in ok),
        "tokens_total": sum(s.input_tokens + s.output_tokens for s in ok),
        "tokens_per_call": (
            sum(s.input_tokens + s.output_tokens for s in ok) / len(ok) if ok else 0.0
        ),
        "cost_usd_total": sum(s.cost_usd for s in ok),
        "cost_usd_per_call": (sum(s.cost_usd for s in ok) / len(ok)) if ok else 0.0,
        "cost_usd_per_1m_requests": (
            (sum(s.cost_usd for s in ok) / len(ok)) * 1_000_000 if ok else 0.0
        ),
    }


def reduction(base: float, new: float) -> float:
    return 0.0 if base == 0 else (base - new) / base * 100


def change(base: float, new: float) -> str:
    """Formats the relative change as e.g. ``-75.0%`` (lower) or ``+41.2%`` (higher)."""
    return f"{-reduction(base, new):+.1f}%"


def render_markdown(
    llm_model: str, llm: dict[str, Any], ts: dict[str, Any], e2e: dict[str, Any] | None,
    misroutes: list[Sample],
) -> str:
    rows = [
        ("Routing accuracy", f"{llm['accuracy']:.1%}", f"{ts['accuracy']:.1%}", ""),
        ("Latency p50 (ms)", f"{llm['latency_ms_p50']:.0f}", f"{ts['latency_ms_p50']:.0f}",
         change(llm['latency_ms_p50'], ts['latency_ms_p50'])),
        ("Latency p95 (ms)", f"{llm['latency_ms_p95']:.0f}", f"{ts['latency_ms_p95']:.0f}",
         change(llm['latency_ms_p95'], ts['latency_ms_p95'])),
        ("Latency mean (ms)", f"{llm['latency_ms_mean']:.0f}", f"{ts['latency_ms_mean']:.0f}",
         change(llm['latency_ms_mean'], ts['latency_ms_mean'])),
        ("Input tokens / call", f"{llm['input_tokens_total']/max(llm['calls'],1):.0f}",
         f"{ts['input_tokens_total']/max(ts['calls'],1):.0f}",
         change(llm['input_tokens_total'], ts['input_tokens_total'])),
        ("Output tokens / call", f"{llm['output_tokens_total']/max(llm['calls'],1):.0f}",
         f"{ts['output_tokens_total']/max(ts['calls'],1):.0f}",
         change(llm['output_tokens_total'], ts['output_tokens_total'])),
        ("Total tokens / call", f"{llm['tokens_per_call']:.0f}", f"{ts['tokens_per_call']:.0f}",
         change(llm['tokens_per_call'], ts['tokens_per_call'])),
        ("Cost / call (USD)", f"{llm['cost_usd_per_call']:.6f}", f"{ts['cost_usd_per_call']:.6f}",
         change(llm['cost_usd_per_call'], ts['cost_usd_per_call'])),
        ("Cost / 1M routing requests (USD)", f"{llm['cost_usd_per_1m_requests']:,.2f}",
         f"{ts['cost_usd_per_1m_requests']:,.2f}",
         change(llm['cost_usd_per_1m_requests'], ts['cost_usd_per_1m_requests'])),
        ("Errors", str(llm["errors"]), str(ts["errors"]), ""),
    ]
    out = [
        "# Routing benchmark: prompt-based LLM vs TypeSafe System One (Jev)",
        "",
        f"- Dataset: {llm['calls']} labelled utterances across {len(AGENT_CRITERIA)} sub-agents",
        f"- LLM router: `{llm_model}` with the full supervisor prompt + `transfer_to_agent` tool",
        f"- TypeSafe router: `{settings.TYPESAFE_MODEL}` Choice over {len(AGENT_CRITERIA)} agents + 2 Noul gates",
        "- Latency = client-observed round trip through OpenRouter for the routing call only",
        "- Cost = `usage.cost` reported by OpenRouter per call",
        "",
        "| Metric | Prompt LLM routing | TypeSafe routing | Change |",
        "|---|---:|---:|---:|",
    ]
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    if e2e:
        out += [
            "",
            "## End-to-end (supervisor + sub-agent reply via HTTP)",
            "",
            "| Metric | /run/supervisor/prompt | /run/supervisor/typesafe | Change |",
            "|---|---:|---:|---:|",
            f"| Latency p50 (ms) | {e2e['prompt_p50']:.0f} | {e2e['typesafe_p50']:.0f} | {change(e2e['prompt_p50'], e2e['typesafe_p50'])} |",
            f"| Latency mean (ms) | {e2e['prompt_mean']:.0f} | {e2e['typesafe_mean']:.0f} | {change(e2e['prompt_mean'], e2e['typesafe_mean'])} |",
            f"| Samples | {e2e['n']} | {e2e['n']} | |",
        ]
    if misroutes:
        out += ["", "## Misroutes", "", "| Router | Message | Expected | Predicted |", "|---|---|---|---|"]
        out += [
            f"| {s.router} | {s.message} | {s.expected} | {s.predicted}"
            + (f" (conf {s.confidence:.2f})" if s.confidence is not None else "") + " |"
            for s in misroutes
        ]
    return "\n".join(out) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-model", default=settings.ROUTER_LLM_MODEL)
    parser.add_argument("--runs", type=int, default=1, help="passes over the dataset")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--e2e", default=None, help="base URL of a running server for end-to-end timing")
    parser.add_argument("--e2e-samples", type=int, default=8)
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args()

    if not settings.OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY is required", file=sys.stderr)
        return 2

    dataset = json.loads(DATASET.read_text())
    router = build_typesafe_router()
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(timeout=60) as client:
        async def run_pair(item: dict[str, str]) -> tuple[Sample, Sample]:
            async with sem:
                llm = await route_with_llm(client, args.llm_model, item["message"])
                ts = await route_with_typesafe(router, item["message"])
            for s in (llm, ts):
                s.expected = item["expected"]
                s.correct = s.predicted == item["expected"]
            return llm, ts

        pairs: list[tuple[Sample, Sample]] = []
        for _ in range(args.runs):
            pairs += await asyncio.gather(*(run_pair(item) for item in dataset))

        e2e: dict[str, Any] | None = None
        if args.e2e:
            subset = dataset[: args.e2e_samples]
            prompt_lat = [await e2e_call(client, args.e2e, "/run/supervisor/prompt", d["message"]) for d in subset]
            ts_lat = [await e2e_call(client, args.e2e, "/run/supervisor/typesafe", d["message"]) for d in subset]
            e2e = {
                "n": len(subset),
                "prompt_p50": _pct(prompt_lat, 50),
                "prompt_mean": statistics.fmean(prompt_lat),
                "typesafe_p50": _pct(ts_lat, 50),
                "typesafe_mean": statistics.fmean(ts_lat),
            }

    llm_samples = [p[0] for p in pairs]
    ts_samples = [p[1] for p in pairs]
    llm_summary = summarize(llm_samples)
    ts_summary = summarize(ts_samples)
    misroutes = [s for s in llm_samples + ts_samples if not s.correct and s.error is None]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = render_markdown(args.llm_model, llm_summary, ts_summary, e2e, misroutes)
    (out_dir / "routing_benchmark.md").write_text(report)
    (out_dir / "routing_benchmark.json").write_text(
        json.dumps(
            {
                "llm_model": args.llm_model,
                "typesafe_model": settings.TYPESAFE_MODEL,
                "llm": llm_summary,
                "typesafe": ts_summary,
                "e2e": e2e,
                "samples": [asdict(s) for s in llm_samples + ts_samples],
            },
            indent=2,
        )
    )
    print(report)
    print(f"Wrote {out_dir / 'routing_benchmark.md'} and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
