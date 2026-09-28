"""Build ``training/data/intents.csv`` from the BANKING77 dataset.

Sources:
  * ``--source hf`` (default): ``datasets.load_dataset("PolyAI/banking77")`` from Hugging Face.
  * ``--source csv``: a downloaded CSV (Kaggle mirror or the upstream PolyAI GitHub files).

Output columns: ``text``, ``intent`` (original 77-class label), ``label`` (the training
target at the requested ``--granularity``: the intent itself or its mapped agent).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_TRAIN_OUT = DATA_DIR / "intents.csv"
DEFAULT_EVAL_OUT = DATA_DIR / "intents_eval.csv"

HF_DATASET = "PolyAI/banking77"
FALLBACK = "fallback"
AGENTS = ("cards", "transactions", "accounts")
AGENT_LABELS = (*AGENTS, FALLBACK)
GRANULARITIES = ("intent", "agent")

# Every BANKING77 intent -> the sub-agent that owns it. Grouped by banking domain; add a
# new sub-agent by adding its name to AGENTS and moving the relevant intents to it.
INTENT_TO_AGENT: dict[str, str] = {
    # --- Cards: card lifecycle, delivery and ordering -------------------------------
    "activate_my_card": "cards",
    "card_about_to_expire": "cards",
    "card_arrival": "cards",
    "card_delivery_estimate": "cards",
    "card_linking": "cards",
    "get_physical_card": "cards",
    "getting_spare_card": "cards",
    "order_physical_card": "cards",
    # --- Cards: virtual & disposable cards ------------------------------------------
    "disposable_card_limits": "cards",
    "get_disposable_virtual_card": "cards",
    "getting_virtual_card": "cards",
    "virtual_card_not_working": "cards",
    # --- Cards: security, PIN and malfunction ---------------------------------------
    "card_not_working": "cards",
    "card_swallowed": "cards",
    "change_pin": "cards",
    "compromised_card": "cards",
    "contactless_not_working": "cards",
    "lost_or_stolen_card": "cards",
    "pin_blocked": "cards",
    # --- Cards: networks, wallets and acceptance ------------------------------------
    "apple_pay_or_google_pay": "cards",
    "card_acceptance": "cards",
    "supported_cards_and_currencies": "cards",
    "visa_or_mastercard": "cards",
    # --- Transactions: card payments ------------------------------------------------
    "card_payment_fee_charged": "transactions",
    "card_payment_not_recognised": "transactions",
    "card_payment_wrong_exchange_rate": "transactions",
    "declined_card_payment": "transactions",
    "direct_debit_payment_not_recognised": "transactions",
    "extra_charge_on_statement": "transactions",
    "pending_card_payment": "transactions",
    "reverted_card_payment?": "transactions",
    "transaction_charged_twice": "transactions",
    # --- Transactions: cash withdrawals ---------------------------------------------
    "cash_withdrawal_charge": "transactions",
    "cash_withdrawal_not_recognised": "transactions",
    "declined_cash_withdrawal": "transactions",
    "pending_cash_withdrawal": "transactions",
    "wrong_amount_of_cash_received": "transactions",
    "wrong_exchange_rate_for_cash_withdrawal": "transactions",
    # --- Transactions: transfers ----------------------------------------------------
    "balance_not_updated_after_bank_transfer": "transactions",
    "beneficiary_not_allowed": "transactions",
    "cancel_transfer": "transactions",
    "declined_transfer": "transactions",
    "failed_transfer": "transactions",
    "pending_transfer": "transactions",
    "receiving_money": "transactions",
    "transfer_fee_charged": "transactions",
    "transfer_into_account": "transactions",
    "transfer_not_received_by_recipient": "transactions",
    "transfer_timing": "transactions",
    # --- Transactions: refunds ------------------------------------------------------
    "Refund_not_showing_up": "transactions",
    "request_refund": "transactions",
    # --- Transactions: top-ups ------------------------------------------------------
    "automatic_top_up": "transactions",
    "balance_not_updated_after_cheque_or_cash_deposit": "transactions",
    "pending_top_up": "transactions",
    "top_up_by_bank_transfer_charge": "transactions",
    "top_up_by_card_charge": "transactions",
    "top_up_by_cash_or_cheque": "transactions",
    "top_up_failed": "transactions",
    "top_up_limits": "transactions",
    "top_up_reverted": "transactions",
    "topping_up_by_card": "transactions",
    "verify_top_up": "transactions",
    # --- Accounts: identity verification & compliance -------------------------------
    "unable_to_verify_identity": "accounts",
    "verify_my_identity": "accounts",
    "verify_source_of_funds": "accounts",
    "why_verify_identity": "accounts",
    # --- Accounts: profile, access and lifecycle ------------------------------------
    "age_limit": "accounts",
    "edit_personal_details": "accounts",
    "lost_or_stolen_phone": "accounts",
    "passcode_forgotten": "accounts",
    "terminate_account": "accounts",
    # --- Fallback: general FX / product questions with no owning sub-agent ----------
    "atm_support": FALLBACK,
    "country_support": FALLBACK,
    "exchange_charge": FALLBACK,
    "exchange_rate": FALLBACK,
    "exchange_via_app": FALLBACK,
    "fiat_currency_support": FALLBACK,
}

# Canonical BANKING77 ClassLabel order (index -> name), used to decode integer labels.
BANKING77_LABELS: tuple[str, ...] = tuple(sorted(INTENT_TO_AGENT, key=str.lower))


def intent_to_agent(intent: str) -> str:
    return INTENT_TO_AGENT.get(intent, FALLBACK)


def target_label(intent: str, granularity: str) -> str:
    if granularity == "intent":
        return intent
    if granularity == "agent":
        return intent_to_agent(intent)
    raise ValueError(f"unknown granularity {granularity!r}; expected one of {GRANULARITIES}")


def load_hf_splits(dataset: str = HF_DATASET) -> dict[str, pd.DataFrame]:
    from datasets import load_dataset

    ds = load_dataset(dataset, trust_remote_code=True)
    names = ds["train"].features["label"].names
    return {
        split: pd.DataFrame({"text": ds[split]["text"], "intent": [names[i] for i in ds[split]["label"]]})
        for split in ("train", "test")
    }


def read_banking77_csv(path: Path) -> pd.DataFrame:
    """Read a BANKING77 CSV with a ``text`` column and a ``category``/``intent``/``label`` column.

    Integer labels are decoded with the canonical HF ClassLabel order.
    """
    df = pd.read_csv(path)
    column = next((c for c in ("intent", "category", "label") if c in df.columns), None)
    if "text" not in df.columns or column is None:
        raise ValueError(f"{path}: expected columns 'text' and one of intent/category/label")
    intents = df[column]
    if pd.api.types.is_integer_dtype(intents):
        intents = intents.map(lambda i: BANKING77_LABELS[int(i)])
    return pd.DataFrame({"text": df["text"].astype(str), "intent": intents.astype(str).str.strip()})


def few_shot(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Keep up to ``n`` examples per intent (all of them if an intent has fewer)."""
    shuffled = df.sample(frac=1.0, random_state=seed)
    sampled = shuffled.groupby("intent", sort=False).head(n)
    return sampled.sort_values("intent", kind="stable").reset_index(drop=True)


def build_frame(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    unknown = sorted(set(df["intent"]) - set(INTENT_TO_AGENT))
    if unknown:
        raise ValueError(f"intents missing from INTENT_TO_AGENT: {unknown}")
    out = df[["text", "intent"]].copy()
    out["label"] = out["intent"].map(lambda i: target_label(i, granularity))
    return out


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info(
        "wrote %d rows (%d intents, %d labels) -> %s",
        len(df),
        df["intent"].nunique(),
        df["label"].nunique(),
        path,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=("hf", "csv"), default="hf")
    p.add_argument("--dataset", default=HF_DATASET, help="HF dataset id (--source hf)")
    p.add_argument("--train-csv", type=Path, help="downloaded train CSV (--source csv)")
    p.add_argument("--test-csv", type=Path, help="downloaded test CSV (--source csv)")
    p.add_argument(
        "--few-shot", type=int, default=None, metavar="N", help="subsample N training examples per intent"
    )
    p.add_argument(
        "--granularity",
        choices=GRANULARITIES,
        default="agent",
        help="value of the `label` column: 77-way intent or mapped agent",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=DEFAULT_TRAIN_OUT)
    p.add_argument("--eval-out", type=Path, default=DEFAULT_EVAL_OUT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    if args.source == "hf":
        splits = load_hf_splits(args.dataset)
    else:
        if args.train_csv is None:
            raise SystemExit("--source csv requires --train-csv")
        splits = {"train": read_banking77_csv(args.train_csv)}
        if args.test_csv is not None:
            splits["test"] = read_banking77_csv(args.test_csv)

    train = splits["train"]
    if args.few_shot is not None:
        if args.few_shot < 1:
            raise SystemExit("--few-shot must be >= 1")
        train = few_shot(train, args.few_shot, args.seed)

    write_csv(build_frame(train, args.granularity), args.out)
    if "test" in splits:
        write_csv(build_frame(splits["test"], args.granularity), args.eval_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
