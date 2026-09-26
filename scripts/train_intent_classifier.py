#!/usr/bin/env python
"""Fine-tune ``bert-base-uncased`` as an 8-way agent intent classifier.

Reads ``data/train.jsonl`` / ``data/test.jsonl`` (produced by
``scripts/prepare_banking77.py``), trains ``BertForSequenceClassification``,
reports per-class precision / recall / F1 on the test split and saves the
model, tokenizer and ``label_map.json`` to ``models/intent_classifier/``.

Device selection: ``--device {auto,cpu,cuda}`` or ``INTENT_TRAIN_DEVICE`` env
var. ``auto`` picks CUDA when available.

Usage::

    python scripts/train_intent_classifier.py --epochs 3 --batch-size 16
    INTENT_TRAIN_DEVICE=cpu python scripts/train_intent_classifier.py --epochs 2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Dataset
from transformers import (
    BertForSequenceClassification,
    BertTokenizerFast,
    get_linear_schedule_with_warmup,
)

logger = logging.getLogger("train_intent_classifier")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT = ROOT / "models" / "intent_classifier"


class JsonlDataset(Dataset):
    def __init__(self, path: Path, tokenizer: BertTokenizerFast, label2id: dict[str, int], max_length: int):
        self.texts: list[str] = []
        self.labels: list[int] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    self.texts.append(row["text"])
                    self.labels.append(label2id[row["label"]])
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        enc = self.tokenizer(
            self.texts[idx],
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "labels": torch.tensor(self.labels[idx], dtype=torch.long),
        }


def read_labels(path: Path) -> list[str]:
    labels: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                labels.add(json.loads(line)["label"])
    return sorted(labels)


def resolve_device(choice: str) -> torch.device:
    if choice == "auto":
        choice = "cuda" if torch.cuda.is_available() else "cpu"
    if choice == "cuda" and not torch.cuda.is_available():
        logger.warning("CUDA requested but not available; falling back to CPU")
        choice = "cpu"
    return torch.device(choice)


@torch.no_grad()
def evaluate(
    model: BertForSequenceClassification,
    loader: DataLoader,
    device: torch.device,
    id2label: dict[int, str],
) -> dict[str, dict[str, float]]:
    model.eval()
    n = len(id2label)
    confusion = [[0] * n for _ in range(n)]
    for batch in loader:
        batch = {k: v.to(device) for k, v in batch.items()}
        logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits
        preds = logits.argmax(dim=-1)
        for gold, pred in zip(batch["labels"].tolist(), preds.tolist()):
            confusion[gold][pred] += 1

    report: dict[str, dict[str, float]] = {}
    correct = sum(confusion[i][i] for i in range(n))
    total = sum(sum(row) for row in confusion)
    for i in range(n):
        tp = confusion[i][i]
        fp = sum(confusion[j][i] for j in range(n)) - tp
        fn = sum(confusion[i]) - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        report[id2label[i]] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(sum(confusion[i])),
        }
    report["_overall"] = {"accuracy": correct / total if total else 0.0, "support": float(total)}
    return report


def print_report(report: dict[str, dict[str, float]]) -> None:
    print(f"\n{'label':<40}{'precision':>10}{'recall':>10}{'f1':>10}{'support':>10}")
    for label, m in report.items():
        if label == "_overall":
            continue
        print(f"{label:<40}{m['precision']:>10.3f}{m['recall']:>10.3f}{m['f1']:>10.3f}{int(m['support']):>10d}")
    print(f"\naccuracy: {report['_overall']['accuracy']:.4f} on {int(report['_overall']['support'])} examples")
    human = report.get("transfer_to_human_agent")
    if human:
        print(f"transfer_to_human_agent recall: {human['recall']:.3f} (this is the safety-critical class)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default="bert-base-uncased")
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train.jsonl")
    parser.add_argument("--test", type=Path, default=DATA_DIR / "test.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=64)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default=os.environ.get("INTENT_TRAIN_DEVICE", "auto"),
    )
    parser.add_argument("--fp16", action="store_true", help="Mixed precision (CUDA only).")
    parser.add_argument("--max-train-samples", type=int, default=None, help="Subsample for smoke tests.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    torch.manual_seed(args.seed)
    device = resolve_device(args.device)
    logger.info("Training on %s", device)

    labels = sorted(set(read_labels(args.train)) | set(read_labels(args.test)))
    label2id = {lbl: i for i, lbl in enumerate(labels)}
    id2label = {i: lbl for lbl, i in label2id.items()}
    logger.info("Labels (%d): %s", len(labels), labels)

    tokenizer = BertTokenizerFast.from_pretrained(args.model_name)
    model = BertForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=len(labels),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    train_ds = JsonlDataset(args.train, tokenizer, label2id, args.max_length)
    test_ds = JsonlDataset(args.test, tokenizer, label2id, args.max_length)
    if args.max_train_samples:
        train_ds.texts = train_ds.texts[: args.max_train_samples]
        train_ds.labels = train_ds.labels[: args.max_train_samples]
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size * 2)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(total_steps * args.warmup_ratio), total_steps
    )
    use_amp = args.fp16 and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    for epoch in range(1, args.epochs + 1):
        model.train()
        start = time.time()
        running = 0.0
        for step, batch in enumerate(train_loader, 1):
            batch = {k: v.to(device) for k, v in batch.items()}
            optimizer.zero_grad()
            with torch.autocast(device_type=device.type, enabled=use_amp):
                loss = model(**batch).loss
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            running += loss.item()
            if step % 50 == 0:
                logger.info("epoch %d step %d/%d loss %.4f", epoch, step, len(train_loader), running / step)
        logger.info("epoch %d done in %.0fs, mean loss %.4f", epoch, time.time() - start, running / max(1, len(train_loader)))
        report = evaluate(model, test_loader, device, id2label)
        logger.info("epoch %d test accuracy %.4f", epoch, report["_overall"]["accuracy"])

    report = evaluate(model, test_loader, device, id2label)
    print_report(report)

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    (args.output / "label_map.json").write_text(
        json.dumps({"id2label": {str(k): v for k, v in id2label.items()}, "label2id": label2id}, indent=2),
        encoding="utf-8",
    )
    (args.output / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Saved model, tokenizer and label_map.json to %s", args.output)


if __name__ == "__main__":
    main()
