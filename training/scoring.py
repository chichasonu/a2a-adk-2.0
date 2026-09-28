"""Shared eval-split loading and intent/agent roll-up scoring for SetFit and LLM runs."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix, f1_score

from training.prepare_data import AGENT_LABELS, DEFAULT_EVAL_OUT, INTENT_TO_AGENT, intent_to_agent

DEFAULT_EVAL_CSV = DEFAULT_EVAL_OUT


def load_eval_split(path: Path = DEFAULT_EVAL_CSV, limit: int | None = None, seed: int = 42) -> pd.DataFrame:
    """Load the eval CSV; ``limit`` draws a seeded stratified-by-intent subsample.

    The same (path, limit, seed) always yields the same rows, so SetFit and LLM runs
    are scored on an identical split.
    """
    df = pd.read_csv(path)
    missing = {"text", "intent"} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    df = df.reset_index(drop=True)
    if limit is not None and limit < len(df):
        quotas = _stratified_quotas(df["intent"].value_counts().sort_index(), limit)
        shuffled = df.sample(frac=1.0, random_state=seed)
        rank = shuffled.groupby("intent").cumcount()
        keep = rank < shuffled["intent"].map(quotas)
        df = shuffled[keep].sort_index().reset_index(drop=True)
    return df


def _stratified_quotas(sizes: pd.Series, limit: int) -> dict[str, int]:
    """Largest-remainder allocation of ``limit`` rows across intents, proportional to size."""
    exact = sizes * (limit / sizes.sum())
    quotas = exact.astype(int)
    remainder = (exact - quotas).sort_values(ascending=False, kind="stable")
    for intent in remainder.index[: limit - int(quotas.sum())]:
        quotas[intent] += 1
    return quotas.to_dict()


def score(
    intents_true: Sequence[str],
    predictions: Sequence[str],
    granularity: str,
) -> dict:
    """Score predictions at their own granularity and rolled up to agents.

    ``predictions`` are intents when ``granularity == "intent"`` and agents otherwise.
    Unknown predictions (e.g. unparseable LLM output) count as errors at every level.
    """
    true_intents = list(intents_true)
    preds = list(predictions)
    if len(true_intents) != len(preds):
        raise ValueError("intents_true and predictions must have equal length")
    n = len(preds)
    true_agents = [intent_to_agent(i) for i in true_intents]
    if granularity == "intent":
        pred_agents = [intent_to_agent(p) if p in INTENT_TO_AGENT else "__invalid__" for p in preds]
    elif granularity == "agent":
        pred_agents = [p if p in AGENT_LABELS else "__invalid__" for p in preds]
    else:
        raise ValueError(f"unknown granularity {granularity!r}")

    report: dict = {"granularity": granularity, "n": n}
    agent_correct = sum(t == p for t, p in zip(true_agents, pred_agents, strict=True))
    report["agent_accuracy"] = agent_correct / n if n else 0.0
    report["agent_macro_f1"] = (
        float(f1_score(true_agents, pred_agents, labels=list(AGENT_LABELS), average="macro", zero_division=0))
        if n
        else 0.0
    )
    report["invalid_predictions"] = sum(p == "__invalid__" for p in pred_agents)
    if granularity == "intent":
        intent_correct = sum(t == p for t, p in zip(true_intents, preds, strict=True))
        report["intent_accuracy"] = intent_correct / n if n else 0.0
        report["intent_errors"] = n - intent_correct
        report["intent_errors_within_agent"] = sum(
            t != p and ta == pa
            for t, p, ta, pa in zip(true_intents, preds, true_agents, pred_agents, strict=True)
        )
    else:
        report["intent_accuracy"] = None
    report["agent_errors"] = n - agent_correct
    report["per_agent"] = {
        k: v
        for k, v in classification_report(
            true_agents, pred_agents, labels=list(AGENT_LABELS), output_dict=True, zero_division=0
        ).items()
        if k in AGENT_LABELS
    }
    report["agent_confusion"] = {
        "labels": list(AGENT_LABELS),
        "matrix": confusion_matrix(true_agents, pred_agents, labels=list(AGENT_LABELS)).tolist(),
    }
    return report


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.2f}%"


def markdown_table(rows: Sequence[dict]) -> str:
    """Render a comparison table from reports that carry a ``system`` key."""
    header = (
        "| system | granularity | n | intent acc (77-way) | agent acc (3 + fallback) "
        "| agent macro-F1 | within-agent intent misroutes | p50 ms | p95 ms | cost USD |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in rows:
        lat = r.get("latency_ms", {})
        cost = r.get("cost_usd")
        lines.append(
            f"| {r.get('system', '?')} | {r['granularity']} | {r['n']} | {_pct(r.get('intent_accuracy'))} "
            f"| {_pct(r['agent_accuracy'])} | {r['agent_macro_f1']:.3f} "
            f"| {r.get('intent_errors_within_agent', '—')} "
            f"| {lat.get('p50', float('nan')):.1f} | {lat.get('p95', float('nan')):.1f} "
            f"| {'—' if cost is None else f'{cost:.4f}'} |"
        )
    return "\n".join(lines)


def latency_summary(latencies_ms: Sequence[float]) -> dict:
    if not latencies_ms:
        return {}
    s = pd.Series(latencies_ms)
    return {"mean": float(s.mean()), "p50": float(s.quantile(0.5)), "p95": float(s.quantile(0.95))}
