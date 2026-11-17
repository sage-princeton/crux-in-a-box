from __future__ import annotations

from crux_scaffold.tools.arguments import Arguments
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class BudgetStatus(Tool):
    type_name = "budget_status"
    description = "Refresh the run's token, dollar, time and external resource usage against its budgets."

    async def invoke(self, ctx: RunContext, args: Arguments) -> str:
        return await ctx.budget_status(force=True)
