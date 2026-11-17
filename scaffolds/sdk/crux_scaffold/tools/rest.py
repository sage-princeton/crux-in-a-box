from __future__ import annotations

from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.rest_arguments import RestArguments
from crux_scaffold.tools.rest_options import RestOptions
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class Rest(Tool):
    """Waiting is a scaffold decision: the drop-in bounds each rest and the total, the agent only asks."""

    type_name = "rest"
    description = ("Pause before checking for something external again, such as a reply to a question you asked. "
                   "The scaffold bounds each rest and the total time you may rest.")
    Arguments = RestArguments
    Options = RestOptions

    def __init__(self, name: str, options: RestOptions) -> None:
        super().__init__(name, options)
        self.used_seconds = 0.0

    async def invoke(self, ctx: RunContext, args: RestArguments) -> str:
        policy = self.options
        remaining = policy.total_seconds - self.used_seconds
        if remaining <= 0:
            return (f"Rest budget exhausted ({self.used_seconds:g}s used of {policy.total_seconds:g}s). "
                    "Do not wait any longer: continue with what you have and record any assumption you make.")
        granted = min(max(args.seconds, policy.min_seconds), policy.max_seconds, remaining)
        waited = await ctx.wait(granted)
        self.used_seconds += waited
        wake = " Woke early for operator steering." if waited < granted else ""
        return (f"Rested {waited:g}s ({args.reason}).{wake} "
                f"Rest budget left: {policy.total_seconds - self.used_seconds:g}s.")
