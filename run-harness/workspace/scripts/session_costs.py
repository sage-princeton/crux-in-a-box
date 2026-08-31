#!/usr/bin/env python3
"""API spend as recorded in the OpenClaw session store — the run's own ledger.

Every assistant record the gateway writes to ~/.openclaw/agents/main/sessions/<sid>.jsonl
carries usage{input,output,cacheRead,cacheWrite,totalTokens,cost{...,total}}, priced by
the gateway at the moment of the call.  Summing usage.cost.total over every transcript,
deduplicated by responseId, is therefore a *record* of what this run spent on its own
turns, heartbeats, subagents and cron jobs — not an estimate.

Why this script exists next to telemetry_costs.py: telemetry_costs.py asks the
cost-tracking Lambda (the provider's admin API) for the spend on this run's key.  That
is the EXTERNAL cross-check — it sees the bill the provider will send, but it lags by
hours, can be down, and cannot tell a subagent from a heartbeat.  The store is the
ledger: it is local, current to the last turn, and attributable per session kind.
Read both.  If they disagree by more than ~10% (after the Lambda has had time to catch
up), trust the store for planning, and note the discrepancy in LOG.md with both numbers
— a persistent gap means something is billing outside the store (a leaked key in an
experiment, a second box) or the store is missing transcripts, and the operator needs
to know either way.

Two pitfalls this script handles that a naive sum does not:
  - the gateway sometimes re-persists a block of history after a prompt error (same
    responseId, new record id, thinking stripped); a naive sum counts those turns twice.
    Turns are keyed by responseId, first occurrence wins.
  - the main session can be re-keyed or reset mid-run, leaving the previous generation's
    transcript orphaned (invisible to `openclaw sessions list`).  Every <sid>.jsonl in the
    store is read, registered or not, plus archived copies (<sid>.jsonl.<suffix>) and any
    snapshots under ~/.openclaw/session-snapshots (cron transcripts are deleted when the
    job next runs; the snapshot is then their only record).  Deduplication by responseId
    makes reading overlapping copies safe.

Usage:
    python3 scripts/session_costs.py [--sessions-dir DIR] [--snapshots-dir DIR | --no-snapshots]

Output: '$X.XX' on the first line (the same shape as telemetry_costs.py, so a heartbeat can
read either with `head -1`), then a per-kind table (main / subagent / cron / other: sessions,
turns, tokens, cost) and the count of zero-cost error turns — turns the provider rejected
outright (auth errors, refusals, aborts) that cost nothing but mean nothing happened.  A run
of them in the main session is the signature of a dead key: stop and tell the operator.

Stdlib only; python3 >= 3.9.  Never prints transcript content.
"""

import argparse
import collections
import json
import os
import re
import sys

SID_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl(?:\.(.+))?$")
TOKEN_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
KINDS = ("main", "subagent", "cron", "other")
DEFAULT_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "agents", "main", "sessions")
DEFAULT_SNAPSHOTS_DIR = os.path.join(os.path.expanduser("~"), ".openclaw", "session-snapshots")


def kind_of(session_key):
    if session_key == "agent:main:main":
        return "main"
    if session_key and session_key.startswith("agent:main:subagent:"):
        return "subagent"
    if session_key and session_key.startswith("agent:main:cron:"):
        return "cron"
    return "other"


def is_error_turn(msg):
    """A turn the provider rejected or the gateway aborted: nothing was generated."""
    return bool(msg.get("errorMessage")) or msg.get("stopReason") in ("error", "aborted")


def transcript_files(root, recursive=False):
    """(sid, path) for every <sid>.jsonl / <sid>.jsonl.<suffix> under root (lock files excluded)."""
    out = []
    if not os.path.isdir(root):
        return out
    walker = os.walk(root) if recursive else [(root, [], os.listdir(root))]
    for dirpath, _dirs, files in walker:
        for base in files:
            m = SID_RE.match(base)
            if not m:
                continue
            suffix = m.group(2)
            if suffix and (suffix == "lock" or suffix.endswith(".lock")):
                continue
            out.append((m.group(1), os.path.join(dirpath, base)))
    return sorted(out)


def session_keys(sessions_dir, sids):
    """sid -> sessionKey from the registry, its usageFamilySessionIds, or the trajectory head
    (the orphaned generation after a re-key is only named there)."""
    keys = {}
    reg_path = os.path.join(sessions_dir, "sessions.json")
    try:
        with open(reg_path, "r", encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError):
        registry = {}
    for key, meta in registry.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("sessionId"):
            keys[meta["sessionId"]] = key
        for old in meta.get("usageFamilySessionIds") or []:
            keys.setdefault(old, key)
    for sid in sids:
        if sid in keys:
            continue
        traj = os.path.join(sessions_dir, sid + ".trajectory.jsonl")
        try:
            with open(traj, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("sessionKey"):
                        keys[sid] = rec["sessionKey"]
                        break
        except OSError:
            pass
    return keys


def tally(files, keys):
    seen = set()
    per_kind = {k: {"sessions": set(), "turns": 0, "errorTurns": 0, "cost": 0.0,
                    "tokens": {f: 0 for f in TOKEN_FIELDS}} for k in KINDS}
    duplicates = 0
    unreadable = 0
    for sid, path in files:
        kind = kind_of(keys.get(sid))
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            unreadable += 1
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "message":
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                turn_key = msg.get("responseId") or msg.get("idempotencyKey") or ("rec:" + str(rec.get("id")))
                if turn_key in seen:
                    duplicates += 1
                    continue
                seen.add(turn_key)
                usage = msg.get("usage") or {}
                cost = float(((usage.get("cost") or {}).get("total")) or 0.0)
                pk = per_kind[kind]
                pk["sessions"].add(sid)
                pk["turns"] += 1
                pk["cost"] += cost
                for f in TOKEN_FIELDS:
                    pk["tokens"][f] += int(usage.get(f) or 0)
                if cost == 0.0 and is_error_turn(msg):
                    pk["errorTurns"] += 1
    return per_kind, duplicates, unreadable


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deduplicated API spend recorded in the OpenClaw session store.")
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, help="default: %(default)s")
    ap.add_argument("--snapshots-dir", default=DEFAULT_SNAPSHOTS_DIR,
                    help="crux-session-snapshot.sh output, read recursively when present (default: %(default)s)")
    ap.add_argument("--no-snapshots", action="store_true", help="read the live store only")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.sessions_dir):
        print("Error: sessions dir not found: %s" % args.sessions_dir, file=sys.stderr)
        return 1
    files = transcript_files(args.sessions_dir)
    snapshot_files = [] if args.no_snapshots else transcript_files(args.snapshots_dir, recursive=True)
    keys = session_keys(args.sessions_dir, {sid for sid, _p in files + snapshot_files})
    per_kind, duplicates, unreadable = tally(files + snapshot_files, keys)

    total = sum(pk["cost"] for pk in per_kind.values())
    print("$%.2f" % total)
    print()
    print("%-9s %8s %7s %14s %11s" % ("kind", "sessions", "turns", "tokens", "cost"))
    for kind in KINDS:
        pk = per_kind[kind]
        if not pk["turns"] and kind == "other":
            continue
        print("%-9s %8d %7d %14d %11s" % (kind, len(pk["sessions"]), pk["turns"], pk["tokens"]["totalTokens"],
                                         "$%.2f" % pk["cost"]))
    all_tokens = {f: sum(pk["tokens"][f] for pk in per_kind.values()) for f in TOKEN_FIELDS}
    print("%-9s %8d %7d %14d %11s" % ("total", sum(len(pk["sessions"]) for pk in per_kind.values()),
                                     sum(pk["turns"] for pk in per_kind.values()), all_tokens["totalTokens"],
                                     "$%.2f" % total))
    print()
    print("tokens: input %d, output %d, cacheRead %d, cacheWrite %d" % (
        all_tokens["input"], all_tokens["output"], all_tokens["cacheRead"], all_tokens["cacheWrite"]))
    err_by_kind = ", ".join("%s %d" % (k, per_kind[k]["errorTurns"]) for k in KINDS if per_kind[k]["errorTurns"])
    print("zero-cost error turns: %d%s" % (sum(pk["errorTurns"] for pk in per_kind.values()),
                                           (" (" + err_by_kind + ")") if err_by_kind else ""))
    print("sources: %d transcripts in %s, %d snapshot copies%s; %d duplicate turns skipped (responseId)%s" % (
        len(files), args.sessions_dir, len(snapshot_files),
        "" if args.no_snapshots else " in " + args.snapshots_dir, duplicates,
        ("; %d files unreadable" % unreadable) if unreadable else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
