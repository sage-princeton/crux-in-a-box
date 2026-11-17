from __future__ import annotations

from crux_scaffold.components import Options


class ShellOptions(Options):
    """The timeout policy for agent-supplied shell commands."""

    timeout_seconds: float = 600
