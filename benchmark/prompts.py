"""Label lists and prompts for LLM classification at intent or agent granularity."""

from __future__ import annotations

import json
import re

from training.prepare_data import AGENT_LABELS, INTENT_TO_AGENT

# Short gloss per BANKING77 intent; raw label names alone are too terse for 77-way prompting.
INTENT_DESCRIPTIONS: dict[str, str] = {
    "activate_my_card": "how to activate a newly received card",
    "age_limit": "minimum age requirements to open or use an account",
    "apple_pay_or_google_pay": "adding the card to Apple Pay or Google Pay",
    "atm_support": "which ATMs can be used / ATM availability",
    "automatic_top_up": "setting up or using auto top-up",
    "balance_not_updated_after_bank_transfer": "balance not updated after a bank transfer in",
    "balance_not_updated_after_cheque_or_cash_deposit": "balance not updated after a cheque or cash deposit",
    "beneficiary_not_allowed": "unable to add or pay a beneficiary / payee",
    "cancel_transfer": "cancel or reverse a transfer that was just made",
    "card_about_to_expire": "card expiring soon, getting a renewal",
    "card_acceptance": "where the card is accepted by merchants",
    "card_arrival": "ordered card has not arrived yet",
    "card_delivery_estimate": "how long card delivery takes",
    "card_linking": "linking an existing card to the app/account",
    "card_not_working": "card not working in general",
    "card_payment_fee_charged": "unexpected fee charged on a card payment",
    "card_payment_not_recognised": "card payment the customer does not recognise",
    "card_payment_wrong_exchange_rate": "wrong exchange rate applied to a card payment",
    "card_swallowed": "ATM kept / swallowed the card",
    "cash_withdrawal_charge": "fee charged for a cash withdrawal",
    "cash_withdrawal_not_recognised": "cash withdrawal the customer did not make",
    "change_pin": "how to change the card PIN",
    "compromised_card": "card details possibly compromised or used fraudulently",
    "contactless_not_working": "contactless payments not working",
    "country_support": "which countries the service is available in",
    "declined_card_payment": "card payment was declined",
    "declined_cash_withdrawal": "cash withdrawal was declined",
    "declined_transfer": "transfer was declined",
    "direct_debit_payment_not_recognised": "unrecognised direct debit",
    "disposable_card_limits": "limits on disposable virtual cards",
    "edit_personal_details": "change name, address or other personal details",
    "exchange_charge": "fees for currency exchange",
    "exchange_rate": "what exchange rate is used / current rates",
    "exchange_via_app": "how to exchange currencies in the app",
    "extra_charge_on_statement": "unexplained extra charge on the statement",
    "failed_transfer": "transfer failed",
    "fiat_currency_support": "which fiat currencies can be held or exchanged",
    "get_disposable_virtual_card": "how to get a disposable virtual card",
    "get_physical_card": "getting a physical card (e.g. PIN for new physical card)",
    "getting_spare_card": "ordering an additional or spare card",
    "getting_virtual_card": "how to get a virtual card",
    "lost_or_stolen_card": "card lost or stolen",
    "lost_or_stolen_phone": "phone with the app lost or stolen",
    "order_physical_card": "ordering a physical card",
    "passcode_forgotten": "forgot the app passcode",
    "pending_card_payment": "card payment still pending",
    "pending_cash_withdrawal": "cash withdrawal still pending",
    "pending_top_up": "top-up still pending",
    "pending_transfer": "transfer still pending",
    "pin_blocked": "PIN blocked after wrong attempts",
    "receiving_money": "how to receive money / incoming payments",
    "Refund_not_showing_up": "merchant refund not showing up",
    "request_refund": "asking for a refund of a purchase",
    "reverted_card_payment?": "card payment was reverted",
    "supported_cards_and_currencies": "which cards and currencies are supported for top-up/payments",
    "terminate_account": "close or delete the account",
    "top_up_by_bank_transfer_charge": "fee for topping up by bank transfer",
    "top_up_by_card_charge": "fee for topping up by card",
    "top_up_by_cash_or_cheque": "topping up with cash or a cheque",
    "top_up_failed": "top-up failed",
    "top_up_limits": "limits on top-up amounts",
    "top_up_reverted": "top-up was reverted",
    "topping_up_by_card": "how to top up using a card / problems topping up by card",
    "transaction_charged_twice": "same transaction charged twice",
    "transfer_fee_charged": "fee charged on a transfer",
    "transfer_into_account": "how to transfer money into the account",
    "transfer_not_received_by_recipient": "recipient has not received a transfer",
    "transfer_timing": "how long transfers take",
    "unable_to_verify_identity": "identity verification failing",
    "verify_my_identity": "how to verify identity",
    "verify_source_of_funds": "verifying source of funds",
    "verify_top_up": "verifying a top-up (e.g. card verification code)",
    "virtual_card_not_working": "virtual card not working",
    "visa_or_mastercard": "whether the card is Visa or Mastercard",
    "why_verify_identity": "why identity verification is required",
    "wrong_amount_of_cash_received": "ATM dispensed the wrong amount of cash",
    "wrong_exchange_rate_for_cash_withdrawal": "wrong exchange rate on a cash withdrawal",
}

AGENT_DESCRIPTIONS: dict[str, str] = {
    "cards": "card lifecycle and security: activation, delivery, ordering physical/virtual/disposable "
    "cards, PIN, lost/stolen/compromised or malfunctioning cards, Apple/Google Pay, card networks",
    "transactions": "money movement: card payments, cash withdrawals, transfers, refunds, top-ups, "
    "pending/declined/duplicate/unrecognised transactions and their fees",
    "accounts": "the customer's account and profile: identity and source-of-funds verification, "
    "personal details, passcode, lost phone, age limits, closing the account",
    "fallback": "general questions no specialist owns (exchange rates, FX fees, supported countries or "
    "currencies, ATM support) or anything unclear",
}


def valid_labels(granularity: str) -> list[str]:
    return sorted(INTENT_TO_AGENT, key=str.lower) if granularity == "intent" else list(AGENT_LABELS)


def system_prompt(granularity: str) -> str:
    if granularity == "intent":
        lines = "\n".join(f"- {label}: {INTENT_DESCRIPTIONS[label]}" for label in valid_labels("intent"))
        task = "Classify the customer message into exactly one of these 77 banking intents"
    else:
        lines = "\n".join(f"- {label}: {AGENT_DESCRIPTIONS[label]}" for label in valid_labels("agent"))
        task = "Route the customer message to exactly one of these banking agents"
    return (
        f"You are an intent classifier for a retail bank. {task}:\n{lines}\n\n"
        'Respond with JSON only, e.g. {"label": "<label>"}, using a label exactly as written above.'
    )


_JSON_RE = re.compile(r"\{.*?\}", re.S)


def parse_label(raw: str, granularity: str) -> str | None:
    """Extract a valid label from model output; returns None if nothing valid is found."""
    labels = valid_labels(granularity)
    by_lower = {label.lower(): label for label in labels}
    text = raw.strip()
    for match in _JSON_RE.findall(text):
        try:
            value = json.loads(match).get("label")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(value, str) and value.strip().lower() in by_lower:
            return by_lower[value.strip().lower()]
    cleaned = text.strip("`\"' \n").lower()
    if cleaned in by_lower:
        return by_lower[cleaned]
    hits = [
        label
        for label in labels
        if re.search(rf"(?<![\w?]){re.escape(label.lower())}(?![\w?])", text.lower())
    ]
    return max(hits, key=len) if hits else None
