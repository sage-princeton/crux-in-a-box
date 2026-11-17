from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.path_arguments import PathArguments


class PdfArguments(PathArguments):
    """A workspace PDF path and the one-based page number to inspect."""

    page: int = Field(ge=1, description="The PDF page to extract and render, starting at 1.")
