#!/usr/bin/env python3
"""Upload the Codex turns that never reached the tracing plugin's Stop hook.

Codex fires Stop only when a turn ends. A turn whose gateway is killed (SIGTERM,
OOM, a crash, a reboot) never gets one, so the plugin never uploads it. The
gateway unit runs this after the gateway exits (ExecStopPost) and before it
starts (ExecStartPre), as the run user.

For each main-thread rollout whose last turn is missing from the plugin's
`<rollout>.langfuse` record, this pipes the plugin a Stop payload naming that
turn, so the plugin treats it as final. The plugin uploads it, nests its
subagent threads under it, and records it, so a later Stop never uploads it
again. Its traces are tagged `flush:<reason>`.

A rollout that a live process still holds open (an operator's codex outside
the gateway) is skipped: its turn may still be running.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TURN_EVENTS = (b'"task_started"', b'"task_complete"', b'"turn_aborted"')


def is_subagent(meta: dict) -> bool:
    source = meta.get("source")
    return meta.get("thread_source") == "subagent" or (isinstance(source, dict) and "subagent" in source)


def read_rollout(path: Path) -> tuple[dict, str | None]:
    """The session_meta payload and the id of the last turn that started."""
    meta, last = {}, None
    with path.open("rb") as f:
        for n, raw in enumerate(f):
            if n and not any(event in raw for event in TURN_EVENTS):
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            payload = line.get("payload") or {}
            if line.get("type") == "session_meta":
                meta = payload
            elif line.get("type") == "event_msg" and payload.get("type") == "task_started":
                last = payload.get("turn_id") or last
    return meta, last


def uploaded_turns(path: Path) -> set[str]:
    try:
        return set(Path(f"{path}.langfuse").read_text().split())
    except FileNotFoundError:
        return set()


def open_rollouts() -> set[Path]:
    """Rollout files any process this user can inspect holds open (Linux /proc)."""
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


def run_tags(home: Path) -> list[str]:
    """The tags the plugin would use. LANGFUSE_CODEX_TAGS replaces langfuse.json's, so carry them over."""
    env = os.environ.get("LANGFUSE_CODEX_TAGS", "").strip()
    if env:
        return json.loads(env) if env.startswith("[") else [t.strip() for t in env.split(",") if t.strip()]
    try:
        tags = json.loads((home / ".codex" / "langfuse.json").read_text()).get("tags") or []
    except (FileNotFoundError, ValueError):
        return []
    return [str(t) for t in tags]


def upload(plugin: Path, rollout: Path, meta: dict, turn_id: str, tags: list[str], timeout: float) -> str | None:
    """Run the plugin's Stop hook for one turn; returns an error message, or None on success."""
    payload = {"session_id": meta.get("id"), "transcript_path": str(rollout), "cwd": meta.get("cwd") or str(Path.cwd()),
               "hook_event_name": "Stop", "stop_hook_active": False, "turn_id": turn_id,
               "last_assistant_message": None, "model": "", "permission_mode": "default"}
    env = {**os.environ, "LANGFUSE_CODEX_TAGS": json.dumps(tags), "LANGFUSE_CODEX_FAIL_ON_ERROR": "true"}
    try:
        proc = subprocess.run(["node", str(plugin)], input=json.dumps(payload), env=env,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return f"plugin timed out after {timeout:.0f}s"
    if proc.returncode != 0:
        return (proc.stderr.strip().splitlines() or [f"plugin exited {proc.returncode}"])[-1]
    if turn_id not in uploaded_turns(rollout):
        return "plugin exited cleanly but did not record the turn as uploaded"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plugin", type=Path, required=True, help="the tracing plugin's dist/index.mjs")
    parser.add_argument("--reason", required=True, help="tagged on uploaded traces as flush:<reason>")
    parser.add_argument("--sessions", type=Path, help="default: ~/.codex/sessions")
    parser.add_argument("--check", action="store_true", help="report what would be uploaded; upload nothing")
    parser.add_argument("--timeout", type=float, default=60, help="seconds per plugin run")
    args = parser.parse_args()

    home = Path.home()
    sessions = args.sessions or home / ".codex" / "sessions"
    if not args.check and not args.plugin.is_file():
        print(f"codex-flush: plugin not found at {args.plugin}; nothing uploaded", file=sys.stderr)
        return 1
    tags = [*run_tags(home), f"flush:{args.reason}"]
    held = open_rollouts()

    checked, pending, uploaded, in_use, failed = 0, 0, 0, 0, 0
    for rollout in sorted(sessions.rglob("rollout-*.jsonl")):
        meta, turn_id = read_rollout(rollout)
        if is_subagent(meta) or turn_id is None:
            continue
        checked += 1
        if turn_id in uploaded_turns(rollout):
            continue
        if rollout.resolve() in held:
            in_use += 1
            print(f"codex-flush: {rollout.name} turn {turn_id}: in use by a live process, left for its Stop")
            continue
        pending += 1
        if args.check:
            print(f"codex-flush: {rollout.name} turn {turn_id}: would upload")
            continue
        error = upload(args.plugin, rollout, meta, turn_id, tags, args.timeout)
        if error:
            failed += 1
            print(f"codex-flush: {rollout.name} turn {turn_id}: upload failed: {error}")
        else:
            uploaded += 1
            print(f"codex-flush: {rollout.name} turn {turn_id}: uploaded (flush:{args.reason})")

    if args.check:
        print(f"codex-flush: checked {checked} rollouts, {pending} to upload, {in_use} in use")
    else:
        print(f"codex-flush: checked {checked} rollouts, uploaded {uploaded}, {in_use} in use, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
