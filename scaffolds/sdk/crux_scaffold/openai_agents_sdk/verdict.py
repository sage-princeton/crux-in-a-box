from __future__ import annotations

from pydantic import BaseModel


class Verdict(BaseModel):
    """An isolated judge's structured result, consumed by a phase gate.

    `passed` determines whether the gate succeeds; `feedback` and optional `next_prompt` guide the next iteration.
    """

    passed: bool
    feedback: str
    next_prompt: str | None
