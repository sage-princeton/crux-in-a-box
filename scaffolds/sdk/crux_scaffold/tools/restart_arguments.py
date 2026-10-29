from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class RestartArguments(Arguments):
    handoff: str = Field(min_length=1, max_length=20_000, description=(
        "Notes for your next self: the direction you are taking and why, what carries over (data, code, findings, "
        "reviewer points that still apply), what not to repeat, and where the archive of the old direction is."))
