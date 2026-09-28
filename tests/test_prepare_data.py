from __future__ import annotations

from collections import Counter

import pandas as pd
import pytest

from training.prepare_data import (
    AGENT_LABELS,
    BANKING77_LABELS,
    INTENT_TO_AGENT,
    build_frame,
    few_shot,
    main,
    read_banking77_csv,
)


def test_mapping_covers_all_77_intents_with_valid_agents():
    assert len(INTENT_TO_AGENT) == 77
    assert set(INTENT_TO_AGENT.values()) == set(AGENT_LABELS)
    assert "reverted_card_payment?" in INTENT_TO_AGENT
    assert "Refund_not_showing_up" in INTENT_TO_AGENT


def test_canonical_label_order_matches_banking77_classlabel():
    assert BANKING77_LABELS[0] == "activate_my_card"
    assert BANKING77_LABELS[51] == "Refund_not_showing_up"
    assert BANKING77_LABELS[53] == "reverted_card_payment?"
    assert BANKING77_LABELS[76] == "wrong_exchange_rate_for_cash_withdrawal"


@pytest.mark.parametrize(("granularity", "expected"), [("intent", "card_arrival"), ("agent", "cards")])
def test_build_frame_label_column(granularity, expected):
    df = pd.DataFrame({"text": ["where is my card"], "intent": ["card_arrival"]})
    out = build_frame(df, granularity)
    assert list(out.columns) == ["text", "intent", "label"]
    assert out.loc[0, "label"] == expected


def test_build_frame_rejects_unknown_intent():
    with pytest.raises(ValueError, match="missing from INTENT_TO_AGENT"):
        build_frame(pd.DataFrame({"text": ["x"], "intent": ["not_an_intent"]}), "agent")


def test_few_shot_caps_per_intent_and_is_deterministic():
    df = pd.DataFrame({"text": [f"t{i}" for i in range(30)], "intent": ["a"] * 20 + ["b"] * 10})
    out = few_shot(df, 4, seed=1)
    assert Counter(out["intent"]) == {"a": 4, "b": 4}
    assert out.equals(few_shot(df, 4, seed=1))
    assert Counter(few_shot(df, 15, seed=1)["intent"]) == {"a": 15, "b": 10}


def test_read_csv_accepts_kaggle_category_and_integer_labels(tmp_path):
    named = tmp_path / "named.csv"
    pd.DataFrame({"text": ["hi"], "category": ["card_arrival"]}).to_csv(named, index=False)
    numeric = tmp_path / "numeric.csv"
    pd.DataFrame({"text": ["hi"], "label": [11]}).to_csv(numeric, index=False)
    assert read_banking77_csv(named).loc[0, "intent"] == "card_arrival"
    assert read_banking77_csv(numeric).loc[0, "intent"] == "card_arrival"


def test_cli_csv_source_writes_train_and_eval(tmp_path):
    src = tmp_path / "train.csv"
    rows = [(f"text {i}", intent) for intent in ("card_arrival", "exchange_rate") for i in range(5)]
    pd.DataFrame(rows, columns=["text", "category"]).to_csv(src, index=False)
    out, eval_out = tmp_path / "intents.csv", tmp_path / "eval.csv"
    assert (
        main(
            [
                "--source",
                "csv",
                "--train-csv",
                str(src),
                "--test-csv",
                str(src),
                "--few-shot",
                "2",
                "--out",
                str(out),
                "--eval-out",
                str(eval_out),
            ]
        )
        == 0
    )
    train = pd.read_csv(out)
    assert list(train.columns) == ["text", "intent", "label"]
    assert len(train) == 4
    assert set(train["label"]) == {"cards", "fallback"}
    assert len(pd.read_csv(eval_out)) == 10


def test_sample_data_is_consistent_with_mapping():
    for name in ("intents_sample.csv", "intents_sample_eval.csv"):
        df = pd.read_csv(f"sample_data/{name}")
        assert list(df.columns) == ["text", "intent", "label"]
        assert (df["intent"].map(INTENT_TO_AGENT) == df["label"]).all()
