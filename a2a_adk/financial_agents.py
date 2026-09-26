"""Financial-assistant supervisor built three ways.

* :func:`build_prompt_supervisor` — the classic ADK pattern: an ``LlmAgent``
  whose instruction lists every sub-agent and relies on the LLM calling
  ``transfer_to_agent`` (routing lives in the prompt).
* :func:`build_typesafe_supervisor` — an ADK ``Workflow`` whose first node asks
  TypeSafe's System One model (Jev) a typed ``Choice`` question and dispatches
  deterministically on the answer (routing lives in code).
* :func:`build_contrastive_supervisor` — the same ``Workflow`` with the open
  Contrastive Language Model (CLM-8B) answering the typed question.

All share the same sub-agents so the approaches can be benchmarked against
each other.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import START, Workflow
from google.genai import types

from .config import settings
from .typesafe_router import ContrastiveRouter, RouteDecision, TypeSafeRouter

logger = logging.getLogger(__name__)

FALLBACK_AGENT = "fallback_agent"
HUMAN_AGENT = "transfer_to_human_agent"

# One-line capability descriptions. These are the *only* routing artefact the
# TypeSafe supervisor needs; the prompt supervisor expands them into the long
# instruction below.
AGENT_CRITERIA: dict[str, str] = {
    "card_management_agent": (
        "Credit and debit card operations: card replacement, activation or "
        "deactivation, cards pending activation, card status, lost or stolen "
        "card reporting, PIN management, card delivery tracking, and FICO / "
        "credit score inquiries."
    ),
    "account_management_agent": (
        "Account inquiries and operations: balance, account details and "
        "numbers, switching accounts, date opened, customer name change, "
        "authorized user management, payoff quotes, flex loan eligibility, "
        "and disputing a transaction on the account (not fee disputes)."
    ),
    HUMAN_AGENT: (
        "Escalation to a live representative: closing or cancelling an "
        "account, asking why an account was closed, suspended, frozen or "
        "locked, card fraud reports, bankruptcy, disability, TRS, complaints "
        "or dissatisfaction, or an explicit request for a human, agent or rep."
    ),
    "answer_hub_agent": (
        "General banking questions and informational inquiries that are not "
        "specific to cards, transactions, account operations, rates, fees or "
        "limits."
    ),
    FALLBACK_AGENT: (
        "Requests that do not match any known capability or whose intent is "
        "ambiguous or off-topic."
    ),
    "transaction_agent": (
        "Transaction history: recent transactions, searching transactions by "
        "merchant, date, amount or category, transaction details, summaries "
        "and transaction status. Not fee disputes."
    ),
    "rates_fees_limits_management_agent": (
        "Interest rates, fees, account and spending limits, minimum balance "
        "requirements, cash advance eligibility/limits/fees, and any request to "
        "dispute, reverse, remove, refund or waive a fee."
    ),
    "spending_insights_agent": (
        "Spending insights and analysis: spending by category, subcategory, "
        "merchant or account, top spending categories and merchants, trends "
        "over time, and subscription / recurring spending."
    ),
}

# Extra yes/no gates evaluated in the same Jev request. They cost no extra
# latency and replace the hand-written "do NOT route X here" prompt rules.
ROUTING_GATES: dict[str, str] = {
    "wants_human": "Does the user explicitly ask to speak with a human, agent, or representative?",
    "fee_dispute": "Is the user asking to dispute, reverse, refund, remove, or waive a fee?",
}

SUPERVISOR_INSTRUCTION = """### Prompt for Financial Assistant

You are a supervisor agent in an advanced financial assistant system.
Your role is to analyze the user's request and delegate it to the appropriate sub-agent based on the topic of the request.
Use the below agent cards and descriptions to determine the best sub-agent for handling the request.

### Available Sub-Agents

1. **card_management_agent**
   - Handles all credit and debit card related requests.
   - Capabilities: card replacement, card activation/deactivation, cards pending activation retrieval, card status inquiries, lost/stolen card reporting, card PIN management, credit score inquiries.
   - Route to this agent when the user asks about: replacing a card, activating a card, "activate my card", "activate my debit card", "activate my credit card", "which cards need activation", reporting a lost or stolen card, PIN reset, card delivery tracking, "show fico score", "what's my credit score", "check my FICO", "view my credit score", or any card-related operation.

2. **account_management_agent**
   - Handles account-related inquiries and operations.
   - Capabilities: balance inquiry, account details and number retrieval, account switching, date opened, customer name change, card ETA, authorized user management, payoff quotes and disputes.
   - Route to this agent when the user asks about: checking their balance, viewing accounts, account numbers, switching accounts, changing their name on an account, when an account was opened, adding/removing authorized users, payoff amounts, flex loan eligibility/offers/fees, or disputing a transaction on their account.
   - Do NOT route card replacement, card activation, card toggle on/off, or FICO/credit score requests here - those belong to card_management_agent.

3. **transfer_to_human_agent**
   - Handles all requests that require escalation to a human agent.
   - Capabilities: transferring to a live representative; returning outdial number / OTP / wait time from MCP tool.
   - Route here when the user: asks to close/cancel an account; asks why an account was closed/suspended/frozen/locked; reports card fraud; mentions bankruptcy, disability, or TRS; can't be self-served; has any complaint, dissatisfaction; explicitly asks for a human/agent/rep; or makes any request outside the other sub-agents' scope.

4. **answer_hub_agent**
   - Handles general banking-related questions and informational inquiries.
   - Route to this agent when the user asks general banking questions that are not specific to cards, transactions, or account operations.
   - Do NOT route rates, fees, limits, or cash advance questions here - those belong to rates_fees_limits_management_agent.

5. **fallback_agent**
   - Handles requests that cannot be handled by other agents.
   - Route to this agent when the user's request does not match any known sub-agent's capabilities or when the intent is ambiguous.

6. **transaction_agent**
   - Handles transaction-related inquiries and operations.
   - Capabilities: viewing recent transactions, searching transactions by merchant/date/amount/category, providing transaction details and summaries, answering questions about transactions.
   - Route to this agent when the user asks about recent transactions, searching for specific transactions, transaction details, or transaction status.
   - Do NOT route fee disputes here. Any request to dispute, contest, reverse, remove, refund, or waive a FEE (e.g. "dispute a wire transfer fee") belongs to rates_fees_limits_management_agent, not to a wire transfer or Zelle.

7. **rates_fees_limits_management_agent**
   - Handles requests related to rates, fees, and limits management.
   - Capabilities: providing information about interest rates, transaction fees, account limits, spending limits, minimum balance requirements, and account-specific rate and fee structures.
   - Route to this agent when the user asks about interest rates, fees, account limits, minimum balance requirements, minimum balance, cash advances, or any rates, fees, and limits related request, or asks to dispute, reverse, remove, refund, or waive a fee of any type.
   - Cash advance requests belong here, including eligibility, availability, limits, and fees - never route them to answer_hub_agent.
   - Example phrasings to route here: "minimum balance requirement", "minimum balance", "what's the minimum balance", "min balance to avoid fees", "lowest balance I need to keep", "balance requirement on my account", "dispute a wire transfer fee", "dispute this fee", "reverse a fee", "remove/refund/waive a fee", "can i withdraw cash advance", "can I take a cash advance", "how much cash advance can I get".

8. **spending_insights_agent**
   - Handles requests related to spending insights and analysis.
   - Capabilities: retrieving spending details and insights, summarizing spending by category, subcategory, merchant, and account, showing top spending categories and merchants, providing spending trends over time (up to 15 months), and answering questions about subscription/recurring spending.

Always delegate by calling the transfer_to_agent tool with the exact sub-agent name. Do not answer the user yourself.
"""


def resolve_model(model: str | None = None) -> str | LiteLlm:
    """Returns an ADK model spec.

    With ``OPENROUTER_API_KEY`` set, models are OpenRouter ids served through
    LiteLLM (``openrouter/<id>``); otherwise the plain Gemini model name is
    used with ``GOOGLE_API_KEY``.
    """
    model = model or settings.ROUTER_LLM_MODEL
    if settings.OPENROUTER_API_KEY:
        return LiteLlm(model=f"openrouter/{model}")
    return settings.GEMINI_MODEL


def _extract_text(node_input: Any) -> str:
    if isinstance(node_input, types.Content):
        return "".join(p.text or "" for p in node_input.parts or [] if p.text)
    if isinstance(node_input, str):
        return node_input
    return str(node_input)


def build_sub_agents(model: str | LiteLlm | None = None) -> dict[str, LlmAgent]:
    """Builds one stub specialist per entry in :data:`AGENT_CRITERIA`.

    Real deployments replace these with the existing cards / accounts /
    transactions agents; routing does not depend on their internals.
    """
    model = model or resolve_model()
    agents: dict[str, LlmAgent] = {}
    for name, description in AGENT_CRITERIA.items():
        agents[name] = LlmAgent(
            name=name,
            model=model,
            description=description,
            instruction=(
                f"You are the {name.replace('_', ' ')} of a bank's virtual "
                f"assistant. Scope: {description} Answer the user's request "
                "briefly, and begin your reply with your agent name in square "
                "brackets."
            ),
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
    return agents


def build_prompt_supervisor(model: str | LiteLlm | None = None) -> LlmAgent:
    """Prompt-routed supervisor: the LLM reads the instruction and transfers."""
    model = model or resolve_model()
    subs = build_sub_agents(model)
    return LlmAgent(
        name="supervisor_agent",
        model=model,
        description="Financial assistant supervisor that delegates to specialists.",
        instruction=SUPERVISOR_INSTRUCTION,
        sub_agents=list(subs.values()),
    )


def apply_gates(decision: RouteDecision, floor: float = 0.8) -> str:
    """Applies the Noul gates on top of the Choice answer.

    Gates are absolute (each is "is this true?") while the Choice is relative,
    so a strong gate wins over a weak choice. Thresholds live here, in code.
    """
    if decision.gates.get("wants_human", 0.0) >= floor:
        return HUMAN_AGENT
    if (
        decision.gates.get("fee_dispute", 0.0) >= floor
        and decision.agent in {"transaction_agent", "account_management_agent"}
    ):
        return "rates_fees_limits_management_agent"
    return decision.agent


def build_typesafe_router() -> TypeSafeRouter:
    return TypeSafeRouter(
        AGENT_CRITERIA, gates=ROUTING_GATES, fallback_agent=FALLBACK_AGENT
    )


def build_contrastive_router(base_url: str | None = None) -> ContrastiveRouter:
    return ContrastiveRouter(
        AGENT_CRITERIA,
        gates=ROUTING_GATES,
        fallback_agent=FALLBACK_AGENT,
        base_url=base_url,
    )


def build_system_one_supervisor(
    router: TypeSafeRouter,
    model: str | LiteLlm | None = None,
    name: str = "system_one_supervisor",
) -> Workflow:
    """Workflow supervisor: a System One router decides, the graph dispatches."""
    subs = build_sub_agents(model)
    for agent in subs.values():
        agent.mode = "single_turn"

    async def route_with_system_one(ctx, node_input: Any) -> Event:
        text = _extract_text(node_input)
        decision = await router.route(text)
        target = apply_gates(decision)
        logger.info(
            "%s supervisor routed to %s (choice=%s confidence=%.2f)",
            router.name,
            target,
            decision.agent,
            decision.confidence,
        )
        return Event(output=node_input, route=target)

    return Workflow(
        name=name,
        edges=[
            (START, route_with_system_one),
            (route_with_system_one, dict(subs)),
        ],
    )


def build_typesafe_supervisor(
    model: str | LiteLlm | None = None,
    router: TypeSafeRouter | None = None,
) -> Workflow:
    """Jev (TypeSafe) decides the route."""
    return build_system_one_supervisor(
        router or build_typesafe_router(), model, name="typesafe_supervisor"
    )


def build_contrastive_supervisor(
    model: str | LiteLlm | None = None,
    router: ContrastiveRouter | None = None,
) -> Workflow:
    """CLM-8B (contrastive System One model) decides the route."""
    return build_system_one_supervisor(
        router or build_contrastive_router(), model, name="contrastive_supervisor"
    )
