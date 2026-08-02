"""Home Assistant MCP Server as the tool backend.

Connects to HA's Model Context Protocol server (the `mcp_server` integration,
SSE transport at <ha>/mcp_server/sse) and exposes its tools — the full Assist
intent set over everything the user exposed to Assist — to the Deepslate
session. Tool schemas map 1:1 onto the SDK's FunctionToolDict shape.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


def mcp_tools_to_function_dicts(tools) -> list[dict]:
    """Translate MCP tool descriptors to Deepslate FunctionToolDicts."""
    defs = []
    for tool in tools:
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return defs


def result_to_text(result) -> str:
    """Flatten an MCP CallToolResult into a string for the model."""
    parts = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    text = "\n".join(parts) or "(empty result)"
    if getattr(result, "isError", False):
        return f"Error: {text}"
    return text


class MCPTools:
    """Lazy, self-healing client for the HA MCP server."""

    def __init__(self, base_url: str, token: str):
        self._url = base_url.rstrip("/") + "/mcp_server/sse"
        self._token = token
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def _ensure_connected(self) -> ClientSession:
        if self._session is not None:
            return self._session
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(
                sse_client(self._url, headers={"Authorization": f"Bearer {self._token}"})
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        logger.info("connected to HA MCP server at %s", self._url)
        return session

    async def get_tools(self) -> list[dict]:
        session = await self._ensure_connected()
        listed = await session.list_tools()
        defs = mcp_tools_to_function_dicts(listed.tools)
        logger.info("HA MCP exposes %d tools: %s", len(defs),
                    ", ".join(d["function"]["name"] for d in defs))
        return defs

    async def execute(self, name: str, params: dict) -> str:
        """Call an MCP tool; never raises — errors become the result string."""
        try:
            session = await self._ensure_connected()
            result = await session.call_tool(name, params or {})
            return result_to_text(result)
        except Exception as e:
            logger.exception("MCP tool %s failed", name)
            await self.close()  # drop the (possibly broken) connection; reconnect next call
            return f"Error executing {name}: {e}"

    async def close(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as e:
                logger.debug("mcp close error: %r", e)
