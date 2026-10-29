from __future__ import annotations

from crux_scaffold.errors import ToolError
from crux_scaffold.steering import Escalation
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class Escalate(Tool):
    """Post an operator action request through the configured steering outbox."""

    type_name = "escalate"
    description = ("Ask the operator to fix a broken resource or take another action. Include the failure, "
                   "diagnosis, requested action and independent work you will continue. Reuse request_id "
                   "for retries of the same request. Replies arrive through operator steering.")
    Arguments = Escalation

    async def invoke(self, ctx: RunContext, args: Escalation) -> str:
        if ctx.steering is None:
            raise ToolError("escalate requires runtime.steering")
        return await ctx.steering.escalate(args)
