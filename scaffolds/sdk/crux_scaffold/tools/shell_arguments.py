from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class ShellArguments(Arguments):
    """An agent-supplied Bash command to execute in the workspace."""

    background: bool = Field(False, description="Start a detached job and return its PID and log file.")
    command: str = Field(description="A bash command line, run from the workspace root.")
