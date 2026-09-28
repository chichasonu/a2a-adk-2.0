"""Helpers for running the demo banking MCP servers over SSE."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

DEFAULT_PORTS = {"cards": 8101, "transactions": 8102, "accounts": 8103}


def make_server(domain: str, instructions: str) -> FastMCP:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get(f"{domain.upper()}_MCP_PORT", DEFAULT_PORTS[domain]))
    return FastMCP(name=f"{domain}-mcp", instructions=instructions, host=host, port=port)


def not_found(kind: str, key: str) -> dict:
    return {"ok": False, "error": f"{kind} {key!r} not found"}
