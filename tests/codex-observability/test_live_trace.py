"""The live exporter that streams Codex turns to Langfuse as they run (codex-live-trace.py).

Each test writes a fixture rollout a piece at a time, as Codex would, and runs one
exporter pass after each piece against the local collector. Langfuse v4 never
updates an observation, so across all passes every span id must arrive exactly once.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from collector import Collector, Span
from pins import REPO

LIVE = REPO / "src" / "ec2-workspaces" / "codex-live-trace.py"
TAGS = ["workspace:0wsFixture01", "run:crux-fixture", "platform:codex"]


@pytest.fixture
def home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    return home


@pytest.fixture
def live(home):
    """Run one exporter pass the way the service and the gateway unit do."""

    def run(url: str, *extra: str) -> subprocess.CompletedProcess:
        (home / ".codex" / "langfuse.json").write_text(json.dumps({
            "enabled": True, "public_key": "pk-lf-fixture", "secret_key": "sk-lf-fixture", "base_url": url,
            "environment": "crux-fixture", "user_id": "crux-fixture", "tags": TAGS,
            "metadata": {"runSlug": "crux-fixture"}}))
        return subprocess.run([sys.executable, str(LIVE), "--once", *extra], env={"PATH": os.environ["PATH"],
                              "HOME": str(home)}, capture_output=True, text=True, timeout=120, check=False)

    return run


def sessions(home: Path) -> Path:
    return home / ".codex" / "sessions"


def passes(rollout, home: Path, collector: Collector, live, cuts: list[int]) -> None:
    for cut in cuts:
        rollout.write(sessions(home), upto=cut)
        proc = live(collector.url)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not collector.bad_requests, collector.bad_requests


def every(rollout, step: int = 7) -> list[int]:
    return [*range(1, len(rollout.lines), step), len(rollout.lines)]


def index_of(rollout, kind: str, nth: int = 0) -> int:
    found = [i for i, line in enumerate(rollout.lines)
             if line["type"] == "event_msg" and line["payload"]["type"] == kind]
    return found[nth]


def assert_sent_once(spans: list[Span]) -> None:
    dupes = [k for k, n in collections.Counter((s.trace_id, s.span_id) for s in spans).items() if n > 1]
    assert not dupes, f"{len(dupes)} observations were sent more than once"


def roots(spans: list[Span]) -> list[Span]:
    return sorted((s for s in spans if s.parent_id is None), key=lambda s: s.start_ns)


def test_the_turn_input_is_sent_before_any_model_output(rollouts, home, live):
    scenario = rollouts.long_turn()
    main = scenario.main
    prompt_line = next(i for i, line in enumerate(main.lines)
                       if line["type"] == "event_msg" and line["payload"]["type"] == "item_completed")
    with Collector() as collector:
        passes(main, home, collector, live, [prompt_line])
    [started] = collector.spans
    assert started.name == "Codex Turn started" and started.type == "event"
    assert started.input == scenario.turns[0].prompt
    assert started.parent_id is not None, "the started event must point at the root it will get"


def test_a_running_turn_sends_finished_work_but_not_its_root(rollouts, home, live):
    scenario = rollouts.long_turn()
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [index_of(scenario.main, "task_complete") - 40])
    spans = collector.spans
    assert {s.type for s in spans} >= {"event", "generation", "tool"}
    assert roots(spans) == [], "the root must wait until the turn ends"
    assert len({s.trace_id for s in spans}) == 1
    assert len({s.parent_id for s in spans}) == 1, "every observation should hang off the one pending root"


def test_passes_send_each_observation_once_and_end_with_the_full_turn(rollouts, home, live):
    scenario = rollouts.long_turn()
    [turn] = scenario.turns
    with Collector() as collector:
        passes(scenario.main, home, collector, live, every(scenario.main))
    spans = collector.spans
    assert_sent_once(spans)
    [root] = roots(spans)
    assert (root.name, root.type, root.input, root.output) == ("Codex Turn", "agent", turn.prompt, turn.final_text)
    assert all(s.parent_id == root.span_id for s in spans if s is not root)
    assert collections.Counter(s.name for s in spans if s.type == "tool") == collections.Counter(turn.tool_names)
    assert len([s for s in spans if s.type == "generation"]) == len(turn.steps)
    assert {s.attributes["langfuse.session.id"] for s in spans} == {turn.thread_id}
    assert {s.attributes["langfuse.user.id"] for s in spans} == {"crux-fixture"}
    assert set(root.attributes["langfuse.trace.tags"]) == set(TAGS)
    assert root.attributes["langfuse.environment"] == "crux-fixture"


def test_model_responses_carry_their_tool_calls_and_usage(rollouts, home, live):
    scenario = rollouts.long_turn()
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [len(scenario.main.lines)])
    generations = [s for s in collector.spans if s.type == "generation"]
    calls = [c["name"] for g in generations for c in json.loads(g.output).get("tool_calls", [])]
    assert collections.Counter(calls) == collections.Counter(scenario.turns[0].tool_names)
    assert all(json.loads(g.attributes["langfuse.observation.usage_details"])["input"] > 0 for g in generations)


# Per-token prices of Langfuse's managed gpt-6.1-sol definition; Langfuse prices every usage_details key on its own.
PRICES = {"input": 2e-06, "input_cached_tokens": 1e-07, "output": 1e-05, "output_reasoning_tokens": 1e-05}


def test_usage_buckets_are_disjoint_so_langfuse_bills_cached_and_reasoning_tokens_once(rollouts, home, live):
    scenario = rollouts.long_turn()
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [len(scenario.main.lines)])
    reported = [json.loads(g.attributes["langfuse.observation.usage_details"])
                for g in collector.spans if g.type == "generation"]
    lines = scenario.main.lines
    raw = [line["payload"]["usage"] for line, after in zip(lines, [*lines[1:], {}])
           if line["type"] == "token_usage_record" and after.get("type") != "compacted"]
    assert len(reported) == len(raw) > 0
    assert any(u["cached_input_tokens"] > 0 and u["reasoning_output_tokens"] > 0 for u in raw)
    expected = [{"input": u["input_tokens"] - u["cached_input_tokens"], "input_cached_tokens": u["cached_input_tokens"],
                 "output": u["output_tokens"] - u["reasoning_output_tokens"],
                 "output_reasoning_tokens": u["reasoning_output_tokens"]} for u in raw]
    assert sorted(reported, key=json.dumps) == sorted(expected, key=json.dumps)
    true_cost = sum((u["input_tokens"] - u["cached_input_tokens"]) * PRICES["input"]
                    + u["cached_input_tokens"] * PRICES["input_cached_tokens"]
                    + u["output_tokens"] * PRICES["output"] for u in raw)
    billed = sum(count * PRICES[key] for usage in reported for key, count in usage.items())
    assert billed == pytest.approx(true_cost)


def test_subagent_turns_nest_under_the_turn_that_spawned_them(rollouts, home, live):
    scenario = rollouts.subagents()
    [turn] = scenario.turns
    for child in scenario.children:
        child.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, every(scenario.main, 5))
    spans = collector.spans
    assert_sent_once(spans)
    assert len({s.trace_id for s in spans}) == 1
    [root] = roots(spans)
    child_turns = [t for child in scenario.children for t in child.turns]
    sub_roots = [s for s in spans if s.name == "Codex Subagent Turn"]
    assert sorted(s.input for s in sub_roots) == sorted(t.prompt for t in child_turns)
    assert {s.parent_id for s in sub_roots} == {root.span_id}
    assert collections.Counter(s.name for s in spans if s.type == "tool") == \
        collections.Counter(turn.tool_names + [n for t in child_turns for n in t.tool_names])
    assert {s.attributes["langfuse.session.id"] for s in spans} == {turn.thread_id}


def test_an_interrupted_turn_is_flagged_and_the_next_turn_traced_on_its_own(rollouts, home, live):
    scenario = rollouts.interrupted()
    for child in scenario.children:
        child.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, every(scenario.main, 9))
    assert_sent_once(collector.spans)
    first, second = roots(collector.spans)
    assert (first.level, first.attributes["langfuse.observation.status_message"]) == ("WARNING", "Turn interrupted")
    assert (second.level, second.output) == ("DEFAULT", scenario.turns[1].final_text)
    assert first.trace_id != second.trace_id


def test_a_killed_turn_gets_its_root_when_the_gateway_has_stopped(rollouts, home, live):
    scenario = rollouts.killed()
    for child in scenario.children:
        child.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [len(scenario.main.lines)])
        assert roots(collector.spans) == []
        assert live(collector.url, "--finalize").returncode == 0
        assert live(collector.url, "--finalize").returncode == 0
    assert_sent_once(collector.spans)
    [root] = roots(collector.spans)
    assert root.input == scenario.turns[0].prompt
    assert (root.level, root.attributes["langfuse.observation.status_message"]) == \
        ("WARNING", "Codex stopped before the turn ended")


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs Linux /proc to see open files")
def test_finalize_leaves_a_rollout_a_live_codex_still_has_open(rollouts, home, live):
    transcript = rollouts.killed().write(sessions(home))
    with Collector() as collector, open(transcript, "a"):
        assert live(collector.url, "--finalize").returncode == 0
    assert collector.spans and roots(collector.spans) == []


def test_a_failed_export_is_retried_on_the_next_pass_without_duplicates(rollouts, home, live):
    scenario = rollouts.long_turn()
    scenario.write(sessions(home))
    failed = live("http://127.0.0.1:9")
    assert failed.returncode == 1 and "retrying next pass" in failed.stdout
    with Collector() as collector:
        assert live(collector.url).returncode == 0
        assert live(collector.url).returncode == 0
    assert_sent_once(collector.spans)
    assert len(roots(collector.spans)) == 1


def test_thread_events_and_goal_turns_trace_one_root_per_turn(rollouts, home, live):
    for name in ("turns_with_thread_events", "goal_continuation"):
        scenario = rollouts.SCENARIOS[name]()
        with Collector() as collector:
            passes(scenario.main, home, collector, live, every(scenario.main, 11))
        assert_sent_once(collector.spans)
        found = roots(collector.spans)
        assert [r.input for r in found] == [t.prompt for t in scenario.turns], name
        assert [r.output for r in found] == [t.final_text for t in scenario.turns], name


def test_code_mode_exec_calls_complete_with_their_output_while_the_turn_runs(rollouts, home, live):
    """Code mode writes custom_tool_call / custom_tool_call_output, not function_call / function_call_output."""
    scenario = rollouts.code_mode()
    for child in scenario.children:
        child.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [index_of(scenario.main, "task_complete") - 6])
        running = [s for s in collector.spans if s.type == "tool" and s.name == "exec"]
        assert roots(collector.spans) == []
        assert running and all(s.output for s in running), "exec calls should arrive with their output mid-turn"
        assert all(s.level == "DEFAULT" for s in running)
        passes(scenario.main, home, collector, live, [len(scenario.main.lines)])
    assert_sent_once(collector.spans)
    calls = [t for turn in scenario.turns + [t for c in scenario.children for t in c.turns]
             for step in turn.steps for t in step.tools if t.kind == "custom"]
    tools = [s for s in collector.spans if s.type == "tool" and s.name == "exec"]
    assert len(tools) == len(calls)
    assert {s.output for s in tools} == {"done\n", "beta-1\n", "step-a\n", "step-b\n", "step-c\n"}, \
        "list-of-text outputs should arrive as their text"
    [wait] = [s for s in collector.spans if s.type == "tool" and s.name == "wait"]
    assert wait.output == "completed"


def test_code_mode_shell_commands_are_traced_with_their_command_and_output(rollouts, home, live):
    """Inside code mode's exec, the real shell command is recorded only as an item_completed CommandExecution."""
    scenario = rollouts.code_mode()
    for child in scenario.children:
        child.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, [index_of(scenario.main, "task_complete") - 6])
        assert any(s.name == "CommandExecution" for s in collector.spans), "commands should show while the turn runs"
        passes(scenario.main, home, collector, live, [len(scenario.main.lines)])
    assert_sent_once(collector.spans)
    commands = sorted((s for s in collector.spans if s.name == "CommandExecution"), key=lambda s: s.start_ns)
    expected = ["mkdir -p /tmp/live-trace-test", "sleep 30 && echo beta-1", "sleep 60 && echo step-a",
                "sleep 60 && echo step-b", "sleep 60 && echo step-c"]
    assert sorted(c for s in commands for c in expected if c in s.input) == sorted(expected)
    by_command = {c: s for s in commands for c in expected if c in s.input}
    assert by_command["sleep 60 && echo step-b"].output == "step-b\n"
    assert 59 <= by_command["sleep 60 && echo step-b"].seconds <= 61
    assert all(s.type == "span" and s.level == "DEFAULT" for s in commands), \
        "commands are spans, not tool calls: in normal mode they repeat an exec_command call"


def _rename(rollout, old: str, new: str) -> None:
    for line in rollout.lines:
        if line["type"] == "response_item" and line["payload"].get("type") == old:
            line["payload"]["type"] = new


def test_a_tool_call_type_the_exporter_has_never_seen_is_still_paired_with_its_output(rollouts, home, live):
    scenario = rollouts.code_mode()
    for rollout in [scenario.main, *scenario.children]:
        _rename(rollout, "custom_tool_call", "future_tool_call")
        _rename(rollout, "custom_tool_call_output", "future_tool_call_output")
        if rollout is not scenario.main:
            rollout.write(sessions(home))
    with Collector() as collector:
        passes(scenario.main, home, collector, live, every(scenario.main, 9))
    tools = [s for s in collector.spans if s.type == "tool" and s.name == "exec"]
    assert len(tools) == 5 and all(s.output and s.level == "DEFAULT" for s in tools)


def test_a_line_the_exporter_has_never_seen_is_sent_as_an_event_and_flagged_on_the_root(rollouts, home, live):
    scenario = rollouts.code_mode()
    main = scenario.main
    for child in scenario.children:
        child.write(sessions(home))
    at = index_of(main, "token_count")
    stamp = main.lines[at]["timestamp"]
    main.lines[at + 1:at + 1] = [
        {"timestamp": stamp, "type": "event_msg", "payload": {"type": "future_event", "detail": "kept"}},
        {"timestamp": stamp, "type": "future_line", "payload": {"detail": "also kept"}},
        {"timestamp": stamp, "type": "world_state", "payload": {"full": False, "state": {}}},
    ]
    with Collector() as collector:
        main.write(sessions(home))
        proc = live(collector.url)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = {s.name: s for s in collector.spans if s.type == "event"}
    assert "kept" in events["event_msg/future_event"].input
    assert "also kept" in events["future_line"].input
    assert "world_state" not in events, "known noise should stay out"
    [root] = roots(collector.spans)
    assert root.meta("codex.unrecognized_types") == "event_msg/future_event,future_line"
    assert "event_msg/future_event" in proc.stdout
