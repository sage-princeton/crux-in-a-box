#!/usr/bin/env bash
# verify.sh <iteration> <mode> — the hybrid verifier, run after every iteration.
#
# Two halves, per the harness design grammar:
#   Mechanical — budget/clock numbers (loop_status.py) and the gate script's
#     greps over the deliverable, plus commit/LOG.md hygiene checks. No
#     judgment, no model, can't be argued with.
#   Judgment — an ISOLATED Codex referee (fresh context, read-only sandbox,
#     prompt the agent cannot author) that grades the work like a NeurIPS
#     reviewer and names the next iteration's priorities.
#
# Output: workspace/VERIFIER_FEEDBACK.md (overwritten each round) — the first
# thing the next iteration reads. The feedback never offers "stop" as an
# option; the loop, not the agent and not the verifier, owns termination.
set -u

N="${1:?usage: verify.sh <iteration> <mode>}"
MODE="${2:-RESEARCH}"

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LOOP_DIR
# shellcheck source=env.sh
source "$LOOP_DIR/env.sh"

FEEDBACK="$WORKSPACE_DIR/VERIFIER_FEEDBACK.md"
RAW="$LOOP_DIR/logs/verifier_$(printf '%03d' "$N").raw.md"

# loop_status also emits MODE (recomputed); the mode the iteration actually
# ran under is authoritative here, so restore the argument after the eval.
eval "$(python3 "$LOOP_DIR/bin/loop_status.py" --shell)"
MODE="${2:-$MODE}"

# ── Mechanical half ───────────────────────────────────────────────────────
# Run the LOOP's canonical gate copy (loop/bin/), not the agent-writable
# workspace copy — editing scripts/gate_artifact.sh must not change what the
# verifier measures. Fall back to the workspace copy only if missing.
GATE_BIN="$LOOP_DIR/bin/gate_artifact.sh"
[ -x "$GATE_BIN" ] || GATE_BIN="./scripts/gate_artifact.sh"
GATE_OUT="$(
  cd "$WORKSPACE_DIR" && \
  if [ "$MODE" = "POLISH" ]; then
    REQUIRE_PRESENTATION=1 REQUIRE_README=1 REQUIRE_EXTERNAL_REVIEWS=1 "$GATE_BIN" paper/paper.pdf paper 2>&1
  else
    "$GATE_BIN" paper/paper.pdf paper 2>&1
  fi
)"
# Pre-draft, the pdf gate failing is expected — annotate so it doesn't read as
# pressure to draft prematurely (the scouting mandate outranks it).
if [ "$MODE" = "RESEARCH" ] && [ ! -f "$WORKSPACE_DIR/paper/paper.pdf" ]; then
  GATE_OUT="note: no draft PDF yet — the pdf-exists failure below is expected pre-draft; do not start drafting just to silence it.
$GATE_OUT"
fi

LAST_COMMIT_AGE_MIN="n/a"
if git -C "$WORKSPACE_DIR" rev-parse HEAD >/dev/null 2>&1; then
  LAST_COMMIT_AGE_MIN=$(( ( $(date -u +%s) - $(git -C "$WORKSPACE_DIR" log -1 --format=%ct) ) / 60 ))
fi
LAST_LOG_ENTRY="$(grep '^## ' "$WORKSPACE_DIR/LOG.md" 2>/dev/null | tail -1)"
DIRTY_FILES=$(git -C "$WORKSPACE_DIR" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
EXT_REVIEWS="$(ls -t "$WORKSPACE_DIR/reviews/external/" 2>/dev/null | head -5 | tr '\n' ' ')"
PLAN_LINE="missing — the hour-0 resource plan (AGENTS.md § Resources) has not been written"
if [ -f "$WORKSPACE_DIR/PLAN.md" ]; then
  PLAN_MTIME="$(stat -c %Y "$WORKSPACE_DIR/PLAN.md" 2>/dev/null || stat -f %m "$WORKSPACE_DIR/PLAN.md" 2>/dev/null)"
  if [ -n "$PLAN_MTIME" ]; then
    PLAN_LINE="last touched $(( ( $(date -u +%s) - PLAN_MTIME ) / 60 )) min ago"
  else
    PLAN_LINE="present"
  fi
fi

MECH_NOTES=""
if [ "$LAST_COMMIT_AGE_MIN" != "n/a" ] && [ "$LAST_COMMIT_AGE_MIN" -gt $(( ITERATION_TIMEOUT_MIN + 30 )) ]; then
  MECH_NOTES="$MECH_NOTES
- **No commit from the last iteration** (latest commit is ${LAST_COMMIT_AGE_MIN} min old). The hand-off contract in AGENTS.md was not honored — commit first thing this iteration."
fi
if [ "$DIRTY_FILES" -gt 0 ]; then
  MECH_NOTES="$MECH_NOTES
- ${DIRTY_FILES} uncommitted change(s) in the working tree — commit or discard deliberately before new work."
fi

# ── Judgment half: isolated read-only referee ────────────────────────────
VPROMPT="$(python3 "$LOOP_DIR/bin/render_prompt.py" "$LOOP_DIR/prompts/verifier.md" \
  "MODE=$MODE" "TIME_REMAINING_H=$REMAINING_H" "API_REMAINING=$API_REMAINING" \
  "API_SPENT=$API_SPENT" "API_BUDGET=$API_BUDGET")" || VPROMPT=""

REFEREE="_Referee unavailable this round (prompt render failed). The previous round's priorities stand; re-read the last feedback in git history._"
if [ -n "$VPROMPT" ]; then
  # env -u: the org admin key is the loop's metering credential — the referee
  # doesn't need it. project_doc_max_bytes=0: don't auto-ingest the
  # agent-writable AGENTS.md as referee instructions (the rubric lives in
  # verifier.md); the referee still reads repo files as data via tools.
  if timeout --signal=INT --kill-after=60 $(( VERIFIER_TIMEOUT_MIN * 60 )) \
    env -u OPENAI_ADMIN_KEY codex exec \
      --sandbox read-only \
      --cd "$WORKSPACE_DIR" \
      -m "$CODEX_MODEL" \
      -c model_reasoning_effort="\"$VERIFIER_REASONING_EFFORT\"" \
      -c project_doc_max_bytes=0 \
      -o "$RAW" \
      "$VPROMPT" >/dev/null 2>&1 && [ -s "$RAW" ]; then
    REFEREE="$(cat "$RAW")"
  else
    REFEREE="_Referee run failed or timed out this round. The previous round's priorities stand; the mechanical results above are current._"
  fi
fi

# ── Assemble the feedback file ────────────────────────────────────────────
cat > "$FEEDBACK" <<EOF
# VERIFIER_FEEDBACK.md — after iteration $N
_Written by the outer loop. Overwritten every round; prior rounds are in git history. Read this before doing anything else._

**The run continues.** ${REMAINING_H}h remain (deadline ${DEADLINE_ISO}); \$${API_SPENT} of \$${API_BUDGET} API budget spent (\$${API_REMAINING} remaining, meter: ${SPEND_METHOD}). Mode: **${MODE}**. The loop ends the run only when the budget is fully utilized or the clock runs out — "finished" is not a state you can reach by declaring it. Convert the remainder into evidence.

## Mechanical checks
- Last commit: ${LAST_COMMIT_AGE_MIN} min ago · uncommitted files: ${DIRTY_FILES}
- Last LOG.md entry: ${LAST_LOG_ENTRY:-none found}
- Resource plan (PLAN.md): ${PLAN_LINE}
- External reviews on file (reviews/external/): ${EXT_REVIEWS:-none}${MECH_NOTES}

### Gate script (scripts/gate_artifact.sh)
\`\`\`
${GATE_OUT}
\`\`\`

## Referee report (isolated, read-only — did not write this work)
${REFEREE}
EOF

echo "verify.sh: wrote $FEEDBACK (iteration $N, mode $MODE)"
