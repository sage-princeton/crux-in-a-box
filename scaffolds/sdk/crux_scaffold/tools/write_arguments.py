from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.path_arguments import PathArguments


class WriteArguments(PathArguments):
    content: str = Field(description="The complete new file content.")
