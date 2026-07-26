"""Redis-backed cache for tool function declarations with source-change refresh."""

from __future__ import annotations

import hashlib
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from google.adk.tools import BaseTool
from google.adk.tools import FunctionTool
from google.genai import types
from typing_extensions import override

from .config import settings
from .redis_client import create_redis_client
from .tools import TOOLS

logger = logging.getLogger(__name__)


def _source_hash(func: Callable) -> str:
    """Compute a hash of a function's source code for cache invalidation."""
    try:
        src = inspect.getsource(func)
    except Exception:
        # Fallback to the compiled code object if source is unavailable.
        code = getattr(func, "__code__", None)
        src = code.co_code.hex() if code else str(func)
    return hashlib.md5(src.encode("utf-8")).hexdigest()


def _declaration_to_json(function_decl: types.FunctionDeclaration | None) -> str:
    """Serialize a FunctionDeclaration to JSON."""
    if function_decl is None:
        return "{}"
    return function_decl.model_dump_json(exclude_none=True, by_alias=False)


class CachedFunctionTool(FunctionTool):
    """A FunctionTool that uses a cached declaration instead of rebuilding it."""

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        declaration: types.FunctionDeclaration | None = None,
    ) -> None:
        super().__init__(func)
        self._cached_declaration = declaration

    @override
    def _get_declaration(self) -> types.FunctionDeclaration | None:  # type: ignore[override]
        if self._cached_declaration is not None:
            return self._cached_declaration
        return super()._get_declaration()


class ToolCache:
    """Caches ADK tool declarations in Redis and rebuilds them on source changes.

    The cache stores each tool's JSON declaration and a hash of its source
    code under ``adk:tools:{app_name}:{tool_name}``. When a tool is requested,
    the cache checks the stored source hash; if the source has changed (or the
    cache entry is missing), the declaration is rebuilt and persisted.
    """

    KEY_PREFIX = "adk:tools"

    def __init__(self, app_name: str | None = None) -> None:
        self.app_name = app_name or settings.APP_NAME
        self._tools: dict[str, Callable] = {}
        self._tool_objects: dict[str, BaseTool] = {}
        self._redis = create_redis_client()

    def register_tool(self, func: Callable[..., Any]) -> None:
        """Register a single callable as a cached tool."""
        name = getattr(func, "__name__", str(func))
        self._tools[name] = func
        self._tool_objects.pop(name, None)

    def register_tools(self, funcs: list[Callable[..., Any]]) -> None:
        """Register a list of callable tools."""
        for func in funcs:
            self.register_tool(func)

    async def get_tool(self, name: str) -> BaseTool | None:
        """Return a tool object asynchronously, loading from cache or building it."""
        if name not in self._tool_objects:
            func = self._tools.get(name)
            if func is None:
                return None
            await self._load_or_build_tool(name, func)
        return self._tool_objects[name]

    def get_tool_sync(self, name: str) -> BaseTool | None:
        """Return a tool object synchronously, building it if necessary."""
        if name not in self._tool_objects:
            func = self._tools.get(name)
            if func is None:
                return None
            # Without an async context we fall back to a plain FunctionTool.
            self._tool_objects[name] = FunctionTool(func)
        return self._tool_objects[name]

    def get_tools_sync(self, names: list[str] | None = None) -> list[BaseTool]:
        """Return a list of tool objects synchronously.

        Args:
            names: Optional list of tool names to return. If omitted, all
                registered tools are returned.
        """
        names = names or list(self._tools.keys())
        tools: list[BaseTool] = []
        for name in names:
            tool = self.get_tool_sync(name)
            if tool is not None:
                tools.append(tool)
        return tools

    async def initialize(self) -> list[str]:
        """Load or build and cache all registered tool declarations.

        Returns the list of tool names that were (re-)cached.
        """
        initialized: list[str] = []
        for name, func in self._tools.items():
            await self._load_or_build_tool(name, func)
            initialized.append(name)
        return initialized

    async def _load_or_build_tool(self, name: str, func: Callable[..., Any]) -> BaseTool:
        """Load a tool declaration from Redis or build and cache it."""
        key = f"{self.KEY_PREFIX}:{self.app_name}:{name}"
        src_hash = _source_hash(func)

        cached = await self._redis.hgetall(key)
        if cached and cached.get("source_hash") == src_hash:
            try:
                decl = types.FunctionDeclaration.model_validate_json(
                    cached["declaration"]
                )
                tool = CachedFunctionTool(func, declaration=decl)
                self._tool_objects[name] = tool
                logger.debug("Tool %s loaded from cache", name)
                return tool
            except Exception:
                logger.exception("Failed to load cached tool %s; rebuilding", name)

        # Build the declaration from scratch and wrap it in a cached tool.
        temp_tool = FunctionTool(func)
        decl = temp_tool._get_declaration()
        tool = CachedFunctionTool(func, declaration=decl)
        mapping = {
            "declaration": _declaration_to_json(decl),
            "source_hash": src_hash,
            "updated_at": str(time.time()),
            "tool_name": name,
        }
        try:
            await self._redis.hset(key, mapping=mapping)
        except Exception:
            logger.exception("Failed to persist tool cache for %s", name)

        self._tool_objects[name] = tool
        logger.debug("Tool %s rebuilt and cached", name)
        return tool

    async def refresh(self, name: str | None = None) -> list[str]:
        """Invalidate and rebuild one or all cached tool declarations.

        Args:
            name: Optional tool name. If omitted, all registered tools are
                refreshed.

        Returns:
            The list of tool names that were refreshed.
        """
        names = [name] if name else list(self._tools.keys())
        refreshed: list[str] = []
        for n in names:
            if n not in self._tools:
                continue
            key = f"{self.KEY_PREFIX}:{self.app_name}:{n}"
            await self._redis.delete(key)
            self._tool_objects.pop(n, None)
            await self._load_or_build_tool(n, self._tools[n])
            refreshed.append(n)
        logger.info("Refreshed tools: %s", refreshed)
        return refreshed

    async def list_cached(self) -> list[dict[str, Any]]:
        """Return metadata for all cached tool declarations."""
        keys = [
            f"{self.KEY_PREFIX}:{self.app_name}:{name}"
            for name in self._tools
        ]
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
                        "updated_at": float(result.get("updated_at") or 0),
                        "declaration": result.get("declaration"),
                    }
                )
        return cached


# Global tool cache pre-populated with the tools defined in tools.py.
tool_cache = ToolCache()
tool_cache.register_tools(TOOLS)
