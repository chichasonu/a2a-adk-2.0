"""CLI entrypoint for the A2A ADK 2.0 agent, backed by Uvicorn."""

import argparse
import logging

import uvicorn

from .config import settings

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the Uvicorn-backed server."""
    parser = argparse.ArgumentParser(
        description="A2A ADK 2.0 agent server (Uvicorn)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind the server (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.PORT,
        help=f"Port to bind the server (default: {settings.PORT}).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable Uvicorn auto-reload for development.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Uvicorn log level (default: info).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of Uvicorn worker processes (default: 1).",
    )
    return parser


def main() -> None:
    """Run the FastAPI/A2A server with Uvicorn."""
    parser = build_parser()
    args = parser.parse_args()

    uvicorn.run(
        "a2a_adk.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
