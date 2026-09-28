"""Transactions MCP server (SSE): card payments, cash, transfers, refunds and top-ups."""

from __future__ import annotations

from mcp_servers import banking_data as db
from mcp_servers.common import make_server, not_found

mcp = make_server("transactions", "Transaction, transfer, refund and top-up tools for the demo bank.")


@mcp.tool()
def list_transactions(account_id: str = "acc-001", status: str | None = None, limit: int = 20) -> dict:
    """List recent transactions on an account, optionally filtered by status (pending/completed)."""
    txns = [
        {"transaction_id": k, **v}
        for k, v in db.state()["transactions"].items()
        if v["account_id"] == account_id and (status is None or v["status"] == status)
    ]
    txns.sort(key=lambda t: t["date"], reverse=True)
    return {"ok": True, "transactions": txns[:limit]}


@mcp.tool()
def get_transaction(transaction_id: str) -> dict:
    """Get details for a single transaction."""
    txn = db.state()["transactions"].get(transaction_id)
    return {"ok": True, "transaction": txn} if txn else not_found("transaction", transaction_id)


@mcp.tool()
def find_duplicate_charges(account_id: str = "acc-001") -> dict:
    """Find card payments charged more than once (same merchant, amount and date)."""
    seen: dict[tuple, list[str]] = {}
    for k, v in db.state()["transactions"].items():
        if v["account_id"] == account_id and v["type"] == "card_payment":
            seen.setdefault((v["merchant"], v["amount"], v["date"]), []).append(k)
    return {"ok": True, "duplicates": [ids for ids in seen.values() if len(ids) > 1]}


@mcp.tool()
def dispute_transaction(transaction_id: str, reason: str) -> dict:
    """Open a dispute for an unrecognised or incorrect transaction."""
    with db.locked():
        if transaction_id not in db.state()["transactions"]:
            return not_found("transaction", transaction_id)
        dispute_id = db.new_id("dsp")
        db.state()["disputes"][dispute_id] = {
            "transaction_id": transaction_id,
            "reason": reason,
            "status": "open",
            "opened_at": db.now(),
        }
        return {"ok": True, "dispute_id": dispute_id, "status": "open"}


@mcp.tool()
def request_refund(transaction_id: str, reason: str = "") -> dict:
    """Request a refund for a completed card payment."""
    with db.locked():
        txn = db.state()["transactions"].get(transaction_id)
        if txn is None:
            return not_found("transaction", transaction_id)
        refund_id = db.new_id("rfd")
        db.state()["refunds"][refund_id] = {
            "transaction_id": transaction_id,
            "amount": -txn["amount"],
            "reason": reason,
            "status": "requested",
        }
        return {
            "ok": True,
            "refund_id": refund_id,
            "status": "requested",
            "note": "Refunds usually appear within 5 business days.",
        }


@mcp.tool()
def get_transfer_status(transaction_id: str) -> dict:
    """Get status and expected arrival of an outgoing transfer."""
    txn = db.state()["transactions"].get(transaction_id)
    if txn is None or not txn["type"].startswith("transfer"):
        return not_found("transfer", transaction_id)
    eta = "within 1 business day" if txn["status"] == "pending" else "delivered"
    return {"ok": True, "transaction_id": transaction_id, "status": txn["status"], "eta": eta}


@mcp.tool()
def cancel_transfer(transaction_id: str) -> dict:
    """Cancel a pending outgoing transfer."""
    with db.locked():
        txn = db.state()["transactions"].get(transaction_id)
        if txn is None or not txn["type"].startswith("transfer"):
            return not_found("transfer", transaction_id)
        if txn["status"] != "pending":
            return {
                "ok": False,
                "error": f"transfer is {txn['status']}; only pending transfers can be cancelled",
            }
        txn["status"] = "cancelled"
        return {"ok": True, "transaction_id": transaction_id, "status": "cancelled"}


@mcp.tool()
def get_fees(kind: str) -> dict:
    """Fees for 'top_up_card', 'top_up_bank_transfer', 'transfer', 'cash_withdrawal' or 'card_payment'."""
    fees = {
        "top_up_card": "0.5% for international cards, free for domestic cards",
        "top_up_bank_transfer": "free",
        "transfer": "free domestic; 0.4% for international transfers",
        "cash_withdrawal": "free up to 200 GBP per month, then 2%",
        "card_payment": "free in GBP; 0.5% FX markup on weekends",
    }
    return {"ok": True, "kind": kind, "fee": fees.get(kind, "unknown fee type")}


if __name__ == "__main__":
    mcp.run(transport="sse")
