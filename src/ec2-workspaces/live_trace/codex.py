"""Codex rollouts (~/.codex/sessions/**/rollout-*.jsonl) as Threads.

Two layers, so the rollout format and the turn logic can change independently:

  decode(line)    one JSON line -> one typed event, or None for known noise.
  RolloutReader   applies the events in file order to build the turns.

Strict where a wrong guess would corrupt the trace, tolerant everywhere else:

  Strict (exact shapes): what gives a trace its structure, namely session_meta,
  task_started / task_complete / turn_aborted, turn_context, the prompt, model
  messages and reasoning, and token usage. If these change, the exporter sends
  nothing for the turn and provisioning's probe fails loudly.

  Tolerant (by convention): tool calls. Any response item whose type ends in
  `_call` and has a call_id opens a tool call; any whose type ends in `_output`
  completes the call with that call_id. That covers function_call,
  custom_tool_call (code mode's `exec`), local_shell_call, tool_search_call and
  whatever comes next. A `_call` item without a call_id (web_search_call) is
  complete when written.

  Kept, not interpreted: item_completed events are recorded with their own
  timing; under code mode they are the only record of the shell commands `exec`
  ran. Any other line inside a turn is kept as an unrecognized record, unless it
  is on the short list of known noise below. Nothing in a turn is dropped
  because this module has not seen its shape before.

The line shapes are those codex 0.153.4 (installed by codex-acp 1.10.0) writes,
checked against ae240-test's rollouts; tests/codex-observability/fixtures
reproduces them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from live_trace.model import Record, Thread, ToolCall, Turn, TurnEnd, Usage
from live_trace.sources import TranscriptSource

#: Lines that carry no turn activity: environment snapshots, compacted history,
#: settings changes. Kept out so they don't flood the trace.
KNOWN_NOISE = {"world_state", "compacted", "event_msg/thread_settings_applied"}
#: item_completed copies of response items the reader already models.
DUPLICATE_ITEMS = {"UserMessage", "AgentMessage", "Reasoning", "WebSearch"}
#: item fields that hold an item's result rather than its request.
RESULT_FIELDS = ("aggregated_output", "formatted_output", "output", "result")
#: Fields that only repeat the result or identify the item.
DROPPED_FIELDS = {"id", "type", "stdout", "stderr", *RESULT_FIELDS}
#: Cap on a record's input or output, so an unexpected payload cannot swamp Langfuse.
CLIP = 16_000


@dataclass(frozen=True)
class SessionStarted:
    thread_id: str
    parent_thread_id: str | None


@dataclass(frozen=True)
class TurnStarted:
    turn_id: str


@dataclass(frozen=True)
class TurnSettings:
    model: str | None


@dataclass(frozen=True)
class TurnCompleted:
    output: str | None


@dataclass(frozen=True)
class TurnAborted:
    pass


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str


@dataclass(frozen=True)
class Reasoning:
    pass


@dataclass(frozen=True)
class ResponseCompleted:
    """token_usage_record or token_count: the model response so far is complete."""

    usage: Usage | None


@dataclass(frozen=True)
class ToolRequested:
    call_id: str
    name: str
    arguments: object
    namespace: str | None


@dataclass(frozen=True)
class ToolReturned:
    call_id: str
    output: object


@dataclass(frozen=True)
class InstantCall:
    """A tool call complete when written, with no separate output (web_search_call)."""

    call_id: str
    name: str
    arguments: object


@dataclass(frozen=True)
class ItemCompleted:
    name: str
    started: float | None
    ended: float | None
    input: object
    output: object
    failed: bool


@dataclass(frozen=True)
class Unrecognized:
    kind: str
    payload: object


Event = (SessionStarted | TurnStarted | TurnSettings | TurnCompleted | TurnAborted | UserMessage
         | AssistantMessage | Reasoning | ResponseCompleted | ToolRequested | ToolReturned | InstantCall
         | ItemCompleted | Unrecognized)


def line_kind(line: dict) -> str:
    """`type/payload.type`, or just `type` for lines whose payload has no type, as rollout_shape.py names them."""
    match line.get("payload"):
        case {"type": str(payload_type)}:
            return f"{line.get('type')}/{payload_type}"
        case _:
            return str(line.get("type"))


def decode(line: dict) -> Event | None:
    match line:
        case {"type": "session_meta", "payload": {"id": str(thread_id)} as meta}:
            return SessionStarted(thread_id, _parent_thread_id(meta))
        case {"type": "event_msg", "payload": {"type": "task_started", "turn_id": str(turn_id)}}:
            return TurnStarted(turn_id)
        case {"type": "event_msg", "payload": {"type": "task_complete"} as done}:
            return TurnCompleted(done.get("last_agent_message"))
        case {"type": "event_msg", "payload": {"type": "turn_aborted"}}:
            return TurnAborted()
        case {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": usage}}}:
            return ResponseCompleted(_usage(usage))
        case {"type": "event_msg", "payload": {"type": "token_count"}}:
            return ResponseCompleted(None)
        case {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": str(kind)} as item} as event}:
            return None if kind in DUPLICATE_ITEMS else _item_completed(kind, item, event)
        case {"type": "token_usage_record", "payload": {"usage": usage}}:
            return ResponseCompleted(_usage(usage))
        case {"type": "turn_context", "payload": {} as context}:
            return TurnSettings(context.get("model"))
        case {"type": "response_item", "payload": {} as item}:
            return _decode_item(item)
        case _ if line_kind(line) in KNOWN_NOISE:
            return None
        case _:
            return Unrecognized(line_kind(line), line.get("payload"))


def _decode_item(item: dict) -> Event | None:
    match item:
        case {"type": "message", "role": "user", "content": content}:
            return UserMessage(_text(content))
        case {"type": "message", "role": "assistant", "content": content}:
            return AssistantMessage(_text(content))
        case {"type": "message"}:
            return None
        case {"type": "reasoning"}:
            return Reasoning()
        case {"type": str(kind), "call_id": str(call_id)} if kind.endswith("_output"):
            return ToolReturned(call_id, _text_or_raw(item.get("output")))
        case {"type": str(kind), "call_id": str(call_id)} if kind.endswith("_call"):
            arguments = next((item[k] for k in ("arguments", "input", "action") if k in item), None)
            return ToolRequested(call_id, item.get("name") or kind, _arguments(arguments), item.get("namespace"))
        case {"type": "web_search_call"}:
            return InstantCall(item.get("id") or "", "web_search", {"query": (item.get("action") or {}).get("query")})
        case {"type": str(kind)} if kind.endswith("_call"):
            return InstantCall(item.get("id") or "", kind, _without(item, DROPPED_FIELDS))
        case _:
            return Unrecognized(f"response_item/{item.get('type')}", item)


def _item_completed(kind: str, item: dict, event: dict) -> ItemCompleted:
    exit_code = item.get("exit_code")
    return ItemCompleted(
        name=kind,
        started=_seconds(event.get("started_at_ms")),
        ended=_seconds(event.get("completed_at_ms")),
        input=_without(item, DROPPED_FIELDS),
        output=_text_or_raw(next((item[k] for k in RESULT_FIELDS if item.get(k) is not None), None)),
        failed=item.get("status") in {"failed", "declined"} or (isinstance(exit_code, int) and exit_code != 0),
    )


def _parent_thread_id(meta: dict) -> str | None:
    match meta:
        case {"parent_thread_id": str(parent)}:
            return parent
        case {"source": {"subagent": {"thread_spawn": {"parent_thread_id": str(parent)}}}}:
            return parent
        case _:
            return None


def _usage(raw: object) -> Usage | None:
    match raw:
        case dict():
            return Usage(input=raw.get("input_tokens"), output=raw.get("output_tokens"),
                         cached_input=raw.get("cached_input_tokens"),
                         reasoning_output=raw.get("reasoning_output_tokens"))
        case _:
            return None


def _text(content: object) -> str:
    match content:
        case str():
            return content
        case list():
            return "\n".join(part["text"] for part in content if isinstance(part, dict) and part.get("text"))
        case _:
            return ""


def _text_or_raw(value: object) -> object:
    """A tool result as text when it is text or a list of text parts; otherwise unchanged."""
    match value:
        case str():
            return value
        case [{"text": str()}, *_]:
            return _text(value)
        case _:
            return value


def _arguments(raw: object) -> object:
    """Tool arguments often arrive as a JSON string; keep the raw value if it isn't JSON."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _without(item: dict, fields: set[str]) -> dict:
    return {k: v for k, v in item.items() if k not in fields and k != "internal_chat_message_metadata_passthrough"}


def _seconds(ms: object) -> float | None:
    return ms / 1000 if isinstance(ms, (int, float)) else None


def clip(value: object) -> object:
    """`value`, or its JSON cut to CLIP characters when it is larger."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) <= CLIP:
        return value
    return f"{text[:CLIP]}… [{len(text) - CLIP} more characters]"


class RolloutReader:
    """Builds a Thread from one rollout's events, applied in the order Codex wrote them."""

    def __init__(self, transcript: Path):
        self.transcript = transcript
        self.session: SessionStarted | None = None
        self.turns: list[Turn] = []
        self.turn: Turn | None = None
        self.awaiting_prompt = False

    def apply(self, at: float, event: Event | None) -> None:
        """`event` is None for a line with a timestamp that tracing otherwise ignores."""
        match event:
            case SessionStarted():
                self.session = event
            case TurnStarted(turn_id=turn_id):
                self._start_turn(at, turn_id)
            case _ if self.turn is not None and self.turn.end is None:
                self.turn.last_activity = at
                if event is not None:
                    self._within_turn(self.turn, at, event)

    def _start_turn(self, at: float, turn_id: str) -> None:
        if self.turn is not None and self.turn.end is None:
            self.turn.finish(self.turn.last_activity, TurnEnd.SUPERSEDED)
        self.turn = Turn(turn_id, at, at)
        self.turns.append(self.turn)
        self.awaiting_prompt = False

    def _within_turn(self, turn: Turn, at: float, event: Event) -> None:
        match event:
            case TurnSettings(model=model):
                turn.model = turn.model or model
                self.awaiting_prompt = not turn.steps
            case UserMessage(text=text) if self.awaiting_prompt:
                turn.prompt, turn.prompt_at = text, at
            case TurnCompleted(output=output):
                turn.finish(at, TurnEnd.COMPLETE, output)
            case TurnAborted():
                turn.finish(at, TurnEnd.ABORTED)
            case ResponseCompleted(usage=usage):
                if step := turn.current_step():
                    step.usage, step.complete = step.usage or usage, True
            case ToolReturned(call_id=call_id, output=output):
                if call := turn.tool_calls.get(call_id):
                    call.finished, call.output = at, clip(output)
            case Reasoning():
                self._model_output(turn, at)
            case AssistantMessage(text=text):
                self._model_output(turn, at).text.append(text)
            case InstantCall(call_id=call_id, name=name, arguments=arguments):
                self._add_call(turn, at, ToolCall(call_id or f"call-{len(turn.tool_calls)}", name, clip(arguments), at,
                                                  finished=at))
            case ToolRequested(call_id=call_id, name=name, arguments=arguments, namespace=namespace):
                self._add_call(turn, at, ToolCall(call_id or f"call-{len(turn.tool_calls)}", name, clip(arguments), at,
                                                  namespace))
            case ItemCompleted(name=name, started=started, ended=ended, input=item_input, output=output, failed=failed):
                turn.records.append(Record(name, started or at, ended or at, clip(item_input), clip(output), failed))
            case Unrecognized(kind=kind, payload=payload):
                turn.records.append(Record(kind, at, at, clip(payload), unrecognized=True))

    def _model_output(self, turn: Turn, at: float):
        self.awaiting_prompt = False
        return turn.open_step(at)

    def _add_call(self, turn: Turn, at: float, call: ToolCall) -> None:
        self._model_output(turn, at).calls.append(call)
        turn.tool_calls[call.call_id] = call

    def thread(self) -> Thread | None:
        if self.session is None:
            return None
        return Thread(self.session.thread_id, self.transcript, self.turns, self.session.parent_thread_id)


class CodexRollouts(TranscriptSource):
    agent = "Codex"
    transcript_glob = "rollout-*.jsonl"

    def __init__(self, home: Path | None = None):
        self._home = home or Path.home() / ".codex"

    @property
    def home(self) -> Path:
        return self._home

    @property
    def transcripts_dir(self) -> Path:
        return self._home / "sessions"

    def parse(self, transcript: Path) -> Thread | None:
        reader = RolloutReader(transcript)
        with transcript.open("rb") as f:
            for raw in f:
                try:
                    line = json.loads(raw)
                    at = datetime.fromisoformat(line["timestamp"].replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                reader.apply(at, decode(line))
        return reader.thread()
