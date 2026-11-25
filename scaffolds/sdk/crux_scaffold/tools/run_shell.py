from __future__ import annotations

from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.shell_arguments import ShellArguments
from crux_scaffold.tools.shell_options import ShellOptions
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import JOBS_DIR, RunContext


@TOOLS.register
class RunShell(Tool):
    type_name = "run_shell"
    Arguments = ShellArguments
    Options = ShellOptions

    @property
    def description(self) -> str:  # type: ignore[override]
        return ("Run a bash command in the workspace root. Returns the exit code and combined output. A command "
                f"still running after {self.options.timeout_seconds:g}s is killed and returns its output so far; "
                f"set background=true to run longer jobs detached, logging to a file under {JOBS_DIR}/.")

    async def invoke(self, ctx: RunContext, args: ShellArguments) -> str:
        if args.background:
            return ctx.workspace.run_background(args.command)
        return await ctx.workspace.run_shell_async(args.command, self.options.timeout_seconds)
