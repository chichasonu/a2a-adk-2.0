"""Cards MCP server (SSE): card lifecycle, delivery, PIN and security actions."""

from __future__ import annotations

from mcp_servers import banking_data as db
from mcp_servers.common import make_server, not_found

mcp = make_server("cards", "Card management tools for the demo bank.")


@mcp.tool()
def list_cards(customer_id: str = "cust-001") -> dict:
    """List the customer's physical and virtual cards with status and expiry."""
    cards = {k: v for k, v in db.state()["cards"].items() if v["customer_id"] == customer_id}
    return {"ok": True, "cards": cards}


@mcp.tool()
def activate_card(card_id: str) -> dict:
    """Activate a newly received card."""
    with db.locked():
        card = db.state()["cards"].get(card_id)
        if card is None:
            return not_found("card", card_id)
        card["status"] = "active"
        return {"ok": True, "card_id": card_id, "status": card["status"]}


@mcp.tool()
def freeze_card(card_id: str, reason: str = "customer_request") -> dict:
    """Temporarily freeze a card (e.g. suspected lost, stolen or compromised)."""
    with db.locked():
        card = db.state()["cards"].get(card_id)
        if card is None:
            return not_found("card", card_id)
        card["status"] = "frozen"
        return {"ok": True, "card_id": card_id, "status": "frozen", "reason": reason}


@mcp.tool()
def report_lost_or_stolen(card_id: str, order_replacement: bool = True) -> dict:
    """Permanently block a lost/stolen card and optionally order a replacement."""
    with db.locked():
        card = db.state()["cards"].get(card_id)
        if card is None:
            return not_found("card", card_id)
        card["status"] = "blocked"
        result = {"ok": True, "card_id": card_id, "status": "blocked"}
        if order_replacement:
            order_id = db.new_id("ord")
            db.state()["card_orders"][order_id] = {
                "customer_id": card["customer_id"],
                "kind": card["kind"],
                "status": "processing",
                "estimated_delivery": "5-7 business days",
            }
            result["replacement_order_id"] = order_id
        return result


@mcp.tool()
def order_card(customer_id: str = "cust-001", kind: str = "physical") -> dict:
    """Order a new physical, virtual or disposable virtual card."""
    if kind not in {"physical", "virtual", "disposable"}:
        return {"ok": False, "error": "kind must be physical, virtual or disposable"}
    with db.locked():
        order_id = db.new_id("ord")
        eta = "instant" if kind != "physical" else "5-7 business days"
        db.state()["card_orders"][order_id] = {
            "customer_id": customer_id,
            "kind": kind,
            "status": "processing",
            "estimated_delivery": eta,
        }
        return {"ok": True, "order_id": order_id, "estimated_delivery": eta}


@mcp.tool()
def get_card_delivery_status(customer_id: str = "cust-001") -> dict:
    """Show delivery status and ETA for the customer's card orders."""
    orders = {k: v for k, v in db.state()["card_orders"].items() if v["customer_id"] == customer_id}
    return {"ok": True, "orders": orders}


@mcp.tool()
def unblock_pin(card_id: str) -> dict:
    """Reset PIN attempts on a card whose PIN is blocked."""
    with db.locked():
        card = db.state()["cards"].get(card_id)
        if card is None:
            return not_found("card", card_id)
        card["pin_attempts_left"] = 3
        return {
            "ok": True,
            "card_id": card_id,
            "pin_attempts_left": 3,
            "note": "PIN can be changed at any ATM or viewed in the app.",
        }


@mcp.tool()
def get_card_capabilities() -> dict:
    """Supported networks, wallets and currencies for cards."""
    return {
        "ok": True,
        "networks": ["Visa", "Mastercard"],
        "wallets": ["Apple Pay", "Google Pay"],
        "currencies": ["GBP", "EUR", "USD"],
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
