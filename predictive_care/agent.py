"""ADK 2.0 agent for proactive card issue resolution."""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent
from google.adk.apps.app import App
from google.adk.runners import Runner
from google.adk.tools import load_memory
from google.adk.tools import preload_memory

from .config import settings
from .memory import RedisMemoryService
from .session_store import RedisCareSessionService
from .tools import TOOLS

logger = logging.getLogger(__name__)

INSTRUCTION = """
You are the proactive card care agent for a retail bank. The app detected that
the customer is struggling with their debit card (repeated visits to the card
management page, card-related searches, or declined transactions) and opened
this conversation on its own.

How to behave:
- Open by naming the likely problem and offering the four resolutions you can
  perform: replace, activate, dispute or unlock the card.
- Use `recall_customer_history` and the memory you are given to personalise the
  offer. If long-term memory shows a preferred resolution or a recurring issue,
  say so briefly ("last time we replaced your card").
- Call `get_card_status` before recommending an action so your advice matches
  the real card state.
- Perform exactly the action the customer confirms, with the matching tool, and
  report the concrete outcome (case id, new card digits, ETA).
- Never invent case ids, amounts or dates: use tool results only.
- Keep replies to two or three short sentences; no bullet lists, no markdown.
"""


def build_care_agent() -> LlmAgent:
    """Build the card care LlmAgent with resolution tools and memory access."""
    return LlmAgent(
        name="card_care_agent",
        model=settings.GEMINI_MODEL,
        description=(
            "Resolves debit card issues (replace, activate, dispute, unlock) "
            "using long-term customer memory."
        ),
        instruction=INSTRUCTION,
        tools=[*TOOLS, preload_memory, load_memory],
    )


def build_care_runner(
    *,
    memory: RedisMemoryService,
    session_service: RedisCareSessionService,
) -> Runner:
    """Build the ADK Runner wired to Redis sessions and long-term memory."""
    app = App(name=settings.APP_NAME, root_agent=build_care_agent())
    return Runner(
        app=app,
        session_service=session_service,
        memory_service=memory,
    )
