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
