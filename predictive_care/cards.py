"""Mock card servicing backend (stands in for the bank's card system of record)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from redis import asyncio as aioredis

from .config import settings
from .redis_client import create_redis_client

logger = logging.getLogger(__name__)

CardStatus = Literal["active", "blocked", "inactive", "replaced"]


class Transaction(BaseModel):
    """A recent card transaction the customer may want to dispute."""

    id: str
    merchant: str
    amount: float
    currency: str = "USD"
    status: str = "posted"
    timestamp: float = Field(default_factory=time.time)


class Card(BaseModel):
    """A debit card record."""

    card_id: str
    user_id: str
    last4: str
    network: str = "VISA"
    status: CardStatus = "active"
    blocked_reason: str | None = None
    transactions: list[Transaction] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


def default_card(user_id: str) -> Card:
    """Seed card used by the demo: blocked after suspected fraud."""
    now = time.time()
    return Card(
        card_id=f"card_{user_id}",
        user_id=user_id,
        last4="4821",
        status="blocked",
        blocked_reason="Temporary block after suspicious activity",
        transactions=[
            Transaction(
                id="txn_1001",
                merchant="Bright Coffee",
                amount=6.40,
                timestamp=now - 3600,
            ),
            Transaction(
                id="txn_1002",
                merchant="QuickCash ATM",
                amount=220.00,
                status="declined",
                timestamp=now - 1800,
            ),
            Transaction(
                id="txn_1003",
                merchant="GlobalNet Digital",
                amount=189.99,
                timestamp=now - 900,
            ),
        ],
    )


class CardService:
    """Redis-backed card store with the four self-service resolution actions."""

    CARD_PREFIX = "care:card"

    def __init__(self, redis_url: str | None = None, redis: aioredis.Redis | None = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis = redis

    @property
    def client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = create_redis_client(self.redis_url)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _key(self, user_id: str) -> str:
        return f"{self.CARD_PREFIX}:{settings.APP_NAME}:{user_id}"

    async def get_card(self, user_id: str) -> Card:
        """Return the user's card, seeding the demo card on first access."""
        raw = await self.client.get(self._key(user_id))
        if raw:
            try:
                return Card.model_validate_json(raw)
            except ValueError:
                logger.warning("Corrupt card record for user=%s; reseeding", user_id)
        card = default_card(user_id)
        await self._save(card)
        return card

    async def _save(self, card: Card) -> Card:
        card.updated_at = time.time()
        await self.client.set(self._key(card.user_id), card.model_dump_json())
        return card

    async def reset(self, user_id: str) -> Card:
        return await self._save(default_card(user_id))

    # ------------------------------------------------------------------
    # Resolution actions
    # ------------------------------------------------------------------

    async def unlock(self, user_id: str) -> dict[str, Any]:
        card = await self.get_card(user_id)
        if card.status == "active":
            return {
                "action": "unlock",
                "status": "noop",
                "card_last4": card.last4,
                "message": f"Card ending {card.last4} is already active.",
            }
        if card.status in ("inactive", "replaced"):
            return {
                "action": "unlock",
                "status": "rejected",
                "card_last4": card.last4,
                "message": (
                    f"Card ending {card.last4} is {card.status} and cannot be "
                    "unlocked; it needs activation or replacement instead."
                ),
            }
        card.status = "active"
        card.blocked_reason = None
        await self._save(card)
        return {
            "action": "unlock",
            "status": "completed",
            "card_last4": card.last4,
            "message": f"Card ending {card.last4} is unlocked and ready to use.",
        }

    async def activate(self, user_id: str) -> dict[str, Any]:
        card = await self.get_card(user_id)
        if card.status == "active":
            return {
                "action": "activate",
                "status": "noop",
                "card_last4": card.last4,
                "message": f"Card ending {card.last4} is already activated.",
            }
        card.status = "active"
        card.blocked_reason = None
        await self._save(card)
        return {
            "action": "activate",
            "status": "completed",
            "card_last4": card.last4,
            "message": f"Card ending {card.last4} is now activated.",
        }

    async def replace(self, user_id: str, reason: str = "damaged") -> dict[str, Any]:
        card = await self.get_card(user_id)
        old_last4 = card.last4
        card.status = "replaced"
        card.blocked_reason = f"Replaced ({reason})"
        await self._save(card)

        new_card = Card(
            card_id=f"card_{user_id}_{uuid.uuid4().hex[:6]}",
            user_id=user_id,
            last4=str(1000 + int(time.time()) % 9000),
            status="inactive",
            transactions=card.transactions,
        )
        await self._save(new_card)
        return {
            "action": "replace",
            "status": "completed",
            "card_last4": new_card.last4,
            "replaced_last4": old_last4,
            "reason": reason,
            "eta_days": 3,
            "message": (
                f"Replacement card ending {new_card.last4} ordered (reason: {reason}); "
                "it arrives in about 3 business days and needs activation."
            ),
        }

    async def dispute(
        self, user_id: str, transaction_id: str = "", reason: str = "unauthorized"
    ) -> dict[str, Any]:
        card = await self.get_card(user_id)
        transaction = None
        if transaction_id:
            transaction = next(
                (t for t in card.transactions if t.id == transaction_id), None
            )
        if transaction is None:
            transaction = max(
                card.transactions, key=lambda t: t.amount, default=None
            )
        if transaction is None:
            return {
                "action": "dispute",
                "status": "rejected",
                "message": "No transactions are available to dispute.",
            }

        transaction.status = "disputed"
        await self._save(card)
        case_id = f"DSP-{uuid.uuid4().hex[:8].upper()}"
        return {
            "action": "dispute",
            "status": "completed",
            "case_id": case_id,
            "transaction_id": transaction.id,
            "merchant": transaction.merchant,
            "amount": transaction.amount,
            "reason": reason,
            "message": (
                f"Dispute {case_id} opened for {transaction.merchant} "
                f"${transaction.amount:.2f} ({reason}); provisional credit in 2 days."
            ),
        }

    async def snapshot(self, user_id: str) -> dict[str, Any]:
        """Card state for the UI and for agent grounding."""
        card = await self.get_card(user_id)
        return json.loads(card.model_dump_json())
