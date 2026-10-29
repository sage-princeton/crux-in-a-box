from __future__ import annotations

from crux_scaffold.components import Options


class CommandOptions(Options):
    """The fixed command, model-facing description and timeout declared by a drop-in."""

    command: str
    description: str
    timeout_seconds: float = 600
