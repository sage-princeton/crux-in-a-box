from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class RestArguments(Arguments):
    seconds: int = Field(description="How long you would like to wait.")
    reason: str = Field(description="What you are waiting for.")
