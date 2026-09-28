"""Environment-driven settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT_MODEL = "openrouter/google/gemini-3.5-flash-lite"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "banking_router"))
    agent_model: str = field(default_factory=lambda: _env("AGENT_MODEL", DEFAULT_AGENT_MODEL))
    router_model_dir: Path = field(
        default_factory=lambda: Path(_env("ROUTER_MODEL_DIR", str(ROOT / "models" / "setfit-agent")))
    )
    router_confidence_threshold: float = field(
        default_factory=lambda: float(_env("ROUTER_CONFIDENCE_THRESHOLD", "0.35"))
    )
    routing_metrics_log: Path = field(
        default_factory=lambda: Path(
            _env("ROUTING_METRICS_LOG", str(ROOT / "logs" / "routing_metrics.jsonl"))
        )
    )
    routing_log_text: bool = field(
        default_factory=lambda: _env("ROUTING_LOG_TEXT", "false").lower() in {"1", "true", "yes"}
    )
    mcp_urls: dict[str, str] = field(
        default_factory=lambda: {
            "cards": _env("CARDS_MCP_URL", "http://127.0.0.1:8101/sse"),
            "transactions": _env("TRANSACTIONS_MCP_URL", "http://127.0.0.1:8102/sse"),
            "accounts": _env("ACCOUNTS_MCP_URL", "http://127.0.0.1:8103/sse"),
        }
    )
    mcp_timeout_s: float = field(default_factory=lambda: float(_env("MCP_TIMEOUT_S", "10")))
