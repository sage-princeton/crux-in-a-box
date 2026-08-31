#!/usr/bin/env bash
# ==========================================================================
# status.sh  –  quick health-check for a CRUX-in-a-box Linux instance
# ==========================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { printf "${GREEN}✔${NC} %s\n" "$*"; }
fail() { printf "${RED}✘${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }

echo "====== CRUX-in-a-box  ·  Linux status ======"
echo ""

# ----- VNC -----
if systemctl is-active --quiet vncserver@1; then
  pass "VNC server is running"
else
  fail "VNC server is NOT running"
fi

# ----- GitHub CLI -----
if command -v gh &>/dev/null; then
  # start.sh persists gh creds to ~/.config/gh AND writes GH_TOKEN to
  # ~/.openclaw/.env; pick up the latter as a fallback for tool-env parity.
  GH_TOK=$(grep -E '^GH_TOKEN=' "$HOME/.openclaw/.env" 2>/dev/null | head -1 | cut -d= -f2-)
  if gh auth status &>/dev/null || GH_TOKEN="$GH_TOK" gh auth status &>/dev/null; then
    pass "gh CLI: authenticated"
  else
    warn "gh CLI: installed but NOT authenticated (check GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN)"
  fi
else
  fail "gh CLI: not installed"
fi

# ----- AWS CLI -----
if command -v aws &>/dev/null; then
  if aws sts get-caller-identity &>/dev/null; then
    pass "aws CLI: authenticated"
  else
    warn "aws CLI: installed but NOT authenticated"
  fi
else
  fail "aws CLI: not installed"
fi

# ----- gogcli -----
if command -v gog &>/dev/null; then
  # The keyring env (GOG_HOME / GOG_KEYRING_BACKEND / GOG_KEYRING_PASSWORD /
  # GOG_ACCOUNT) is written to ~/.openclaw/.env by start.sh, not the shell —
  # load it so gog can decrypt the file keyring non-interactively.
  OPENCLAW_ENV="$HOME/.openclaw/.env"
  GOG_ENV=()
  if [ -f "$OPENCLAW_ENV" ]; then
    while IFS= read -r kv; do GOG_ENV+=("$kv"); done < <(grep -E '^GOG_' "$OPENCLAW_ENV" 2>/dev/null)
  fi
  # Green ONLY if we can actually read the inbox end-to-end: this hits the Gmail
  # API (decrypt keyring -> refresh token -> access token -> messages.list),
  # which is the real capability the agent depends on. A bare token check can
  # pass while the inbox is still unreadable (wrong scope, account, etc.).
  if [ "${#GOG_ENV[@]}" -eq 0 ]; then
    warn "gog CLI: installed but no GOG_* env in ~/.openclaw/.env — not configured"
  elif env "${GOG_ENV[@]}" gog gmail search 'in:inbox' --max 1 --json &>/dev/null; then
    pass "gog CLI: inbox readable"
  else
    warn "gog CLI: installed but inbox NOT readable (auth/scope/account issue — run: gog gmail search 'in:inbox' --max 1)"
  fi
else
  fail "gog CLI: not installed"
fi

# ----- openclaw -----
if command -v openclaw &>/dev/null; then
  pass "openclaw: installed"
else
  fail "openclaw: not installed"
fi

# ----- Telegram bot token -----
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
if [ -f "$OPENCLAW_CONFIG" ] && command -v jq &>/dev/null; then
  BOT_TOKEN=$(jq -r '.channels.telegram.botToken // empty' "$OPENCLAW_CONFIG" 2>/dev/null)
  if [ -n "$BOT_TOKEN" ]; then
    pass "Telegram bot token: configured"
  else
    fail "Telegram bot token: NOT configured in openclaw.json"
  fi
else
  fail "Telegram bot token: openclaw.json not found or jq missing"
fi

# ----- Telegram pairing -----
TELEGRAM_ALLOW="$HOME/.openclaw/credentials/telegram-allowFrom.json"
if [ -f "$TELEGRAM_ALLOW" ]; then
  PAIRED_COUNT=$(jq 'length' "$TELEGRAM_ALLOW" 2>/dev/null || echo "0")
  if [ "$PAIRED_COUNT" -gt 0 ] 2>/dev/null; then
    pass "Telegram pairing: $PAIRED_COUNT sender(s) approved"
  else
    warn "Telegram pairing: allowlist exists but empty — DM the bot and run: openclaw pairing approve telegram <CODE>"
  fi
else
  warn "Telegram pairing: no senders paired yet — DM the bot and run: openclaw pairing approve telegram <CODE>"
fi

# ----- Telegram pairing (config) -----
if [ -f "$OPENCLAW_CONFIG" ] && command -v jq &>/dev/null; then
  OWNER_ALLOW_FROM=$(jq -r '.commands.ownerAllowFrom // empty' "$OPENCLAW_CONFIG" 2>/dev/null)
  OWNER_ALLOW_COUNT=$(jq -r '.commands.ownerAllowFrom | if type == "array" then length else 0 end' "$OPENCLAW_CONFIG" 2>/dev/null || echo "0")
  if [ "$OWNER_ALLOW_COUNT" -gt 0 ] 2>/dev/null; then
    pass "Telegram pairing (config): complete ($OWNER_ALLOW_COUNT owner(s) in ownerAllowFrom)"
  else
    warn "Telegram pairing (config): ownerAllowFrom is empty or missing — pairing not yet complete"
  fi
else
  fail "Telegram pairing (config): openclaw.json not found or jq missing"
fi

# ----- Gateway (systemd --user) -----
# systemctl/journalctl --user need the runtime dir; an ssh login normally has it,
# a bare `bash status.sh` from cron or sudo may not.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
GW_UNIT="openclaw-gateway.service"
if systemctl --user is-active --quiet "$GW_UNIT" 2>/dev/null; then
  pass "Gateway: $GW_UNIT active"
else
  warn "Gateway: $GW_UNIT is NOT active (systemctl --user status $GW_UNIT; if ~/.openclaw/watchdog/auth-halt exists, see the AUTH HALT line below)"
fi

# ----- Telemetry plugin -----
# The plugin has two switches and both must be on: plugins.entries.telemetry-hal
# .enabled LOADS it (hooks fire); .config.enabled STARTS the service (seq/ts,
# redaction, rotation, llm.usage). With only the first, the raw fallback writer
# logs the whole context every turn, unredacted — one run went a week like that.
# hooks.allowConversationAccess must sit at the ENTRY level, or the gateway
# refuses agent_end/llm_input/llm_output ("typed hook ... blocked" in the journal).
TELEMETRY_FILE="$HOME/.openclaw/logs/telemetry.jsonl"
if [ -d "$HOME/openclaw-telemetry-hal" ]; then
  pass "Telemetry plugin repo present"
else
  fail "Telemetry plugin repo NOT found"
fi
if [ -f "$OPENCLAW_CONFIG" ] && command -v jq &>/dev/null; then
  TEL_ENTRY_ON=$(jq -r '.plugins.entries["telemetry-hal"].enabled // false' "$OPENCLAW_CONFIG" 2>/dev/null)
  TEL_SERVICE_ON=$(jq -r '.plugins.entries["telemetry-hal"].config.enabled // false' "$OPENCLAW_CONFIG" 2>/dev/null)
  TEL_HOOKS_ON=$(jq -r '.plugins.entries["telemetry-hal"].hooks.allowConversationAccess // false' "$OPENCLAW_CONFIG" 2>/dev/null)
  if [ "$TEL_ENTRY_ON" = "true" ] && [ "$TEL_SERVICE_ON" = "true" ]; then
    pass "Telemetry config: entry enabled + config.enabled=true (service starts)"
  else
    fail "Telemetry config: plugins.entries.telemetry-hal enabled=$TEL_ENTRY_ON config.enabled=$TEL_SERVICE_ON — both must be true (start.sh TELEMETRY CONFIG block); without config.enabled the service never starts"
  fi
  if [ "$TEL_HOOKS_ON" = "true" ]; then
    pass "Telemetry hooks: allowConversationAccess=true at the entry level (agent_end/llm_input/llm_output allowed)"
  else
    fail "Telemetry hooks: plugins.entries.telemetry-hal.hooks.allowConversationAccess is not true — must sit at the ENTRY level (next to config, not inside it)"
  fi
else
  fail "Telemetry config: openclaw.json not found or jq missing"
fi

# Journal since the LAST gateway start: the unit's invocation id scopes it
# exactly (an earlier start's 'blocked' line must not count against a fixed
# config, and an earlier 'telemetry:' line must not vouch for a broken one).
GW_INVOCATION=$(systemctl --user show -p InvocationID --value "$GW_UNIT" 2>/dev/null)
if [ -n "$GW_INVOCATION" ]; then
  GW_JOURNAL=$(journalctl --user "_SYSTEMD_INVOCATION_ID=$GW_INVOCATION" --no-pager -o cat 2>/dev/null)
else
  GW_JOURNAL=$(journalctl --user -u "$GW_UNIT" --no-pager -o cat 2>/dev/null)
fi
if [ -z "$GW_JOURNAL" ]; then
  warn "Telemetry journal: no gateway journal readable (unit never started, or journalctl --user unavailable here) — cannot confirm the service started"
else
  TEL_JOURNAL_PATH=$(printf '%s\n' "$GW_JOURNAL" | grep -o -E 'telemetry: /[^ "]+' | tail -1)
  if [ -n "$TEL_JOURNAL_PATH" ]; then
    pass "Telemetry service: started since the last gateway start (journal: $TEL_JOURNAL_PATH)"
  else
    fail "Telemetry service: no 'telemetry: <path>' line in the gateway journal since the last start — the service did not start (config.enabled missing? plugin not loaded? stale build?)"
  fi
  BLOCKED_HOOKS=$(printf '%s\n' "$GW_JOURNAL" | grep -o -E 'typed hook "[a-z_]+" blocked' | sort -u | tr '\n' ' ')
  if [ -n "$BLOCKED_HOOKS" ]; then
    fail "Telemetry hooks: gateway journal shows ${BLOCKED_HOOKS}— allowConversationAccess not honoured (entry level, not inside config)"
  else
    pass "Telemetry hooks: no 'typed hook ... blocked' line since the last gateway start"
  fi
  if printf '%s\n' "$GW_JOURNAL" | grep -q 'telemetry: redaction DISABLED'; then
    warn "Telemetry redaction: DISABLED by config.redact.enabled=false — telemetry.jsonl* holds raw tool arguments and message bodies; scrub before it leaves the box"
  elif [ -n "$TEL_JOURNAL_PATH" ] && ! printf '%s\n' "$GW_JOURNAL" | grep -q 'telemetry: redaction enabled'; then
    warn "Telemetry redaction: service started but no 'telemetry: redaction enabled' line — plugin build predates the patch?"
  fi
  if printf '%s\n' "$GW_JOURNAL" | grep -q 'llm.usage from the internal diagnostic bus'; then
    pass "Telemetry llm.usage: internal diagnostic bus (trusted model.usage with costUsd)"
  else
    warn "Telemetry llm.usage: journal does not name the internal diagnostic bus — usage is coming from the llm_output fallback (no costUsd) or not at all; check the plugin patch / SDK subpath"
  fi
fi

# The file itself: service-written lines carry seq + ts; fallback-written lines
# carry neither (and fallback:true after the patch) and are unredacted.
if [ ! -e "$TELEMETRY_FILE" ]; then
  warn "Telemetry file: $TELEMETRY_FILE not written yet (no event since the gateway started?) — re-check after the first heartbeat"
elif [ ! -s "$TELEMETRY_FILE" ]; then
  warn "Telemetry file: $TELEMETRY_FILE is empty yet — re-check after the first heartbeat"
else
  TEL_STAMPED=$(head -1 "$TELEMETRY_FILE" | jq -r '(.seq != null) and (.ts != null)' 2>/dev/null)
  TEL_LINES=$(wc -l < "$TELEMETRY_FILE" | tr -d ' ')
  TEL_ROTATED=$(ls "$TELEMETRY_FILE".* 2>/dev/null | wc -l | tr -d ' ')
  if [ "$TEL_STAMPED" = "true" ]; then
    pass "Telemetry file: first line carries seq + ts (service-stamped) — $TEL_LINES line(s) live, $TEL_ROTATED rotated file(s); collect telemetry.jsonl*"
  else
    fail "Telemetry file: first line of $TELEMETRY_FILE has no seq/ts — written by the raw fallback, not the service (unredacted; fix the config and restart the gateway)"
  fi
  TEL_FALLBACK_N=$(grep -c '"fallback":true' "$TELEMETRY_FILE" 2>/dev/null)
  if [ "${TEL_FALLBACK_N:-0}" -gt 0 ] 2>/dev/null; then
    warn "Telemetry file: $TEL_FALLBACK_N fallback:true line(s) — events written while the service was not started (unredacted by construction)"
  fi
fi

# ----- Watchdog crons -----
# crux-thinking-watchdog (wedged-session reset), crux-auth-watchdog (provider
# says no to every call → halt + page), crux-session-snapshot (store copies so
# deleted cron transcripts survive). All installed by start.sh WATCHDOGS.
CRON_LINES=$(crontab -l 2>/dev/null)
for job in crux-thinking-watchdog crux-auth-watchdog crux-session-snapshot; do
  CRON_LINE=$(printf '%s\n' "$CRON_LINES" | grep -m1 "$job")
  if [ -n "$CRON_LINE" ]; then
    pass "cron: $job installed (schedule: ${CRON_LINE%% /*})"
  else
    fail "cron: $job NOT in crontab (start.sh WATCHDOGS block did not run?)"
  fi
done

# ----- Session snapshots -----
SNAP_DIR="$HOME/.openclaw/session-snapshots"
if [ -d "$SNAP_DIR" ]; then
  SNAP_N=$(find "$SNAP_DIR" -maxdepth 1 -type f ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
  pass "Session snapshots: $SNAP_DIR present ($SNAP_N file(s); fills on the first cron tick)"
else
  fail "Session snapshots: $SNAP_DIR missing — cron transcripts are lost when their job next runs; run ~/.openclaw/watchdog/crux-session-snapshot.sh once"
fi

# ----- Auth watchdog halt marker -----
AUTH_HALT="$HOME/.openclaw/watchdog/auth-halt"
if [ -f "$AUTH_HALT" ]; then
  warn "AUTH HALT: $AUTH_HALT exists — the auth watchdog stopped the gateway because the provider rejected every call (revoked key / permission / credit). $(head -2 "$AUTH_HALT" | tr '\n' ' ')"
  warn "AUTH HALT recovery: put the new key in ~/.openclaw/.env, then either re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start $GW_UNIT; then rm $AUTH_HALT (the watchdog is dormant while the marker exists; log: ~/.openclaw/watchdog/auth-watchdog.log)"
else
  pass "Auth watchdog: no halt marker"
fi

# # ----- monitor.sh -----
# # Commented out — monitoring was filling up the disk.
# if pgrep -f "monitor.sh" > /dev/null; then
#   pass "monitor.sh is running ($(pgrep -cf 'monitor.sh') process(es))"
# else
#   fail "monitor.sh is NOT running"
# fi

# # ----- Monitor output -----
# MONITOR_DIR="$HOME/monitor_output"
# if [ -d "$MONITOR_DIR" ]; then
#   SCREENSHOTS=$(find "$MONITOR_DIR" -name 'screen_*.png' 2>/dev/null | wc -l)
#   BACKUPS=$(find "$MONITOR_DIR" -name 'backup_*.tar.gz' 2>/dev/null | wc -l)
#   HEALTH_FILE="$HOME/last_update.txt"
#   if [ -f "$HEALTH_FILE" ]; then
#     LAST_UPDATE=$(cat "$HEALTH_FILE")
#     pass "Monitor output: $SCREENSHOTS screenshots, $BACKUPS backups (last update: $LAST_UPDATE)"
#   else
#     warn "Monitor output: $SCREENSHOTS screenshots, $BACKUPS backups (no health file yet)"
#   fi
# else
#   warn "Monitor output directory not found yet"
# fi

echo ""
echo "============================================="
