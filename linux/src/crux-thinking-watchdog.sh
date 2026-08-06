#!/usr/bin/env bash
# crux-thinking-watchdog.sh — auto-recovery for OpenClaw main sessions wedged by
# cascading "Invalid `signature` in `thinking` block" provider errors
# (openclaw/openclaw#44370 / #45010: context rebuild after a yield/compaction
# boundary corrupts replayed thinking blocks; every retry then fails identically
# and the session never recovers without a reset).
#
# Run from cron every 5 minutes as the openclaw user:
#   */5 * * * * /home/ubuntu/.openclaw/watchdog/crux-thinking-watchdog.sh
#
# What it does when the CURRENT main session is wedged:
#   1. Archives the session file (.reset-watchdog.<ts>) — mirrors what /reset does.
#   2. Points sessions.json at a fresh session id (old id kept in usageFamilySessionIds).
#   3. Restarts the gateway and dispatches a recovery turn telling the agent to
#      resume from the state capsule per AGENTS.md § Session startup.
#
# Testing knobs:
#   DRY_RUN=1                 detect + log only, never reset
#   SESSION_FILE_OVERRIDE=... run detection against a specific file (testing)
set -u

STORE="$HOME/.openclaw/agents/main/sessions"
WD="$HOME/.openclaw/watchdog"
LOG="$WD/watchdog.log"
LOCKF="$WD/lock"
COOLDOWN_FILE="$WD/last_reset_epoch"
COOLDOWN_SECS=1800
DAILY_CAP=4
DRY_RUN="${DRY_RUN:-0}"
SESSION_FILE_OVERRIDE="${SESSION_FILE_OVERRIDE:-}"

# Detection pattern: an invalid_request_error about thinking blocks inside an
# errorMessage field on the same stored record. The bug class has multiple API
# message variants — "Invalid `signature` in `thinking` block" AND "`thinking`
# or `redacted_thinking` blocks in the latest assistant message cannot be
# modified" have both been observed — so match the class, not one string. The
# errorMessage + invalid_request_error co-occurrence keeps agent prose that
# merely *mentions* the bug from matching.
ERR_PAT='errorMessage.*invalid_request_error.*(`thinking`|`signature`|redacted_thinking)'

mkdir -p "$WD"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# systemd --user needs the runtime dir when invoked from cron
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export PATH="$HOME/.npm-global/bin:$PATH"

exec 9>"$LOCKF"
flock -n 9 || exit 0

SID=$(jq -r '."agent:main:main".sessionId // empty' "$STORE/sessions.json" 2>/dev/null)
[ -n "$SID" ] || exit 0
F="$STORE/$SID.jsonl"
[ -n "$SESSION_FILE_OVERRIDE" ] && F="$SESSION_FILE_OVERRIDE"
[ -f "$F" ] || exit 0

# Wedged iff: >=2 strict error records AND the newest stopReason record is one
# (a session that errored once but moved on is not wedged).
CNT=$(grep -cE "$ERR_PAT" "$F" 2>/dev/null || true)
[ "${CNT:-0}" -ge 2 ] || exit 0
LAST_ERR=$(grep -nE "$ERR_PAT" "$F" | tail -1 | cut -d: -f1)
LAST_STOP=$(grep -n '"stopReason"' "$F" | tail -1 | cut -d: -f1)
{ [ -z "$LAST_ERR" ] || [ -z "$LAST_STOP" ] || [ "$LAST_ERR" -lt "$LAST_STOP" ]; } && exit 0

log "WEDGE DETECTED session=$SID errors=$CNT lastErrLine=$LAST_ERR file=$F"

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1 — would archive $F and reset agent:main:main"
  exit 0
fi

NOW=$(date +%s)
LAST=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt "$COOLDOWN_SECS" ]; then
  log "within cooldown ($(((NOW - LAST)))s since last reset) — skipping"
  exit 0
fi
TODAY_RESETS=$(grep -c "RESET EXECUTED $(date -u +%F)" "$LOG" 2>/dev/null || true)
if [ "${TODAY_RESETS:-0}" -ge "$DAILY_CAP" ]; then
  log "daily reset cap ($DAILY_CAP) reached — NOT resetting; manual attention needed"
  exit 0
fi
echo "$NOW" > "$COOLDOWN_FILE"

TS=$(date -u +%Y-%m-%dT%H-%M-%S)
NEWID=$(uuidgen | tr '[:upper:]' '[:lower:]')
log "RESET EXECUTED $(date -u +%F) archiving $SID -> reset-watchdog.$TS ; new main session $NEWID"

systemctl --user stop openclaw-gateway.service
mv "$F" "$F.reset-watchdog.$TS"
rm -f "$F.lock"
TMP=$(mktemp)
NOW_MS=$(( $(date +%s) * 1000 ))
jq --arg old "$SID" --arg new "$NEWID" --argjson now "$NOW_MS" '
  ."agent:main:main".sessionId = $new
  | ."agent:main:main".updatedAt = $now
  | ."agent:main:main".sessionStartedAt = $now
  | ."agent:main:main".usageFamilySessionIds =
      ((."agent:main:main".usageFamilySessionIds // []) + [$old] | unique)
' "$STORE/sessions.json" > "$TMP" && mv "$TMP" "$STORE/sessions.json"
systemctl --user start openclaw-gateway.service
sleep 20

openclaw agent --session-key agent:main:main --timeout 900 --message "[Environment watchdog — automated recovery, not an operator instruction] The previous main session hit an unrecoverable provider error (cascading invalid-thinking-signature, a known OpenClaw bug) and was automatically reset; the old transcript is archived in the session store. Re-orient as HEARTBEAT.md prescribes: the task, requirements, and budgets are at the top of AGENTS.md; read PLAN.md (resource ledger, milestones, work in flight) and the tail of LOG.md; check running jobs (runs/*/pid), in-flight subagents, cron list, and any GPU pods. Add a one-line LOG.md note that this reset happened, then take the next action from PLAN.md." >> "$LOG" 2>&1
log "recovery turn dispatched (exit=$?)"
