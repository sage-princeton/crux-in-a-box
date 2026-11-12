from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools import Arguments


class BriefArguments(Arguments):
    """Tool-call payload containing the complete brief for a fresh coding-agent invocation."""

    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")
