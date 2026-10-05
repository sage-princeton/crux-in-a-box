"""The gateway's flush of turns Codex never fired Stop for (codex-flush-turns.py).

systemd runs the flush after the gateway exits (ExecStopPost) and before it
starts (ExecStartPre). These tests run it the same way, against the rollout
fixtures and the pinned plugin, with HOME holding the langfuse.json that
configure-run.sh writes.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from collector import Collector
from pins import REPO
from test_tracing import by_trace, root_of

FLUSH = REPO / "src" / "ec2-workspaces" / "codex-flush-turns.py"
RUN_TAGS = ["workspace:0wsFixture01", "run:crux-fixture", "platform:codex"]


@pytest.fixture
def home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    return home


@pytest.fixture
def flush(plugin, home):
    """Run the flush as the gateway unit does; langfuse.json points the plugin at `collector`."""

    def run(collector: Collector, reason: str = "gateway-stop", *extra: str,
            cwd: Path | None = None) -> subprocess.CompletedProcess:
        (home / ".codex" / "langfuse.json").write_text(json.dumps({
            "enabled": True, "public_key": "pk-lf-fixture", "secret_key": "sk-lf-fixture",
            "base_url": collector.url, "environment": "crux-fixture", "tags": RUN_TAGS}))
        proc = subprocess.run(
            [sys.executable, str(FLUSH), "--plugin", str(plugin.resolve() / "dist" / "index.mjs"),
             "--reason", reason, *extra],
            env={"PATH": os.environ["PATH"], "HOME": str(home)}, cwd=cwd or home,
            capture_output=True, text=True, timeout=300, check=False)
        assert not collector.bad_requests, collector.bad_requests
        return proc

    return run


def sessions(home: Path) -> Path:
    return home / ".codex" / "sessions"


def line_index(rollout, kind: str) -> int:
    return next(i for i, line in enumerate(rollout.lines)
                if line["type"] == "event_msg" and line["payload"]["type"] == kind)


def test_flush_uploads_a_killed_turn_with_its_subagents_tagged_as_flushed(rollouts, flush, home, expect):
    scenario = rollouts.killed()
    scenario.write(sessions(home))
    [turn] = scenario.turns
    with Collector() as collector:
        proc = flush(collector)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    [trace] = by_trace(collector.spans).values()
    root = root_of(trace)
    assert root.name == expect["names"]["turn"] and root.input == turn.prompt
    assert set(root.attributes["langfuse.trace.tags"]) == {*RUN_TAGS, "flush:gateway-stop"}

    child_turns = [t for child in scenario.children for t in child.turns]
    assert len([s for s in trace if s.name == expect["names"]["subagent_turn"]]) == len(child_turns)
    assert collections.Counter(s.name for s in trace if s.type == "tool") == \
        collections.Counter(turn.tool_names + [n for t in child_turns for n in t.tool_names])


def test_flush_twice_uploads_once(rollouts, flush, home):
    rollouts.killed().write(sessions(home))
    with Collector() as collector:
        assert flush(collector).returncode == 0
        first = len(collector.spans)
        proc = flush(collector, "gateway-start")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert first and len(collector.spans) == first


def test_stop_after_a_resumed_thread_does_not_reupload_the_flushed_turn(rollouts, flush, stop_hook, home):
    """The gateway restarts, the thread resumes, and its next turn ends with a normal Stop."""
    scenario = rollouts.killed()
    scenario.write(sessions(home))
    with Collector() as collector:
        assert flush(collector).returncode == 0
        main = scenario.main
        main.settings_applied()
        main.begin_turn("[Response to task 0wsTask07] action=text: carry on")
        main.finish_turn("Carried on.")
        stop_hook(main.write(sessions(home)), collector, scenario.turns[-1])

    killed, resumed = scenario.turns
    traces = by_trace(collector.spans)
    assert sorted(root_of(t).input for t in traces.values()) == sorted([killed.prompt, resumed.prompt])
    assert all(len({s.export for s in t}) == 1 for t in traces.values()), "a turn was exported twice"


def test_flush_uploads_an_interrupted_turn_no_stop_followed(rollouts, flush, home, expect):
    scenario = rollouts.interrupted()
    for child in scenario.children:
        child.write(sessions(home))
    scenario.main.write(sessions(home), upto=line_index(scenario.main, "turn_aborted") + 1)
    with Collector() as collector:
        assert flush(collector).returncode == 0
    [trace] = by_trace(collector.spans).values()
    root = root_of(trace)
    assert root.input == scenario.turns[0].prompt
    assert root.level == expect["interrupted_turn"]["level"]


def test_flush_leaves_turns_stop_already_uploaded(rollouts, flush, stop_hook, home):
    scenario = rollouts.subagents()
    transcript = scenario.write(sessions(home))
    with Collector() as collector:
        stop_hook(transcript, collector, scenario.turns[-1])
        uploaded = len(collector.spans)
        proc = flush(collector)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert uploaded and len(collector.spans) == uploaded


def test_check_reports_without_uploading(rollouts, flush, home):
    rollouts.killed().write(sessions(home))
    rollouts.long_turn().write(sessions(home))
    with Collector() as collector:
        proc = flush(collector, "provision-check", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert collector.spans == []
    assert "checked 2 rollouts, 2 to upload" in proc.stdout


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs Linux /proc to see open files")
def test_flush_skips_a_rollout_a_live_codex_still_has_open(rollouts, flush, home):
    """Only the gateway's codex is stopped; an operator's codex on the same box may still be mid-turn."""
    transcript = rollouts.killed().write(sessions(home))
    with Collector() as collector, open(transcript, "a"):
        proc = flush(collector)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert collector.spans == []
    assert "in use" in proc.stdout


def test_a_langfuse_config_in_the_working_directory_cannot_redirect_the_upload(rollouts, flush, home, tmp_path):
    """The gateway runs the flush in the agent's workspace, and the plugin also reads <cwd>/.codex/langfuse.json.

    An agent that wrote one could send the flush's uploads, with the real keys, anywhere.
    """
    rollouts.killed().write(sessions(home))
    workspace = tmp_path / "srv-crux-run"
    (workspace / ".codex").mkdir(parents=True)
    with Collector() as collector, Collector() as decoy:
        (workspace / ".codex" / "langfuse.json").write_text(json.dumps({"base_url": decoy.url}))
        proc = flush(collector, cwd=workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert decoy.spans == [], "the upload followed a config file the agent can write"
    assert collector.spans
