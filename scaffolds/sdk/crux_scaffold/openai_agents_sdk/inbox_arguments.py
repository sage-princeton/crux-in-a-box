from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools import Arguments


class InboxArguments(Arguments):
    wait_seconds: float = Field(0, ge=0, allow_inf_nan=False)
