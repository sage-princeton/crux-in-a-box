from __future__ import annotations

from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.shell_arguments import ShellArguments
from crux_scaffold.tools.shell_options import ShellOptions
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class RunShell(Tool):
    """Execute an agent-supplied workspace command with cancellable process cleanup."""

    type_name = "run_shell"
    description = "Run a bash command in the workspace root. Returns the exit code and combined output."
    Arguments = ShellArguments
    Options = ShellOptions

    async def invoke(self, ctx: RunContext, args: ShellArguments) -> str:
        return await ctx.workspace.run_shell_async(args.command, self.options.timeout_seconds)
