"""Codex rollouts (~/.codex/sessions/**/rollout-*.jsonl) as Threads.

Two layers, so the rollout format and the turn logic can change independently:

  decode(line)    one JSON line -> one typed event, or None for a line tracing
                  ignores. Lines are matched by shape, so keys Codex adds later
                  do not break decoding.
  RolloutReader   applies the events in file order to build the turns.

The line shapes are those codex 0.153.4 (installed by codex-acp 1.10.0) writes;
tests/codex-observability/fixtures reproduces them.

How a rollout maps onto the model:
  task_started                   starts a turn; a turn still open is SUPERSEDED
  turn_context                   the turn's model; opens the window for its prompt
  user message                   the turn's prompt, if no model output came yet
  reasoning / assistant message  part of the current model response
  function_call / custom_tool_call / web_search
                                 a tool call the current model response made; code
                                 mode makes every call a custom_tool_call to `exec`
  function_call_output / custom_tool_call_output
                                 completes the tool call with the same call_id
  token_usage_record             the model response is complete (with its usage)
  token_count                    also completes it, for rollouts without the record
  task_complete / turn_aborted   ends the turn
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from live_trace.model import Thread, ToolCall, Turn, TurnEnd, Usage
from live_trace.sources import TranscriptSource


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
class ToolRequested:
    call_id: str
    name: str
    arguments: object
    namespace: str | None


@dataclass(frozen=True)
class WebSearched:
    call_id: str
    query: str | None


@dataclass(frozen=True)
class ToolReturned:
    call_id: str
    output: object


@dataclass(frozen=True)
class ResponseCompleted:
    """token_usage_record or token_count: the model response so far is complete."""

    usage: Usage | None


Event = (SessionStarted | TurnStarted | TurnSettings | TurnCompleted | TurnAborted | UserMessage
         | AssistantMessage | Reasoning | ToolRequested | WebSearched | ToolReturned | ResponseCompleted)


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
        case {"type": "token_usage_record", "payload": {"usage": usage}}:
            return ResponseCompleted(_usage(usage))
        case {"type": "turn_context", "payload": {} as context}:
            return TurnSettings(context.get("model"))
        case {"type": "response_item", "payload": {} as item}:
            return _decode_item(item)
        case _:
            return None


def _decode_item(item: dict) -> Event | None:
    match item:
        case {"type": "message", "role": "user", "content": content}:
            return UserMessage(_text(content))
        case {"type": "message", "role": "assistant", "content": content}:
            return AssistantMessage(_text(content))
        case {"type": "reasoning"}:
            return Reasoning()
        case {"type": "function_call_output" | "custom_tool_call_output", "call_id": str(call_id)}:
            return ToolReturned(call_id, item.get("output"))
        case {"type": "web_search_call"}:
            return WebSearched(item.get("id") or "", (item.get("action") or {}).get("query"))
        case {"type": "function_call" | "custom_tool_call" | "local_shell_call" as kind}:
            return ToolRequested(item.get("call_id") or item.get("id") or "", item.get("name") or kind,
                                 _arguments(item.get("arguments", item.get("input"))), item.get("namespace"))
        case _:
            return None


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


def _arguments(raw: object) -> object:
    """Tool arguments arrive as a JSON string; keep the raw value if it isn't JSON."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


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
                    call.finished, call.output = at, output
            case Reasoning():
                self._model_output(turn, at)
            case AssistantMessage(text=text):
                self._model_output(turn, at).text.append(text)
            case WebSearched(call_id=call_id, query=query):
                self._add_call(turn, at, ToolCall(call_id or f"ws-{len(turn.tool_calls)}", "web_search",
                                                  {"query": query}, at, finished=at))
            case ToolRequested(call_id=call_id, name=name, arguments=arguments, namespace=namespace):
                self._add_call(turn, at, ToolCall(call_id or f"call-{len(turn.tool_calls)}", name, arguments, at,
                                                  namespace))

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
