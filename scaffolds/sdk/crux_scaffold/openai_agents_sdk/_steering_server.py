from typing import Any

from agents.mcp import MCPServerStdio
from mcp.types import CallToolResult, TextContent

from crux_scaffold.steering import MESSAGE_TOOLS, WITHHELD, within_run


class _SteeringServer(MCPServerStdio):
    """The steering MCP server: agents see only Slack messages posted after the run start (`after_ts`)."""

    def __init__(self, *, after_ts: str, **options: Any) -> None:
        super().__init__(**options)
        self.after_ts = after_ts

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None,
                        meta: dict[str, Any] | None = None) -> CallToolResult:
        result = await super().call_tool(tool_name, arguments, meta)
        if result.is_error:
            return result
        text = "\n".join(block.text for block in result.content if block.type == "text")
        try:
            visible = within_run(tool_name, text, self.after_ts)
        except ValueError:
            return CallToolResult(content=[TextContent(type="text", text=WITHHELD)], is_error=True)
        if visible is None and tool_name not in MESSAGE_TOOLS:
            return result
        # Message listings reach agents as filtered text only, never as other content blocks.
        return CallToolResult(content=[TextContent(type="text", text=visible or "")])
