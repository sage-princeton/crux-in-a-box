#!/usr/bin/env python3
"""index_iteration.py <N> <mode> <exit_code> <duration_s> <since_epoch>

Append a rich per-iteration telemetry record to loop/state/loop_journal.jsonl:
the Codex session rollout file(s) this iteration produced, their token usage, a
priced estimate, and the workspace commit sha. This makes the canonical
per-iteration timeline a LIVE artifact instead of a post-hoc reconstruction
(instead of post-hoc forensic reconstruction).

Attribution: each `codex exec` is a fresh thread -> a fresh rollout file, so
rollouts with mtime >= since_epoch belong to this iteration; ultra spawns
subagents that add their own rollouts, all captured. The verifier runs after
this indexer, so its rollout is (correctly) not attributed to the agent
iteration — but the run-end bundle keeps every rollout regardless.

Reads env: LOOP_DIR, WORKSPACE_DIR, CODEX_HOME, OPENAI_*_USD_PER_MTOK.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

LOOP_DIR = os.environ.get("LOOP_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(LOOP_DIR, "state", "loop_journal.jsonl")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
WORKSPACE = os.environ.get("WORKSPACE_DIR") or ""


def env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def find_key(obj, key):
    """Depth-first search for `key` anywhere in a nested JSON object."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_key(v, key)
            if hit is not None:
                return hit
    return None


def session_id_of(path):
    # rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.jsonl  ->  <uuid>
    base = os.path.basename(path)[:-6] if path.endswith(".jsonl") else os.path.basename(path)
    parts = base.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else base


def main():
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    n, mode, rc, dur, since = sys.argv[1:6]
    try:
        since = float(since) - 5  # small slack: the rollout is created just after T0
    except ValueError:
        since = 0

    sessions = os.path.join(CODEX_HOME, "sessions")
    rollouts, sids = [], []
    totals = {"input": 0, "cached": 0, "output": 0, "total": 0}
    for root, _, files in os.walk(sessions):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(root, fn)
            try:
                if os.path.getmtime(p) < since:
                    continue
            except OSError:
                continue
            last = None
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if "total_token_usage" not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        u = find_key(rec, "total_token_usage")
                        if isinstance(u, dict):
                            last = u  # cumulative: keep the final one
            except OSError:
                continue
            rollouts.append(os.path.relpath(p, CODEX_HOME))
            sids.append(session_id_of(p))
            if last:
                totals["input"] += int(last.get("input_tokens") or 0)
                totals["cached"] += int(last.get("cached_input_tokens") or 0)
                totals["output"] += int(last.get("output_tokens") or 0)
                totals["total"] += int(last.get("total_tokens") or 0)

    p_in = env_float("OPENAI_INPUT_USD_PER_MTOK", 1.25)
    p_cached = env_float("OPENAI_CACHED_INPUT_USD_PER_MTOK", 0.125)
    p_out = env_float("OPENAI_OUTPUT_USD_PER_MTOK", 10.0)
    uncached = max(0, totals["input"] - totals["cached"])
    cached = min(totals["cached"], totals["input"])
    est = (uncached * p_in + cached * p_cached + totals["output"] * p_out) / 1e6

    sha = ""
    if WORKSPACE:
        try:
            sha = subprocess.run(["git", "-C", WORKSPACE, "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pass

    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "event": "iteration.telemetry", "iteration": n, "mode": mode,
           "exit_code": rc, "duration_s": dur,
           "n_rollouts": len(rollouts), "session_ids": sorted(set(sids)),
           "rollouts": sorted(rollouts), "tokens": totals,
           "est_cost_usd": round(est, 4), "commit": sha}
    try:
        with open(JOURNAL, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print(f"index_iteration: could not write journal: {e}", file=sys.stderr)
        return
    print(f"iteration {n} telemetry: {len(rollouts)} rollout(s), "
          f"{totals['total']:,} tok, ~${est:.3f}, commit {sha or '?'}", file=sys.stderr)


if __name__ == "__main__":
    main()
