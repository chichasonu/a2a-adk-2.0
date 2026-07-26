"""CLI entrypoint for the A2A ADK 2.0 agent."""

import argparse
import logging

import uvicorn

from .config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the FastAPI/A2A server or an interactive console."""
    parser = argparse.ArgumentParser(
        description="A2A ADK 2.0 agent server"
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
        help="Enable uvicorn reload for development.",
    )
    args = parser.parse_args()

    uvicorn.run(
        "a2a_adk.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
