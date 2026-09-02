#!/usr/bin/env bash
# crux-auth-watchdog.sh — halt-and-notify for OpenClaw main sessions whose
# provider is rejecting EVERY call for a non-transient reason: a revoked or
# rotated API key, a key without permission for the model, an exhausted credit
# balance. Retrying cannot fix this class — only a human with a new key can —
# so the right move is to stop the bleed and page the operator.
#
# Why it exists: on one run the provider key was revoked mid-run and the
# 30-minute heartbeat then failed with `authentication_error` for six days —
# hundreds of dead turns, ~90% of the telemetry volume, and nobody noticed
# because the gateway's own notification path runs through the model that was
# failing. Nothing on the box watched for a provider that says "no" every time.
#
# Run from cron every 5 minutes as the openclaw user:
#   */5 * * * * /home/ubuntu/.openclaw/watchdog/crux-auth-watchdog.sh
#
# Detection (class, not string — see the *_PAT variables below):
#   the newest THRESHOLD assistant records of the CURRENT main session all carry
#   an errorMessage of the auth/permission/quota class, and the newest of them
#   is more recent than the last successful (error-free) assistant record. A
#   single error, rate limits / overloads / timeouts, refusals, or errors
#   interleaved with successes never fire. Only the errorMessage field of
#   assistant records is classified, so agent prose that merely mentions these
#   words cannot match.
#
# What it does when the main session is dead:
#   1. Writes the marker $WD/auth-halt (timestamp + reason + recovery). While
#      the marker exists the watchdog is dormant. The operator removes it after
#      fixing the key — or the watchdog clears it itself once the gateway unit
#      is active AND an error-free assistant record newer than the marker's
#      halted_at has landed (the natural reflex after a key fix is `systemctl
#      --user start` without the rm; a marker left behind would otherwise keep
#      the watchdog dormant for the rest of the run).
#   2. Stops the gateway (systemctl --user stop openclaw-gateway.service) so
#      heartbeats stop burning turns. CLAUDE.md's "never restart the gateway
#      while a turn is in flight" rule guards the abort/resume boundary of a
#      turn doing real work (thinking-block corruption). Under this class every
#      turn fails in ~1.5 s with zero tokens generated and nothing is in
#      flight — the session is already dead, so stopping is safe.
#   3. Notifies the operator directly over the Telegram Bot API with curl
#      (bot token + owner chat id from ~/.openclaw/openclaw.json). The token is
#      handed to curl on stdin (-K -), never on argv, never in the message text,
#      never in the log. A failed send is retried on later runs until it works.
#   4. Logs everything to $WD/auth-watchdog.log.
#
# Recovery (also in the marker and the Telegram message):
#   put the new key in ~/.openclaw/.env, then either re-run `openclaw gateway
#   install` (regenerates ~/.openclaw/gateway.systemd.env from the managed keys
#   — the unit's EnvironmentFile, which is what the running gateway carries) or
#   edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start
#   openclaw-gateway; then rm ~/.openclaw/watchdog/auth-halt.
#   The watchdog re-arms itself: the dead tail left in the session file is the
#   record it already halted on, so it stays silent until a NEW assistant
#   record lands after the halt. If that record is another auth failure it
#   halts and notifies again (after the cooldown); if it is a success the
#   streak is broken and the watchdog goes back to sleep.
#
# Daily cap: at most DAILY_CAP halts per UTC day. Beyond it the gateway is left
#   running (a dead key then burns heartbeats until a human acts), so the
#   operator is paged exactly once per UTC day about that — stamp file
#   $WD/auth-cap-notified.<date>; the stamps are kept as evidence.
#
# Testing knobs:
#   DRY_RUN=1                 detect + log only, never halt or notify
#   SESSION_FILE_OVERRIDE=... run detection against a specific file (testing)
#   AUTH_WATCHDOG_THRESHOLD=N consecutive failures required (default below)
set -u
export LC_ALL=C

STORE="$HOME/.openclaw/agents/main/sessions"
WD="$HOME/.openclaw/watchdog"
LOG="$WD/auth-watchdog.log"
LOCKF="$WD/auth-lock"
MARKER="$WD/auth-halt"
NOTIFY_PENDING="$WD/auth-halt.notify-pending"
HALT_STATE="$WD/last_auth_halt"          # epoch / session / newest record at the last halt
COOLDOWN_SECS=1800
DAILY_CAP=4                               # halts per UTC day; beyond it, page once and stand down
DRY_RUN="${DRY_RUN:-0}"
SESSION_FILE_OVERRIDE="${SESSION_FILE_OVERRIDE:-}"
CONFIG="$HOME/.openclaw/openclaw.json"
GATEWAY_UNIT="openclaw-gateway.service"

# Consecutive failed assistant turns required before firing. The default is the
# {{AUTH_WATCHDOG_THRESHOLD|4}} placeholder, resolved by configure.sh at install
# time. It is kept on its own line because bash does not nest braces inside
# ${var:-...}: "${X:-{{K|4}}}" expands to "7}}" when X=7 (bash 3.2, observed),
# so the one-line form corrupts an operator override whenever the placeholder
# is left unresolved. If it is unresolved, the check below falls back to 4.
THRESHOLD_DEFAULT="{{AUTH_WATCHDOG_THRESHOLD|4}}"
THRESHOLD="${AUTH_WATCHDOG_THRESHOLD:-$THRESHOLD_DEFAULT}"

# Non-transient class: the provider will reject every call until a human acts.
# Split into STRONG markers (an explicit error *type* or key-invalid message —
# these win even when transient words co-occur, e.g. OpenAI's insufficient_quota
# is delivered as HTTP 429) and WEAK markers (bare 401/403 / unauthorized /
# forbidden, which also show up inside retry/overload texts and therefore yield
# to the transient class). Extending either list is a deliberate change.
AUTH_STRONG_PAT='authentication_error|permission_error|invalid[ _-]?x-api-key|api[ _-]?key is invalid|(invalid|incorrect) api[ _-]?key|insufficient_quota|credit balance is too low'
AUTH_WEAK_PAT='(status|http|code)[^0-9a-z]{0,6}40[13]([^0-9]|$)|\b40[13]\b[^a-z0-9]{0,3}(unauthorized|forbidden)|\bunauthorized\b|\bforbidden\b'
# Transient class — retries are the right response, never a halt.
TRANSIENT_PAT='rate_limit|overloaded|too many requests|\b429\b|\b50[0-9]\b|timed? ?out|timeout|econnreset|econnrefused|socket hang up|aborted|temporar'

mkdir -p "$WD"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# systemd --user needs the runtime dir when invoked from cron
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

exec 9>"$LOCKF"
flock -n 9 || exit 0

# Validate silently; the fallback is only mentioned on the line that uses it,
# so a healthy box does not get a warning every five minutes.
THRESHOLD_NOTE=""
case "$THRESHOLD" in
  ''|*[!0-9]*|0) THRESHOLD_NOTE=" (AUTH_WATCHDOG_THRESHOLD='$THRESHOLD' is not a positive integer, placeholder unresolved? — using 4)"; THRESHOLD=4 ;;
esac

# --- Telegram notification (direct Bot API; the gateway cannot deliver) -------
# Returns 0 sent, 1 send failed (retry later), 2 not configured (never retry).
notify() {
  local token chat code
  token=$(jq -r '.channels.telegram.botToken // empty' "$CONFIG" 2>/dev/null)
  chat=$(jq -r '(.commands.ownerAllowFrom // []) as $a
                | ([$a[] | strings | select(startswith("telegram:"))][0] // ([$a[] | strings][0] // ""))
                | ltrimstr("telegram:")' "$CONFIG" 2>/dev/null)
  case "$chat" in ''|*[!0-9-]*) chat="" ;; esac
  if [ -z "$token" ] || [ -z "$chat" ]; then
    log "telegram not configured (channels.telegram.botToken / commands.ownerAllowFrom) — cannot notify directly"
    return 2
  fi
  # The URL (which carries the token) goes in via a curl config on stdin so it
  # is never visible in argv / ps. stderr is dropped: curl error text can echo
  # the request URL.
  code=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
         | curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - \
             --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" 2>/dev/null)
  if [ "$code" = "200" ]; then
    log "telegram notification sent to owner chat (http=200)"
    return 0
  fi
  log "telegram notification FAILED (http=${code:-none}) — will retry on later runs"
  return 1
}

# --- Resolve the CURRENT main session (mirrors the thinking watchdog) ---------
# Sets SID and F; returns 1 when there is no current session file to judge.
resolve_session() {
  SID=$(jq -r '."agent:main:main".sessionId // empty' "$STORE/sessions.json" 2>/dev/null)
  [ -n "$SID" ] || return 1
  F="$STORE/$SID.jsonl"
  [ -n "$SESSION_FILE_OVERRIDE" ] && F="$SESSION_FILE_OVERRIDE"
  [ -f "$F" ] || return 1
  return 0
}

# --- Classify every assistant record; summarise the tail ----------------------
# Emits one line: total|streak|newest_ts|newest_class|newest_key|last_ok_ts|matched
#   total       assistant records in the file
#   streak      consecutive auth-class errors at the end of the file
#   newest_key  identity of the newest assistant record (id, else timestamp)
#   last_ok_ts  newest timestamp among error-free assistant records (any position)
#   matched     the class keyword that matched in the newest record
scan_session() {
  jq -R -r --arg strong "$AUTH_STRONG_PAT" --arg weak "$AUTH_WEAK_PAT" --arg transient "$TRANSIENT_PAT" '
    fromjson? | select(.type == "message" and ((.message | objects | .role) // "") == "assistant")
    | ((.message.errorMessage // "") | tostring) as $e
    | ((.message.stopReason // "") | tostring) as $stop
    | [ ((.timestamp // "") | tostring),
        (if $e != "" then
           (if   ($e | test($strong; "i"))    then "auth"
            elif ($e | test($transient; "i")) then "transient"
            elif ($e | test($weak; "i"))      then "auth"
            else "other" end)
         elif $stop == "error"   then "other"        # errored without a message: not a success
         elif $stop == "aborted" then "transient"    # client-side cancel, not a provider verdict
         else "ok" end),
        ((.id // .timestamp // "") | tostring),
        ($e | [match($strong + "|" + $weak; "i")] | (.[0].string // "") | .[0:60]) ]
    | @tsv' "$1" 2>/dev/null \
  | awk -F'\t' '
      NF >= 3 { n++; ts[n]=$1; cls[n]=$2; key[n]=$3; m[n]=$4
                if ($2 == "ok" && $1 > last_ok) last_ok = $1 }
      END {
        streak = 0
        for (i = n; i >= 1; i--) { if (cls[i] == "auth") streak++; else break }
        printf "%d|%d|%s|%s|%s|%s|%s\n", n, streak, (n ? ts[n] : ""), (n ? cls[n] : ""), (n ? key[n] : ""), last_ok, (n ? m[n] : "")
      }'
}

# --- Dormant while the marker exists ------------------------------------------
# The marker is operator-owned: removing it is the explicit re-arm. It also
# clears itself when the evidence says the key works again — gateway unit
# active AND an error-free assistant record newer than the marker's halted_at —
# because the natural reflex after fixing the key is `systemctl --user start`
# without the rm, and a marker left behind would keep the watchdog dormant for
# the rest of the run (a second revocation would then go unnoticed). A newer
# record that is another failure, no new record, or a stopped gateway keeps
# the marker; a hand-written marker without halted_at is never cleared.
if [ -f "$MARKER" ]; then
  if [ -f "$NOTIFY_PENDING" ] && [ "$DRY_RUN" != "1" ]; then
    notify "$(cat "$NOTIFY_PENDING")"
    rc=$?
    [ "$rc" -eq 1 ] || rm -f "$NOTIFY_PENDING"
  fi
  HALTED_AT=$(sed -n 's/^halted_at=//p' "$MARKER" | head -1)
  HEALTHY=0
  if [ -n "$HALTED_AT" ] && systemctl --user is-active --quiet "$GATEWAY_UNIT" && resolve_session; then
    IFS='|' read -r _ _ _ _ _ LAST_OK_TS _ <<< "$(scan_session "$F")"
    # Compare at whole-second resolution: halted_at is %FT%TZ, record
    # timestamps carry milliseconds. Strictly newer only.
    if [ -n "$LAST_OK_TS" ] && [[ "${LAST_OK_TS:0:19}" > "${HALTED_AT:0:19}" ]]; then HEALTHY=1; fi
  fi
  [ "$HEALTHY" = 1 ] || exit 0
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN=1 — gateway healthy after halt (success at $LAST_OK_TS, halted at $HALTED_AT); would clear $MARKER"
    exit 0
  fi
  log "gateway healthy after halt — clearing marker (success at $LAST_OK_TS, halted at $HALTED_AT, session=$SID); monitoring resumes"
  rm -f "$MARKER" "$HALT_STATE" "$NOTIFY_PENDING"
fi

resolve_session || exit 0

IFS='|' read -r TOTAL STREAK NEWEST_TS NEWEST_CLS NEWEST_KEY LAST_OK_TS MATCHED <<< "$(scan_session "$F")"
[ "${STREAK:-0}" -ge "$THRESHOLD" ] || exit 0
# Recency: the newest failure must postdate the last success. File order already
# implies this; the timestamp check additionally guards against out-of-order
# history rewrites. Records without timestamps fall back to file order.
if [ -n "$NEWEST_TS" ] && [ -n "$LAST_OK_TS" ] && [[ ! "$NEWEST_TS" > "$LAST_OK_TS" ]]; then
  exit 0
fi

# --- Re-arm guard: only records that landed AFTER the last halt count ---------
# After the operator fixes the key, removes the marker and restarts, the dead
# tail is still the newest thing in the file until the next heartbeat runs. The
# halt state remembers which record was newest once the gateway had fully
# stopped; while that is still the newest record there is nothing new to judge,
# so stay silent rather than re-halting on evidence already acted on.
HALT_EPOCH=0; HALT_SID=""; HALT_KEY=""
if [ -f "$HALT_STATE" ]; then
  HALT_EPOCH=$(sed -n 's/^epoch=//p' "$HALT_STATE" | head -1)
  HALT_SID=$(sed -n 's/^session=//p' "$HALT_STATE" | head -1)
  HALT_KEY=$(sed -n 's/^newest_record=//p' "$HALT_STATE" | head -1)
fi
if [ "$HALT_SID" = "$SID" ] && [ -n "$HALT_KEY" ] && [ "$HALT_KEY" = "$NEWEST_KEY" ]; then
  exit 0
fi

log "AUTH FAILURE DETECTED session=$SID streak=$STREAK threshold=$THRESHOLD records=$TOTAL newest=${NEWEST_TS:-?} lastSuccess=${LAST_OK_TS:-none} matched='$MATCHED' file=$F$THRESHOLD_NOTE"

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1 — would stop $GATEWAY_UNIT, write $MARKER and notify the operator"
  exit 0
fi

NOW=$(date +%s)
if [ $((NOW - ${HALT_EPOCH:-0})) -lt "$COOLDOWN_SECS" ]; then
  log "within cooldown ($((NOW - ${HALT_EPOCH:-0}))s since last halt) — skipping"
  exit 0
fi
TODAY=$(date -u +%F)
TODAY_HALTS=$(grep -c "AUTH HALT EXECUTED $TODAY" "$LOG" 2>/dev/null || true)
if [ "${TODAY_HALTS:-0}" -ge "$DAILY_CAP" ]; then
  # Standing down leaves a dead key burning heartbeats for the rest of the UTC
  # day — the very class this script exists for — so page the operator about
  # it exactly once per day. A failed send leaves no stamp and is retried on
  # the next run; "not configured" stamps, since retrying cannot help.
  CAP_STAMP="$WD/auth-cap-notified.$TODAY"
  if [ -f "$CAP_STAMP" ]; then
    log "daily halt cap ($DAILY_CAP) reached — NOT stopping the gateway; operator already paged today ($CAP_STAMP)"
    exit 0
  fi
  log "daily halt cap ($DAILY_CAP) reached — NOT stopping the gateway; paging the operator once for $TODAY"
  notify "[crux auth watchdog] $(hostname 2>/dev/null || echo unknown-host) — daily halt cap ($DAILY_CAP) reached $TODAY; gateway LEFT RUNNING with a dead key
Class: provider still rejecting every call, non-transient auth/permission failure (matched '$MATCHED'); $STREAK consecutive failed assistant turns in main session $SID
Done: nothing — no further automatic halt today; heartbeats keep failing until you intervene
Intervene: systemctl --user stop $GATEWAY_UNIT; put the new key in ~/.openclaw/.env, then either re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start $GATEWAY_UNIT
Log: ~/.openclaw/watchdog/auth-watchdog.log"
  rc=$?
  [ "$rc" -eq 1 ] || : > "$CAP_STAMP"
  exit 0
fi

# --- Halt --------------------------------------------------------------------
TS=$(date -u +%FT%TZ)
log "AUTH HALT EXECUTED $(date -u +%F) session=$SID streak=$STREAK matched='$MATCHED' — stopping $GATEWAY_UNIT"
{
  echo "halted_at=$TS"
  echo "reason=provider rejecting every call, non-transient auth/permission class (matched '$MATCHED'); $STREAK consecutive failed assistant turns; newest=$NEWEST_TS; last success=${LAST_OK_TS:-none}"
  echo "session=$SID"
  echo "action=systemctl --user stop $GATEWAY_UNIT"
  echo "recover=put the new key in ~/.openclaw/.env, then either re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start $GATEWAY_UNIT; then rm $MARKER (the watchdog also clears this marker itself once the gateway is active and a new error-free turn has landed)"
} > "$MARKER"

systemctl --user stop "$GATEWAY_UNIT"
log "gateway stop exit=$?"

# Record the halt AFTER the stop completed: the newest record is now final, so a
# turn that squeezed in while the service was shutting down cannot re-trigger.
IFS='|' read -r _ _ _ _ NEWEST_KEY_AFTER _ _ <<< "$(scan_session "$F")"
{
  echo "epoch=$NOW"
  echo "session=$SID"
  echo "newest_record=${NEWEST_KEY_AFTER:-$NEWEST_KEY}"
  echo "halted_at=$TS"
} > "$HALT_STATE"

MSG="[crux auth watchdog] $(hostname 2>/dev/null || echo unknown-host) — gateway HALTED at $TS
Class: provider rejecting every call, non-transient auth/permission failure (matched '$MATCHED')
Count: $STREAK consecutive failed assistant turns in main session $SID (threshold $THRESHOLD); newest $NEWEST_TS; last success ${LAST_OK_TS:-none}
Done: systemctl --user stop $GATEWAY_UNIT; marker written at ~/.openclaw/watchdog/auth-halt (watchdog dormant while it exists)
Recover: 1) put the new key in ~/.openclaw/.env  2) re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match  3) systemctl --user start $GATEWAY_UNIT  4) rm ~/.openclaw/watchdog/auth-halt (cleared automatically once the gateway is active and a new error-free turn has landed)
Log: ~/.openclaw/watchdog/auth-watchdog.log"

notify "$MSG"
rc=$?
if [ "$rc" -eq 1 ]; then
  printf '%s\n' "$MSG" > "$NOTIFY_PENDING"
  log "notification queued in $NOTIFY_PENDING for retry"
fi
exit 0
