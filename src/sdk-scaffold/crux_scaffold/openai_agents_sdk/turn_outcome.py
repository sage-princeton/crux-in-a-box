from __future__ import annotations

from pydantic import BaseModel


class TurnOutcome(BaseModel):
    """Return one orchestrator turn's output and completion status to the outer loop.

    `completed` is false when the SDK turn limit is reached. `final_output` also includes any background coding
    results that the orchestrator left uncollected before the runtime returned.
    """

    final_output: str
    completed: bool
