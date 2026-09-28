"""Train a SetFit router on the BANKING77-derived CSV.

Targets are derived from the CSV's ``intent`` column, so a single ``intents.csv`` trains
either granularity. ``--granularity agent`` (default) is the production router
(cards / transactions / accounts / fallback); ``intent`` trains the 77-way classifier
whose predictions are rolled up to agents at routing time.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from setfit_router.router import RouterMeta
from training.prepare_data import (
    AGENT_LABELS,
    DEFAULT_TRAIN_OUT,
    GRANULARITIES,
    INTENT_TO_AGENT,
    few_shot,
    target_label,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_MODEL = "BAAI/bge-small-en-v1.5"


def default_output_dir(granularity: str) -> Path:
    return ROOT / "models" / f"setfit-{granularity}"


def load_training_frame(path: Path, granularity: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "intent" in df.columns:
        df = df.assign(target=df["intent"].map(lambda i: target_label(i, granularity)))
    elif granularity == "agent" and "label" in df.columns:
        df = df.assign(target=df["label"], intent=df["label"])
    else:
        raise ValueError(f"{path}: needs an `intent` column to train at {granularity} granularity")
    return df


def train(
    train_csv: Path,
    granularity: str,
    output_dir: Path,
    base_model: str = DEFAULT_BASE_MODEL,
    num_iterations: int = 20,
    num_epochs: int = 1,
    batch_size: int = 16,
    max_steps: int = -1,
    few_shot_n: int | None = None,
    seed: int = 42,
) -> Path:
    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments

    df = load_training_frame(train_csv, granularity)
    if few_shot_n is not None:
        df = few_shot(df, few_shot_n, seed)
    labels = sorted(INTENT_TO_AGENT, key=str.lower) if granularity == "intent" else list(AGENT_LABELS)
    present = set(df["target"])
    labels = [label for label in labels if label in present]
    label_ids = {label: i for i, label in enumerate(labels)}
    logger.info(
        "training %s router: %d rows, %d labels, base=%s", granularity, len(df), len(labels), base_model
    )

    dataset = Dataset.from_dict({"text": df["text"].tolist(), "label": df["target"].map(label_ids).tolist()})
    model = SetFitModel.from_pretrained(base_model, labels=labels)
    args = TrainingArguments(
        batch_size=batch_size,
        num_epochs=num_epochs,
        num_iterations=num_iterations,
        max_steps=max_steps,
        seed=seed,
        report_to="none",
    )
    trainer = Trainer(model=model, args=args, train_dataset=dataset)
    start = time.perf_counter()
    trainer.train()
    elapsed = time.perf_counter() - start

    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output_dir))
    RouterMeta(
        granularity=granularity,
        labels=labels,
        intent_to_agent=dict(INTENT_TO_AGENT),
        base_model=base_model,
    ).save(
        output_dir,
        train_csv=str(train_csv),
        train_rows=len(df),
        few_shot=few_shot_n,
        num_iterations=num_iterations,
        num_epochs=num_epochs,
        seed=seed,
        train_seconds=round(elapsed, 1),
    )
    logger.info("saved %s router to %s (%.1fs)", granularity, output_dir, elapsed)
    return output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train-csv", type=Path, default=DEFAULT_TRAIN_OUT)
    p.add_argument("--granularity", choices=(*GRANULARITIES, "both"), default="agent")
    p.add_argument("--output-dir", type=Path, default=None, help="defaults to models/setfit-<granularity>")
    p.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    p.add_argument("--num-iterations", type=int, default=20, help="contrastive pairs per example")
    p.add_argument("--num-epochs", type=int, default=1)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--max-steps", type=int, default=-1, help="cap embedding fine-tuning steps")
    p.add_argument(
        "--few-shot",
        type=int,
        default=None,
        metavar="N",
        help="subsample N examples per intent before training",
    )
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    granularities = GRANULARITIES if args.granularity == "both" else (args.granularity,)
    if args.output_dir is not None and len(granularities) > 1:
        raise SystemExit("--output-dir cannot be combined with --granularity both")
    saved = [
        str(
            train(
                train_csv=args.train_csv,
                granularity=g,
                output_dir=args.output_dir or default_output_dir(g),
                base_model=args.base_model,
                num_iterations=args.num_iterations,
                num_epochs=args.num_epochs,
                batch_size=args.batch_size,
                max_steps=args.max_steps,
                few_shot_n=args.few_shot,
                seed=args.seed,
            )
        )
        for g in granularities
    ]
    print(json.dumps({"saved": saved}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
