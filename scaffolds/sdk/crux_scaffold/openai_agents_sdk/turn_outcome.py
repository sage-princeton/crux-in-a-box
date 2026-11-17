from __future__ import annotations

from pydantic import BaseModel


class TurnOutcome(BaseModel):
    """The orchestrator's answer to one prompt; `completed` is false when it ran out of turns."""

    final_output: str
    completed: bool
