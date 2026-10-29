from __future__ import annotations

from crux_scaffold.tools.path_arguments import PathArguments
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.workspace import RunContext


@TOOLS.register
class ListFiles(Tool):
    """List workspace files beneath a requested directory."""

    type_name = "list_files"
    description = "List files under a workspace directory, recursively."
    Arguments = PathArguments

    async def invoke(self, ctx: RunContext, args: PathArguments) -> str:
        return ctx.workspace.list_files(args.path)
