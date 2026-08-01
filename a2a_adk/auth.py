"""API key / Bearer token authentication helpers."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from typing import AsyncGenerator

from fastapi import Request
from mcp.client.streamable_http import streamable_http_client
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings

try:
    import httpx
except ImportError:  # pragma: no cover - mcp already depends on httpx
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/agents",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(("/docs", "/redoc", "/openapi.json"))


def _extract_token(request: Request) -> str | None:
    token = request.headers.get("X-API-Key")
    if token:
        return token.strip()
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid API key when API_KEY is configured."""

    async def dispatch(self, request: Request, call_next):
        if not settings.API_KEY or _is_public(request.url.path):
            return await call_next(request)

        provided = _extract_token(request)
        if provided != settings.API_KEY:
            logger.warning(
                "Unauthorized request method=%s path=%s",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Invalid or missing API key"},
            )
        return await call_next(request)


def a2a_http_client() -> Any | None:
    """Return an httpx client carrying the ADK API key, or None if auth is disabled."""
    if not settings.API_KEY or httpx is None:
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.API_KEY}"},
        timeout=httpx.Timeout(60.0),
    )


def _mcp_auth_headers() -> dict[str, str]:
    if not settings.MCP_API_KEY:
        return {}
    return {"Authorization": f"Bearer {settings.MCP_API_KEY}"}


@asynccontextmanager
async def mcp_authenticated_client(url: str) -> AsyncGenerator[tuple[Any, Any, Any], None]:
    """Open a streamable HTTP MCP client that sends the configured MCP API key."""
    headers = _mcp_auth_headers()
    if not headers or httpx is None:
        async with streamable_http_client(url) as (read_stream, write_stream, get_session_id):
            yield read_stream, write_stream, get_session_id
        return

    client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0))
    try:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            get_session_id,
        ):
            yield read_stream, write_stream, get_session_id
    finally:
        await client.aclose()
