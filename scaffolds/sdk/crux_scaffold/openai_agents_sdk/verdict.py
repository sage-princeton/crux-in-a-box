from __future__ import annotations

from pydantic import BaseModel


class Verdict(BaseModel):
    """A judge's structured answer."""

    passed: bool
    feedback: str
    next_prompt: str | None
