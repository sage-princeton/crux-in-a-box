from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class RestArguments(Arguments):
    """The agent's requested wait duration and reason for waiting."""

    seconds: int = Field(description="How long you would like to wait.")
    reason: str = Field(description="What you are waiting for.")
