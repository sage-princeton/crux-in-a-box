from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.path_arguments import PathArguments


class WriteArguments(PathArguments):
    """A workspace path and the complete replacement text for that file."""

    content: str = Field(description="The complete new file content.")
