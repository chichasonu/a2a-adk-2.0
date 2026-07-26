"""Google ADK 2.0 agent with Redis session memory, route graphs and A2A executor."""

__version__ = "0.1.0"

from .config import settings
from .session_service import RedisSessionService
from .agents import build_team_agent, build_graph_agent
from .runner import build_runner, run_team_agent, run_graph_agent
from .main import build_app

__all__ = [
    "settings",
    "RedisSessionService",
    "build_team_agent",
    "build_graph_agent",
    "build_runner",
    "run_team_agent",
    "run_graph_agent",
    "build_app",
]
