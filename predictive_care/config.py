"""Configuration for the predictive card-care application."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _env_bool(key: str, default: str = "false") -> bool:
    return _env(key, default).lower() in ("true", "1", "yes")


class Settings:
    """Runtime settings for the predictive care app.

    ``GOOGLE_API_KEY`` is optional: without it the app runs the deterministic
    rule engine instead of the Gemini-backed ADK agent, so the POC can be
    demonstrated offline.
    """

    APP_NAME: str = _env("CARE_APP_NAME", "predictive-card-care")
    GOOGLE_API_KEY: str = _env("GOOGLE_API_KEY")
    GEMINI_MODEL: str = _env("GEMINI_MODEL", "gemini-2.0-flash")
    REDIS_URL: str = _env("REDIS_URL", "redis://localhost:6379/0")
    USE_FAKEREDIS: bool = _env_bool("USE_FAKEREDIS", "true")
    PORT: int = int(_env("CARE_PORT", "8100"))
    LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

    # Signal retention: behavioural signals older than this are ignored by the
    # predictor but stay in long-term memory as durable facts.
    SIGNAL_WINDOW_SECONDS: int = int(_env("CARE_SIGNAL_WINDOW_SECONDS", "900"))
    SIGNAL_TTL_SECONDS: int = int(_env("CARE_SIGNAL_TTL_SECONDS", "86400"))
    MEMORY_TTL_SECONDS: int = int(_env("CARE_MEMORY_TTL_SECONDS", "0"))

    # Predictor tuning.
    INTERVENE_THRESHOLD: float = float(_env("CARE_INTERVENE_THRESHOLD", "0.6"))
    REPEAT_VIEW_THRESHOLD: int = int(_env("CARE_REPEAT_VIEW_THRESHOLD", "2"))
    # Do not re-prompt the same user more often than this.
    INTERVENTION_COOLDOWN_SECONDS: int = int(
        _env("CARE_INTERVENTION_COOLDOWN_SECONDS", "60")
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.GOOGLE_API_KEY) and not _env_bool("CARE_FORCE_RULES")


settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
