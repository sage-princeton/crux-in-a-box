#!/usr/bin/env python3
"""Codex PostToolUse hook: nudge a long main-thread turn to end so its trace uploads.

The tracing plugin uploads a turn only when it ends, and nothing in Codex caps a
turn's length; only the model ends one. Once a main-thread turn has run for
--minutes, this adds a note to the model's context asking it to end the turn if
the thread has an active goal. Codex's goal extension then starts the next turn
on its own, so the run continues with its context intact and each turn reaches
Langfuse when it ends. The note repeats once per interval while the turn runs.

Subagent turns are left alone: ending one hands half-done work back to its
parent. A turn's clock starts at its first tool call. Any error prints nothing,
so the hook never gets in the agent's way.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

KEEP_RECORDS_SECONDS = 3 * 24 * 3600

NOTE = (
    "[crux turn-length note] This turn has been running for about {minutes} minutes. "
    "Codex turns are traced to Langfuse only when they end, so the operators cannot see "
    "this turn's work yet. If this thread has an active goal (check with get_goal), "
    "finish the step you are on, reply with a two-line progress note, and end your turn: "
    "Codex starts the next turn on the goal automatically, with your context intact. "
    "If there is no active goal, ignore this note and keep working."
)


def nudge(hook: dict, minutes: float, state_dir: Path, now: float) -> str | None:
    turn_id = hook.get("turn_id")
    if hook.get("hook_event_name") != "PostToolUse" or not isinstance(turn_id, str) or not turn_id:
        return None
    if hook.get("agent_id"):
        return None

    record_path = state_dir / f"{Path(turn_id).name}.json"
    try:
        record = json.loads(record_path.read_text())
    except (FileNotFoundError, ValueError):
        state_dir.mkdir(parents=True, exist_ok=True)
        for old in state_dir.glob("*.json"):
            if now - old.stat().st_mtime > KEEP_RECORDS_SECONDS:
                old.unlink(missing_ok=True)
        record_path.write_text(json.dumps({"started": now}))
        return None

    interval = minutes * 60
    started = float(record["started"])
    last = float(record.get("nudged", started))
    if now - started < interval or now - last < interval:
        return None
    record_path.write_text(json.dumps({**record, "nudged": now}))
    return NOTE.format(minutes=int((now - started) // 60))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, required=True, help="turn length before the first nudge; 0 disables")
    parser.add_argument("--state-dir", type=Path, help="default: ~/.codex/turn-nudge")
    args = parser.parse_args()
    if args.minutes <= 0:
        return 0
    try:
        hook = json.loads(sys.stdin.read())
        if not isinstance(hook, dict):
            return 0
        note = nudge(hook, args.minutes, args.state_dir or Path.home() / ".codex" / "turn-nudge", time.time())
    except Exception:
        return 0
    if note:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
