"""In-memory demo banking backend shared by the three MCP servers."""

from __future__ import annotations

import copy
import itertools
import threading
from datetime import datetime, timezone

_SEED: dict = {
    "customers": {
        "cust-001": {
            "name": "Alex Morgan",
            "email": "alex.morgan@example.com",
            "phone": "+44 7700 900123",
            "address": "1 Example Street, London",
            "identity_verified": True,
            "source_of_funds_verified": False,
            "status": "active",
            "passcode_reset_pending": False,
        }
    },
    "accounts": {
        "acc-001": {"customer_id": "cust-001", "currency": "GBP", "balance": 1523.40, "type": "current"},
        "acc-002": {"customer_id": "cust-001", "currency": "EUR", "balance": 310.00, "type": "savings"},
    },
    "cards": {
        "card-001": {
            "customer_id": "cust-001",
            "account_id": "acc-001",
            "kind": "physical",
            "network": "Visa",
            "last4": "4242",
            "status": "active",
            "expires": "2027-08",
            "contactless": True,
            "pin_attempts_left": 3,
        },
        "card-002": {
            "customer_id": "cust-001",
            "account_id": "acc-001",
            "kind": "virtual",
            "network": "Mastercard",
            "last4": "5555",
            "status": "active",
            "expires": "2028-01",
            "contactless": False,
            "pin_attempts_left": 3,
        },
    },
    "card_orders": {
        "ord-001": {
            "customer_id": "cust-001",
            "kind": "physical",
            "status": "shipped",
            "estimated_delivery": "2026-10-02",
        },
    },
    "transactions": {
        "txn-001": {
            "account_id": "acc-001",
            "card_id": "card-001",
            "type": "card_payment",
            "merchant": "Coffee House",
            "amount": -3.80,
            "currency": "GBP",
            "status": "completed",
            "date": "2026-09-25",
        },
        "txn-002": {
            "account_id": "acc-001",
            "card_id": "card-001",
            "type": "card_payment",
            "merchant": "Coffee House",
            "amount": -3.80,
            "currency": "GBP",
            "status": "completed",
            "date": "2026-09-25",
        },
        "txn-003": {
            "account_id": "acc-001",
            "card_id": None,
            "type": "transfer_out",
            "merchant": "J. Smith",
            "amount": -250.00,
            "currency": "GBP",
            "status": "pending",
            "date": "2026-09-27",
        },
        "txn-004": {
            "account_id": "acc-001",
            "card_id": None,
            "type": "top_up",
            "merchant": "Bank transfer top-up",
            "amount": 500.00,
            "currency": "GBP",
            "status": "completed",
            "date": "2026-09-20",
        },
        "txn-005": {
            "account_id": "acc-001",
            "card_id": "card-001",
            "type": "cash_withdrawal",
            "merchant": "ATM Oxford St",
            "amount": -100.00,
            "currency": "GBP",
            "status": "pending",
            "date": "2026-09-28",
        },
    },
    "disputes": {},
    "refunds": {},
    "tickets": {},
}

_lock = threading.Lock()
_state: dict = copy.deepcopy(_SEED)
_ids = itertools.count(1)


def reset() -> None:
    global _state
    with _lock:
        _state = copy.deepcopy(_SEED)


def state() -> dict:
    return _state


def new_id(prefix: str) -> str:
    return f"{prefix}-{next(_ids):04d}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def locked():
    return _lock
