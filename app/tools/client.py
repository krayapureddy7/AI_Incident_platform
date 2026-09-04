"""
FastMCP Client Abstraction.

Decouples the Tool Gateway from the FastMCP protocol and server internals.
This is the only component permitted to speak MCP; agents reach it exclusively
through `ToolGateway`, never directly.
"""
import asyncio
import concurrent.futures
import json
from typing import Any, Dict, List, Optional

from .contracts import TOOL_DESCRIPTIONS, TOOL_PARAMETER_SCHEMAS, tool_version
from .server import fastmcp_server

#: Version of the MCP client abstraction and envelope normalization.
__version__ = "2.0.0"

__all__ = ["__version__", "ToolClient", "GLOBAL_TOOL_CLIENT"]


def _run_coroutine_sync(coro) -> Any:
    """
    Execute a coroutine from synchronous code, whether or not a loop is running.

    LangGraph nodes execute synchronously, and node bodies may themselves be
    invoked from a worker thread by the orchestrator's timeout guard. Both cases
    are handled: an already-running loop gets a dedicated worker thread, and a
    thread with no loop falls through to `asyncio.run`.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


class ToolClient:
    """
    Client abstraction for invoking FastMCP tools.

    Agents interact exclusively through the Tool Gateway, which owns an instance
    of this client, rather than coupling to FastMCP internals or cloud SDKs.
    """

    def __init__(self, server: Optional[Any] = None):
        self.server = server or fastmcp_server

    # -- Catalog ------------------------------------------------------------
    async def list_tools(self) -> List[Dict[str, Any]]:
        """List registered tools from the FastMCP server, enriched with versions."""
        mcp_tools = await self.server.list_tools()
        catalog: List[Dict[str, Any]] = []
        for tool in mcp_tools:
            name = tool.name
            catalog.append(
                {
                    "name": name,
                    "description": tool.description or TOOL_DESCRIPTIONS.get(name, ""),
                    "version": tool_version(name),
                    "parameters": getattr(tool, "inputSchema", None)
                    or TOOL_PARAMETER_SCHEMAS.get(name, {}),
                }
            )
        return catalog

    def list_tools_sync(self) -> List[Dict[str, Any]]:
        """Synchronous helper for listing tools."""
        return _run_coroutine_sync(self.list_tools())

    # -- Invocation ---------------------------------------------------------
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke an MCP tool with structured result unwrapping."""
        try:
            tool_res = await self.server.call_tool(name, arguments)
        except Exception as ex:  # noqa: BLE001 - surfaced as a structured envelope
            return {
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": str(ex), "retryable": False},
                "metadata": {"tool": name, "version": tool_version(name)},
            }

        return self._unwrap(tool_res, name)

    def call_tool_sync(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous wrapper for invoking an MCP tool."""
        return _run_coroutine_sync(self.call_tool(name, arguments))

    @staticmethod
    def _unwrap(tool_res: Any, name: str) -> Dict[str, Any]:
        """
        Normalize a FastMCP tool result into the platform's structured envelope.

        FastMCP may return structured content, a plain dict, or text content
        blocks depending on version and transport; all three are handled.
        """
        structured = getattr(tool_res, "structured_content", None)
        if structured:
            return structured

        if isinstance(tool_res, dict):
            return tool_res

        content = getattr(tool_res, "content", None)
        if content:
            first_block = content[0]
            text = getattr(first_block, "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except (ValueError, TypeError):
                    return {
                        "ok": not getattr(tool_res, "is_error", False),
                        "data": text,
                        "metadata": {"tool": name, "version": tool_version(name)},
                    }

        return {
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": f"Tool '{name}' returned an unrecognized result shape.",
                "retryable": False,
            },
            "metadata": {"tool": name, "version": tool_version(name)},
        }


# Global default client bound to the default FastMCP server.
GLOBAL_TOOL_CLIENT = ToolClient()
