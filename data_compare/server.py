"""Uvicorn entrypoint for the comparison API and bundled React UI."""

import argparse

import uvicorn

from .config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the API server."""
    parser = argparse.ArgumentParser(
        description="Serve the Oracle vs BigQuery comparison API and UI."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host.")
    parser.add_argument("--port", type=int, default=8100, help="Bind port.")
    parser.add_argument("--reload", action="store_true", help="Auto-reload.")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Uvicorn log level.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the API server with Uvicorn."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level.upper())
    uvicorn.run(
        "data_compare.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
