"""Agent definitions demonstrating ADK 2.0 team agents and route graphs."""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.workflow import Workflow
from google.adk.workflow import START
from google.genai import types

from .config import settings
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


def _build_fallback_agent() -> LlmAgent:
    return LlmAgent(
        name="fallback_agent",
        model=settings.GEMINI_MODEL,
        description="Handles any request that does not fit the other categories.",
        instruction=(
            "You are a helpful fallback assistant. Answer general questions "
            "briefly and politely. If you cannot help, say so."
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


async def route_by_keyword(ctx, node_input: Any) -> Event:
    """Classifies the user message and emits a route for the workflow."""
    text = _extract_text(node_input).lower()

    if any(k in text for k in ("hello", "hi", "hey", "greet", "good morning")):
        route = "greeting"
    elif any(k in text for k in ("weather", "forecast", "rain", "sunny", "temperature")):
        route = "weather"
    elif any(k in text for k in ("math", "calculate", "sum", "+", "-", "*", "/", "number")):
        route = "math"
    elif any(k in text for k in ("stock", "price", "currency", "convert", "email", "time")):
        route = "mcp"
    else:
        route = "fallback"

    logger.info("Graph router classified input as route=%s", route)

    # Preserve the original input as output so the downstream agent receives it.
    return Event(output=node_input, route=route)


async def build_graph_agent() -> Workflow:
    """Builds a Workflow that routes user input through a conditional graph."""
    greeting = _build_greeting_agent()
    weather = await _build_weather_agent()
    math = _build_math_agent()
    mcp_agent = _build_mcp_agent()
    fallback = _build_fallback_agent()

    # Override the agent modes because nodes in a workflow must be single_turn.
    for agent in (greeting, weather, math, mcp_agent, fallback):
        agent.mode = "single_turn"

    return Workflow(
        name="routing_workflow",
        edges=[
            (START, route_by_keyword),
            (
                route_by_keyword,
                {
                    "greeting": greeting,
                    "weather": weather,
                    "math": math,
                    "mcp": mcp_agent,
                    "fallback": fallback,
                },
            ),
        ],
    )
