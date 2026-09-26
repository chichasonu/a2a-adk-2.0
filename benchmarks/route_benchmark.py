"""Benchmark: prompt-based LLM routing vs System One routing (Jev, optionally CLM-8B).

All routers see the same labelled utterances (``routing_dataset.json``) and
are measured on the *routing step only* — the part of the supervisor that
decides which sub-agent should handle the request:

* ``llm``      — the supervisor prompt is sent to a chat model through
  OpenRouter with a ``transfer_to_agent`` tool, exactly what the ADK
  ``LlmAgent`` supervisor does on every turn.
* ``typesafe`` — the same utterance is sent to Jev through the OpenRouter
  Decisions API as a typed ``Choice`` over the sub-agents.
* ``contrastive`` — (only with ``--clm-url`` / ``CLM_BASE_URL``) the same typed
  request is sent to a self-hosted ``clm-serve`` (Contrastive Language Model,
  CLM-8B). Needs a GPU host; its API-level cost is 0, GPU cost is not counted.

Per call we record latency, input/output tokens and the cost reported by
OpenRouter (``usage.cost``), then aggregate accuracy, p50/p95 latency, tokens
and cost and print the relative reduction.

Usage::

    OPENROUTER_API_KEY=sk-or-... python -m benchmarks.route_benchmark \
        [--llm-model google/gemini-2.5-flash] [--runs 1] [--concurrency 4] \
        [--clm-url http://gpu-host:8700] [--e2e http://localhost:8000] [--out benchmarks/results]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    build_contrastive_router,
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



async def route_with_system_one(router, message: str) -> Sample:
    """Routes with any System One router (TypeSafe Jev, Contrastive LM)."""
    try:
        decision = await router.route(message)
    except Exception as exc:  # noqa: BLE001
        return Sample(router.name, message, "", "error", False, 0, 0, 0, 0.0, error=str(exc))
    return Sample(
        router=router.name,
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


ROUTER_LABELS = {
    "llm": "Prompt LLM routing",
    "typesafe": "TypeSafe (Jev) routing",
    "contrastive": "Contrastive LM routing",
}

# (label, summary key, formatter, compare?)
_METRICS: list[tuple[str, str, str, bool]] = [
    ("Routing accuracy", "accuracy", "{:.1%}", False),
    ("Latency p50 (ms)", "latency_ms_p50", "{:.0f}", True),
    ("Latency p95 (ms)", "latency_ms_p95", "{:.0f}", True),
    ("Latency mean (ms)", "latency_ms_mean", "{:.0f}", True),
    ("Input tokens / call", "input_tokens_per_call", "{:.0f}", True),
    ("Output tokens / call", "output_tokens_per_call", "{:.0f}", True),
    ("Total tokens / call", "tokens_per_call", "{:.0f}", True),
    ("Cost / call (USD)", "cost_usd_per_call", "{:.6f}", True),
    ("Cost / 1M routing requests (USD)", "cost_usd_per_1m_requests", "{:,.2f}", True),
    ("Errors", "errors", "{}", False),
]


def _with_per_call(summary: dict[str, Any]) -> dict[str, Any]:
    n = max(summary["calls"], 1)
    return {
        **summary,
        "input_tokens_per_call": summary["input_tokens_total"] / n,
        "output_tokens_per_call": summary["output_tokens_total"] / n,
    }


def render_markdown(
    llm_model: str,
    summaries: dict[str, dict[str, Any]],
    e2e: dict[str, dict[str, float]] | None,
    misroutes: list[Sample],
) -> str:
    """``summaries`` is ``{"llm": ..., "typesafe": ..., ["contrastive": ...]}``.

    Every System One router is compared against the ``llm`` baseline; the
    Change columns are ``<router> vs prompt LLM``.
    """
    names = list(summaries)
    others = [n for n in names if n != "llm"]
    rows = {n: _with_per_call(summaries[n]) for n in names}
    header = ["Metric"] + [ROUTER_LABELS.get(n, n) for n in names]
    header += [f"{ROUTER_LABELS.get(n, n)} vs LLM" for n in others]
    out = [
        "# Routing benchmark: prompt-based LLM vs System One routers",
        "",
        f"- Dataset: {summaries['llm']['calls']} labelled utterances across {len(AGENT_CRITERIA)} sub-agents",
        f"- LLM router: `{llm_model}` with the full supervisor prompt + `transfer_to_agent` tool",
    ]
    if "typesafe" in summaries:
        out.append(
            f"- TypeSafe router: `{settings.TYPESAFE_MODEL}` Choice over {len(AGENT_CRITERIA)} agents + 2 Noul gates (OpenRouter Decisions API)"
        )
    if "contrastive" in summaries:
        out.append(
            f"- Contrastive router: `{settings.CLM_MODEL}` (CLM-8B via `clm-serve`), same typed questions; self-hosted so cost is reported as 0"
        )
    out += [
        "- Latency = client-observed round trip for the routing call only",
        "- Cost = `usage.cost` reported by the provider per call",
        "",
        "| " + " | ".join(header) + " |",
        "|---|" + "---:|" * (len(header) - 1),
    ]
    for label, key, fmt, compare in _METRICS:
        cells = [label] + [fmt.format(rows[n][key]) for n in names]
        cells += [
            change(rows["llm"][key], rows[n][key]) if compare else "" for n in others
        ]
        out.append("| " + " | ".join(cells) + " |")
    if e2e:
        kinds = list(e2e)
        e2e_others = [k for k in kinds if k != "prompt"]
        hdr = ["Metric"] + [f"/run/supervisor/{k}" for k in kinds]
        hdr += [f"{k} vs prompt" for k in e2e_others]
        out += [
            "",
            "## End-to-end (supervisor + sub-agent reply via HTTP)",
            "",
            "| " + " | ".join(hdr) + " |",
            "|---|" + "---:|" * (len(hdr) - 1),
        ]
        for label, key in (("Latency p50 (ms)", "p50"), ("Latency mean (ms)", "mean")):
            cells = [label] + [f"{e2e[k][key]:.0f}" for k in kinds]
            cells += [change(e2e["prompt"][key], e2e[k][key]) for k in e2e_others]
            out.append("| " + " | ".join(cells) + " |")
        out.append("| Samples | " + " | ".join(str(int(e2e[k]["n"])) for k in kinds) + " |" + " |" * len(e2e_others))
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
    parser.add_argument(
        "--clm-url",
        default=settings.CLM_BASE_URL or None,
        help="base URL of a running clm-serve (e.g. http://gpu-host:8700); adds the Contrastive LM router",
    )
    parser.add_argument(
        "--no-typesafe",
        action="store_true",
        help="skip the TypeSafe (Jev) router, e.g. for a pure prompt-LLM vs CLM comparison",
    )
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args()

    if not settings.OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY is required", file=sys.stderr)
        return 2
    if args.no_typesafe and not args.clm_url:
        print("--no-typesafe requires --clm-url (nothing to compare against the LLM)", file=sys.stderr)
        return 2

    dataset = json.loads(DATASET.read_text())
    routers = [] if args.no_typesafe else [build_typesafe_router()]
    if args.clm_url:
        routers.append(build_contrastive_router(base_url=args.clm_url))
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(timeout=60) as client:
        async def run_item(item: dict[str, str]) -> list[Sample]:
            async with sem:
                samples = [await route_with_llm(client, args.llm_model, item["message"])]
                for router in routers:
                    samples.append(await route_with_system_one(router, item["message"]))
            for s in samples:
                s.expected = item["expected"]
                s.correct = s.predicted == item["expected"]
            return samples

        groups: list[list[Sample]] = []
        for _ in range(args.runs):
            groups += await asyncio.gather(*(run_item(item) for item in dataset))

        e2e: dict[str, dict[str, float]] | None = None
        if args.e2e:
            subset = dataset[: args.e2e_samples]
            e2e = {}
            for kind in ["prompt"] + [r.name for r in routers]:
                lat = [
                    await e2e_call(client, args.e2e, f"/run/supervisor/{kind}", d["message"])
                    for d in subset
                ]
                e2e[kind] = {"n": len(subset), "p50": _pct(lat, 50), "mean": statistics.fmean(lat)}

    by_router: dict[str, list[Sample]] = {"llm": []}
    for r in routers:
        by_router[r.name] = []
    for group in groups:
        for s in group:
            by_router[s.router].append(s)
    summaries = {name: summarize(samples) for name, samples in by_router.items()}
    all_samples = [s for samples in by_router.values() for s in samples]
    misroutes = [s for s in all_samples if not s.correct and s.error is None]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = render_markdown(args.llm_model, summaries, e2e, misroutes)
    (out_dir / "routing_benchmark.md").write_text(report)
    (out_dir / "routing_benchmark.json").write_text(
        json.dumps(
            {
                "llm_model": args.llm_model,
                "typesafe_model": None if args.no_typesafe else settings.TYPESAFE_MODEL,
                "clm_model": settings.CLM_MODEL if args.clm_url else None,
                "clm_url": args.clm_url,
                **summaries,
                "e2e": e2e,
                "samples": [asdict(s) for s in all_samples],
            },
            indent=2,
        )
    )
    print(report)
    print(f"Wrote {out_dir / 'routing_benchmark.md'} and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
