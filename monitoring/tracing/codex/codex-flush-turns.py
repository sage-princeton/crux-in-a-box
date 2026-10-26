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
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RolloutSummary:
    """The parts of a rollout the flush needs: whose thread it is, and its last turn.

    Only two line shapes matter, so every other line is skipped before it is
    parsed: the first line (session_meta) and each task_started event.
    """

    thread_id: str | None = None
    cwd: str | None = None
    subagent: bool = False
    last_turn_id: str | None = None

    @classmethod
    def read(cls, path: Path) -> RolloutSummary:
        summary = cls()
        with path.open("rb") as f:
            for n, raw in enumerate(f):
                if n and b'"task_started"' not in raw:
                    continue
                try:
                    summary.apply(json.loads(raw))
                except ValueError:
                    continue
        return summary

    def apply(self, line: dict) -> None:
        match line:
            case {"type": "session_meta", "payload": {"id": str(thread_id)} as meta}:
                self.thread_id, self.cwd = thread_id, meta.get("cwd")
                self.subagent = spawned_by_another_thread(meta)
            case {"type": "event_msg", "payload": {"type": "task_started", "turn_id": str(turn_id)}}:
                self.last_turn_id = turn_id


def spawned_by_another_thread(meta: dict) -> bool:
    """A subagent's turns are uploaded nested in the parent turn that spawned it, never on their own."""
    match meta:
        case {"thread_source": "subagent"} | {"source": {"subagent": _}}:
            return True
        case _:
            return False


def uploaded_turns(path: Path) -> set[str]:
    """Turn ids the plugin has recorded as uploaded, in its `<rollout>.langfuse` file."""
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


def upload(plugin: Path, rollout: Path, summary: RolloutSummary, turn_id: str, tags: list[str],
           timeout: float) -> str | None:
    """Run the plugin's Stop hook for one turn; returns an error message, or None on success."""
    payload = {"session_id": summary.thread_id, "transcript_path": str(rollout),
               "cwd": summary.cwd or str(Path.cwd()),
               "hook_event_name": "Stop", "stop_hook_active": False, "turn_id": turn_id,
               "last_assistant_message": None, "model": "", "permission_mode": "default"}
    # The plugin also reads <cwd>/.codex/langfuse.json over the global one, and the
    # gateway runs this in the agent's workspace: run it from / so a file the agent
    # writes cannot redirect the upload. It gets none of the gateway's API keys either.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
           "LANGFUSE_CODEX_TAGS": json.dumps(tags), "LANGFUSE_CODEX_FAIL_ON_ERROR": "true"}
    try:
        proc = subprocess.run(["node", str(plugin)], input=json.dumps(payload), env=env, cwd="/",
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
        summary = RolloutSummary.read(rollout)
        turn_id = summary.last_turn_id
        if summary.thread_id is None or summary.subagent or turn_id is None:
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
        error = upload(args.plugin, rollout, summary, turn_id, tags, args.timeout)
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
