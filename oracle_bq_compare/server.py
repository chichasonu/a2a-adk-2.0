"""Run the API with Uvicorn."""

from __future__ import annotations

import argparse
import logging

import uvicorn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="oracle-bq-compare-api")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run("oracle_bq_compare.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0
