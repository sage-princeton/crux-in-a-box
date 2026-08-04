#!/usr/bin/env bash
# launch.sh [--fresh] — start (or restart) the outer loop.
#
# First run: stamps the run clock — deadline = now + DEADLINE_HOURS — and the
# budgets into state/loop_state.json, records the RunPod starting balance,
# then starts run_loop.sh inside tmux session "crux-codex-loop".
#
# Restart after a crash/reboot: reuses the existing state (the original
# deadline and budget stand — a restart is not a fresh run). Use --fresh to
# deliberately re-stamp the clock for a brand-new run.
set -eu

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LOOP_DIR
# shellcheck source=env.sh
source "$LOOP_DIR/env.sh"

STATE_DIR="$LOOP_DIR/state"
mkdir -p "$STATE_DIR" "$LOOP_DIR/logs"

# ── Preflight ─────────────────────────────────────────────────────────────
command -v codex >/dev/null || { echo "FATAL: codex CLI not on PATH (run setup-codex.sh)"; exit 1; }
command -v tmux  >/dev/null || { echo "FATAL: tmux not installed"; exit 1; }
[ -n "${OPENAI_API_KEY:-}" ] || { echo "FATAL: OPENAI_API_KEY empty in env.sh"; exit 1; }
[ -d "$WORKSPACE_DIR" ] || { echo "FATAL: workspace $WORKSPACE_DIR missing (run setup-codex.sh)"; exit 1; }
git -C "$WORKSPACE_DIR" rev-parse HEAD >/dev/null 2>&1 \
  || { echo "FATAL: $WORKSPACE_DIR is not a git repo with an initial commit"; exit 1; }
echo "codex $(codex --version 2>/dev/null | head -1) · workspace $WORKSPACE_DIR"

# One-call smoke test with the PRODUCTION flag set (a flag the CLI rejects, a
# bad model name, or a bad effort value must surface here, not as loop backoff).
if ! env -u OPENAI_ADMIN_KEY codex exec \
     --dangerously-bypass-approvals-and-sandbox \
     --cd "$WORKSPACE_DIR" \
     -m "$CODEX_MODEL" \
     -c model_reasoning_effort="\"$CODEX_REASONING_EFFORT\"" \
     -o "$STATE_DIR/smoke.last.md" \
     "Reply with the single word OK. Do not spawn agents or run tools for this trivial request." >/dev/null 2>&1; then
  echo "FATAL: codex exec smoke test failed — check the API key/login, model ($CODEX_MODEL), effort ($CODEX_REASONING_EFFORT), and ~/.codex/config.toml"
  exit 1
fi
echo "✔ codex exec smoke test passed (production flags; model $CODEX_MODEL @ $CODEX_REASONING_EFFORT)"

# ── Stamp the run state ───────────────────────────────────────────────────
if [ "${1:-}" = "--fresh" ] && [ -f "$STATE_DIR/loop_state.json" ]; then
  mv "$STATE_DIR/loop_state.json" "$STATE_DIR/loop_state.json.old.$(date -u +%Y%m%dT%H%M%SZ)"
  rm -f "$STATE_DIR/iteration"
  echo "⚠ --fresh: previous run state archived; clock and iteration counter reset"
fi

if [ ! -f "$STATE_DIR/loop_state.json" ]; then
  python3 - "$STATE_DIR/loop_state.json" <<'PY'
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
hours = float(os.environ.get("DEADLINE_HOURS") or 144)

balance = None
key = os.environ.get("RUNPOD_API_KEY")
if key:
    import urllib.parse
    body = json.dumps({"query": "query { myself { clientBalance } }"}).encode()
    ua = "Mozilla/5.0 (X11; Linux x86_64) crux-codex-loop/1.0"  # default urllib UA is 403'd by RunPod's edge
    attempts = (
        ("https://api.runpod.io/graphql?api_key=" + urllib.parse.quote(key, safe=""),
         {"Content-Type": "application/json", "User-Agent": ua}),
        ("https://api.runpod.io/graphql",
         {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": ua}),
    )
    for _ in range(2):  # one retry covers a transient 403/5xx
        for url, headers in attempts:
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    bal = (((json.load(resp) or {}).get("data") or {}).get("myself") or {}).get("clientBalance")
                if bal is not None:
                    balance = float(bal); break
            except Exception:
                continue
        if balance is not None:
            break
    if balance is None:
        print("⚠ could not record RunPod start balance (tried query-param + Bearer)", file=sys.stderr)

state = {
    "launch_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "deadline_iso": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "api_budget_usd": float(os.environ.get("API_BUDGET_USD") or 0),
    "cloud_budget_usd": float(os.environ.get("CLOUD_SPEND_LIMIT_USD") or 0),
    "runpod_start_balance": balance,
}
with open(sys.argv[1], "w") as f:
    json.dump(state, f, indent=2)
print(f"✔ run stamped: launch {state['launch_iso']}, deadline {state['deadline_iso']}, "
      f"API budget ${state['api_budget_usd']}, RunPod start balance {balance}")
PY
else
  echo "✔ existing run state kept (restart): $(python3 -c "import json;s=json.load(open('$STATE_DIR/loop_state.json'));print('deadline',s['deadline_iso'])")"
fi

# ── Start the loop ────────────────────────────────────────────────────────
if tmux has-session -t crux-codex-loop 2>/dev/null; then
  echo "⚠ tmux session crux-codex-loop already exists — loop already running? (tmux attach -t crux-codex-loop)"
  exit 1
fi
tmux new-session -d -s crux-codex-loop "$LOOP_DIR/run_loop.sh"
echo "✔ loop started in tmux session crux-codex-loop"
echo "   watch:  tmux attach -t crux-codex-loop   (detach: Ctrl-b d)"
echo "   logs:   tail -f $LOOP_DIR/logs/loop.log"
echo "   status: $WORKSPACE_DIR/scripts/budget_status.sh"
