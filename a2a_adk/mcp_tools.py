"""MCP tool discovery, caching, and remote execution for the ADK agent."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from google.adk.tools import BaseTool
from google.adk.tools import ToolContext
from google.genai import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from .config import settings
from .redis_client import create_redis_client

logger = logging.getLogger(__name__)


def _schema_hash(schema: dict[str, Any]) -> str:
    """Return a stable hash of a tool input schema for cache invalidation."""
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=True)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


class McpTool(BaseTool):
    """A remote MCP tool exposed as an ADK BaseTool.

    The declaration is built from the MCP tool's JSON schema and is cached in
    Redis. Executing the tool makes a streamable-HTTP call back to the MCP
    server.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        server_url: str,
    ) -> None:
        super().__init__(name=name, description=description)
        self.input_schema = input_schema
        self.server_url = server_url
        self._declaration = types.FunctionDeclaration(
            name=name,
            description=description,
            parameters_json_schema=input_schema,
        )

    def _get_declaration(self) -> types.FunctionDeclaration | None:
        return self._declaration

    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        logger.info("Calling MCP tool %s on %s with args %s", self.name, self.server_url, args)
        try:
            async with streamable_http_client(self.server_url) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(self.name, args or {})
                    if result.isError:
                        return f"MCP tool {self.name} failed: {result.content}"
                    texts = [
                        block.text
                        for block in result.content
                        if isinstance(block, TextContent)
                    ]
                    return "\n".join(texts) if texts else str(result.content)
        except Exception as exc:
            logger.exception("MCP tool %s call failed", self.name)
            return f"Error calling MCP tool {self.name}: {exc}"


class McpToolCache:
    """Discover, cache and refresh MCP tools from a Spring MCP server.

    Tool declarations are stored in Redis keyed by an MD5 hash of the tool's
    JSON input schema. When ``discover()`` runs, any tool whose schema hash has
    changed is rebuilt and its Redis entry is updated. ``refresh()`` forces a
    full re-fetch from the MCP server.
    """

    KEY_PREFIX = "adk:mcp_tools"

    def __init__(self, app_name: str | None = None) -> None:
        self.app_name = app_name or settings.APP_NAME
        self._redis = create_redis_client()
        self._tools: dict[str, McpTool] = {}
        self.server_url: str = settings.MCP_SERVER_URL

    def _key(self, name: str) -> str:
        return f"{self.KEY_PREFIX}:{self.app_name}:{name}"

    async def discover(self, server_url: str | None = None) -> list[str]:
        """List tools from the MCP server and register them in memory and Redis.

        Returns the list of tool names that were discovered.
        """
        url = server_url or self.server_url
        if not settings.MCP_ENABLED:
            logger.info("MCP is disabled; skipping tool discovery")
            return []

        try:
            async with streamable_http_client(url) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    tools = response.tools
        except Exception:
            logger.exception("Failed to discover MCP tools from %s", url)
            return []

        discovered: list[str] = []
        for tool_def in tools:
            name = tool_def.name
            schema = tool_def.inputSchema
            description = tool_def.description or name
            schema_hash = _schema_hash(schema)

            cached = await self._redis.hgetall(self._key(name))
            if cached and cached.get("source_hash") == schema_hash:
                # Load from cached declaration if the schema has not changed.
                try:
                    decl = types.FunctionDeclaration.model_validate_json(
                        cached["declaration"]
                    )
                    self._tools[name] = McpTool(
                        name=name,
                        description=decl.description or description,
                        input_schema=schema,
                        server_url=url,
                    )
                    logger.debug("MCP tool %s loaded from Redis cache", name)
                    discovered.append(name)
                    continue
                except Exception:
                    logger.exception("Failed to load cached MCP tool %s", name)

            # New or changed tool: persist the declaration.
            mcp_tool = McpTool(
                name=name,
                description=description,
                input_schema=schema,
                server_url=url,
            )
            mapping = {
                "tool_name": name,
                "declaration": _declaration_to_json(mcp_tool._declaration),
                "source_hash": schema_hash,
                "server_url": url,
                "updated_at": str(time.time()),
            }
            try:
                await self._redis.hset(self._key(name), mapping=mapping)
            except Exception:
                logger.exception("Failed to persist MCP tool %s", name)

            self._tools[name] = mcp_tool
            logger.info("MCP tool discovered and cached: %s", name)
            discovered.append(name)

        return discovered

    async def refresh(self) -> list[str]:
        """Force a fresh fetch from the MCP server and invalidate any changes."""
        # Clear in-memory state so changed tools are replaced and removed tools
        # disappear from the cache.
        self._tools.clear()
        keys = [self._key(name) for name in await self._list_keys()]
        if keys:
            try:
                await self._redis.delete(*keys)
            except Exception:
                logger.exception("Failed to clear MCP tool cache")
        return await self.discover()

    async def _list_keys(self) -> list[str]:
        try:
            return list(await self._redis.keys(f"{self.KEY_PREFIX}:{self.app_name}:*"))
        except Exception:
            logger.exception("Failed to list MCP tool cache keys")
            return []

    def get_tool(self, name: str) -> McpTool | None:
        return self._tools.get(name)

    def get_tools(self) -> list[McpTool]:
        return list(self._tools.values())

    async def list_cached(self) -> list[dict[str, Any]]:
        """Return metadata for all cached MCP tool declarations."""
        keys = await self._list_keys()
        if not keys:
            return []

        pipe = self._redis.pipeline()
        for key in keys:
            pipe.hgetall(key)
        results = await pipe.execute()

        cached: list[dict[str, Any]] = []
        for result in results:
            if result:
                cached.append(
                    {
                        "name": result.get("tool_name"),
                        "source_hash": result.get("source_hash"),
                        "server_url": result.get("server_url"),
                        "updated_at": float(result.get("updated_at") or 0),
                        "declaration": result.get("declaration"),
                    }
                )
        return cached


def _declaration_to_json(declaration: types.FunctionDeclaration | None) -> str:
    if declaration is None:
        return "{}"
    return declaration.model_dump_json(exclude_none=True, by_alias=False)


# Global MCP tool cache.
mcp_tool_cache = McpToolCache()
