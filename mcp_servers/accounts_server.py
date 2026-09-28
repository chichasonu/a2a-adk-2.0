"""Accounts MCP server (SSE): balances, profile, identity verification and account lifecycle."""

from __future__ import annotations

from mcp_servers import banking_data as db
from mcp_servers.common import make_server, not_found

mcp = make_server("accounts", "Account, profile and verification tools for the demo bank.")

_EDITABLE = {"name", "email", "phone", "address"}


@mcp.tool()
def get_account_summary(customer_id: str = "cust-001") -> dict:
    """Customer profile, verification status and account balances."""
    customer = db.state()["customers"].get(customer_id)
    if customer is None:
        return not_found("customer", customer_id)
    accounts = {k: v for k, v in db.state()["accounts"].items() if v["customer_id"] == customer_id}
    return {"ok": True, "customer": customer, "accounts": accounts}


@mcp.tool()
def update_personal_details(field: str, value: str, customer_id: str = "cust-001") -> dict:
    """Update one of: name, email, phone, address."""
    if field not in _EDITABLE:
        return {"ok": False, "error": f"field must be one of {sorted(_EDITABLE)}"}
    with db.locked():
        customer = db.state()["customers"].get(customer_id)
        if customer is None:
            return not_found("customer", customer_id)
        customer[field] = value
        return {"ok": True, "customer_id": customer_id, "updated": {field: value}}


@mcp.tool()
def start_identity_verification(customer_id: str = "cust-001", check: str = "identity") -> dict:
    """Start an identity or source-of-funds verification check."""
    if check not in {"identity", "source_of_funds"}:
        return {"ok": False, "error": "check must be 'identity' or 'source_of_funds'"}
    with db.locked():
        if customer_id not in db.state()["customers"]:
            return not_found("customer", customer_id)
        ticket = db.new_id("kyc")
        db.state()["tickets"][ticket] = {
            "customer_id": customer_id,
            "type": check,
            "status": "awaiting_documents",
        }
        return {
            "ok": True,
            "verification_id": ticket,
            "status": "awaiting_documents",
            "required": ["photo ID", "selfie"] if check == "identity" else ["payslip or bank statement"],
        }


@mcp.tool()
def reset_passcode(customer_id: str = "cust-001") -> dict:
    """Send a passcode reset link to the customer's verified email."""
    with db.locked():
        customer = db.state()["customers"].get(customer_id)
        if customer is None:
            return not_found("customer", customer_id)
        customer["passcode_reset_pending"] = True
        return {"ok": True, "sent_to": customer["email"]}


@mcp.tool()
def close_account(customer_id: str = "cust-001", confirm: bool = False) -> dict:
    """Close the customer's account. Requires confirm=true."""
    if not confirm:
        return {"ok": False, "error": "confirmation required: call again with confirm=true"}
    with db.locked():
        customer = db.state()["customers"].get(customer_id)
        if customer is None:
            return not_found("customer", customer_id)
        customer["status"] = "closing"
        return {"ok": True, "customer_id": customer_id, "status": "closing"}


@mcp.tool()
def get_eligibility_rules() -> dict:
    """Age and residency eligibility requirements."""
    return {"ok": True, "minimum_age": 18, "teen_accounts_from": 13, "residency": "EEA and UK residents"}


if __name__ == "__main__":
    mcp.run(transport="sse")
