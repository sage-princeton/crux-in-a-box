#!/usr/bin/env bash
# run_loop.sh — outer orchestration loop for a Codex research run.
#
# Each pass: check the stop conditions (API budget fully utilized OR deadline
# hit — nothing else stops the run), launch `codex exec` with a FRESH context
# on the workspace, then run the hybrid verifier (verify.sh) which writes
# VERIFIER_FEEDBACK.md for the next iteration. The agent cannot end the run:
# "done" just means the next iteration starts from a better repo.
#
# Start via launch.sh (writes state/loop_state.json, starts this in tmux).
# Survives iteration crashes and timeouts by design: set -u, never -e.
set -u

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LOOP_DIR
# shellcheck source=env.sh
source "$LOOP_DIR/env.sh"

STATE_DIR="$LOOP_DIR/state"
LOG_DIR="$LOOP_DIR/logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# Single instance only.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE_DIR/loop.lock"
  if ! flock -n 9; then
    echo "run_loop.sh: another loop instance holds $STATE_DIR/loop.lock — exiting." >&2
    exit 1
  fi
else
  echo "run_loop.sh: WARNING — flock unavailable; single-instance lock disabled" >&2
fi

if [ ! -f "$STATE_DIR/loop_state.json" ]; then
  echo "run_loop.sh: no $STATE_DIR/loop_state.json — run launch.sh first." >&2
  exit 1
fi

journal() { # journal <event> <key=val>...
  python3 - "$@" <<'PY' >> "$STATE_DIR/loop_journal.jsonl"
import json, sys
from datetime import datetime, timezone
rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": sys.argv[1]}
for pair in sys.argv[2:]:
    k, _, v = pair.partition("=")
    rec[k] = v
print(json.dumps(rec))
PY
}

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_DIR/loop.log"; }

ITER_FILE="$STATE_DIR/iteration"
[ -f "$ITER_FILE" ] || echo 0 > "$ITER_FILE"
FAST_FAILS=0

say "loop starting (budget \$${API_BUDGET_USD}, stop at ${API_STOP_FRACTION}×; model ${CODEX_MODEL})"
journal loop.start model="$CODEX_MODEL" budget="$API_BUDGET_USD"

while true; do
  # ── Stop conditions: the ONLY way the run ends ─────────────────────────
  STATUS="$(python3 "$LOOP_DIR/bin/loop_status.py" --shell)" || {
    say "loop_status.py failed — retrying in 60s"; sleep 60; continue; }
  eval "$STATUS"

  if [ "$STOP" = "1" ]; then
    say "stop condition hit: $STOP_REASON (spent \$$API_SPENT of \$$API_BUDGET, ${REMAINING_H}h remaining)"
    break
  fi
  # Not enough clock left for a meaningful iteration.
  if [ "$REMAINING_S" -lt 300 ]; then
    say "under 5 minutes to deadline — waiting it out"
    sleep "$REMAINING_S"
    continue
  fi

  N=$(( $(cat "$ITER_FILE") + 1 ))
  echo "$N" > "$ITER_FILE"

  # Iteration wall-clock cap: configured cap, never past the deadline, and a
  # RESEARCH iteration may not straddle deep into the reserved polish window
  # (else one long research block can consume most of it).
  T=$(( ITERATION_TIMEOUT_MIN * 60 ))
  [ "$REMAINING_S" -lt "$T" ] && T="$REMAINING_S"
  if [ "$MODE" = "RESEARCH" ]; then
    PB="$(python3 -c "print(int(float('${FINAL_POLISH_HOURS}')*3600))")"
    MAXR=$(( REMAINING_S - PB ))
    [ "$MAXR" -lt 600 ] && MAXR=600
    [ "$T" -gt "$MAXR" ] && T="$MAXR"
  fi

  # ── Build the iteration prompt (fresh thread + goal anew, every time) ──
  # Each iteration is a new codex thread; a thread carries at most one Goal,
  # so the loop re-establishes the same Goal contract (prompts/goal.md) at the
  # top of every iteration via the create_goal tool.
  GOAL_TEXT="$(cat "$LOOP_DIR/prompts/goal.md")"
  PROMPT="$(python3 "$LOOP_DIR/bin/render_prompt.py" "$LOOP_DIR/prompts/iteration.md" \
    "ITERATION=$N" "MODE=$MODE" "TIME_REMAINING_H=$REMAINING_H" \
    "DEADLINE_ISO=$DEADLINE_ISO" "API_SPENT=$API_SPENT" "API_BUDGET=$API_BUDGET" \
    "API_REMAINING=$API_REMAINING" "API_PCT=$API_PCT" "GOAL=$GOAL_TEXT" \
    "CAP_MIN=$(( T / 60 ))")" || {
      say "prompt render failed — aborting loop (fix prompts/, then rerun launch.sh)"; exit 1; }
  if [ "$MODE" = "POLISH" ]; then
    POLISH="$(python3 "$LOOP_DIR/bin/render_prompt.py" "$LOOP_DIR/prompts/polish.md" \
      "TIME_REMAINING_H=$REMAINING_H")" || { say "polish prompt render failed"; exit 1; }
    PROMPT="$PROMPT

$POLISH"
  fi

  say "iteration $N starting (mode=$MODE, ${REMAINING_H}h left, \$$API_SPENT/\$$API_BUDGET spent [$SPEND_METHOD], cap ${T}s)"
  journal iteration.start iteration="$N" mode="$MODE" remaining_h="$REMAINING_H" api_spent="$API_SPENT"

  ITER_LOG="$LOG_DIR/iter_$(printf '%03d' "$N").log"
  T0=$(date -u +%s)
  # (web search comes from config.toml [tools] web_search=true; the --search
  # flag is position-sensitive across CLI versions and redundant with it.
  # env -u: the org admin key is loop-only metering credential — keep it out
  # of the agent's environment and transcripts.)
  timeout --signal=INT --kill-after=120 "$T" \
    env -u OPENAI_ADMIN_KEY codex exec \
      --dangerously-bypass-approvals-and-sandbox \
      --cd "$WORKSPACE_DIR" \
      -m "$CODEX_MODEL" \
      -c model_reasoning_effort="\"$CODEX_REASONING_EFFORT\"" \
      -o "$LOG_DIR/iter_$(printf '%03d' "$N").last_message.md" \
      "$PROMPT" >> "$ITER_LOG" 2>&1
  RC=$?
  DUR=$(( $(date -u +%s) - T0 ))
  say "iteration $N ended (exit=$RC, ${DUR}s)"
  journal iteration.end iteration="$N" exit_code="$RC" duration_s="$DUR"

  # Index this iteration's Codex rollouts + token usage into the journal (live
  # telemetry — captured before the verifier runs, so its rollout isn't counted
  # against the agent iteration). Non-fatal.
  python3 "$LOOP_DIR/bin/index_iteration.py" "$N" "$MODE" "$RC" "$DUR" "$T0" 2>&1 \
    | while IFS= read -r l; do say "  $l"; done || true

  # Fast-failure backoff (auth/config breakage): never give up before a
  # cutoff — time burning with a broken loop still ends the run on schedule,
  # but back off so we don't burn the journal with a hot spin.
  if [ "$RC" -ne 0 ] && [ "$DUR" -lt 60 ]; then
    FAST_FAILS=$(( FAST_FAILS + 1 ))
    if [ "$FAST_FAILS" -ge "$MAX_FAST_FAILURES" ]; then
      say "iteration $N: $FAST_FAILS consecutive fast failures — backing off 15 min (check $ITER_LOG: auth? config?)"
      journal loop.backoff fails="$FAST_FAILS"
      sleep 900
      FAST_FAILS=0
    else
      sleep $(( FAST_FAILS * 60 ))
    fi
    continue  # skip the verifier: there is nothing new to review
  fi
  FAST_FAILS=0

  # ── Hybrid verifier: mechanical gates + isolated Codex referee ─────────
  "$LOOP_DIR/verify.sh" "$N" "$MODE" >> "$LOG_DIR/verify_$(printf '%03d' "$N").log" 2>&1 \
    || say "verify.sh exited nonzero for iteration $N (see verify log) — continuing"

  sleep "$LOOP_PAUSE_SECONDS"
done

# ── Run over: final mechanical gate sweep + closing journal ──────────────
say "run over — final gate sweep"
GATE_BIN="$LOOP_DIR/bin/gate_artifact.sh"
[ -x "$GATE_BIN" ] || GATE_BIN="./scripts/gate_artifact.sh"
(
  cd "$WORKSPACE_DIR" &&
  REQUIRE_PRESENTATION=1 REQUIRE_README=1 REQUIRE_EXTERNAL_REVIEWS=1 \
    "$GATE_BIN" paper/paper.pdf paper
) > "$STATE_DIR/final_gate.txt" 2>&1
say "final gates: $(grep -c '^gate ok' "$STATE_DIR/final_gate.txt" 2>/dev/null || echo 0) ok, $(grep -c '^GATE FAIL' "$STATE_DIR/final_gate.txt" 2>/dev/null || echo 0) failed (see $STATE_DIR/final_gate.txt)"

say "collecting telemetry bundle (rollouts + logs + git history)..."
"$LOOP_DIR/collect_telemetry.sh" 2>&1 | tee "$STATE_DIR/telemetry_bundle.txt" | while IFS= read -r l; do say "  $l"; done || say "telemetry bundle failed (see $STATE_DIR/telemetry_bundle.txt)"

journal loop.finish reason="${STOP_REASON:-unknown}" iterations="$(cat "$ITER_FILE")"
say "loop finished: $(cat "$ITER_FILE") iterations, reason=${STOP_REASON:-unknown}"
