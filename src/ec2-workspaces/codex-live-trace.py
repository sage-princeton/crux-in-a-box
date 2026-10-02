#!/usr/bin/env python3
"""Stream Codex turns to Langfuse while they run, one complete observation at a time.

Langfuse v4 treats observations as immutable: a re-sent span id is a duplicate,
not an update. So each pass rebuilds every turn from the rollout files and sends
only the observations that are complete and not yet sent:

  "Codex Turn started"  event with the turn's input, as soon as the prompt is read
  "LLM"                 generation per model response, once the response is complete
  <tool name>           tool span per call, once its output is written
  "Codex Turn"          the turn's root (input, output), once the turn has ended

Every id is derived from the thread, turn and call, so children name their root
before it is sent and a re-run never sends a new copy. A subagent's turns join the
parent turn that was running when they started. Sent ids are recorded per rollout
after Langfuse accepts them; a failed export is retried on the next pass.

A turn ends with task_complete or turn_aborted, or when a later turn starts in the
same thread. --finalize also ends the last turn of every rollout no live process
holds open, for the gateway unit to run once codex has exited.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BATCH = 200
MODEL_OUTPUT = {"reasoning", "message", "function_call", "custom_tool_call", "web_search_call", "local_shell_call"}


def hexid(*parts: object, n: int = 16) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:n]


def epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
    return ""


@dataclass
class Tool:
    call_id: str
    name: str
    args: object
    start: float
    namespace: str | None = None
    end: float | None = None
    output: object = None


@dataclass
class Step:
    index: int
    start: float
    end: float
    texts: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    usage: dict | None = None
    closed: bool = False


@dataclass
class Turn:
    turn_id: str
    start: float
    last: float
    model: str | None = None
    prompt: str | None = None
    prompt_at: float | None = None
    steps: list[Step] = field(default_factory=list)
    tools: dict[str, Tool] = field(default_factory=dict)
    end: float | None = None
    output: str | None = None
    ended_by: str | None = None
    seen_model_output: bool = False
    context_seen: bool = False


@dataclass
class Rollout:
    path: Path
    thread_id: str
    parent_thread_id: str | None
    turns: list[Turn]


def parse(path: Path) -> Rollout | None:
    meta, turns, turn = {}, [], None
    with path.open("rb") as f:
        for raw in f:
            try:
                line = json.loads(raw)
                at = epoch(line["timestamp"])
            except (ValueError, KeyError, TypeError):
                continue
            kind, p = line.get("type"), line.get("payload") or {}
            if kind == "session_meta":
                meta = p
                continue
            if kind == "event_msg" and p.get("type") == "task_started" and p.get("turn_id"):
                if turn and turn.end is None:
                    turn.end, turn.ended_by = turn.last, "superseded"
                turn = Turn(p["turn_id"], at, at)
                turns.append(turn)
                continue
            if turn is None or turn.end is not None:
                continue
            turn.last = at
            if kind == "turn_context":
                turn.context_seen = True
                turn.model = turn.model or p.get("model")
            elif kind == "event_msg":
                step = turn.steps[-1] if turn.steps else None
                if p.get("type") == "token_count" and step and not step.closed:
                    info = p.get("info") or {}
                    step.usage = step.usage or info.get("last_token_usage")
                    step.closed = True
                elif p.get("type") == "task_complete":
                    turn.end, turn.ended_by = at, "complete"
                    turn.output = p.get("last_agent_message")
                elif p.get("type") == "turn_aborted":
                    turn.end, turn.ended_by = at, "aborted"
            elif kind == "token_usage_record":
                step = turn.steps[-1] if turn.steps else None
                if step and not step.closed:
                    step.usage, step.closed = p.get("usage"), True
            elif kind == "response_item":
                item = p.get("type")
                if item == "message" and p.get("role") == "user":
                    if turn.context_seen and not turn.seen_model_output:
                        turn.prompt, turn.prompt_at = text_of(p.get("content")), at
                    continue
                if item == "function_call_output":
                    tool = turn.tools.get(p.get("call_id"))
                    if tool:
                        tool.end, tool.output = at, p.get("output")
                    continue
                if item not in MODEL_OUTPUT or (item == "message" and p.get("role") != "assistant"):
                    continue
                turn.seen_model_output = True
                if not turn.steps or turn.steps[-1].closed:
                    turn.steps.append(Step(len(turn.steps), at, at))
                step = turn.steps[-1]
                step.end = at
                if item == "message":
                    step.texts.append(text_of(p.get("content")))
                elif item == "web_search_call":
                    call_id = p.get("id") or f"ws-{len(turn.tools)}"
                    query = (p.get("action") or {}).get("query")
                    turn.tools[call_id] = Tool(call_id, "web_search", {"query": query}, at, end=at)
                    step.calls.append({"name": "web_search", "arguments": {"query": query}})
                elif item != "reasoning":
                    call_id = p.get("call_id") or p.get("id") or f"call-{len(turn.tools)}"
                    args = p.get("arguments", p.get("input"))
                    try:
                        args = json.loads(args) if isinstance(args, str) else args
                    except ValueError:
                        pass
                    turn.tools[call_id] = Tool(call_id, p.get("name") or item, args, at, p.get("namespace"))
                    step.calls.append({"name": p.get("name") or item, "arguments": args})
    if not meta.get("id"):
        return None
    source = meta.get("source")
    parent = meta.get("parent_thread_id")
    if not parent and isinstance(source, dict):
        parent = ((source.get("subagent") or {}).get("thread_spawn") or {}).get("parent_thread_id")
    return Rollout(path, meta["id"], parent, turns)


class Exporter:
    def __init__(self, config: dict, state_dir: Path):
        self.config = config
        self.state_dir = state_dir
        self.url = config["base_url"].rstrip("/") + "/api/public/otel/v1/traces"
        token = base64.b64encode(f"{config['public_key']}:{config['secret_key']}".encode()).decode()
        self.headers = {"Content-Type": "application/json", "Authorization": f"Basic {token}"}

    def ledger_path(self, rollout: Path) -> Path:
        return self.state_dir / f"{hexid(rollout.resolve(), n=24)}.json"

    def sent(self, rollout: Path) -> dict:
        try:
            return json.loads(self.ledger_path(rollout).read_text())
        except (FileNotFoundError, ValueError):
            return {"ids": []}

    def record(self, rollout: Path, ledger: dict) -> None:
        path = self.ledger_path(rollout)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger))
        tmp.replace(path)

    def post(self, spans: list[dict]) -> None:
        attrs = [{"key": "service.name", "value": {"stringValue": "codex"}}]
        body = {"resourceSpans": [{"resource": {"attributes": attrs},
                                   "scopeSpans": [{"scope": {"name": "crux-codex-live-trace"}, "spans": spans}]}]}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=30):
            pass


def attr(key: str, value) -> dict:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return {"key": key, "value": {"arrayValue": {"values": [{"stringValue": v} for v in value]}}}
    if not isinstance(value, str):
        value = json.dumps(value)
    return {"key": key, "value": {"stringValue": value}}


def build(rollout: Rollout, turn: Turn, trace_id: str, parent_span: str | None, session: str,
          config: dict, finalize: bool) -> list[dict]:
    """Every observation of `turn` that is complete now, in a stable order."""
    subagent = rollout.parent_thread_id is not None
    root_id = hexid(rollout.thread_id, turn.turn_id, "root")
    trace_attrs = {"langfuse.environment": config.get("environment") or "default", "langfuse.session.id": session,
                   "langfuse.trace.name": "Codex Turn", "langfuse.trace.tags": list(config.get("tags") or [])}
    if config.get("user_id"):
        trace_attrs["langfuse.user.id"] = config["user_id"]
    for key, value in (config.get("metadata") or {}).items():
        trace_attrs[f"langfuse.trace.metadata.{key}"] = value

    def span(span_id: str, name: str, start: float, end: float, parent: str | None, **attrs) -> dict:
        meta = {"codex.thread_id": rollout.thread_id, "codex.turn_id": turn.turn_id}
        out = {"traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
               "startTimeUnixNano": str(int(start * 1e9)), "endTimeUnixNano": str(int(end * 1e9)), "status": {},
               "attributes": [attr(k, v) for k, v in {**trace_attrs, **attrs}.items()]
               + [attr(f"langfuse.observation.metadata.{k}", v) for k, v in meta.items()]}
        if parent:
            out["parentSpanId"] = parent
        return out

    ended_by, end = turn.ended_by, turn.end
    if ended_by is None and finalize:
        ended_by, end = "stopped", turn.last
    spans = []
    if turn.prompt is not None:
        spans.append(span(hexid(rollout.thread_id, turn.turn_id, "started"),
                          "Codex Subagent Turn started" if subagent else "Codex Turn started",
                          turn.prompt_at, turn.prompt_at, root_id,
                          **{"langfuse.observation.type": "event", "langfuse.observation.input": turn.prompt}))
    for step in turn.steps:
        if not step.closed and ended_by is None:
            continue
        output = {"role": "assistant", "content": "\n".join(step.texts)}
        if step.calls:
            output["tool_calls"] = step.calls
        attrs = {"langfuse.observation.type": "generation", "langfuse.observation.output": output,
                 "langfuse.observation.metadata.codex.step_index": str(step.index)}
        if turn.model:
            attrs["langfuse.observation.model.name"] = turn.model
        if step.usage:
            usage = {"input": step.usage.get("input_tokens"), "output": step.usage.get("output_tokens"),
                     "input_cached_tokens": step.usage.get("cached_input_tokens"),
                     "output_reasoning_tokens": step.usage.get("reasoning_output_tokens")}
            attrs["langfuse.observation.usage_details"] = {k: v for k, v in usage.items() if isinstance(v, int)}
        spans.append(span(hexid(rollout.thread_id, turn.turn_id, "step", step.index),
                          "LLM Subagent" if subagent else "LLM", step.start, step.end, root_id, **attrs))
    for tool in turn.tools.values():
        if tool.end is None and ended_by is None:
            continue
        attrs = {"langfuse.observation.type": "tool", "langfuse.observation.input": tool.args,
                 "langfuse.observation.metadata.codex.call_id": tool.call_id}
        if tool.end is None:
            attrs["langfuse.observation.level"] = "WARNING"
            attrs["langfuse.observation.status_message"] = "The turn ended before this call wrote an output"
        if tool.output is not None:
            attrs["langfuse.observation.output"] = tool.output if isinstance(tool.output, str) else json.dumps(tool.output)
        if tool.namespace:
            attrs["langfuse.observation.metadata.codex.namespace"] = tool.namespace
        spans.append(span(hexid(rollout.thread_id, turn.turn_id, "tool", tool.call_id), tool.name,
                          tool.start, tool.end if tool.end is not None else end, root_id, **attrs))
    if ended_by is not None:
        attrs = {"langfuse.observation.type": "agent", "langfuse.observation.input": turn.prompt or "",
                 "langfuse.observation.metadata.codex.ended_by": ended_by,
                 "langfuse.observation.metadata.codex.tool_call_count": str(len(turn.tools))}
        if turn.output is not None:
            attrs["langfuse.observation.output"] = turn.output
        if ended_by != "complete":
            attrs["langfuse.observation.level"] = "WARNING"
            attrs["langfuse.observation.status_message"] = {
                "aborted": "Turn interrupted", "superseded": "A later turn started before this one ended",
                "stopped": "Codex stopped before the turn ended"}[ended_by]
        spans.append(span(root_id, "Codex Subagent Turn" if subagent else "Codex Turn",
                          turn.start, end, parent_span, **attrs))
    return spans


def open_rollouts() -> set[Path]:
    held = set()
    for fd_dir in Path("/proc").glob("[0-9]*/fd"):
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = Path(os.readlink(fd))
            except OSError:
                continue
            if target.name.startswith("rollout-"):
                held.add(target)
    return held


PARSED: dict[Path, tuple[tuple[int, int], Rollout | None]] = {}


def parse_cached(path: Path) -> Rollout | None:
    """Reparse a rollout only when it has changed since the last pass of this process."""
    st = path.stat()
    key = (st.st_size, st.st_mtime_ns)
    if path not in PARSED or PARSED[path][0] != key:
        PARSED[path] = (key, parse(path))
    return PARSED[path][1]


def run_pass(sessions: Path, exporter: Exporter, finalize: bool) -> tuple[int, list[str]]:
    rollouts = {}
    for path in sorted(sessions.rglob("rollout-*.jsonl")):
        parsed = parse_cached(path)
        if parsed:
            rollouts[parsed.thread_id] = parsed
    held = open_rollouts() if finalize else set()

    def main_thread(r: Rollout) -> Rollout:
        seen = set()
        while r.parent_thread_id and r.parent_thread_id in rollouts and r.thread_id not in seen:
            seen.add(r.thread_id)
            r = rollouts[r.parent_thread_id]
        return r

    def parent_turn(child: Rollout, turn: Turn) -> tuple[Rollout, Turn] | None:
        parent = rollouts.get(child.parent_thread_id)
        if parent is None:
            return None
        started = [t for t in parent.turns if t.start <= turn.start]
        return (parent, started[-1]) if started else None

    def placement(r: Rollout, turn: Turn) -> tuple[str, str | None] | None:
        if r.parent_thread_id is None:
            return hexid(r.thread_id, turn.turn_id, "trace", n=32), None
        found = parent_turn(r, turn)
        if found is None:
            return None
        parent, pturn = found
        outer = placement(parent, pturn)
        return (outer[0], hexid(parent.thread_id, pturn.turn_id, "root")) if outer else None

    sent_total, errors = 0, []
    for r in sorted(rollouts.values(), key=lambda r: r.parent_thread_id is not None):
        ledger = exporter.sent(r.path)
        already = set(ledger["ids"])
        session = main_thread(r).thread_id
        stop_last = finalize and r.path.resolve() not in held
        pending = []
        for n, turn in enumerate(r.turns):
            where = placement(r, turn)
            if where is None:
                continue
            fin = stop_last and n == len(r.turns) - 1
            pending += [s for s in build(r, turn, where[0], where[1], session, exporter.config, fin)
                        if s["spanId"] not in already]
        for i in range(0, len(pending), BATCH):
            batch = pending[i:i + BATCH]
            try:
                exporter.post(batch)
            except (urllib.error.URLError, OSError) as err:
                errors.append(f"{r.path.name}: {err}")
                break
            already |= {s["spanId"] for s in batch}
            ledger["ids"] = sorted(already)
            exporter.record(r.path, ledger)
            sent_total += len(batch)
    return sent_total, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sessions", type=Path, help="default: ~/.codex/sessions")
    parser.add_argument("--config", type=Path, help="default: ~/.codex/langfuse.json")
    parser.add_argument("--state-dir", type=Path, help="default: ~/.codex/live-trace")
    parser.add_argument("--interval", type=float, default=30, help="seconds between passes")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--finalize", action="store_true",
                        help="also end each rollout's unfinished last turn; for after codex has exited")
    args = parser.parse_args()

    home = Path.home()
    codex = home / ".codex"
    config = json.loads((args.config or codex / "langfuse.json").read_text())
    state_dir = args.state_dir or codex / "live-trace"
    state_dir.mkdir(parents=True, exist_ok=True)
    exporter = Exporter(config, state_dir)
    sessions = args.sessions or codex / "sessions"

    while True:
        with open(state_dir / "lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            sent, errors = run_pass(sessions, exporter, args.finalize)
        for error in errors:
            print(f"codex-live-trace: export failed, retrying next pass: {error}", flush=True)
        if sent or args.once:
            print(f"codex-live-trace: sent {sent} observations", flush=True)
        if args.once:
            return 1 if errors else 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
