"""Benchmark SetFit routers against an OpenRouter LLM on the same BANKING77 eval split.

Every system is reported at its own granularity (77-way intent or agent) and rolled up to
agent level (cards / transactions / accounts / fallback).

    OPENROUTER_API_KEY=... python benchmark/run_benchmark.py --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from benchmark.prompts import parse_label, system_prompt
from training.evaluate import evaluate_setfit
from training.scoring import DEFAULT_EVAL_CSV, latency_summary, load_eval_split, markdown_table, score
from training.train import default_output_dir

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LLM = "google/gemini-3.5-flash-lite"
INVALID = "__invalid__"


async def _classify_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    model: str,
    system: str,
    text: str,
    granularity: str,
    max_retries: int,
) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 64,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "usage": {"include": True},
    }
    async with sem:
        for attempt in range(max_retries + 1):
            start = time.perf_counter()
            try:
                resp = await client.post(OPENROUTER_URL, json=body)
            except httpx.HTTPError as exc:
                err = f"{type(exc).__name__}: {exc}"
            else:
                latency = (time.perf_counter() - start) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data["choices"][0]["message"].get("content") or ""
                    usage = data.get("usage") or {}
                    return {
                        "raw": raw,
                        "label": parse_label(raw, granularity) or INVALID,
                        "latency_ms": latency,
                        "cost": usage.get("cost"),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                    }
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code not in {408, 429, 500, 502, 503, 504}:
                    break
            await asyncio.sleep(min(2**attempt, 30))
    logger.warning("LLM request failed: %s", err)
    return {"raw": "", "label": INVALID, "latency_ms": None, "cost": None, "error": err}


async def evaluate_llm(
    df: pd.DataFrame,
    granularity: str,
    model: str,
    api_key: str,
    concurrency: int = 8,
    max_retries: int = 4,
    timeout_s: float = 60.0,
) -> dict:
    system = system_prompt(granularity)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/chichasonu/a2a-adk-2.0",
        "X-Title": "adk-setfit-router benchmark",
    }
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=headers, timeout=timeout_s) as client:
        results = await asyncio.gather(
            *(
                _classify_one(client, sem, model, system, text, granularity, max_retries)
                for text in df["text"].tolist()
            )
        )
    preds = [r["label"] for r in results]
    report = score(df["intent"].tolist(), preds, granularity)
    costs = [r["cost"] for r in results if r.get("cost") is not None]
    report.update(
        system=f"llm:{model}",
        model=model,
        latency_ms=latency_summary([r["latency_ms"] for r in results if r["latency_ms"] is not None]),
        cost_usd=sum(costs) if costs else None,
        request_errors=sum("error" in r for r in results),
        prompt_tokens=sum(r.get("prompt_tokens") or 0 for r in results),
        completion_tokens=sum(r.get("completion_tokens") or 0 for r in results),
    )
    return report | {"predictions": preds, "raw": [r["raw"] for r in results]}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--eval-csv", type=Path, default=DEFAULT_EVAL_CSV)
    p.add_argument(
        "--limit", type=int, default=None, help="seeded stratified subsample (shared by all systems)"
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--setfit-model",
        type=Path,
        action="append",
        dest="setfit_models",
        help="repeatable; defaults to every models/setfit-{agent,intent} that exists",
    )
    p.add_argument("--llm-model", default=DEFAULT_LLM)
    p.add_argument("--llm-granularity", choices=("intent", "agent", "both"), default="both")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--skip-llm", action="store_true")
    p.add_argument("--skip-setfit", action="store_true")
    p.add_argument("--output-dir", type=Path, default=ROOT / "benchmark" / "results")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parse_args(argv)
    df = load_eval_split(args.eval_csv, args.limit, args.seed)
    logger.info("eval split: %d rows, %d intents from %s", len(df), df["intent"].nunique(), args.eval_csv)
    reports: list[dict] = []

    if not args.skip_setfit:
        models = args.setfit_models or [
            d for d in (default_output_dir("agent"), default_output_dir("intent")) if d.exists()
        ]
        if not models:
            logger.warning("no SetFit models found; train with training/train.py or pass --setfit-model")
        reports.extend(evaluate_setfit(m, df) for m in models)

    if not args.skip_llm:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY is not set (or pass --skip-llm)")
        granularities = ("intent", "agent") if args.llm_granularity == "both" else (args.llm_granularity,)
        for g in granularities:
            logger.info("querying %s at %s granularity", args.llm_model, g)
            reports.append(asyncio.run(evaluate_llm(df, g, args.llm_model, api_key, args.concurrency)))

    table = markdown_table(reports)
    print(table)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "eval_csv": str(args.eval_csv),
        "limit": args.limit,
        "seed": args.seed,
        "n": len(df),
        "reports": [{k: v for k, v in r.items() if k not in {"predictions", "raw"}} for r in reports],
    }
    (args.output_dir / f"benchmark_{stamp}.json").write_text(json.dumps(summary, indent=2))
    (args.output_dir / f"benchmark_{stamp}.md").write_text(table + "\n")
    preds = df[["text", "intent"]].copy()
    for r in reports:
        preds[r["system"] + ":" + r["granularity"]] = r["predictions"]
    preds.to_csv(args.output_dir / f"predictions_{stamp}.csv", index=False)
    logger.info("wrote results to %s", args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
