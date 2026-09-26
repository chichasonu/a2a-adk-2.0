"""Agent definitions demonstrating ADK 2.0 team agents and route graphs."""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import BaseTool
from google.adk.workflow import Workflow
from google.adk.workflow import START
from google.genai import types

from .config import settings
from .intent_classifier import FALLBACK_LABEL
from .intent_classifier import intent_classifier
from .mcp_tools import mcp_tool_cache
from .tool_cache import tool_cache

logger = logging.getLogger(__name__)


def _extract_text(node_input: Any) -> str:
    """Extract plain text from a node input (Content or string)."""
    if isinstance(node_input, types.Content):
        return "".join(
            part.text or "" for part in node_input.parts or [] if part.text
        )
    if isinstance(node_input, str):
        return node_input
    return str(node_input)


# ------------------------------------------------------------------
# Shared sub-agents
# ------------------------------------------------------------------


def _build_greeting_agent() -> LlmAgent:
    return LlmAgent(
        name="greeting_agent",
        model=settings.GEMINI_MODEL,
        description="Greets the user warmly and handles social niceties.",
        instruction=(
            "You are a friendly greeting assistant. Respond warmly and briefly. "
            "If the user just says hello, greet them back and ask how you can help."
        ),
    )


async def _build_weather_agent() -> LlmAgent:
    tool = await tool_cache.get_tool("get_weather")
    tools = [tool] if tool else []
    return LlmAgent(
        name="weather_agent",
        model=settings.GEMINI_MODEL,
        description="Provides brief, cheerful weather information.",
        instruction=(
            "You are a weather assistant. Provide a short, friendly weather "
            "forecast. If a city is mentioned, include it in your answer. "
            "Keep your response to one or two sentences."
        ),
        tools=tools,
    )


def _build_math_agent() -> LlmAgent:
    return LlmAgent(
        name="math_agent",
        model=settings.GEMINI_MODEL,
        description="Solves arithmetic and simple math problems.",
        instruction=(
            "You are a math assistant. Solve the math problem step by step and "
            "return the final answer clearly. Be concise."
        ),
    )


def _build_mcp_agent() -> LlmAgent:
    """Build an agent that can call remote MCP tools exposed by the Spring server."""
    mcp_tools = mcp_tool_cache.get_tools()
    tool_names = [t.name for t in mcp_tools]
    return LlmAgent(
        name="mcp_agent",
        model=settings.GEMINI_MODEL,
        description="Agent that invokes remote MCP tools (finance, email, time, weather).",
        instruction=(
            "You are a general-purpose assistant with access to remote tools. "
            "Use the available tool when the user asks about stock prices, "
            "currency conversion, sending an email, or the current date/time. "
            "Be concise and use the tool result directly in your answer. "
            f"Available tools: {tool_names}."
        ),
        tools=mcp_tools,
    )


# ------------------------------------------------------------------
# Financial supervisor sub-agents (Gemini flash-lite via LiteLLM)
# ------------------------------------------------------------------


def _sub_agent_model() -> LiteLlm:
    return LiteLlm(model=settings.sub_agent_litellm_model)


def _mcp_tools_named(*names: str) -> list[BaseTool]:
    """Return cached MCP tools whose name matches one of ``names``.

    Matching is case-insensitive and ignores underscores so ``get_balance``
    matches a Spring ``getBalance`` tool.
    """
    wanted = {n.lower().replace("_", "") for n in names}
    return [
        t for t in mcp_tool_cache.get_tools()
        if t.name.lower().replace("_", "") in wanted
    ]


def _build_card_agent() -> LlmAgent:
    return LlmAgent(
        name="card_management_agent",
        model=_sub_agent_model(),
        description="Handles card lifecycle: activation, PIN, blocking/freezing, ordering, delivery, expiry.",
        instruction=(
            "You are the card management specialist of a retail bank. Help the "
            "customer activate, block, freeze, unfreeze, replace or order physical "
            "and virtual cards, change or unblock a PIN, link cards to wallets "
            "(Apple Pay / Google Pay) and track card delivery. Use the available "
            "tools to perform actions; never invent card numbers. If the card is "
            "lost, stolen or compromised, advise blocking it immediately and "
            "recommend contacting a human agent for the fraud investigation."
        ),
        tools=_mcp_tools_named(
            "getCardStatus", "blockCard", "unblockCard", "freezeCard",
            "activateCard", "orderCard", "changePin", "getCardDelivery",
        ),
    )


def _build_account_agent() -> LlmAgent:
    return LlmAgent(
        name="account_management_agent",
        model=_sub_agent_model(),
        description="Handles account profile, identity verification, passcodes, auto top-up and account closure.",
        instruction=(
            "You are the account management specialist. Help the customer update "
            "personal details (address, phone, email), verify their identity or "
            "source of funds, recover a forgotten passcode, configure automatic "
            "top-ups and close or terminate an account. Explain which documents "
            "are needed and confirm every change back to the customer."
        ),
        tools=_mcp_tools_named(
            "getAccountDetails", "updateAccountDetails", "getBalance",
            "verifyIdentity", "setAutoTopUp", "closeAccount",
        ),
    )


def _build_transfer_to_human_agent() -> LlmAgent:
    return LlmAgent(
        name="transfer_to_human_agent",
        model=_sub_agent_model(),
        description="Escalates fraud, disputes, lost/stolen devices or explicit requests to a human agent.",
        instruction=(
            "You are the escalation specialist. The customer is reporting "
            "suspected fraud, an unrecognised payment, a lost or stolen card or "
            "phone, a failed identity verification, or has explicitly asked for a "
            "human. Reassure them briefly, collect the minimum details needed "
            "(what happened, when, amount), immediately secure the account if a "
            "tool allows it (e.g. block the card) and hand over to a human agent "
            "using the escalation tool. Never attempt to resolve a fraud case "
            "yourself."
        ),
        tools=_mcp_tools_named("escalateToHuman", "createSupportTicket", "blockCard"),
    )


def _build_answer_hub_agent() -> LlmAgent:
    return LlmAgent(
        name="answer_hub_agent",
        model=_sub_agent_model(),
        description="Answers general product and policy questions (supported countries, cards, currencies, timings).",
        instruction=(
            "You are the bank's knowledge hub. Answer general questions about the "
            "product: supported countries and currencies, Visa vs Mastercard, "
            "age limits, how to top up, how long transfers take, ATM support and "
            "how identity verification works. Be accurate and concise; if a "
            "question is about the customer's own account or a specific "
            "transaction, say that a specialist will handle it."
        ),
        tools=_mcp_tools_named("searchFaq", "getCurrentDateTime"),
    )


def _build_transaction_agent() -> LlmAgent:
    return LlmAgent(
        name="transaction_agent",
        model=_sub_agent_model(),
        description="Handles payments, transfers, top-ups, refunds and declined or pending transactions.",
        instruction=(
            "You are the transactions specialist. Help the customer with "
            "pending, failed, declined or duplicated payments, transfers that "
            "have not arrived, cancelling a transfer, refunds not showing up, "
            "and top-ups that failed or reverted. Look up the relevant "
            "transaction with the tools before answering and explain the status "
            "and next steps clearly."
        ),
        tools=_mcp_tools_named(
            "getTransactions", "getTransaction", "cancelTransfer",
            "requestRefund", "getBalance", "convertCurrency",
        ),
    )


def _build_rates_fees_agent() -> LlmAgent:
    return LlmAgent(
        name="rates_fees_limits_management_agent",
        model=_sub_agent_model(),
        description="Explains exchange rates, fees, charges and account/card limits.",
        instruction=(
            "You are the rates, fees and limits specialist. Explain exchange "
            "rates and FX charges, card payment and cash withdrawal fees, "
            "transfer and top-up fees, unexpected charges on a statement, and "
            "daily/monthly limits for withdrawals, top-ups and disposable cards. "
            "Use the tools to quote live rates and current limits; offer to "
            "adjust limits where a tool allows it."
        ),
        tools=_mcp_tools_named(
            "getExchangeRate", "convertCurrency", "getFees", "getLimits", "updateLimits",
        ),
    )


def _build_spending_insights_agent() -> LlmAgent:
    return LlmAgent(
        name="spending_insights_agent",
        model=_sub_agent_model(),
        description="Provides spending analytics: category breakdowns, trends, top merchants, budgets.",
        instruction=(
            "You are the spending insights analyst. Summarise the customer's "
            "spending by category, merchant or period, compare periods, highlight "
            "trends and subscriptions and suggest simple budgeting tips. Retrieve "
            "the transactions with the tools and present numbers clearly."
        ),
        tools=_mcp_tools_named("getTransactions", "getSpendingSummary", "getCurrentDateTime"),
    )


def _build_fallback_agent() -> LlmAgent:
    return LlmAgent(
        name="fallback_agent",
        model=_sub_agent_model(),
        description="Handles out-of-scope or low-confidence requests.",
        instruction=(
            "You are a polite general assistant for a banking app. The request "
            "could not be confidently matched to a banking specialist. If it is "
            "a banking question, ask a short clarifying question so it can be "
            "routed correctly. If it is unrelated to banking, answer briefly if "
            "you can or explain that you can only help with banking matters."
        ),
    )


# ------------------------------------------------------------------
# Team agent (sub-agent auto-delegation)
# ------------------------------------------------------------------


async def build_team_agent() -> LlmAgent:
    """Builds a root coordinator agent that delegates to specialized sub-agents."""
    greeting = _build_greeting_agent()
    weather = await _build_weather_agent()
    math = _build_math_agent()
    mcp_agent = _build_mcp_agent()

    return LlmAgent(
        name="team_coordinator",
        model=settings.GEMINI_MODEL,
        description="Coordinates a team of greeting, weather, math and MCP tool agents.",
        instruction=(
            "You are the coordinator for a small team of agents. "
            "Route the user's request to the most appropriate agent:\n"
            "- greeting_agent: for hellos, goodbyes, or general social chat\n"
            "- weather_agent: for weather or forecast questions\n"
            "- math_agent: for arithmetic, calculations, or math problems\n"
            "- mcp_agent: for stock prices, currency conversion, sending emails, "
            "  current date/time, or any tool exposed by the MCP server\n"
            "Use the transfer_to_agent tool when another agent is better suited."
        ),
        sub_agents=[greeting, weather, math, mcp_agent],
    )


# ------------------------------------------------------------------
# Route graph agent (Workflow)
# ------------------------------------------------------------------


_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "transfer_to_human_agent",
        ("human", "real person", "agent please", "fraud", "stolen", "lost my",
         "don't recognise", "don't recognize", "didn't make", "never made",
         "compromised", "scam"),
    ),
    (
        "card_management_agent",
        ("card", "pin", "freeze", "block", "activate", "apple pay", "google pay",
         "contactless", "expire"),
    ),
    (
        "spending_insights_agent",
        ("spend", "spent", "spending", "breakdown", "budget", "category",
         "merchants", "summary of my", "how much did i"),
    ),
    (
        "rates_fees_limits_management_agent",
        ("fee", "charge", "exchange rate", "rate", "limit", "commission"),
    ),
    (
        "transaction_agent",
        ("transfer", "payment", "transaction", "refund", "declined", "pending",
         "top up", "top-up", "topup", "charged twice", "not arrived", "balance"),
    ),
    (
        "account_management_agent",
        ("account", "address", "phone number", "email", "passcode", "password",
         "verify", "identity", "close my", "personal details"),
    ),
    (
        "answer_hub_agent",
        ("which countries", "country", "currencies", "visa", "mastercard",
         "age", "how long", "do you support", "can i use", "what is", "how do"),
    ),
)


def route_by_keyword_rules(text: str) -> str:
    """Keyword-based fallback classifier used when the BERT model is absent."""
    lowered = text.lower()
    for label, keywords in _KEYWORD_RULES:
        if any(k in lowered for k in keywords):
            return label
    return FALLBACK_LABEL


async def route_by_keyword(ctx, node_input: Any) -> Event:
    """Classifies the user message and emits a route for the workflow.

    Uses the fine-tuned BERT intent classifier when the model directory is
    present; otherwise falls back to keyword rules. The function keeps its
    historical name so existing graph wiring and imports continue to work.
    """
    text = _extract_text(node_input)

    if intent_classifier.available:
        route, confidence = intent_classifier.classify(text)
        logger.info(
            "Graph router (bert) classified input as route=%s confidence=%.3f",
            route,
            confidence,
        )
    else:
        route = route_by_keyword_rules(text)
        logger.info("Graph router (keyword) classified input as route=%s", route)

    # Preserve the original input as output so the downstream agent receives it.
    return Event(output=node_input, route=route)


async def build_graph_agent() -> Workflow:
    """Builds the financial supervisor Workflow: BERT router -> 8 sub-agents."""
    sub_agents = {
        "card_management_agent": _build_card_agent(),
        "account_management_agent": _build_account_agent(),
        "transfer_to_human_agent": _build_transfer_to_human_agent(),
        "answer_hub_agent": _build_answer_hub_agent(),
        "transaction_agent": _build_transaction_agent(),
        "rates_fees_limits_management_agent": _build_rates_fees_agent(),
        "spending_insights_agent": _build_spending_insights_agent(),
        FALLBACK_LABEL: _build_fallback_agent(),
    }

    # Override the agent modes because nodes in a workflow must be single_turn.
    for agent in sub_agents.values():
        agent.mode = "single_turn"

    return Workflow(
        name="financial_supervisor_workflow",
        edges=[
            (START, route_by_keyword),
            (route_by_keyword, sub_agents),
        ],
    )
