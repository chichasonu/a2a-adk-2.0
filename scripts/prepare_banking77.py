#!/usr/bin/env python
"""Prepare the intent-routing dataset.

Loads ``PolyAI/banking77`` from the Hugging Face Hub, collapses its 77 intents
into the 8 supervisor agent labels using ``data/intent_mapping.json``, merges
the hand-written seed examples from ``data/financial_intents_seed.jsonl`` and
writes a stratified train/test split to ``data/train.jsonl`` and
``data/test.jsonl``.

Usage::

    python scripts/prepare_banking77.py [--test-size 0.2] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import Counter, defaultdict
from pathlib import Path

from datasets import load_dataset

logger = logging.getLogger("prepare_banking77")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MAPPING_PATH = DATA_DIR / "intent_mapping.json"
SEED_PATH = DATA_DIR / "financial_intents_seed.jsonl"
TRAIN_PATH = DATA_DIR / "train.jsonl"
TEST_PATH = DATA_DIR / "test.jsonl"

AGENT_LABELS = [
    "card_management_agent",
    "account_management_agent",
    "transfer_to_human_agent",
    "answer_hub_agent",
    "transaction_agent",
    "rates_fees_limits_management_agent",
    "spending_insights_agent",
    "fallback_agent",
]


def load_mapping() -> dict[str, str]:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    unknown = set(mapping.values()) - set(AGENT_LABELS)
    if unknown:
        raise ValueError(f"intent_mapping.json contains unknown agent labels: {unknown}")
    return mapping


def load_jsonl(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_banking77(mapping: dict[str, str]) -> list[dict[str, str]]:
    ds = load_dataset("PolyAI/banking77")
    label_names: list[str] = ds["train"].features["label"].names
    missing = set(label_names) - set(mapping)
    if missing:
        raise ValueError(f"intent_mapping.json is missing Banking77 intents: {sorted(missing)}")

    rows: list[dict[str, str]] = []
    for split in ("train", "test"):
        for example in ds[split]:
            intent = label_names[example["label"]]
            rows.append({"text": example["text"], "label": mapping.get(intent, "fallback_agent")})
    logger.info("Loaded %d Banking77 examples", len(rows))
    return rows


def stratified_split(
    rows: list[dict[str, str]], test_size: float, seed: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rng = random.Random(seed)
    by_label: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_label[row["label"]].append(row)

    train: list[dict[str, str]] = []
    test: list[dict[str, str]] = []
    for label, items in sorted(by_label.items()):
        rng.shuffle(items)
        # Keep at least one example of every label in each split.
        n_test = max(1, int(round(len(items) * test_size))) if len(items) > 1 else 0
        test.extend(items[:n_test])
        train.extend(items[n_test:])
    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-banking77",
        action="store_true",
        help="Only use the seed dataset (useful offline / for smoke tests).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    mapping = load_mapping()
    rows: list[dict[str, str]] = []
    if not args.skip_banking77:
        rows.extend(load_banking77(mapping))

    seed_rows = load_jsonl(SEED_PATH)
    for row in seed_rows:
        # Accept both {"text","label"} and the original {"message","expected"} shape.
        text = row.get("text") or row.get("message")
        label = row.get("label") or row.get("expected")
        if not text or not label:
            raise ValueError(f"Malformed seed row: {row}")
        if label not in AGENT_LABELS:
            raise ValueError(f"Seed row has unknown label {label!r}: {row}")
        rows.append({"text": text, "label": label})
    logger.info("Loaded %d seed examples", len(seed_rows))

    # Deduplicate on text (Banking77 test split overlaps a little with train).
    seen: set[str] = set()
    unique_rows = []
    for row in rows:
        key = row["text"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)

    train, test = stratified_split(unique_rows, args.test_size, args.seed)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(TRAIN_PATH, train)
    write_jsonl(TEST_PATH, test)

    logger.info("Wrote %d train rows to %s", len(train), TRAIN_PATH)
    logger.info("Wrote %d test rows to %s", len(test), TEST_PATH)
    logger.info("Train label distribution: %s", dict(Counter(r["label"] for r in train)))
    logger.info("Test label distribution:  %s", dict(Counter(r["label"] for r in test)))
    absent = [lbl for lbl in AGENT_LABELS if lbl not in {r["label"] for r in unique_rows}]
    if absent:
        logger.warning("No examples for labels: %s", absent)


if __name__ == "__main__":
    main()
