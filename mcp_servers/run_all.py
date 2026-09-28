"""Run the cards, transactions and accounts MCP servers (SSE) in one process group."""

from __future__ import annotations

import multiprocessing
import signal
import sys


def _serve(module: str) -> None:
    import importlib

    importlib.import_module(module).mcp.run(transport="sse")


def main() -> int:
    modules = ["mcp_servers.cards_server", "mcp_servers.transactions_server", "mcp_servers.accounts_server"]
    procs = [multiprocessing.Process(target=_serve, args=(m,), name=m, daemon=True) for m in modules]
    for p in procs:
        p.start()

    def stop(*_: object) -> None:
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for p in procs:
        p.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
