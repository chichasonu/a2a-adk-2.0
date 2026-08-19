"""Card resolution tools exposed to the ADK agent.

Every tool writes its outcome into long-term memory so future sessions (and the
predictor) know what happened and what the customer preferred.
"""

from __future__ import annotations

import logging
from typing import Any

from .cards import CardService
from .memory import CareMemoryService

logger = logging.getLogger(__name__)

_card_service: CardService | None = None
_memory: CareMemoryService | None = None


def bind_services(card_service: CardService, memory: CareMemoryService) -> None:
    """Wire the tool functions to the running app's services."""
    global _card_service, _memory
    _card_service = card_service
    _memory = memory


def _services() -> tuple[CardService, CareMemoryService]:
    if _card_service is None or _memory is None:
        raise RuntimeError(
            "Card tools are not bound to services; call bind_services() first."
        )
    return _card_service, _memory


async def _remember_action(
    memory: RedisMemoryService, user_id: str, result: dict[str, Any]
) -> None:
    """Persist a completed resolution as a durable memory + profile fact."""
    action = result.get("action", "unknown")
    if result.get("status") != "completed":
        return
    await memory.remember(
        user_id=user_id,
        text=f"Resolved a debit card issue with the '{action}' action: {result.get('message', '')}",
        author="card_care_agent",
        kind="resolution",
        metadata={"action": action, **{k: v for k, v in result.items() if k != "message"}},
    )
    profile = await memory.get_profile(user_id=user_id)
    resolutions = profile.get("resolution_counts")
    counts = dict(resolutions) if isinstance(resolutions, dict) else {}
    counts[action] = int(counts.get(action, 0)) + 1
    await memory.update_profile(
        user_id=user_id,
        updates={
            "preferred_resolution": action,
            "resolution_counts": counts,
            "last_resolution": {"action": action, "detail": result.get("message", "")},
        },
    )


async def get_card_status(user_id: str) -> dict[str, Any]:
    """Look up the customer's debit card status and recent transactions.

    Args:
        user_id: Identifier of the signed-in customer.
    """
    card_service, _ = _services()
    card = await card_service.get_card(user_id)
    return {
        "card_last4": card.last4,
        "status": card.status,
        "blocked_reason": card.blocked_reason,
        "transactions": [
            {
                "id": t.id,
                "merchant": t.merchant,
                "amount": t.amount,
                "status": t.status,
            }
            for t in card.transactions
        ],
    }


async def unlock_card(user_id: str) -> dict[str, Any]:
    """Remove a temporary block so the debit card can be used again.

    Args:
        user_id: Identifier of the signed-in customer.
    """
    card_service, memory = _services()
    result = await card_service.unlock(user_id)
    await _remember_action(memory, user_id, result)
    return result


async def activate_card(user_id: str) -> dict[str, Any]:
    """Activate a newly issued or inactive debit card.

    Args:
        user_id: Identifier of the signed-in customer.
    """
    card_service, memory = _services()
    result = await card_service.activate(user_id)
    await _remember_action(memory, user_id, result)
    return result


async def replace_card(user_id: str, reason: str = "lost") -> dict[str, Any]:
    """Order a replacement debit card and cancel the current one.

    Args:
        user_id: Identifier of the signed-in customer.
        reason: Why the card is being replaced (lost, stolen, damaged, fraud).
    """
    card_service, memory = _services()
    result = await card_service.replace(user_id, reason=reason)
    await _remember_action(memory, user_id, result)
    return result


async def dispute_transaction(
    user_id: str, transaction_id: str = "", reason: str = "unauthorized"
) -> dict[str, Any]:
    """Open a dispute case for a card transaction.

    Args:
        user_id: Identifier of the signed-in customer.
        transaction_id: Transaction to dispute; defaults to the largest recent one.
        reason: Dispute reason (unauthorized, duplicate, not_received, wrong_amount).
    """
    card_service, memory = _services()
    result = await card_service.dispute(
        user_id, transaction_id=transaction_id, reason=reason
    )
    await _remember_action(memory, user_id, result)
    return result


async def recall_customer_history(user_id: str, query: str = "debit card") -> dict[str, Any]:
    """Recall what previous sessions learned about this customer's card issues.

    Args:
        user_id: Identifier of the signed-in customer.
        query: What to recall, e.g. "blocked card" or "preferred resolution".
    """
    _, memory = _services()
    records = await memory.search_records(user_id=user_id, query=query, top_k=5)
    profile = await memory.get_profile(user_id=user_id)
    return {
        "profile": profile,
        "memories": [
            {"text": r["text"], "kind": r.get("kind"), "score": r.get("score")}
            for r in records
        ],
    }


# Actions callable directly from the proactive prompt buttons.
ACTION_TOOLS = {
    "unlock": unlock_card,
    "activate": activate_card,
    "replace": replace_card,
    "dispute": dispute_transaction,
}

TOOLS = [
    get_card_status,
    unlock_card,
    activate_card,
    replace_card,
    dispute_transaction,
    recall_customer_history,
]
