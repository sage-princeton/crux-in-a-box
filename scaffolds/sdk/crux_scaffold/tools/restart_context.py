from __future__ import annotations

from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.restart_arguments import RestartArguments
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext

# Starts every successful restart_context result; the runtime cuts the orchestrator's history only at such a result.
RESTARTED = "Context restarted."


@TOOLS.register
class RestartContext(Tool):
    """The orchestrator's deliberate context reset: its runtime drops the conversation before this call."""

    type_name = "restart_context"
    description = ("Clear your conversation history and continue from a handoff you write. Use it after archiving a "
                   "direction you are abandoning, so its conversation stops shaping your decisions. Your "
                   "instructions, the workspace (PLAN.md, LOG.md, archives) and your delegates' work are unaffected; "
                   "only your earlier conversation leaves your context.")
    Arguments = RestartArguments

    async def invoke(self, ctx: RunContext, args: RestartArguments) -> str:
        return (f"{RESTARTED} Your earlier conversation has left your context; the run's state keeps it. "
                f"Continue from your handoff:\n\n{args.handoff}")
