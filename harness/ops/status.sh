#!/usr/bin/env bash
# =============================================================================
# status.sh — one screen of "how is the run going", for a human, on demand.
#
#   ops/status.sh <run-name>          # on the box
#   ssh <box> 'crux-harness/ops/status.sh <run-name>'   # from your laptop
#
# Read-only. Touches nothing the run owns. Safe to run as often as you like.
#
# It answers, in order: is it alive, how much clock and money are left, how fast
# is it burning and when does that run out, what is it doing, and is it asking
# for anything. The NEEDS OPERATOR block comes first because it is the only part
# that can require you to act.
# =============================================================================
set -uo pipefail

NAME="${1:-}"
[ -n "$NAME" ] || { echo "usage: ops/status.sh <run-name>" >&2; exit 2; }
HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$HARNESS/logs/$NAME"
[ -d "$LOGS" ] || { echo "no logs/$NAME on this box" >&2; exit 2; }

CONTAINER="$(docker ps --filter "ancestor=$(grep -oE 'crux-harness:[0-9-]+' "$HARNESS/.env" 2>/dev/null | head -1)" \
  --format '{{.Names}}' 2>/dev/null | head -1)"
[ -n "$CONTAINER" ] || CONTAINER="$(docker ps --format '{{.Names}}' | grep '^inspect-' | head -1)"

python3 - "$LOGS" "$NAME" "$CONTAINER" <<'PY'
import datetime, glob, json, os, subprocess, sys

logs, name, container = sys.argv[1], sys.argv[2], sys.argv[3]


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception:
        return ""


def dexec(cmd):
    return sh(f"docker exec {container} sh -c {json.dumps(cmd)}") if container else ""


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


now = datetime.datetime.now(datetime.timezone.utc)

# ── the agent's own operator channel, first: it is the only actionable part ──
snaps = dexec("cat /workspace/SNAPSHOTS.md 2>/dev/null")
if "## NEEDS OPERATOR" in snaps:
    block = snaps.split("## NEEDS OPERATOR", 1)[1].split("\n# ", 1)[0]
    print("\033[1;33m*** NEEDS OPERATOR ***\033[0m")
    for line in block.strip().splitlines()[:8]:
        print("   " + line[:150])
    print()

# ── liveness ────────────────────────────────────────────────────────────────
alive = sh("pgrep -c -f '[i]nspect eval'") or "0"
exited = sh(f"grep -h EXITED {logs}/console.log 2>/dev/null | tail -1")
print(f"\033[1mrun {name}\033[0m  eval={'running' if alive != '0' else 'NOT RUNNING'}"
      + (f"  {exited}" if exited else ""))

# ── the timeline: clock, money, turns ───────────────────────────────────────
tl = glob.glob(os.path.join(logs, "*.timeline.jsonl"))
if not tl:
    print("no timeline yet"); raise SystemExit
calls = spawns = 0
cost = None
turn = None
started = None
costs = []
errors = []
for line in open(tl[0], errors="replace"):
    try:
        e = json.loads(line)
    except Exception:
        continue
    v, t = e.get("event"), e.get("ts")
    if v == "run.start":
        started = t
    if v == "model.usage":
        calls += 1
        cost = e.get("cum_cost_usd", cost)
        if t:
            costs.append((t, cost))
    if v == "turn.start":
        turn = (e.get("turn"), e.get("kind"))
    if v == "model.event":
        spawns += sum(1 for x in (e.get("tools_called") or []) if x == "spawn_agent")
    if v in ("turn.fast_failure", "loop.error", "cli.kill.error", "status_line.error",
             "budget.write.error", "final_gate"):
        errors.append((t or "", v, {k: e[k] for k in ("passed", "failures", "reason", "why") if k in e}))

bj = dexec("cat /workspace/BUDGET.json 2>/dev/null")
limit = deadline = None
if bj:
    try:
        b = json.loads(bj)
        limit = b.get("cost", {}).get("limit_usd")
        cost = b.get("cost", {}).get("used_usd", cost)
        deadline = b.get("clock", {}).get("deadline_utc") or b.get("clock", {}).get("deadline")
    except Exception:
        pass

if started:
    el = (now - ts(started)).total_seconds() / 3600
    print(f"  clock   : {el:6.1f} h elapsed" + (f", deadline {deadline}" if deadline else ""))
    # Two burn rates, because they answer different questions and the recent one
    # lies on its own. Once the launch turn ends the agent idles between
    # heartbeats with the real work detached, so a 30-minute window can read $8/h
    # and project 957 hours. The run-average is what sizes the budget; the recent
    # rate only says whether it is busy right now.
    avg = (cost or 0) / el if el > 0 else 0
    recent = [c for c in costs if (now - ts(c[0])).total_seconds() <= 1800]
    rate = None
    if len(recent) > 1:
        mins = (ts(recent[-1][0]) - ts(recent[0][0])).total_seconds() / 60
        if mins > 0:
            rate = (recent[-1][1] - recent[0][1]) / mins * 60
    line = f"  spend   : ${cost or 0:,.2f}"
    if limit:
        line += f" of ${limit:,.0f} ({(cost or 0)/limit:.1%})"
    line += f"   avg ${avg:,.0f}/h"
    if rate is not None:
        line += f"   last30m ${rate:,.0f}/h"
    print(line)
    if limit and avg > 0:
        hrs = (limit * 0.95 - (cost or 0)) / avg
        end = now + datetime.timedelta(hours=hrs)
        print(f"  budget  : at the average, 95% of cap in ~{hrs:,.0f} h "
              f"({end:%Y-%m-%d %H:%M}Z)" + (f"; deadline {deadline}" if deadline else ""))
print(f"  work    : turn {turn}   {calls} calls   {spawns} subagent spawns")

# ── what it is actually doing ───────────────────────────────────────────────
tail = dexec("grep '^### ' /workspace/LOG.md 2>/dev/null | tail -3")
if tail:
    print("  log     :")
    for line in tail.splitlines():
        print("      " + line[4:130])
commits = dexec("git -C /workspace log --oneline 2>/dev/null | head -3")
if commits:
    print("  commits :")
    for line in commits.splitlines():
        print("      " + line[:130])

if errors:
    print("  events  :")
    for t, v, extra in errors[-4:]:
        print(f"      {t[11:19]} {v} {extra if extra else ''}")
PY
