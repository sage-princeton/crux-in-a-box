"""The agent-neutral model a TranscriptSource parses a transcript into.

A Thread is one transcript: the agent's conversation, or a subagent's. It holds
Turns: one user (or harness) request and everything the agent did for it. A Turn
holds the model responses (ModelStep), the tool calls (ToolCall) they made, and
Records: anything else the agent wrote during the turn, so nothing is dropped
just because a source does not model it. Each object knows when it is complete;
observations.py turns complete objects into Langfuse observations, and nothing
here knows about Langfuse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TurnEnd(Enum):
    """How a turn ended. Anything but COMPLETE is flagged as a warning in Langfuse."""

    COMPLETE = "complete"
    ABORTED = "aborted"
    SUPERSEDED = "superseded"
    STOPPED = "stopped"

    def warning(self, agent: str) -> str | None:
        match self:
            case TurnEnd.COMPLETE:
                return None
            case TurnEnd.ABORTED:
                return "Turn interrupted"
            case TurnEnd.SUPERSEDED:
                return "A later turn started before this one ended"
            case TurnEnd.STOPPED:
                return f"{agent} stopped before the turn ended"


@dataclass(frozen=True)
class Usage:
    """Token counts for one model response. The four counts are disjoint: `input` excludes
    `cached_input` and `output` excludes `reasoning_output`, because Langfuse prices each count on its own."""

    input: int | None = None
    output: int | None = None
    cached_input: int | None = None
    reasoning_output: int | None = None


@dataclass
class ToolCall:
    """One tool call. Complete once its output is recorded."""

    call_id: str
    name: str
    arguments: object
    started: float
    namespace: str | None = None
    finished: float | None = None
    output: object = None

    @property
    def complete(self) -> bool:
        return self.finished is not None


@dataclass
class ModelStep:
    """One model response: its text and the tool calls it asked for. Complete once usage is recorded."""

    index: int
    started: float
    ended: float
    text: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    complete: bool = False


@dataclass(frozen=True)
class Record:
    """Something recorded during a turn that is neither a model response nor a tool call.

    A source that understands it (a command the agent ran, say) fills in input and
    output; anything it cannot interpret is kept whole with `unrecognized` set, so
    a transcript format the source has not seen yet still reaches Langfuse.
    """

    name: str
    started: float
    ended: float
    input: object = None
    output: object = None
    failed: bool = False
    unrecognized: bool = False


@dataclass
class Turn:
    """One request and the agent's work on it. Ended once `end` is set."""

    turn_id: str
    started: float
    last_activity: float
    model: str | None = None
    prompt: str | None = None
    prompt_at: float | None = None
    steps: list[ModelStep] = field(default_factory=list)
    tool_calls: dict[str, ToolCall] = field(default_factory=dict)
    records: list[Record] = field(default_factory=list)
    ended: float | None = None
    end: TurnEnd | None = None
    output: str | None = None

    def finish(self, at: float, end: TurnEnd, output: str | None = None) -> None:
        self.ended, self.end, self.output = at, end, output

    def open_step(self, at: float) -> ModelStep:
        """The model response `at` belongs to: the current one, or a new one if the last is complete."""
        if not self.steps or self.steps[-1].complete:
            self.steps.append(ModelStep(len(self.steps), at, at))
        step = self.steps[-1]
        step.ended = at
        return step

    @property
    def unrecognized(self) -> list[str]:
        """Names of the records the source could not interpret, for spotting format drift."""
        return sorted({r.name for r in self.records if r.unrecognized})

    def current_step(self) -> ModelStep | None:
        """The model response still waiting for its usage, if any."""
        return self.steps[-1] if self.steps and not self.steps[-1].complete else None


@dataclass
class Thread:
    """One transcript. A subagent's thread names the thread that spawned it."""

    thread_id: str
    transcript: Path
    turns: list[Turn]
    parent_thread_id: str | None = None

    @property
    def is_subagent(self) -> bool:
        return self.parent_thread_id is not None
