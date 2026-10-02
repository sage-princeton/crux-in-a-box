"""The PostToolUse hook that nudges a long main-thread turn to end (codex-turn-nudge.py).

Codex pipes each payload the way codex-rs/hooks does; every payload is checked
against the post-tool-use input schema from the codex the fixtures model, and
every output against the output schema, so a codex that changes either fails here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pins import REPO, fixture_dir, read_pins

NUDGE = REPO / "src" / "ec2-workspaces" / "codex-turn-nudge.py"
SCHEMAS = fixture_dir(read_pins()) / "hook-schemas"
TURN = "019a0f57-30df-7d03-abcb-6558c72108ff"


def conforms(value, schema: dict, root: dict) -> list[str]:
    """The subset of JSON Schema draft-07 the codex hook schemas use."""
    if schema is True:
        return []
    if "$ref" in schema:
        return conforms(value, root["definitions"][schema["$ref"].split("/")[-1]], root)
    errors = [e for sub in schema.get("allOf", []) for e in conforms(value, sub, root)]
    kind = schema.get("type")
    types = {"object": dict, "string": str, "boolean": bool, "null": type(None)}
    if isinstance(kind, list):
        if not any(isinstance(value, types[k]) for k in kind):
            errors.append(f"{value!r} is not {kind}")
    elif kind and not isinstance(value, types[kind]):
        errors.append(f"{value!r} is not {kind}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{value!r} != {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{value!r} not in {schema['enum']}")
    if isinstance(value, dict) and kind == "object":
        props = schema.get("properties", {})
        errors += [f"missing {k}" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"unknown key {k}" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errors += [f"{k}: {e}" for e in conforms(v, props[k], root)]
    return errors


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"post-tool-use.command.{name}.schema.json").read_text())


def payload(turn_id: str = TURN, **extra) -> dict:
    p = {"session_id": "019a0f4e-aac0-70a2-ab6c-58e80b205512", "turn_id": turn_id,
         "transcript_path": "/home/ubuntu/.codex/sessions/2026/10/01/rollout-x.jsonl", "cwd": "/srv/crux-run",
         "hook_event_name": "PostToolUse", "model": "gpt-5.5", "permission_mode": "default",
         "tool_name": "exec_command", "tool_input": {"cmd": "make check"}, "tool_response": "done\n",
         "tool_use_id": "call_fixture", **extra}
    assert conforms(p, schema("input"), schema("input")) == []
    return p


@pytest.fixture
def state(tmp_path) -> Path:
    return tmp_path / "turn-nudge"


@pytest.fixture
def nudge(state):
    """Run the hook once; returns the parsed additionalContext, or None when it printed nothing."""

    def run(p: dict | str, minutes: int = 20) -> str | None:
        stdin = p if isinstance(p, str) else json.dumps(p)
        proc = subprocess.run([sys.executable, str(NUDGE), "--minutes", str(minutes), "--state-dir", str(state)],
                              input=stdin, capture_output=True, text=True, timeout=30, check=False)
        assert proc.returncode == 0, proc.stderr
        if not proc.stdout.strip():
            return None
        out = json.loads(proc.stdout)
        assert conforms(out, schema("output"), schema("output")) == []
        return out["hookSpecificOutput"]["additionalContext"]

    return run


def started(state: Path, turn_id: str, minutes_ago: float, nudged_minutes_ago: float | None = None) -> None:
    """Record that the turn's first tool call finished `minutes_ago`."""
    state.mkdir(parents=True, exist_ok=True)
    record = {"started": time.time() - minutes_ago * 60}
    if nudged_minutes_ago is not None:
        record["nudged"] = time.time() - nudged_minutes_ago * 60
    (state / f"{turn_id}.json").write_text(json.dumps(record))


def test_a_short_turn_is_not_nudged(nudge, state):
    assert nudge(payload()) is None
    assert nudge(payload()) is None
    started(state, TURN, minutes_ago=19)
    assert nudge(payload()) is None


def test_a_long_turn_is_nudged_to_end_only_if_it_has_a_goal(nudge, state):
    started(state, TURN, minutes_ago=21)
    text = nudge(payload())
    assert text is not None
    assert "21 minutes" in text
    assert "get_goal" in text and "end your turn" in text
    assert "no active goal" in text


def test_the_nudge_repeats_once_per_interval(nudge, state):
    started(state, TURN, minutes_ago=25)
    assert nudge(payload()) is not None
    assert nudge(payload()) is None, "nudged again on the very next tool call"
    started(state, TURN, minutes_ago=45, nudged_minutes_ago=21)
    assert nudge(payload()) is not None


def test_each_turn_has_its_own_clock(nudge, state):
    started(state, TURN, minutes_ago=30)
    other = "019a0f58-0000-7000-8000-000000000001"
    assert nudge(payload(other)) is None
    assert nudge(payload()) is not None


def test_subagent_turns_are_never_nudged(nudge, state):
    """Ending a subagent's turn hands half-done work back to its parent."""
    started(state, TURN, minutes_ago=60)
    assert nudge(payload(agent_id="019a0f4f-a2f7-7000-8000-000000000002", agent_type="default")) is None


def test_zero_minutes_disables_the_hook(nudge, state):
    started(state, TURN, minutes_ago=600)
    assert nudge(payload(), minutes=0) is None


@pytest.mark.parametrize("stdin", ["", "not json", "[]", json.dumps({"hook_event_name": "PostToolUse"})])
def test_a_payload_it_cannot_read_is_ignored(nudge, stdin):
    assert nudge(stdin) is None


def test_records_of_old_turns_are_removed(nudge, state):
    started(state, "019a0000-0000-7000-8000-00000000dead", minutes_ago=5 * 24 * 60)
    old = state / "019a0000-0000-7000-8000-00000000dead.json"
    stale = time.time() - 5 * 24 * 3600
    os.utime(old, (stale, stale))
    nudge(payload())
    assert not old.exists()
    assert (state / f"{TURN}.json").exists()
