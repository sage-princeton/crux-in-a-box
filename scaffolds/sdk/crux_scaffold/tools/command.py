from __future__ import annotations

from crux_scaffold.tools.arguments import Arguments
from crux_scaffold.tools.command_options import CommandOptions
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class Command(Tool):
    """A fixed command exposed as a no-argument tool, so a drop-in can declare checks without code."""

    type_name = "command"
    Options = CommandOptions

    @property
    def description(self) -> str:  # type: ignore[override]
        return self.options.description

    async def invoke(self, ctx: RunContext, args: Arguments) -> str:
        return ctx.workspace.run_shell(self.options.command, self.options.timeout_seconds)
