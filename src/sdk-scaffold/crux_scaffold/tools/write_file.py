from __future__ import annotations

from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.tools.write_arguments import WriteArguments
from crux_scaffold.workspace import RunContext


@TOOLS.register
class WriteFile(Tool):
    """Create or replace a workspace text file with the supplied contents."""

    type_name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the workspace, creating parent directories."
    Arguments = WriteArguments

    async def invoke(self, ctx: RunContext, args: WriteArguments) -> str:
        return ctx.workspace.write_file(args.path, args.content)
