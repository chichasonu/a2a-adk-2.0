from __future__ import annotations

from benchmark.prompts import INTENT_DESCRIPTIONS, parse_label, system_prompt, valid_labels
from training.prepare_data import INTENT_TO_AGENT


def test_every_intent_has_a_description():
    assert set(INTENT_DESCRIPTIONS) == set(INTENT_TO_AGENT)


def test_prompts_list_labels_for_granularity():
    intent_prompt = system_prompt("intent")
    assert all(f"- {label}: " in intent_prompt for label in INTENT_TO_AGENT)
    agent_prompt = system_prompt("agent")
    assert all(f"- {label}: " in agent_prompt for label in valid_labels("agent"))
    assert "card_arrival" not in agent_prompt


def test_parse_label_variants():
    assert parse_label('{"label": "cards"}', "agent") == "cards"
    assert parse_label('```json\n{"label": "Transactions"}\n```', "agent") == "transactions"
    assert parse_label("accounts", "agent") == "accounts"
    assert parse_label('{"label": "reverted_card_payment?"}', "intent") == "reverted_card_payment?"
    assert parse_label("I think pending_top_up fits", "intent") == "pending_top_up"
    assert parse_label("no idea", "agent") is None
