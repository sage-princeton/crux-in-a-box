"""Components specific to this CRUX. The scaffold imports this module because scaffold.toml lists it under
`extensions`; each class registers itself and becomes available to agents by its type name."""

import re
import tempfile
from pathlib import Path

from pydantic import Field

from crux_scaffold.errors import ToolError
from crux_scaffold.tools import TOOLS, Arguments, Tool
from crux_scaffold.workspace import RunContext


class PreviewArguments(Arguments):
    page: str = Field(description="'index' for the events listing, or an event slug.")


@TOOLS.register
class SitePreview(Tool):
    type_name = "site_preview"
    description = "Build the site and return one page's HTML, to check what visitors will see."
    Arguments = PreviewArguments

    async def invoke(self, ctx: RunContext, args: PreviewArguments) -> str:
        if not re.fullmatch(r"[a-z0-9-]+", args.page):
            raise ToolError("page must be 'index' or an event slug")
        with tempfile.TemporaryDirectory() as out:
            build = ctx.workspace.run_shell(f"python3 site/sitegen.py {out}", 60)
            page = Path(out) / f"{args.page}.html"
            if not page.is_file():
                raise ToolError(f"no page '{args.page}' was built\n{build}")
            return page.read_text()
