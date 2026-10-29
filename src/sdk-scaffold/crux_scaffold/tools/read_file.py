from __future__ import annotations

from crux_scaffold.tools.path_arguments import PathArguments
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class ReadFile(Tool):
    """Read a workspace text file through the caller's RunContext."""

    type_name = "read_file"
    description = "Read a UTF-8 text file in the workspace."
    Arguments = PathArguments

    async def invoke(self, ctx: RunContext, args: PathArguments) -> str:
        return ctx.workspace.read_file(args.path)
