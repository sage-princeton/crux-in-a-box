from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class ShellArguments(Arguments):
    command: str = Field(description="A bash command line, run from the workspace root.")
    background: bool = Field(False, description=(
        "Start the command as a detached job and return at once with its PID and log file instead of waiting. "
        "Use it for anything that may outlast the timeout, such as training or a benchmark."))
