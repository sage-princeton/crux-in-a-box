"""Synthetic Codex rollouts in the shape written by the pinned Codex.

Line kinds, their order and their payload keys follow rollouts that the codex
bundled in codex-acp 1.10.0 (codex 0.153.4) wrote during the crux-web-pilot run,
reduced to structure by `../../rollout_shape.py`. Every value here is made up.

Each scenario returns the rollout it wrote plus the ground truth the tests
compare the plugin's export against.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLI_VERSION = "0.153.4"
ORIGINATOR = "@agentrq/acp-gateway"
MODEL = "gpt-5.5"
MCP_NAMESPACE = "mcp__0wsFixture01"
START = datetime(2026, 10, 1, 0, 52, 54, 455000, tzinfo=timezone.utc)
GOAL_PROMPT = (
    '<codex_internal_context source="goal">\n'
    "Continue working toward the active thread goal.\n"
    "</codex_internal_context>"
)


def _id(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "crux-fixture:" + ":".join(map(str, parts))))


def _usage(n: int) -> dict:
    return {"input_tokens": 1000 + 37 * n, "cached_input_tokens": 800 + 30 * n,
            "cache_write_input_tokens": 0, "output_tokens": 40 + n, "reasoning_output_tokens": 10,
            "total_tokens": 1040 + 38 * n}


@dataclass
class Tool:
    """One tool call in a model step. `kind` picks the rollout shape."""

    kind: str
    name: str
    namespace: str | None = None
    arguments: dict | str = field(default_factory=dict)
    output: str | list = "ok"
    seconds: float = 0.4
    child: Rollout | None = None
    call_id: str = ""
    command: str | None = None


def exec_command(cmd: str, output: str = "done\n", seconds: float = 0.4) -> Tool:
    return Tool("function", "exec_command", None,
                {"cmd": cmd, "justification": "fixture", "max_output_tokens": 2000,
                 "sandbox_permissions": "require_escalated", "workdir": "/srv/fixture"},
                output, seconds)


def write_stdin(chars: str = "", seconds: float = 0.2) -> Tool:
    return Tool("function", "write_stdin", None,
                {"chars": chars, "max_output_tokens": 2000, "session_id": 7, "yield_time_ms": 1000},
                "Process running\n", seconds)


def mcp(tool: str, arguments: dict | None = None) -> Tool:
    return Tool("function", tool, MCP_NAMESPACE, arguments or {}, '{"ok": true}', 0.3)


def web_search(query: str) -> Tool:
    return Tool("web_search", "web_search", None, {"query": query}, "", 1.2)


def create_goal(objective: str) -> Tool:
    return Tool("function", "create_goal", None, {"objective": objective}, '{"status": "active"}', 0.05)


def exec_code(command: str, output: str | list = "done\n", seconds: float = 0.4) -> Tool:
    """Code mode: one freeform `exec` call whose JavaScript runs `command` (seen on ae240-test).

    The shell command itself is recorded only in an item_completed CommandExecution event.
    """
    code = f'const r = await tools.exec_command({{cmd:{json.dumps(command)}}}); text(r.output)'
    return Tool("custom", "exec", None, code, output, seconds, command=command)


def wait_code_mode(*children: Rollout) -> Tool:
    """Code mode's `wait`: a plain function call whose output is a list of text parts (seen on ae240-test)."""
    return Tool("function", "wait", None, {"targets": [c.thread_id for c in children]},
                [{"type": "input_text", "text": "completed"}], 1.0)


def spawn_agent(child: Rollout, message: str) -> Tool:
    out = json.dumps({"agent_id": child.thread_id, "nickname": child.nickname})
    return Tool("function", "spawn_agent", "multi_agent_v1", {"message": message}, out, 0.1, child)


def send_input(child: Rollout, message: str) -> Tool:
    return Tool("function", "send_input", "multi_agent_v1", {"message": message, "target": child.thread_id},
                json.dumps({"submission_id": _id("submission", child.thread_id, message)}), 0.1)


def wait_agent(child: Rollout) -> Tool:
    return Tool("function", "wait_agent", "multi_agent_v1",
                {"targets": [child.thread_id], "timeout_ms": 600000},
                json.dumps({"status": "completed", "timed_out": False}), 2.0)


@dataclass
class ToolTruth:
    name: str
    kind: str
    call_at: datetime
    output_at: datetime | None = None


@dataclass
class StepTruth:
    """When each line of one model step was written."""

    reasoning_at: datetime
    message_at: datetime | None
    first_tool_at: datetime | None
    closed_at: datetime
    tools: list[ToolTruth]


@dataclass
class TurnTruth:
    turn_id: str
    thread_id: str
    prompt: str
    final_text: str | None = None
    completed: bool = False
    aborted: bool = False
    steps: list[StepTruth] = field(default_factory=list)
    children: list[Rollout] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for s in self.steps for t in s.tools]


class Rollout:
    """Appends rollout lines with a moving clock, recording ground truth per turn."""

    def __init__(self, thread_id: str, start: datetime = START, parent: Rollout | None = None,
                 nickname: str | None = None):
        self.thread_id = thread_id
        self.t = start
        self.parent = parent
        self.nickname = nickname
        self.lines: list[dict] = []
        self.turns: list[TurnTruth] = []
        self._session_meta()

    def tick(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    def _line(self, kind: str, payload: dict, **extra) -> datetime:
        written = self.t
        self.lines.append({"timestamp": self._iso(), "type": kind, "payload": payload,
                           "ordinal": len(self.lines), **extra})
        self.tick(0.004)
        return written

    def _session_meta(self) -> None:
        payload = {
            "id": self.thread_id, "session_id": self.thread_id, "timestamp": self._iso(),
            "cwd": "/srv/fixture", "originator": ORIGINATOR, "cli_version": CLI_VERSION,
            "source": "vscode", "model_provider": "openai", "history_mode": "full",
            "base_instructions": {"text": "You are Codex, a coding agent.", "provenance": {"model": MODEL, "type": "model"}},
            "context_window": {"window_id": _id("window", self.thread_id)},
        }
        if self.parent is not None:
            payload.update({
                "source": {"subagent": {"thread_spawn": {
                    "parent_thread_id": self.parent.thread_id, "depth": 1, "agent_path": None,
                    "agent_nickname": self.nickname, "agent_role": None}}},
                "thread_source": "subagent", "agent_nickname": self.nickname,
                "parent_thread_id": self.parent.thread_id, "multi_agent_version": "v1",
            })
        self._line("session_meta", payload)

    def _iso(self) -> str:
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.t.microsecond // 1000:03d}Z"

    def _meta(self, extra: dict | None = None) -> dict:
        return {"turn_id": self.turns[-1].turn_id, **(extra or {})}

    def _item(self, item: dict, seconds: float = 0.005) -> None:
        now = int(self.t.timestamp() * 1000)
        self._line("event_msg", {"type": "item_completed", "thread_id": self.thread_id,
                                 "turn_id": self.turns[-1].turn_id, "started_at_ms": now - int(seconds * 1000),
                                 "completed_at_ms": now, "item": {"id": _id("item", len(self.lines)), **item}})

    def _message(self, role: str, text: str, phase: str | None = None, kind: str | None = None) -> datetime:
        part = "output_text" if role == "assistant" else "input_text"
        payload = {"type": "message", "role": role, "id": _id("msg", self.thread_id, len(self.lines)),
                   "content": [{"type": part, "text": text}],
                   "internal_chat_message_metadata_passthrough": self._meta(
                       {"content_item_kinds": [kind or part], "create_time": self.t.timestamp()})}
        if phase:
            payload["phase"] = phase
        return self._line("response_item", payload)

    def settings_applied(self) -> None:
        """The thread-level event Codex writes before a turn a client starts."""
        self._line("event_msg", {"type": "thread_settings_applied", "thread_id": self.thread_id,
                                 "thread_settings": {"approval_policy": "on-request", "cwd": "/srv/fixture",
                                                     "model": MODEL, "model_provider_id": "openai",
                                                     "reasoning_effort": "high", "reasoning_summary": "auto"}})

    def begin_turn(self, prompt: str) -> TurnTruth:
        """A turn a client started with a user message."""
        return self._begin(prompt, internal=False)

    def begin_goal_turn(self) -> TurnTruth:
        """The turn the goal extension starts once the thread is idle with an active goal.

        Codex submits the goal prompt as an internal-context response item, not user
        input, so the turn has no UserMessage item (codex-rs ext/goal continue_if_idle).
        """
        return self._begin(GOAL_PROMPT, internal=True)

    def _begin(self, prompt: str, internal: bool) -> TurnTruth:
        first = not self.turns
        turn = TurnTruth(_id("turn", self.thread_id, len(self.turns)), self.thread_id, prompt)
        self.turns.append(turn)
        started = int(self.t.timestamp())
        self._line("event_msg", {"type": "task_started", "turn_id": turn.turn_id, "started_at": started,
                                 "model_context_window": 258400, "collaboration_mode_kind": "default"})
        if first:
            self._message("developer", "<skills_instructions>\nfixture skills\n</skills_instructions>")
            self._message("user", "<environment_context>\n  <cwd>/srv/fixture</cwd>\n</environment_context>")
            self._line("world_state", {"full": True, "state": {"model": MODEL, "environments": {"timezone": "UTC"}}})
        self._turn_context()
        if internal:
            self._message("user", prompt, kind="goal.internal_context")
        else:
            self._message("user", prompt)
            self._item({"type": "UserMessage", "content": [{"type": "text", "text": prompt, "text_elements": []}]})
        self.tick(1.0)
        return turn

    def _turn_context(self) -> None:
        self._line("turn_context", {"turn_id": self.turns[-1].turn_id, "cwd": "/srv/fixture", "model": MODEL,
                                    "effort": "high", "summary": "auto", "approval_policy": "on-request",
                                    "personality": "pragmatic", "timezone": "UTC", "realtime_active": False,
                                    "sandbox_policy": {"type": "workspace-write", "network_access": False,
                                                       "exclude_tmpdir_env_var": False, "exclude_slash_tmp": False},
                                    "collaboration_mode": {"mode": "default", "settings": {
                                        "model": MODEL, "reasoning_effort": "high", "developer_instructions": None}},
                                    "workspace_roots": ["/srv/fixture"], "current_date": "2026-10-01"})

    def compact(self) -> None:
        """Mid-turn context compaction, in the order the pilot's codex wrote it."""
        self._usage_lines()
        self._line("compacted", {"compaction_response_id": _id("compaction", len(self.lines)),
                                 "first_window_id": _id("window", self.thread_id), "guardian_history": [],
                                 "latest_token_usage_record": {}})
        self._line("world_state", {"full": True, "state": {"model": MODEL, "environments": {"timezone": "UTC"}}})
        self._turn_context()
        self.settings_applied()
        self._token_count()
        self._item({"type": "ContextCompaction"})
        self.tick(0.5)

    def _reasoning(self) -> datetime:
        written = self._line("response_item", {"type": "reasoning", "id": _id("rs", len(self.lines)), "summary": [],
                                     "encrypted_content": "gAAAAfixture",
                                     "internal_chat_message_metadata_passthrough": self._meta()})
        self._item({"type": "Reasoning", "summary_text": [], "raw_content": []})
        return written

    def _usage_lines(self) -> None:
        n = sum(len(t.steps) for t in self.turns)
        self._line("token_usage_record", {"response_id": _id("resp", self.thread_id, n), "session_id": self.thread_id,
                                          "thread_id": self.thread_id, "turn_id": self.turns[-1].turn_id,
                                          "root_turn_id": self.turns[-1].turn_id, "usage": _usage(n),
                                          "turn_token_usage": _usage(n), "thread_token_usage": _usage(n)})

    def _token_count(self) -> datetime:
        n = sum(len(t.steps) for t in self.turns)
        return self._line("event_msg", {"type": "token_count", "rate_limits": None,
                                 "info": {"last_token_usage": _usage(n), "total_token_usage": _usage(n),
                                          "model_context_window": 258400}})

    def step(self, tools: list[Tool], commentary: str | None = None, model_seconds: float = 3.0) -> None:
        """A model response that calls `tools`, then the tools' results."""
        turn = self.turns[-1]
        start = self.t
        reasoning_at = self._reasoning()
        message_at = None
        if commentary:
            self.tick(model_seconds / 2)
            message_at = self._message("assistant", commentary, phase="commentary")
            self._item({"type": "AgentMessage", "text": commentary})
        self.t = start + timedelta(seconds=model_seconds)
        truths = []
        for tool in tools:
            truth = ToolTruth(tool.name, tool.kind, self.t)
            truths.append(truth)
            if tool.kind == "web_search":
                self._line("response_item", {"type": "web_search_call", "id": _id("ws", len(self.lines)),
                                             "status": "completed",
                                             "action": {"type": "search", "query": tool.arguments["query"],
                                                        "queries": [tool.arguments["query"]]},
                                             "internal_chat_message_metadata_passthrough": self._meta()})
            elif tool.kind == "custom":
                call = {"type": "custom_tool_call", "status": "completed", "call_id": _id("call", self.thread_id, len(self.lines)),
                        "name": tool.name, "input": tool.arguments, "id": _id("ctc", len(self.lines)),
                        "internal_chat_message_metadata_passthrough": self._meta()}
                tool.call_id = call["call_id"]
                self._line("response_item", call)
            else:
                call = {"type": "function_call", "name": tool.name, "arguments": json.dumps(tool.arguments),
                        "call_id": _id("call", self.thread_id, len(self.lines)), "id": _id("fc", len(self.lines)),
                        "internal_chat_message_metadata_passthrough": self._meta()}
                if tool.namespace:
                    call["namespace"] = tool.namespace
                tool.call_id = call["call_id"]
                self._line("response_item", call)
        self._usage_lines()
        for tool, truth in zip(tools, truths):
            self.tick(tool.seconds)
            if tool.kind == "web_search":
                self._item({"type": "WebSearch", "query": tool.arguments["query"],
                            "action": {"type": "search", "query": tool.arguments["query"]}})
                continue
            item = {"exec_command": "CommandExecution", "write_stdin": "CommandExecution"}.get(tool.name)
            if tool.namespace == MCP_NAMESPACE:
                item = "McpToolCall"
            elif tool.namespace == "multi_agent_v1":
                item = "CollabAgentToolCall"
            if tool.command is not None:
                stdout = tool.output if isinstance(tool.output, str) else "".join(p["text"] for p in tool.output)
                self._item({"type": "CommandExecution", "command": ["/bin/bash", "-lc", tool.command],
                            "cwd": "/srv/fixture", "process_id": "4242", "source": "unified_exec_startup",
                            "status": "completed", "exit_code": 0, "stdout": stdout, "stderr": "",
                            "aggregated_output": stdout, "formatted_output": stdout,
                            "parsed_cmd": [{"type": "unknown", "cmd": tool.command}],
                            "duration": {"secs": int(tool.seconds), "nanos": 0}}, seconds=tool.seconds)
            elif item:
                self._item({"type": item})
            output_type = "custom_tool_call_output" if tool.kind == "custom" else "function_call_output"
            truth.output_at = self._line("response_item", {"type": output_type, "call_id": tool.call_id,
                                         "id": _id("fco", len(self.lines)), "output": tool.output,
                                         "internal_chat_message_metadata_passthrough": self._meta(
                                             {"create_time": self.t.timestamp()})})
            if tool.child is not None:
                turn.children.append(tool.child)
        closed_at = self._token_count()
        first_tool_at = truths[0].call_at if truths else None
        turn.steps.append(StepTruth(reasoning_at, message_at, first_tool_at, closed_at, truths))
        self.tick(0.3)

    def finish_turn(self, text: str, model_seconds: float = 2.0) -> None:
        turn = self.turns[-1]
        start = self.t
        reasoning_at = self._reasoning()
        self.t = start + timedelta(seconds=model_seconds)
        message_at = self._message("assistant", text, phase="final_answer")
        self._item({"type": "AgentMessage", "text": text})
        self._usage_lines()
        closed_at = self._token_count()
        turn.steps.append(StepTruth(reasoning_at, message_at, None, closed_at, []))
        turn.final_text, turn.completed = text, True
        started = int(start.timestamp())
        self._line("event_msg", {"type": "task_complete", "turn_id": turn.turn_id, "last_agent_message": text,
                                 "started_at": started, "completed_at": int(self.t.timestamp()),
                                 "duration_ms": 1000, "time_to_first_token_ms": 800})
        self.tick(0.008)

    def abort_turn(self) -> None:
        turn = self.turns[-1]
        turn.aborted = True
        self._line("event_msg", {"type": "turn_aborted", "turn_id": turn.turn_id, "reason": "interrupted",
                                 "started_at": int(self.t.timestamp()) - 60, "completed_at": int(self.t.timestamp()),
                                 "duration_ms": 60000})
        self.tick(36.0)

    def filename(self) -> str:
        return f"rollout-{START.strftime('%Y-%m-%dT%H-%M-%S')}-{self.thread_id}.jsonl"

    def write(self, sessions: Path, upto: int | None = None) -> Path:
        day = sessions / START.strftime("%Y/%m/%d")
        day.mkdir(parents=True, exist_ok=True)
        path = day / self.filename()
        lines = self.lines if upto is None else self.lines[:upto]
        path.write_text("".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines))
        return path


@dataclass
class Scenario:
    name: str
    main: Rollout
    children: list[Rollout]

    def write(self, sessions: Path) -> Path:
        for child in self.children:
            child.write(sessions)
        return self.main.write(sessions)

    @property
    def turns(self) -> list[TurnTruth]:
        return self.main.turns


def _child(parent: Rollout, nickname: str, task: str, steps: int, turns: int = 1) -> Rollout:
    child = Rollout(_id("thread", nickname), parent.t + timedelta(seconds=0.2), parent, nickname)
    for n in range(turns):
        child.begin_turn(task if n == 0 else f"Follow-up {n} for {nickname}")
        for i in range(steps):
            child.step([exec_command(f"{nickname.lower()} step {n}.{i}")])
        child.finish_turn(f"{nickname} finished part {n}.")
    return child


def _work(rollout: Rollout, steps: int, pad: int = 0) -> None:
    """A run of mostly-shell steps with the other tool kinds the pilot used mixed in."""
    for i in range(steps):
        if i % 10 == 3:
            tools = [write_stdin()]
        elif i % 10 == 5:
            tools = [mcp("getTask", {"taskId": "0wsTask01", "limit": 5, "includeConversation": True})]
        elif i % 10 == 7:
            tools = [web_search(f"payload cms migration step {i}")]
        elif i % 10 == 9:
            tools = [mcp("reply", {"chatId": "0wsChat01", "text": f"status {i}"})]
        else:
            tools = [exec_command(f"make check-{i}", "x" * pad + "done\n")]
        rollout.step(tools, commentary=f"Working on part {i}." if i % 4 == 0 else None)


def long_turn() -> Scenario:
    """One autonomous turn with many tool calls of every kind the pilot used."""
    main = Rollout(_id("thread", "long"))
    main.begin_turn("[Task 0wsTask01] Run Attempt 1\nYou are the autonomous agent for this run.")
    main.step([create_goal("Migrate the pilot pages")])
    _work(main, 20)
    main.compact()
    _work(main, 20)
    main.finish_turn("Pilot pages migrated.")
    return Scenario("long_turn", main, [])


def subagents() -> Scenario:
    """spawn_agent children, one with two turns, nested via the agent_id in spawn_agent's output."""
    main = Rollout(_id("thread", "parent"))
    main.begin_turn("[Task 0wsTask02] Split the work across subagents.")
    galileo = _child(main, "Galileo", "TASK: write verification scripts", steps=3, turns=2)
    main.step([spawn_agent(galileo, "TASK: write verification scripts")])
    locke = _child(main, "Locke", "TASK: write provisioning", steps=2)
    main.step([spawn_agent(locke, "TASK: write provisioning")])
    main.step([send_input(galileo, "Also check the blog pages")])
    main.step([wait_agent(galileo)])
    main.step([exec_command("git log --oneline -3")])
    main.finish_turn("Subagents finished.")
    return Scenario("subagents", main, [galileo, locke])


def turns_with_thread_events() -> Scenario:
    """Client-started turns, each preceded by a thread_settings_applied event."""
    main = Rollout(_id("thread", "events"))
    for n in range(3):
        if n:
            main.settings_applied()
        main.begin_turn(f"[Response to task 0wsTask03] message {n}")
        main.step([exec_command(f"echo {n}")])
        main.finish_turn(f"Reply {n}.")
    return Scenario("turns_with_thread_events", main, [])


def interrupted() -> Scenario:
    """The pilot's sequence: a long turn that spawned a subagent is interrupted, then a short turn completes."""
    main = Rollout(_id("thread", "interrupted"))
    main.begin_turn("[Task 0wsTask04] Run Attempt 1")
    erdos = _child(main, "Erdos", "TASK: content model", steps=2)
    main.step([spawn_agent(erdos, "TASK: content model")])
    _work(main, 6)
    main.abort_turn()
    main.settings_applied()
    main.begin_turn("[Response to task 0wsTask04] action=text: say exactly: UPLOAD TO LANGFUSE")
    main.finish_turn("UPLOAD TO LANGFUSE")
    return Scenario("interrupted", main, [erdos])


def killed() -> Scenario:
    """The pilot's last turn: a subagent and a run of tool calls, then the gateway is SIGTERMed.

    The turn has no task_complete or turn_aborted line, so Codex never fires Stop for it.
    """
    main = Rollout(_id("thread", "killed"))
    main.begin_turn("[Task 0wsTask07] Run Attempt 2")
    hooke = _child(main, "Hooke", "TASK: audit the migration", steps=2)
    main.step([spawn_agent(hooke, "TASK: audit the migration")])
    _work(main, 12)
    return Scenario("killed", main, [hooke])


def code_mode() -> Scenario:
    """Tools called through code mode's freeform `exec`, in the main thread and a subagent, as on ae240-test."""
    main = Rollout(_id("thread", "code-mode"))
    main.begin_turn("[Task 0wsTask08] LIVE-TRACE-TEST: run the steps in order.")
    main.step([exec_code("mkdir -p /tmp/live-trace-test")])
    beta = Rollout(_id("thread", "code-mode", "Beta"), main.t + timedelta(seconds=0.2), main, "Beta")
    beta.begin_turn('Run `sleep 30 && echo beta-1`, then reply exactly "BETA DONE".')
    beta.step([exec_code("sleep 30 && echo beta-1", [{"type": "input_text", "text": "beta-1\n"}], seconds=30)])
    beta.finish_turn("BETA DONE")
    main.step([spawn_agent(beta, 'Run `sleep 30 && echo beta-1`, then reply exactly "BETA DONE".')])
    for step in ("a", "b", "c"):
        main.step([exec_code(f"sleep 60 && echo step-{step}", f"step-{step}\n", seconds=60)])
    main.step([wait_code_mode(beta)])
    main.finish_turn("LIVE-TRACE-TEST COMPLETE")
    return Scenario("code_mode", main, [beta])


def goal_continuation() -> Scenario:
    """A turn that sets a goal, then a chain of turns Codex starts itself, each the moment the last ends."""
    main = Rollout(_id("thread", "goal"))
    main.begin_turn("[Response to task 0wsTask05] action=text: report the time in Tokyo every minute for 3 minutes")
    main.step([create_goal("Report the time in Tokyo every minute for 3 minutes")])
    main.finish_turn("Goal set.")
    for n in range(3):
        main.begin_goal_turn()
        _work(main, 2)
        main.finish_turn(f"Report {n + 1}: 04:2{n} JST.")
    return Scenario("goal_continuation", main, [])


def pilot_sized() -> Scenario:
    """Roughly the size of the pilot's first upload: ~150 steps and four subagents, ~10 MB of rollout."""
    main = Rollout(_id("thread", "pilot"))
    main.begin_turn("[Task 0wsTask06] Run Attempt 1")
    children = []
    for name in ("Galileo", "Locke", "Erdos", "Lovelace"):
        child = Rollout(_id("thread", "pilot", name), main.t + timedelta(seconds=0.2), main, name)
        child.begin_turn(f"TASK: {name}")
        _work(child, 30, pad=100000)
        child.finish_turn(f"{name} done.")
        children.append(child)
        main.step([spawn_agent(child, f"TASK: {name}")])
    for _ in range(2):
        _work(main, 71, pad=35000)
        main.compact()
    main.finish_turn("Checkpoint.")
    return Scenario("pilot_sized", main, children)


SCENARIOS = {f.__name__: f for f in
             (long_turn, subagents, turns_with_thread_events, interrupted, killed, code_mode, goal_continuation,
              pilot_sized)}


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1])
    for name, build in SCENARIOS.items():
        print(build().write(out / name / "sessions"))
