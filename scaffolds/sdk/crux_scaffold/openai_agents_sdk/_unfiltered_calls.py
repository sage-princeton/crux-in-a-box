from typing import Any

from agents.mcp import MCPServerStdio
from mcp.types import CallToolResult


class _UnfilteredCalls:
    """Steering's own reads and posts. Thread scans need every page and cursor; Steering applies `after_ts` itself."""

    def __init__(self, server: MCPServerStdio) -> None:
        self.server = server

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        return await MCPServerStdio.call_tool(self.server, tool_name, arguments)
