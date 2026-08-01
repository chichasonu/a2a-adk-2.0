"""Application configuration loaded from environment variables."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(key, default)
    if required and not value:
        raise ValueError(f"Environment variable {key} is required.")
    return value or ""


class Settings:
    """Runtime settings."""

    GOOGLE_API_KEY: str = _get_env("GOOGLE_API_KEY", required=True)
    GEMINI_MODEL: str = _get_env("GEMINI_MODEL", "gemini-2.0-flash")
    REDIS_URL: str = _get_env("REDIS_URL", "redis://localhost:6379/0")
    USE_FAKEREDIS: bool = _get_env(
        "USE_FAKEREDIS", "false"
    ).lower() in ("true", "1", "yes")
    APP_NAME: str = _get_env("APP_NAME", "a2a-adk-2-0")
    PORT: int = int(_get_env("PORT", "8000") or "8000")
    A2A_AGENT_URL: str = _get_env(
        "A2A_AGENT_URL", "http://localhost:8000/a2a/team-agent"
    )
    A2A_BASE_URL: str = _get_env(
        "A2A_BASE_URL", f"http://localhost:{PORT}"
    )
    MCP_ENABLED: bool = _get_env(
        "MCP_ENABLED", "true"
    ).lower() in ("true", "1", "yes")
    MCP_SERVER_URL: str = _get_env(
        "MCP_SERVER_URL", "http://localhost:8080/mcp"
    )
    MCP_API_KEY: str = _get_env("MCP_API_KEY", "")

    # Optional API key / Bearer token auth for FastAPI and remote A2A calls.
    # Leave empty to disable auth.
    API_KEY: str = _get_env("API_KEY", "")

    LOG_LEVEL: str = _get_env("LOG_LEVEL", "INFO")


settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
