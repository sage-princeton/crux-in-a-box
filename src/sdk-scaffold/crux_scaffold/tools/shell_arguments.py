from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class ShellArguments(Arguments):
    """An agent-supplied Bash command to execute in the workspace."""

    command: str = Field(description="A bash command line, run from the workspace root.")
