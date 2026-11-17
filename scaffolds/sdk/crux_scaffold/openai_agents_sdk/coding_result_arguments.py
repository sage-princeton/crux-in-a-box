from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools import Arguments


class CodingResultArguments(Arguments):
    """Choose whether a background coding result tool reports status immediately or awaits completion."""

    wait: bool = Field(description="Whether to await completion; false returns immediately if still running.")
