from __future__ import annotations

from pydantic import Field

from crux_scaffold.components import Options


class RestOptions(Options):
    """Per-call rest bounds and the shared tool instance's cumulative allowance."""

    min_seconds: float = Field(10, gt=0)
    max_seconds: float = Field(300, gt=0)
    total_seconds: float = Field(3600, ge=0)
