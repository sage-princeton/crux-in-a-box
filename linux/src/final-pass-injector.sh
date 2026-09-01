#!/usr/bin/env bash
# final-pass-injector.sh — auto-dispatch the final-pass instruction.
#
# The harness's endgame: when the agent finishes, it writes its completion
# report to COMPLETION_REPORT.md at the workspace root (AGENTS.md requirement
# 9) and sends it to the operator. This cron watches for that file and injects
# the standing final-pass instruction (staged at provision time from
# FINAL_PASS.md's body) into the main session as a user message — exactly
# once. Manual send by the operator remains the fallback path
# (OPERATOR_GUIDE.md § The final pass).
#
# Run from cron every 5 minutes as the openclaw user:
#   */5 * * * * $HOME/.openclaw/final_pass/final-pass-injector.sh
#
# Testing knobs:
#   DRY_RUN=1                 detect + log only, never inject
#   WORKSPACE_OVERRIDE=...    point at a test workspace
set -u

WORKSPACE="${WORKSPACE_OVERRIDE:-$HOME/.openclaw/workspace}"
FP="$HOME/.openclaw/final_pass"
REPORT="$WORKSPACE/COMPLETION_REPORT.md"
MESSAGE="$FP/message.md"
MARKER="$FP/sent"
LOG="$FP/injector.log"
LOCKF="$FP/lock"
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$FP"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# systemd --user needs the runtime dir when invoked from cron
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export PATH="$HOME/.npm-global/bin:$PATH"

exec 9>"$LOCKF"
flock -n 9 || exit 0

[ -f "$MARKER" ] && exit 0
[ -s "$REPORT" ] || exit 0
if [ ! -s "$MESSAGE" ]; then
  log "ERROR: completion report present but no staged message at $MESSAGE — send FINAL_PASS.md manually"
  exit 0
fi

log "completion report detected at $REPORT — dispatching the final-pass instruction"
if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1 — would inject"
  exit 0
fi

# Same injection mechanism as the session watchdog: a queued user-message turn
# through the gateway. No gateway restart — never disturb an in-flight turn.
if openclaw agent --session-key agent:main:main --timeout 900 \
     --message "$(cat "$MESSAGE")" >> "$LOG" 2>&1; then
  date -u +%FT%TZ > "$MARKER"
  log "final-pass instruction dispatched; marker written"
else
  log "dispatch FAILED (gateway down or busy) — will retry next tick"
fi
