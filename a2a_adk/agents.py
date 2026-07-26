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


def _build_weather_agent() -> LlmAgent:
    return LlmAgent(
        name="weather_agent",
        model=settings.GEMINI_MODEL,
        description="Provides brief, cheerful weather information.",
        instruction=(
            "You are a weather assistant. Provide a short, friendly weather "
            "forecast. If a city is mentioned, include it in your answer. "
            "Keep your response to one or two sentences."
        ),
        tools=[get_weather],
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


def build_team_agent() -> LlmAgent:
    """Builds a root coordinator agent that delegates to specialized sub-agents."""
    greeting = _build_greeting_agent()
    weather = _build_weather_agent()
    math = _build_math_agent()

    return LlmAgent(
        name="team_coordinator",
        model=settings.GEMINI_MODEL,
        description="Coordinates a team of greeting, weather and math agents.",
        instruction=(
            "You are the coordinator for a small team of agents. "
            "Route the user's request to the most appropriate agent:\n"
            "- greeting_agent: for hellos, goodbyes, or general social chat\n"
            "- weather_agent: for weather or forecast questions\n"
            "- math_agent: for arithmetic, calculations, or math problems\n"
            "Use the transfer_to_agent tool when another agent is better suited."
        ),
        sub_agents=[greeting, weather, math],
    )


# ------------------------------------------------------------------
# Route graph agent (Workflow)
# ------------------------------------------------------------------


def get_weather(city: str = "the user's location") -> str:
    """Returns a mocked weather report for the requested city."""
    return f"The weather in {city} is sunny and 25°C."


async def route_by_keyword(ctx, node_input: Any) -> Event:
    """Classifies the user message and emits a route for the workflow."""
    text = _extract_text(node_input).lower()

    if any(k in text for k in ("hello", "hi", "hey", "greet", "good morning")):
        route = "greeting"
    elif any(k in text for k in ("weather", "forecast", "rain", "sunny", "temperature")):
        route = "weather"
    elif any(k in text for k in ("math", "calculate", "sum", "+", "-", "*", "/", "number")):
        route = "math"
    else:
        route = "fallback"

    logger.info("Graph router classified input as route=%s", route)

    # Preserve the original input as output so the downstream agent receives it.
    return Event(output=node_input, route=route)


def build_graph_agent() -> Workflow:
    """Builds a Workflow that routes user input through a conditional graph."""
    greeting = _build_greeting_agent()
    weather = _build_weather_agent()
    math = _build_math_agent()
    fallback = _build_fallback_agent()

    # Override the agent modes because nodes in a workflow must be single_turn.
    for agent in (greeting, weather, math, fallback):
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
                    "fallback": fallback,
                },
            ),
        ],
    )
