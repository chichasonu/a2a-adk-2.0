"""Evaluate trained SetFit routers on the BANKING77 eval split.

Every model is scored at its own granularity and rolled up to agent level
(cards / transactions / accounts / fallback), so a 77-way misroute that stays inside the
correct agent bucket is not an agent-level error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from setfit_router.router import SetFitRouter
from training.scoring import DEFAULT_EVAL_CSV, latency_summary, load_eval_split, markdown_table, score
from training.train import default_output_dir

logger = logging.getLogger(__name__)


def evaluate_setfit(model_dir: Path, df, batch_size: int = 64, latency_samples: int = 50) -> dict:
    router = SetFitRouter(model_dir)
    router.warmup()
    texts = df["text"].tolist()
    preds: list[str] = []
    start = time.perf_counter()
    for i in range(0, len(texts), batch_size):
        preds.extend(label for label, _ in router.predict(texts[i : i + batch_size]))
    batch_seconds = time.perf_counter() - start

    latencies = []
    for text in texts[:latency_samples]:
        t0 = time.perf_counter()
        router.predict([text])
        latencies.append((time.perf_counter() - t0) * 1000)

    report = score(df["intent"].tolist(), preds, router.meta.granularity)
    report.update(
        system=f"setfit:{model_dir.name}",
        model_dir=str(model_dir),
        base_model=router.meta.base_model,
        latency_ms=latency_summary(latencies),
        throughput_per_s=len(texts) / batch_seconds if batch_seconds else None,
        cost_usd=0.0,
    )
    return report | {"predictions": preds}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--model-dir",
        type=Path,
        action="append",
        dest="model_dirs",
        help="repeatable; defaults to every models/setfit-{agent,intent} that exists",
    )
    p.add_argument("--eval-csv", type=Path, default=DEFAULT_EVAL_CSV)
    p.add_argument("--limit", type=int, default=None, help="seeded stratified subsample size")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", type=Path, default=None, help="write JSON report here")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    model_dirs = args.model_dirs or [
        d for d in (default_output_dir("agent"), default_output_dir("intent")) if d.exists()
    ]
    if not model_dirs:
        raise SystemExit("no trained models found; run training/train.py first or pass --model-dir")
    df = load_eval_split(args.eval_csv, args.limit, args.seed)
    reports = [evaluate_setfit(d, df) for d in model_dirs]
    print(markdown_table(reports))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        slim = [{k: v for k, v in r.items() if k != "predictions"} for r in reports]
        args.output.write_text(
            json.dumps(
                {"eval_csv": str(args.eval_csv), "limit": args.limit, "seed": args.seed, "reports": slim},
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
