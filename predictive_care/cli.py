"""Command line entrypoint for the predictive card-care app."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the predictive card-care app")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default=settings.LOG_LEVEL.lower())
    args = parser.parse_args()

    logger.info(
        "Starting %s on %s:%s (engine=%s)",
        settings.APP_NAME,
        args.host,
        args.port,
        "adk-agent" if settings.llm_enabled else "rule-engine",
    )
    uvicorn.run(
        "predictive_care.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
