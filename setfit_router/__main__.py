"""``python -m setfit_router`` — serve the streaming API with uvicorn."""

from __future__ import annotations

import argparse

import uvicorn

from setfit_router.app import create_app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
