"""Tool definitions for the ADK 2.0 agents."""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)


def get_weather(city: str = "the user's location") -> str:
    """Returns a mocked weather report for the requested city."""
    return f"The weather in {city} is sunny and 25°C."


def get_current_time() -> str:
    """Returns the current date and time in ISO format."""
    return datetime.datetime.now().isoformat()


# All tool functions exported for registration in the tool cache.
TOOLS = [get_weather, get_current_time]
