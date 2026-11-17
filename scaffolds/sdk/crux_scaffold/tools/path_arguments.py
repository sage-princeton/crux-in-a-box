from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class PathArguments(Arguments):
    path: str = Field(description="Path relative to the workspace root; '.' is the root.")
