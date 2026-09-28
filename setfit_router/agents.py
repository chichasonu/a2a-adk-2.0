"""ADK supervisor: a Workflow whose SetFit route node dispatches to MCP-backed sub-agents."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams
from google.adk.workflow import START, Workflow
from google.genai import types

from setfit_router.router import FALLBACK, Router

logger = logging.getLogger(__name__)

ROUTING_METADATA_KEY = "routing"
SUPERVISOR_NAME = "banking_supervisor"
LITELLM_PREFIXES = ("openrouter/",)

SUB_AGENT_SPECS: dict[str, tuple[str, str]] = {
    "cards": (
        "Handles card activation, delivery, ordering, virtual/disposable cards, PIN, "
        "lost/stolen/compromised cards, contactless and wallet support.",
        "You are the Cards specialist for a retail bank. Use the cards tools to look up the "
        "customer's cards and take action (activate, freeze, block and replace, order, unblock PIN). "
        "Confirm destructive actions before taking them.",
    ),
    "transactions": (
        "Handles card payments, cash withdrawals, transfers, refunds, top-ups, pending or "
        "declined transactions, duplicate charges and fees.",
        "You are the Transactions specialist for a retail bank. Use the transactions tools to "
        "inspect payments, transfers and top-ups, open disputes, request refunds, cancel pending "
        "transfers and quote fees.",
    ),
    "accounts": (
        "Handles account balances, personal details, identity and source-of-funds verification, "
        "passcode resets, eligibility and account closure.",
        "You are the Accounts specialist for a retail bank. Use the accounts tools to read the "
        "account summary, update personal details, start verification checks, reset passcodes "
        "and close accounts (only after explicit confirmation).",
    ),
}

FALLBACK_INSTRUCTION = (
    "You are the general banking assistant. The request did not clearly match the cards, "
    "transactions or accounts specialists (for example exchange rates, supported countries "
    "or currencies, ATM questions, or an unclear request). Answer general questions briefly and, "
    "if the customer needs account-specific help, ask a clarifying question about whether it "
    "concerns a card, a transaction, or their account."
)

COMMON_SUFFIX = (
    " The demo customer id is 'cust-001' with accounts 'acc-001' (GBP) and 'acc-002' (EUR). "
    "Be concise and base answers on tool results."
)


@dataclass
class Supervisor:
    workflow: Workflow
    toolsets: list[McpToolset] = field(default_factory=list)

    async def close(self) -> None:
        await asyncio.gather(*(t.close() for t in self.toolsets), return_exceptions=True)


def extract_text(node_input: Any) -> str:
    if isinstance(node_input, types.Content):
        return "".join(part.text or "" for part in node_input.parts or [])
    if isinstance(node_input, str):
        return node_input
    return str(node_input)


def resolve_model(model: str | BaseLlm) -> str | BaseLlm:
    """`openrouter/<provider>/<model>` runs through LiteLLM (OPENROUTER_API_KEY); other strings go to ADK."""
    if isinstance(model, str) and model.startswith(LITELLM_PREFIXES):
        return LiteLlm(model=model)
    return model


def build_supervisor(
    router: Router,
    model: str | BaseLlm,
    mcp_urls: dict[str, str],
    mcp_timeout_s: float = 10.0,
) -> Supervisor:
    """Build the supervisor workflow: START -> setfit_route -> {cards|transactions|accounts|fallback}."""
    model = resolve_model(model)
    toolsets: list[McpToolset] = []
    branches: dict[str, LlmAgent] = {}
    for agent, (description, instruction) in SUB_AGENT_SPECS.items():
        toolset = McpToolset(
            connection_params=SseConnectionParams(url=mcp_urls[agent], timeout=mcp_timeout_s)
        )
        toolsets.append(toolset)
        branches[agent] = LlmAgent(
            name=f"{agent}_agent",
            model=model,
            description=description,
            instruction=instruction + COMMON_SUFFIX,
            tools=[toolset],
            mode="single_turn",
        )
    branches[FALLBACK] = LlmAgent(
        name="fallback_agent",
        model=model,
        description="General banking questions that no specialist owns.",
        instruction=FALLBACK_INSTRUCTION,
        mode="single_turn",
    )

    async def setfit_route(ctx, node_input: Any) -> Event:
        text = extract_text(node_input)
        decision = await asyncio.to_thread(router.route, text)
        agent = decision.agent if decision.agent in branches else FALLBACK
        logger.info("routed to %s (label=%s conf=%.3f)", agent, decision.predicted_label, decision.confidence)
        return Event(
            output=node_input,
            route=agent,
            custom_metadata={ROUTING_METADATA_KEY: {**decision.to_dict(), "agent": agent}},
        )

    workflow = Workflow(name=SUPERVISOR_NAME, edges=[(START, setfit_route), (setfit_route, branches)])
    return Supervisor(workflow=workflow, toolsets=toolsets)
