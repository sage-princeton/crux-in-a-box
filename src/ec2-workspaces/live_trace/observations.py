"""What a Turn is exported as: the Observation schema and its OTLP serialization.

A turn becomes, in the order each piece is complete:

  "<Agent> Turn started"  event       the prompt, as soon as it is written
  "LLM"                   generation  one per model response: text, tool calls, usage
  <tool name>             tool        one per tool call, once it has an output
  "<Agent> Turn"          agent       the root, with input and output, once the turn ends

Subagent turns use "<Agent> Subagent Turn" and "LLM Subagent". Every observation
but a main-thread root is a child of its turn's root; a subagent's root is a child
of the root of the turn that spawned it. Langfuse reads the trace's name, session,
user, environment, tags and metadata from each span, so every span carries them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from live_trace.model import Thread, ToolCall, Turn, TurnEnd, Usage


def hexid(*parts: object, n: int = 16) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:n]


class TurnIds:
    """Observation ids for one turn, a function of its thread and turn so every pass agrees on them."""

    def __init__(self, thread_id: str, turn_id: str):
        self.thread_id, self.turn_id = thread_id, turn_id

    def trace(self) -> str:
        return hexid(self.thread_id, self.turn_id, "trace", n=32)

    def root(self) -> str:
        return hexid(self.thread_id, self.turn_id, "root")

    def started(self) -> str:
        return hexid(self.thread_id, self.turn_id, "started")

    def step(self, index: int) -> str:
        return hexid(self.thread_id, self.turn_id, "step", index)

    def tool(self, call_id: str) -> str:
        return hexid(self.thread_id, self.turn_id, "tool", call_id)


@dataclass(frozen=True)
class TraceContext:
    """The trace-level attributes every span carries."""

    environment: str
    session_id: str
    name: str
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def attributes(self) -> dict[str, object]:
        attrs: dict[str, object] = {"langfuse.environment": self.environment, "langfuse.session.id": self.session_id,
                                    "langfuse.trace.name": self.name, "langfuse.trace.tags": list(self.tags)}
        if self.user_id:
            attrs["langfuse.user.id"] = self.user_id
        attrs.update({f"langfuse.trace.metadata.{k}": v for k, v in self.metadata})
        return attrs


@dataclass(frozen=True)
class Placement:
    """Where a turn's observations go: its trace, and the span its root hangs under (None for a main turn)."""

    trace_id: str
    parent_span_id: str | None


class Kind(Enum):
    EVENT = "event"
    GENERATION = "generation"
    TOOL = "tool"
    AGENT = "agent"


@dataclass(frozen=True)
class Observation:
    """One Langfuse observation, sent once as one OTLP span."""

    span_id: str
    parent_id: str | None
    name: str
    kind: Kind
    start: float
    end: float
    input: object = None
    output: object = None
    model: str | None = None
    usage: Usage | None = None
    level: str | None = None
    status_message: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def attributes(self) -> dict[str, object]:
        attrs: dict[str, object] = {"langfuse.observation.type": self.kind.value}
        optional = {"langfuse.observation.input": self.input, "langfuse.observation.output": self.output,
                    "langfuse.observation.model.name": self.model, "langfuse.observation.level": self.level,
                    "langfuse.observation.status_message": self.status_message}
        attrs.update({k: v for k, v in optional.items() if v is not None})
        if self.usage is not None:
            counts = {"input": self.usage.input, "output": self.usage.output,
                      "input_cached_tokens": self.usage.cached_input,
                      "output_reasoning_tokens": self.usage.reasoning_output}
            attrs["langfuse.observation.usage_details"] = {k: v for k, v in counts.items() if isinstance(v, int)}
        attrs.update({f"langfuse.observation.metadata.{k}": v for k, v in self.metadata.items()})
        return attrs

    def to_otlp(self, trace_id: str, trace: TraceContext) -> dict:
        span = {"traceId": trace_id, "spanId": self.span_id, "name": self.name, "kind": 1, "status": {},
                "startTimeUnixNano": str(int(self.start * 1e9)), "endTimeUnixNano": str(int(self.end * 1e9)),
                "attributes": [_otlp_attribute(k, v) for k, v in {**trace.attributes(), **self.attributes()}.items()]}
        if self.parent_id:
            span["parentSpanId"] = self.parent_id
        return span


def _otlp_attribute(key: str, value: object) -> dict:
    match value:
        case list() if all(isinstance(v, str) for v in value):
            return {"key": key, "value": {"arrayValue": {"values": [{"stringValue": v} for v in value]}}}
        case str():
            return {"key": key, "value": {"stringValue": value}}
        case _:
            return {"key": key, "value": {"stringValue": json.dumps(value)}}


def observations_for(agent: str, thread: Thread, turn: Turn, placement: Placement,
                     finalize: bool = False) -> list[Observation]:
    """Every observation of `turn` that is complete now, in a stable order.

    finalize ends a turn that is still open, as STOPPED: its agent has exited, so
    nothing more will be written. Ending a turn also completes its open model
    response and any tool call still waiting for output (flagged as a warning).
    """
    ids = TurnIds(thread.thread_id, turn.turn_id)
    prefix = agent.lower()
    names = _Names(agent, thread.is_subagent)
    end, ended = turn.end, turn.ended
    if end is None and finalize:
        end, ended = TurnEnd.STOPPED, turn.last_activity

    def meta(**extra: str) -> dict[str, str]:
        return {f"{prefix}.thread_id": thread.thread_id, f"{prefix}.turn_id": turn.turn_id,
                **{f"{prefix}.{k}": v for k, v in extra.items()}}

    out: list[Observation] = []
    if turn.prompt is not None:
        out.append(Observation(ids.started(), ids.root(), names.started, Kind.EVENT, turn.prompt_at, turn.prompt_at,
                               input=turn.prompt, metadata=meta()))
    for step in turn.steps:
        if not step.complete and end is None:
            continue
        output: dict[str, object] = {"role": "assistant", "content": "\n".join(step.text)}
        if step.calls:
            output["tool_calls"] = [{"name": c.name, "arguments": c.arguments} for c in step.calls]
        out.append(Observation(ids.step(step.index), ids.root(), names.generation, Kind.GENERATION, step.started,
                               step.ended, output=output, model=turn.model, usage=step.usage,
                               metadata=meta(step_index=str(step.index))))
    for call in turn.tool_calls.values():
        if not call.complete and end is None:
            continue
        out.append(_tool(call, ids, ended, meta(call_id=call.call_id, **_namespace(call))))
    if end is not None:
        out.append(Observation(ids.root(), placement.parent_span_id, names.turn, Kind.AGENT, turn.started, ended,
                               input=turn.prompt or "", output=turn.output,
                               level="WARNING" if end.warning(agent) else None, status_message=end.warning(agent),
                               metadata=meta(ended_by=end.value, tool_call_count=str(len(turn.tool_calls)))))
    return out


def _tool(call: ToolCall, ids: TurnIds, turn_ended: float | None, metadata: dict[str, str]) -> Observation:
    output = call.output if call.output is None or isinstance(call.output, str) else json.dumps(call.output)
    if call.complete:
        return Observation(ids.tool(call.call_id), ids.root(), call.name, Kind.TOOL, call.started, call.finished,
                           input=call.arguments, output=output, metadata=metadata)
    return Observation(ids.tool(call.call_id), ids.root(), call.name, Kind.TOOL, call.started, turn_ended,
                       input=call.arguments, level="WARNING",
                       status_message="The turn ended before this call wrote an output", metadata=metadata)


def _namespace(call: ToolCall) -> dict[str, str]:
    return {"namespace": call.namespace} if call.namespace else {}


@dataclass(frozen=True)
class _Names:
    agent: str
    subagent: bool

    @property
    def turn(self) -> str:
        return f"{self.agent} Subagent Turn" if self.subagent else f"{self.agent} Turn"

    @property
    def started(self) -> str:
        return f"{self.turn} started"

    @property
    def generation(self) -> str:
        return "LLM Subagent" if self.subagent else "LLM"
