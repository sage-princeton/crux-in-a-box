from pydantic import Field

from crux_scaffold.tools.arguments import Arguments


class InboxArguments(Arguments):
    """Arguments for the delegate inbox tool."""

    wait_seconds: float = Field(0, ge=0, allow_inf_nan=False)
