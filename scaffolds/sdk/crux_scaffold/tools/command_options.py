from __future__ import annotations

from crux_scaffold.components import Options


class CommandOptions(Options):
    command: str
    description: str
    timeout_seconds: float = 600
