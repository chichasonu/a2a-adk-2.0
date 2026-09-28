from __future__ import annotations

import pandas as pd

from training.scoring import load_eval_split, markdown_table, score


def test_intent_misroute_within_same_agent_is_not_an_agent_error():
    truth = ["card_arrival", "card_arrival", "exchange_rate", "terminate_account"]
    preds = ["card_arrival", "card_delivery_estimate", "exchange_charge", "request_refund"]
    r = score(truth, preds, "intent")
    assert r["intent_accuracy"] == 0.25
    assert r["agent_accuracy"] == 0.75
    assert r["intent_errors"] == 3
    assert r["intent_errors_within_agent"] == 2
    assert r["agent_errors"] == 1


def test_agent_granularity_and_invalid_predictions():
    r = score(["card_arrival", "exchange_rate"], ["cards", "garbage"], "agent")
    assert r["intent_accuracy"] is None
    assert r["agent_accuracy"] == 0.5
    assert r["invalid_predictions"] == 1
    assert r["agent_confusion"]["labels"] == ["cards", "transactions", "accounts", "fallback"]
    assert "|" in markdown_table([{**r, "system": "x"}])


def test_eval_split_limit_is_stratified_and_seeded(tmp_path):
    path = tmp_path / "eval.csv"
    rows = [(f"{i}", intent) for intent in ("card_arrival", "exchange_rate", "change_pin") for i in range(40)]
    pd.DataFrame(rows, columns=["text", "intent"]).to_csv(path, index=False)
    a = load_eval_split(path, limit=30, seed=7)
    assert len(a) == 30
    assert a["intent"].value_counts().to_dict() == {"card_arrival": 10, "exchange_rate": 10, "change_pin": 10}
    assert a.equals(load_eval_split(path, limit=30, seed=7))
    assert len(load_eval_split(path)) == 120
