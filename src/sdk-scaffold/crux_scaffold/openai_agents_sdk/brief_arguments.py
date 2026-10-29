from __future__ import annotations

from pydantic import Field

from crux_scaffold.tools import Arguments


class BriefArguments(Arguments):
    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")
