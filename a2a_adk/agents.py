"""Agent definitions demonstrating ADK 2.0 team agents, route graphs and remote A2A orchestration."""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.a2a import _compat
from google.adk.events.event import Event
from google.adk.workflow import Workflow
from google.adk.workflow import START
from google.genai import types

from .auth import a2a_http_client
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
# Public builders for every root agent exposed by the server
# ------------------------------------------------------------------


async def build_greeting_agent() -> LlmAgent:
    return _build_greeting_agent()


async def build_weather_agent() -> LlmAgent:
    return await _build_weather_agent()


async def build_math_agent() -> LlmAgent:
    return _build_math_agent()


async def build_mcp_agent() -> LlmAgent:
    return _build_mcp_agent()


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


# ------------------------------------------------------------------
# Supervisor / orchestrator agent that delegates over A2A
# ------------------------------------------------------------------


def _build_remote_agent_card(slug: str, description: str) -> Any:
    """Build an A2A AgentCard pointing at the RPC endpoint for a local agent."""
    base_url = settings.A2A_BASE_URL.rstrip("/")
    rpc_url = f"{base_url}/a2a/{slug}-agent"
    return _compat.build_agent_card(
        name=f"adk-{slug}-agent",
        description=description,
        version="0.1.0",
        url=rpc_url,
        protocol_binding="jsonrpc",
        default_input_modes=("text/plain",),
        default_output_modes=("text/plain",),
        streaming=True,
    )


async def build_orchestrator_agent() -> LlmAgent:
    """Builds a supervisor agent whose sub-agents are remote A2A agents."""
    base_url = settings.A2A_BASE_URL.rstrip("/")
    a2a_client = a2a_http_client()

    remote_agents = [
        RemoteA2aAgent(
            name="remote_team_agent",
            agent_card=_build_remote_agent_card("team", "Team coordinator exposed over A2A."),
            description="Remote team coordinator exposed over A2A.",
            httpx_client=a2a_client,
        ),
        RemoteA2aAgent(
            name="remote_graph_agent",
            agent_card=_build_remote_agent_card("graph", "Route-graph workflow exposed over A2A."),
            description="Remote route-graph workflow exposed over A2A.",
            httpx_client=a2a_client,
        ),
        RemoteA2aAgent(
            name="remote_greeting_agent",
            agent_card=_build_remote_agent_card("greeting", "Greeting agent exposed over A2A."),
            description="Remote greeting agent exposed over A2A.",
            httpx_client=a2a_client,
        ),
        RemoteA2aAgent(
            name="remote_weather_agent",
            agent_card=_build_remote_agent_card("weather", "Weather agent exposed over A2A."),
            description="Remote weather agent exposed over A2A.",
            httpx_client=a2a_client,
        ),
        RemoteA2aAgent(
            name="remote_math_agent",
            agent_card=_build_remote_agent_card("math", "Math agent exposed over A2A."),
            description="Remote math agent exposed over A2A.",
            httpx_client=a2a_client,
        ),
        RemoteA2aAgent(
            name="remote_mcp_agent",
            agent_card=_build_remote_agent_card("mcp", "MCP tools agent exposed over A2A."),
            description="Remote MCP tools agent exposed over A2A.",
            httpx_client=a2a_client,
        ),
    ]

    return LlmAgent(
        name="supervisor_orchestrator",
        model=settings.GEMINI_MODEL,
        description=(
            "Main supervisor that routes user requests to the most appropriate "
            "remote A2A agent."
        ),
        instruction=(
            "You are the main supervisor for a fleet of remote A2A agents. "
            "Route the user's request to the most appropriate remote agent:\n"
            "- remote_team_agent: general coordination or when unsure\n"
            "- remote_graph_agent: requests that need conditional routing\n"
            "- remote_greeting_agent: hellos, goodbyes, social chat\n"
            "- remote_weather_agent: weather or forecast questions\n"
            "- remote_math_agent: arithmetic, calculations, math problems\n"
            "- remote_mcp_agent: stock prices, currency conversion, emails, "
            "  current date/time or any tool exposed by the MCP server\n"
            "Use the transfer_to_agent tool when another agent is better suited."
        ),
        sub_agents=remote_agents,
    )
