#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# start.sh  –  runs ON the Linux (Ubuntu 22.04) EC2 instance
# ==========================================================================
# Mirrors mac/start.sh: installs a desktop environment, VNC, openclaw,
# monitoring, telemetry, and external service CLIs.
#
# Expected to be run as root (or via sudo) by setup-device.sh.
# ==========================================================================

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

export DEBIAN_FRONTEND=noninteractive

# ====== PLACEHOLDER RESOLUTION ======
# Operator-tunable values travel as {{KEY|default}} tokens. setup-device.sh
# forwards every non-secret config key as PLACEHOLDERS ("K=V|||K=V"). The
# harness workspace, the installed watchdog scripts (which live OUTSIDE the
# workspace) and start.sh's own settings all resolve tokens through the two
# helpers below, in the same order, so one grammar covers all three — a token
# resolved in one place and left literal in another silently becomes a wrong
# default, which is the failure this centralises away.
AGENT_NAME="${AGENT_NAME:-crux}"
OPERATOR_NAME="${OPERATOR_NAME:-operator}"
OPENCLAW_WORKSPACE="$REAL_HOME/.openclaw/workspace"

# placeholder_value '{{KEY|default}}' — print the operator's KEY from the config
# file if one was given, else the token's default. For values start.sh consumes
# itself (jq arguments, crontab cadences) rather than files it edits.
placeholder_value() {
  local token="$1" key default rest pair
  local re='^\{\{([A-Z_][A-Z0-9_]*)(\|(.*))?\}\}$'
  if [[ ! "$token" =~ $re ]]; then
    echo "placeholder_value: not a {{KEY|default}} token: $token" >&2
    return 1
  fi
  key="${BASH_REMATCH[1]}"
  default="${BASH_REMATCH[3]}"
  # Split on the LITERAL '|||' delimiter (IFS='|||' would split on every
  # single '|', silently truncating any value that contains one).
  rest="${PLACEHOLDERS:-}|||"
  while [ -n "$rest" ]; do
    pair="${rest%%|||*}"
    rest="${rest#*|||}"
    [ -z "$pair" ] && continue
    if [ "${pair%%=*}" = "$key" ]; then
      printf '%s' "${pair#*=}"
      return 0
    fi
  done
  printf '%s' "$default"
}

# resolve_placeholders [-q] PATH... — resolve the tokens in files in place:
#   Step 1: operator-supplied values from PLACEHOLDERS (they win);
#   Step 2: environment-derived values (AGENT_NAME, OPERATOR_NAME, ...);
#   Step 3: whatever {{KEY|default}} is left takes its default.
# Directories are walked for *.md, *.sh and *.py; a file named explicitly is
# always processed. '#' is the sed delimiter so '|' inside defaults survives.
# -q suppresses the per-key "Placeholder resolved" lines (second callers).
#
# sed_replacement_escape VALUE — print VALUE escaped for the replacement side of
# a '#'-delimited s### command. Unescaped, '#' ends the expression (sed errors,
# set -e aborts provisioning), '&' re-inserts the match and '\' starts an escape
# (both rewrite the value silently); a newline must be written as '\'+newline.
# Every value substituted below goes through this, so an operator value such as
# a URL with a query string or a '#' in a research question cannot break or
# corrupt the resolution.
sed_replacement_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//&/\\&}"
  v="${v//#/\\#}"
  v="${v//$'\n'/\\$'\n'}"
  printf '%s' "$v"
}
resolve_placeholders() {
  local quiet=0 p f
  if [ "${1:-}" = "-q" ]; then quiet=1; shift; fi
  local files=()
  for p in "$@"; do
    if [ -d "$p" ]; then
      while IFS= read -r -d '' f; do files+=("$f"); done \
        < <(find "$p" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -print0)
    elif [ -f "$p" ]; then
      files+=("$p")
    fi
  done
  [ "${#files[@]}" -gt 0 ] || return 0

  # --- Step 1: Resolve user-supplied placeholders (from the config file) ---
  # These run first so they take priority over built-in defaults. Split on the
  # LITERAL '|||' delimiter (see placeholder_value).
  if [ -n "${PLACEHOLDERS:-}" ]; then
    local rest="${PLACEHOLDERS}|||" pair key value
    while [ -n "$rest" ]; do
      pair="${rest%%|||*}"
      rest="${rest#*|||}"
      [ -z "$pair" ] && continue
      key="${pair%%=*}"
      value="${pair#*=}"
      # The key is the pattern side of the sed: only a placeholder_value-shaped
      # identifier can ever match a token, so anything else is a malformed
      # config line — say so and skip it rather than feed it to sed.
      if [[ ! "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        echo "⚠ Placeholder key '${key}' is not an identifier ([A-Z_][A-Z0-9_]*) — skipped"
        continue
      fi
      # Replace both {{KEY}} and {{KEY|default}} forms
      sed -i \
        -e "s#{{${key}}}#$(sed_replacement_escape "$value")#g" \
        -e "s#{{${key}|[^}]*}}#$(sed_replacement_escape "$value")#g" \
        "${files[@]}"
      [ "$quiet" = 1 ] || echo "✔ Placeholder resolved: ${key}=${value}"
    done
  fi

  # --- Step 2: Resolve environment-derived placeholders ---
  sed -i \
    -e "s#{{AGENT_NAME}}#$(sed_replacement_escape "$AGENT_NAME")#g" \
    -e "s#{{OPERATOR_NAME}}#$(sed_replacement_escape "$OPERATOR_NAME")#g" \
    -e "s#{{WORKSPACE_PATH}}#$(sed_replacement_escape "$OPENCLAW_WORKSPACE")#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 22.04 EC2, amd64#g" \
    -e "s#{{COST_TRACKER_URL}}#$(sed_replacement_escape "${COST_TRACKER_URL:-}")#g" \
    -e "s#{{API_KEY_SUFFIX}}#$(sed_replacement_escape "${API_KEY_SUFFIX:-}")#g" \
    "${files[@]}"

  # --- Step 3: Auto-populate remaining {{KEY|default}} with their defaults ---
  # Any placeholder with a pipe-delimited default that wasn't resolved above
  # gets replaced with its default value (the part after the |).
  sed -i -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g' "${files[@]}"
}


# ====== DEPENDENCIES ======
apt-get update -y
apt-get upgrade -y
apt-get install -y \
  build-essential curl git wget unzip jq \
  software-properties-common apt-transport-https

# ====== GUI DESKTOP ======
# Install a lightweight XFCE desktop and TigerVNC so the instance has a GUI
apt-get install -y xfce4 xfce4-goodies dbus-x11 x11-xserver-utils
apt-get install -y tigervnc-standalone-server tigervnc-common

# Set a default login password for the user (for VNC desktop / sudo prompts)
echo "$REAL_USER:cruxbox1" | chpasswd

# Configure VNC for the real user
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.vnc"

# Set a default VNC password (change after first login)
echo "cruxbox1" | sudo -u "$REAL_USER" vncpasswd -f > "$REAL_HOME/.vnc/passwd"
chmod 600 "$REAL_HOME/.vnc/passwd"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.vnc/passwd"

# VNC xstartup – launch XFCE when a VNC session starts
cat > "$REAL_HOME/.vnc/xstartup" <<'XSTARTUP'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
exec startxfce4
XSTARTUP
chmod +x "$REAL_HOME/.vnc/xstartup"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.vnc/xstartup"

# Create a wrapper script that starts Xtigervnc + XFCE properly
cat > /usr/local/bin/crux-vnc-start <<'WRAPPER'
#!/bin/bash
# Start the raw Xtigervnc server (stays in foreground) and launch XFCE on it.
DISPLAY_NUM="${1:-1}"
PORT=$((5900 + DISPLAY_NUM))

# Launch XFCE once the X server is ready (backgrounded)
(
  sleep 2
  export DISPLAY=":${DISPLAY_NUM}"
  /home/ubuntu/.vnc/xstartup
) &

exec /usr/bin/Xtigervnc ":${DISPLAY_NUM}" \
  -geometry 1920x1080 -depth 24 \
  -rfbauth /home/ubuntu/.vnc/passwd \
  -rfbport "$PORT" \
  -pn -localhost=0
WRAPPER
chmod +x /usr/local/bin/crux-vnc-start

# Create a systemd service so VNC survives reboots
cat > /etc/systemd/system/vncserver@.service <<'VNCUNIT'
[Unit]
Description=TigerVNC server on display %i
After=syslog.target network.target

[Service]
Type=simple
User=ubuntu
PAMName=login
ExecStartPre=-/usr/bin/vncserver -kill :%i > /dev/null 2>&1
ExecStart=/usr/local/bin/crux-vnc-start %i
ExecStop=-/usr/bin/vncserver -kill :%i
Restart=on-failure

[Install]
WantedBy=multi-user.target
VNCUNIT

systemctl daemon-reload
systemctl enable --now vncserver@1


# ====== WALLPAPER ======
# Set a solid-color wallpaper so the monitoring script doesn't get confused
# (mirrors the mac behavior of setting a static wallpaper)
WALLPAPER_COLOR="#FFD700"
sudo -u "$REAL_USER" bash -c "
  mkdir -p $REAL_HOME/.config/xfce4/xfconf/xfce-perchannel-xml
  cat > $REAL_HOME/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml <<XFCEWALL
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<channel name=\"xfce4-desktop\" version=\"1.0\">
  <property name=\"backdrop\" type=\"empty\">
    <property name=\"screen0\" type=\"empty\">
      <property name=\"monitorVNC-0\" type=\"empty\">
        <property name=\"workspace0\" type=\"empty\">
          <property name=\"color-style\" type=\"int\" value=\"0\"/>
          <property name=\"rgba1\" type=\"array\">
            <value type=\"double\" value=\"1.0\"/>
            <value type=\"double\" value=\"0.843\"/>
            <value type=\"double\" value=\"0.0\"/>
            <value type=\"double\" value=\"1.0\"/>
          </property>
          <property name=\"image-style\" type=\"int\" value=\"0\"/>
        </property>
      </property>
    </property>
  </property>
</channel>
XFCEWALL
"



# ====== OPENCLAW ======
# install openclaw (no onboarding)
sudo -u "$REAL_USER" bash -c \
  'curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard'
# The install is unpinned — record the version, because every config key written
# further down is ignored silently if its name drifted in a newer release.
OPENCLAW_INSTALLED_VERSION=$(sudo -u "$REAL_USER" bash -lc 'openclaw --version' 2>/dev/null | head -1 || echo unknown)
echo "✔ OpenClaw installed: ${OPENCLAW_INSTALLED_VERSION:-unknown} (unpinned — record this; the config keys below are version-sensitive)"

# Copy exec-approvals config (unrestricted access for the agent)
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"
cp "$SCRIPT_DIR/exec-approvals.json" "$REAL_HOME/.openclaw/exec-approvals.json"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.openclaw/exec-approvals.json"


# # ====== MONITORING ======
# # Commented out — screenshots + workspace backups were filling up the disk.
# cp "$SCRIPT_DIR/monitor.sh" "$REAL_HOME/monitor.sh"
# chown "$REAL_USER:$REAL_USER" "$REAL_HOME/monitor.sh"
# chmod +x "$REAL_HOME/monitor.sh"
#
# # Install scrot for screenshots (Linux equivalent of macOS screencapture)
# apt-get install -y scrot
#
# sudo -u "$REAL_USER" bash -c \
#   "nohup $REAL_HOME/monitor.sh > /dev/null 2>&1 &"


# ====== TELEMETRY ======
# The whole telemetry plugin is installed by git checkout: the box clones the
# upstream repo, detaches at the PINNED commit below, and builds it. Nothing
# about the plugin is vendored in this harness; the openclaw.json plugins block
# is built inline in the TELEMETRY CONFIG step below (it only needs the two
# host-side switches plus the box-specific path and rotation — everything else
# is the plugin's own default). The fixes the harness depends on (a real
# not-started fallback, redaction on by default, agent context deltas instead of
# the full history every turn, llm.usage from the internal diagnostic bus) live
# in the pinned commit. The pin is applied BEFORE the build and a commit that is
# not in the upstream clone aborts provisioning instead of shipping HEAD.
TELEMETRY_REPO_URL="https://github.com/schwartzadev/openclaw-telemetry-hal"
TELEMETRY_REPO_DIR="$REAL_HOME/openclaw-telemetry-hal"
# Merge of schwartzadev/openclaw-telemetry-hal#1 on main.
TELEMETRY_UPSTREAM_COMMIT="dc51714fa4cd1e8ec5b34e08d2a044a36941e200"
case "$TELEMETRY_UPSTREAM_COMMIT" in
  *[!0-9a-f]*|'') echo "✘ telemetry-hal: TELEMETRY_UPSTREAM_COMMIT must hold one git sha, got '$TELEMETRY_UPSTREAM_COMMIT'" >&2; exit 1 ;;
esac

# Clone (or reuse) the upstream repo and detach at the pin. Runs as the real user
# (the repo is theirs). --force on the checkout discards any local drift so
# re-running start.sh lands cleanly on the pinned commit.
if ! sudo -u "$REAL_USER" env \
       TELEMETRY_REPO_URL="$TELEMETRY_REPO_URL" \
       TELEMETRY_REPO_DIR="$TELEMETRY_REPO_DIR" \
       TELEMETRY_UPSTREAM_COMMIT="$TELEMETRY_UPSTREAM_COMMIT" \
       bash -s <<'TELEMETRY_PIN'
set -eu
# A pre-existing directory that is not a git checkout (a box restored from a
# tarball, a hand-copied plugin) cannot be pinned and would make the clone fail
# with "destination path already exists" — move it aside, keep it, clone fresh.
if [ -e "$TELEMETRY_REPO_DIR" ] && [ ! -d "$TELEMETRY_REPO_DIR/.git" ]; then
  moved="$TELEMETRY_REPO_DIR.pre-pin.$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$TELEMETRY_REPO_DIR" "$moved"
  echo "⚠ telemetry-hal: $TELEMETRY_REPO_DIR existed without a .git (hand-copied or restored, not a pinnable checkout) — moved aside to $moved; cloning fresh"
fi
if [ ! -d "$TELEMETRY_REPO_DIR/.git" ]; then
  git clone --quiet "$TELEMETRY_REPO_URL" "$TELEMETRY_REPO_DIR"
fi
cd "$TELEMETRY_REPO_DIR"
git fetch --quiet origin || echo "⚠ telemetry-hal: git fetch failed — using the commits already cloned"
if ! git checkout --quiet --force "$TELEMETRY_UPSTREAM_COMMIT"; then
  echo "✘ telemetry-hal: commit $TELEMETRY_UPSTREAM_COMMIT is not in the upstream clone — TELEMETRY_UPSTREAM_COMMIT in start.sh is stale or the fork moved; re-pin deliberately, do not fall back to HEAD" >&2
  exit 1
fi
echo "✔ telemetry-hal pinned at $TELEMETRY_UPSTREAM_COMMIT"
TELEMETRY_PIN
then
  echo "✘ telemetry-hal: pin step failed — provisioning aborted (see the ✘ line above)" >&2
  exit 1
fi

sudo -u "$REAL_USER" bash -c "
  cd $REAL_HOME
  curl -fsSL https://get.pnpm.io/install.sh | sh -
  export PNPM_HOME=\"$REAL_HOME/.local/share/pnpm\"
  export PATH=\"\$PNPM_HOME:\$PNPM_HOME/bin:\$PATH\"
  cd $TELEMETRY_REPO_DIR
  pnpm install
  pnpm run build
  $REAL_HOME/.npm-global/bin/openclaw plugins install --link .
"
# The build must postdate the sources (tsc emits dist/index.js and dist/src/*.js
# from index.ts and src/*.ts): a stale or missing dist would load an older build
# with no error anywhere.
for src in index.ts src/service.ts; do
  built="$TELEMETRY_REPO_DIR/dist/${src%.ts}.js"
  if [ ! -f "$built" ] || [ ! "$built" -nt "$TELEMETRY_REPO_DIR/$src" ]; then
    echo "✘ telemetry-hal: $built is missing or older than $src — the build did not run on the pinned sources" >&2
    exit 1
  fi
done
echo "✔ telemetry-hal built from the pinned sources and linked into openclaw"

# Patch the telemetry plugin manifest to ensure activation on startup
# (upstream repo may be missing the activation block, which leaves the
# plugin registered but dormant — OpenClaw's lazy loader never fires it)
TELEMETRY_MANIFEST="$REAL_HOME/openclaw-telemetry-hal/openclaw.plugin.json"
if [ -f "$TELEMETRY_MANIFEST" ] && command -v jq &>/dev/null; then
  HAS_ACTIVATION=$(jq 'has("activation")' "$TELEMETRY_MANIFEST" 2>/dev/null)
  if [ "$HAS_ACTIVATION" != "true" ]; then
    TMP_MANIFEST=$(mktemp)
    jq '
      .name = "OpenClaw Telemetry for HAL" |
      .description = "Captures tool calls, LLM usage, and message events to JSONL" |
      .activation = { "onStartup": true }
    ' "$TELEMETRY_MANIFEST" > "$TMP_MANIFEST" \
      && mv "$TMP_MANIFEST" "$TELEMETRY_MANIFEST"
    chown "$REAL_USER:$REAL_USER" "$TELEMETRY_MANIFEST"
    echo "✔ Patched telemetry plugin manifest with activation.onStartup"
  else
    echo "✔ Telemetry plugin manifest already has activation block"
  fi
fi
# ====== TELEGRAM ======
# If a Telegram bot token was provided, configure it in openclaw.json
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  OPENCLAW_CONFIG="$REAL_HOME/.openclaw/openclaw.json"
  sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"

  # Create the config file if it doesn't exist yet
  if [ ! -f "$OPENCLAW_CONFIG" ]; then
    echo '{}' > "$OPENCLAW_CONFIG"
    chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
  fi

  # Merge the Telegram channel config into the existing config
  TMP_CONFIG=$(mktemp)
  jq --arg token "$TELEGRAM_BOT_TOKEN" '
    .channels.telegram.enabled = true |
    .channels.telegram.botToken = $token |
    .channels.telegram.dmPolicy = "pairing" |
    .channels.telegram.groups."*".requireMention = true
  ' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
    && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
  echo "✔ Telegram bot token configured in openclaw.json"
fi

# If a Telegram owner ID was provided, set commands.ownerAllowFrom in openclaw.json
if [ -n "${TELEGRAM_OWNER_ID:-}" ]; then
  OPENCLAW_CONFIG="$REAL_HOME/.openclaw/openclaw.json"
  sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"

  if [ ! -f "$OPENCLAW_CONFIG" ]; then
    echo '{}' > "$OPENCLAW_CONFIG"
    chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
  fi

  TMP_CONFIG=$(mktemp)
  jq --arg owner "telegram:$TELEGRAM_OWNER_ID" '
    .commands.ownerAllowFrom = [$owner]
  ' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
    && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
  echo "✔ commands.ownerAllowFrom configured: telegram:$TELEGRAM_OWNER_ID"
fi

sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway restart || true

# ====== AI Provider and Model ======
# Requires ANTHROPIC_MODEL and ANTHROPIC_API_KEY to be set as env vars
# (passed in by setup-device.sh).

if [ -z "${ANTHROPIC_MODEL:-}" ]; then
  echo "Error: ANTHROPIC_MODEL is required (e.g. anthropic/claude-opus-4-6)" >&2
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY is required" >&2
  exit 1
fi

# Set agents.defaults.model.primary in openclaw.json
OPENCLAW_CONFIG="$REAL_HOME/.openclaw/openclaw.json"
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"

if [ ! -f "$OPENCLAW_CONFIG" ]; then
  echo '{}' > "$OPENCLAW_CONFIG"
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
fi

# ⚠️ EXTENDED THINKING — enables extended thinking (verify the key against your
# pinned OpenClaw version). Sets a global default thinking level from
# REASONING_EFFORT (default "xhigh"). The key below is the one OpenClaw exposes for
# a global default thinking level (.agents.defaults.thinkingDefault, values
# off|minimal|low|medium|high|xhigh|adaptive|max). VERIFY this key name against
# YOUR pinned OpenClaw version: an unknown key is simply ignored, so thinking just
# stays OFF until corrected — it will NOT break the run. (Unknown config keys
# are ignored fail-soft; the same applies to every key written below.)
THINKING_LEVEL="${REASONING_EFFORT:-xhigh}"

# Prompt cache retention. "long" maps to Anthropic's 1h cache TTL (vs the 5-min
# default). The heartbeat cadence is 15m, so turns are routinely spaced past the
# 5-min TTL — without this, the workspace prompt is a full cache MISS every
# turn; "long" keeps it warm across the gaps → cache-read pricing.
CACHE_RETENTION="${CACHE_RETENTION:-long}"

# Per-request LLM idle watchdog. OpenClaw's default is 120s
# (DEFAULT_LLM_IDLE_TIMEOUT_MS) — too short for heavy reasoning (xhigh/max), whose
# pre-stream thinking pause can exceed 2 min and trip a "model idle timeout".
# models.providers.<id>.timeoutSeconds is the knob that RAISES the watchdog
# (agents.defaults.timeoutSeconds only bounds it DOWN; its 48-min default is fine).
# 600s covers xhigh; raise toward ~900+ if you switch the default to max. Provider
# id = the part before the "/".
PROVIDER_ID="${ANTHROPIC_MODEL%%/*}"
PROVIDER_REQUEST_TIMEOUT_SECONDS="${PROVIDER_REQUEST_TIMEOUT_SECONDS:-600}"

# Whole-turn ceiling. OpenClaw's default is too short for xhigh research turns
# (deep thinking + long tool loops), which can otherwise hit "Request timed out
# before a response was generated... increase agents.defaults.timeoutSeconds".
# 3600s = 1h per turn; the 15m heartbeat sweeper bounds the cost of a runaway.
AGENT_TURN_TIMEOUT_SECONDS="${AGENT_TURN_TIMEOUT_SECONDS:-3600}"

TMP_CONFIG=$(mktemp)
jq --arg model "$ANTHROPIC_MODEL" --arg reasoning "$THINKING_LEVEL" \
   --arg cache "$CACHE_RETENTION" --arg provider "$PROVIDER_ID" \
   --argjson ptimeout "$PROVIDER_REQUEST_TIMEOUT_SECONDS" '
  .agents.defaults.model.primary = $model |
  .agents.defaults.thinkingDefault = $reasoning |
  .agents.defaults.params.cacheRetention = $cache |
  .models.providers[$provider].timeoutSeconds = $ptimeout |
  .tools.profile = "full" |
  .agents.defaults.heartbeat.every = "15m" |
  .agents.defaults.heartbeat.skipWhenBusy = true |
  .agents.defaults.heartbeat.target = "none" |
  .agents.defaults.subagents.maxConcurrent = 8
' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
  && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
echo "✔ Model configured: $ANTHROPIC_MODEL"
echo "→ wrote thinkingDefault=$THINKING_LEVEL — NOT verified: unknown keys are ignored silently. VERIFY at launch that the first responses actually show extended thinking (gateway log / response blocks); a silently-off thinking level changes the run's construct."
echo "→ wrote cacheRetention=$CACHE_RETENTION — NOT verified: if ignored, 15m-spaced turns pay full cache-miss pricing; check early spend against expectations."
echo "✔ Provider request timeout: ${PROVIDER_REQUEST_TIMEOUT_SECONDS}s for '$PROVIDER_ID' (raises the 120s idle watchdog for heavy reasoning)"
echo "✔ Tools profile set to full"
echo "✔ Heartbeat configured: 15m, skipWhenBusy, target=none"
echo "✔ Subagents maxConcurrent: 8 (native sessions_spawn — width for parallel exploration; delegated work runs through the gateway: its TOOL CALLS land in telemetry, its reasoning and output live only in the session store — scripts/session_costs.py on the box, utils/export-run.sh after the run)"

# ====== TELEMETRY CONFIG ======
# `openclaw plugins install --link .` (TELEMETRY above) writes only
# plugins.load.paths and plugins.entries.telemetry-hal.enabled. That flag makes
# the gateway LOAD the plugin (hooks fire); the plugin's service reads
# plugins.entries.telemetry-hal.config.enabled and returns without starting when
# it is absent — two different switches. With only the first set, the service
# never starts: no rotation, no integrity, no llm.usage from the diagnostic bus,
# and hook events go only to the stamped+redacted fallback file (the pinned build
# makes that a real fallback; an unconfigured box still leaves a trail and says
# so in the log). One run went a week with the service off. agent_end / llm_input
# / llm_output are "conversation" hooks: the gateway
# refuses them for non-bundled plugins unless hooks.allowConversationAccess=true
# sits at the ENTRY level (next to config, not inside it — inside config it
# fails schema validation and disables the whole plugin). The block is built
# inline below — it only needs the two host-side switches (enabled +
# hooks.allowConversationAccess), config.enabled, and the box-specific file path
# and rotation. redact.patterns / redact.enabled are the plugin's own defaults
# (redaction is on by default once the service starts), so they are not restated
# here. plugins.load.paths is left exactly as the install wrote it.
TELEMETRY_LOG_DIR="$REAL_HOME/.openclaw/logs"
TELEMETRY_FILE="$TELEMETRY_LOG_DIR/telemetry.jsonl"
TELEMETRY_ROTATE_MAX_BYTES=$(placeholder_value '{{TELEMETRY_ROTATE_MAX_BYTES|104857600}}')
TELEMETRY_ROTATE_MAX_FILES=$(placeholder_value '{{TELEMETRY_ROTATE_MAX_FILES|50}}')
case "$TELEMETRY_ROTATE_MAX_BYTES" in
  ''|*[!0-9]*|0) echo "⚠ TELEMETRY_ROTATE_MAX_BYTES='$TELEMETRY_ROTATE_MAX_BYTES' is not a positive integer — using 104857600"; TELEMETRY_ROTATE_MAX_BYTES=104857600 ;;
esac
case "$TELEMETRY_ROTATE_MAX_FILES" in
  ''|*[!0-9]*|0) echo "⚠ TELEMETRY_ROTATE_MAX_FILES='$TELEMETRY_ROTATE_MAX_FILES' is not a positive integer — using 50"; TELEMETRY_ROTATE_MAX_FILES=50 ;;
esac
sudo -u "$REAL_USER" mkdir -p "$TELEMETRY_LOG_DIR"
TMP_CONFIG=$(mktemp)
jq --arg path "$TELEMETRY_FILE" \
   --argjson maxbytes "$TELEMETRY_ROTATE_MAX_BYTES" \
   --argjson maxfiles "$TELEMETRY_ROTATE_MAX_FILES" '
  ({
    enabled: true,
    config: {
      enabled: true,
      filePath: $path,
      rotate: { enabled: true, maxSizeBytes: $maxbytes, maxFiles: $maxfiles, compress: true },
      integrity: { enabled: false },
      rateLimit: { enabled: false }
    },
    hooks: { allowConversationAccess: true }
  }) as $block |
  .plugins.entries["telemetry-hal"] =
    ((.plugins.entries["telemetry-hal"] // {}) * $block)
' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
  && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
TELEMETRY_CONFIG_OK=$(jq -r '.plugins.entries["telemetry-hal"] | (.enabled == true and .config.enabled == true and .hooks.allowConversationAccess == true)' "$OPENCLAW_CONFIG")
if [ "$TELEMETRY_CONFIG_OK" != "true" ]; then
  echo "✘ telemetry-hal: openclaw.json does not carry enabled + config.enabled + hooks.allowConversationAccess after the merge — inspect $OPENCLAW_CONFIG" >&2
  exit 1
fi
echo "✔ Telemetry plugin configured: config.enabled=true, redaction on, rotation ${TELEMETRY_ROTATE_MAX_BYTES} B × ${TELEMETRY_ROTATE_MAX_FILES} files, hooks.allowConversationAccess=true → $TELEMETRY_FILE"
echo "→ VERIFY after the gateway starts (linux/status.sh runs these): journalctl --user -u openclaw-gateway | grep -E 'telemetry:|blocked' must show 'telemetry: $TELEMETRY_FILE', 'telemetry: rotation enabled', 'telemetry: redaction enabled', 'llm.usage from the internal diagnostic bus' and NO 'typed hook \"agent_end\" blocked'; after the first heartbeat, head -1 $TELEMETRY_FILE | jq '.seq,.ts' must be non-null and no line may carry fallback:true. Rotated files are ${TELEMETRY_FILE}.N.gz — collect telemetry.jsonl*, not just the live file."

# Append ANTHROPIC_API_KEY to ~/.openclaw/.env for daemon/gateway use
OPENCLAW_ENV="$REAL_HOME/.openclaw/.env"
# Remove any existing ANTHROPIC_API_KEY line to avoid duplicates
if [ -f "$OPENCLAW_ENV" ]; then
  sed -i '/^ANTHROPIC_API_KEY=/d' "$OPENCLAW_ENV"
fi
echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" >> "$OPENCLAW_ENV"
# Run-start date for spend metering: telemetry_costs.py sums the costs API from
# this day forward, so the canonical spend number covers the WHOLE run (a
# today-only default would reset to $0 every midnight). Day-bucketed caveat:
# any same-day prior spend on this key before provisioning is included — use a
# fresh key per run.
sed -i '/^COST_START_DATE=/d' "$OPENCLAW_ENV"
echo "COST_START_DATE=$(date -u +%F)" >> "$OPENCLAW_ENV"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"
echo "✔ Anthropic API key + COST_START_DATE=$(date -u +%F) written to ~/.openclaw/.env"

# Append the agent's tool-call API keys (optional). Unlike ANTHROPIC_API_KEY
# (used by the gateway's model calls), these are consumed by the agent's bash
# tool calls — RunPod GPU pods, the refine.ink review API, and the experiments'
# OpenRouter LLM calls — so they must
# reach the tool environment (the gateway loads ~/.openclaw/.env into its
# process env, which tool subprocesses inherit).
for kv in "RUNPOD_API_KEY=${RUNPOD_API_KEY:-}" "REFINE_INK_API_KEY=${REFINE_INK_API_KEY:-}" "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}"; do
  name="${kv%%=*}"; val="${kv#*=}"
  if [ -z "$val" ]; then
    echo "⚠ ${name} not provided — skipping (agent tools needing it will fail until it's added to ~/.openclaw/.env)"
    continue
  fi
  sed -i "/^${name}=/d" "$OPENCLAW_ENV"
  echo "${name}=${val}" >> "$OPENCLAW_ENV"
  echo "✔ ${name} written to ~/.openclaw/.env"
done
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"

# ====== gog (Google Workspace CLI) auth ======
# gog auth is REQUIRED. The pre-authorized bundle (from utils/bootstrap-gog.sh)
# was scp'd by setup-device.sh into the real user's home; unpack it into a fixed
# GOG_HOME and wire the file-keyring env into ~/.openclaw/.env so gog is
# authenticated with no browser. Missing inputs are a hard error (set -e aborts).
if [ -z "${GOG_KEYRING_PASSWORD:-}" ]; then
  echo "Error: GOG_KEYRING_PASSWORD is required but not set (passed by setup-device.sh from the config file)." >&2
  exit 1
fi

# setup-device.sh passes "$HOME/gog-home.tar.gz"; resolve it under the real home.
GOG_TARBALL_PATH="${REAL_HOME}/gog-home.tar.gz"
if [ ! -f "$GOG_TARBALL_PATH" ]; then
  echo "Error: gog auth bundle not found at $GOG_TARBALL_PATH — expected setup-device.sh to scp it (create it with utils/bootstrap-gog.sh)." >&2
  exit 1
fi

GOG_HOME_DIR="$REAL_HOME/.openclaw/gogcli"
sudo -u "$REAL_USER" mkdir -p "$GOG_HOME_DIR"
chmod 700 "$GOG_HOME_DIR"
sudo -u "$REAL_USER" tar xzf "$GOG_TARBALL_PATH" -C "$GOG_HOME_DIR"
chown -R "$REAL_USER:$REAL_USER" "$GOG_HOME_DIR"
rm -f "$GOG_TARBALL_PATH"

# Wire the keyring env so every gog invocation (incl. agent tool subprocesses
# that inherit the gateway env) can decrypt the refresh token non-interactively.
for kv in \
  "GOG_HOME=${GOG_HOME_DIR}" \
  "GOG_KEYRING_BACKEND=file" \
  "GOG_KEYRING_PASSWORD=${GOG_KEYRING_PASSWORD}" \
  "GOG_ACCOUNT=${GOG_ACCOUNT:-}"; do
  name="${kv%%=*}"; val="${kv#*=}"
  sed -i "/^${name}=/d" "$OPENCLAW_ENV"
  echo "${name}=${val}" >> "$OPENCLAW_ENV"
done
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"
echo "✔ gog auth configured (GOG_HOME=$GOG_HOME_DIR, account=${GOG_ACCOUNT:-unset})"

# ====== SERVICES ======

# install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
unzip -qo /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

# install gogcli (Google Workspace CLI) from the GitHub release.
# Homebrew isn't available on this box, so we pull the prebuilt linux binary.
# Pin via GOGCLI_VERSION (default tracks a known-good tag).
# v0.28.0+ honors GOG_HOME (older versions like 0.9.0 ignore it and write to
# the platform default config dir, which breaks the portable-bundle approach).
GOGCLI_VERSION="${GOGCLI_VERSION:-v0.28.0}"
GOGCLI_ARCH="$(dpkg --print-architecture)"   # amd64 or arm64
GOGCLI_TARBALL="gogcli_${GOGCLI_VERSION#v}_linux_${GOGCLI_ARCH}.tar.gz"
GOGCLI_URL="https://github.com/openclaw/gogcli/releases/download/${GOGCLI_VERSION}/${GOGCLI_TARBALL}"
if curl -fsSL "$GOGCLI_URL" -o /tmp/gogcli.tar.gz; then
  tar xzf /tmp/gogcli.tar.gz -C /tmp gog 2>/dev/null || tar xzf /tmp/gogcli.tar.gz -C /tmp
  install -m 0755 /tmp/gog /usr/local/bin/gog 2>/dev/null \
    || { find /tmp -maxdepth 2 -name gog -type f -exec install -m 0755 {} /usr/local/bin/gog \; ; }
  rm -f /tmp/gogcli.tar.gz /tmp/gog
  if command -v gog >/dev/null 2>&1; then
    echo "✔ gogcli installed: $(gog --version 2>&1 | head -1)"
  else
    echo "⚠ gogcli install ran but 'gog' is not on PATH — check $GOGCLI_URL"
  fi
else
  echo "⚠ Could not download gogcli from $GOGCLI_URL — gog will be unavailable (set GOGCLI_VERSION to a valid release tag)."
fi


# set up gateway
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway install
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway restart

# ======

# ====== git (local commits, no remote) ======
# The agent keeps its work under local git version control (small, frequent
# commits) as the run's own history. No remote is configured and no host
# credentials are provisioned — nothing is pushed anywhere. Plain `git` is
# installed with the base packages above; identity for commits is set here so
# the agent's commits are attributable in the local log.
sudo -u "$REAL_USER" git config --global user.name "crux-agent"
sudo -u "$REAL_USER" git config --global user.email "crux-agent@localhost"
sudo -u "$REAL_USER" git config --global init.defaultBranch main
echo "✔ git configured for local commits (no remote, no credentials)"

# Set up gog
# see: https://gogcli.sh/quickstart.html

# ====== HARNESS WORKSPACE ======
# Copy the next-run-harness workspace into the agent's OpenClaw workspace
# and resolve placeholders that are known at provisioning time.
HARNESS_SRC="$REAL_HOME/crux-in-a-box-harness/workspace"
# OPENCLAW_WORKSPACE is set in PLACEHOLDER RESOLUTION (top of file).

if [ -d "$HARNESS_SRC" ]; then
  sudo -u "$REAL_USER" mkdir -p "$OPENCLAW_WORKSPACE"
  sudo -u "$REAL_USER" cp -r "$HARNESS_SRC"/* "$OPENCLAW_WORKSPACE/"
  chown -R "$REAL_USER:$REAL_USER" "$OPENCLAW_WORKSPACE"

  # Make scripts executable
  chmod +x "$OPENCLAW_WORKSPACE/scripts/"*.sh 2>/dev/null || true

  # Resolve {{KEY}} / {{KEY|default}} tokens in every *.md, *.sh, *.py of the
  # workspace — Step 1 operator values, Step 2 environment-derived values,
  # Step 3 defaults (resolve_placeholders, PLACEHOLDER RESOLUTION above).
  resolve_placeholders "$OPENCLAW_WORKSPACE"
  echo "✔ Environment placeholders resolved (AGENT_NAME=$AGENT_NAME, OPERATOR_NAME=$OPERATOR_NAME)"
  echo "✔ Remaining defaults auto-populated"

  echo "✔ Harness workspace copied to $OPENCLAW_WORKSPACE"

  # Report any remaining unresolved placeholders (those without defaults)
  REMAINING=$(grep -rn '{{' "$OPENCLAW_WORKSPACE" --include='*.md' --include='*.sh' --include='*.py' 2>/dev/null | grep -v 'grep for {{' | head -20 || true)
  if [ -n "$REMAINING" ]; then
    echo ""
    echo "⚠ Unresolved placeholders (resolve manually or via the config file before launch):"
    echo "$REMAINING"
  fi
else
  echo "⚠ Harness workspace not found at $HARNESS_SRC — skipping workspace setup"
fi

# ====== WATCHDOGS ======
# Box-side cron jobs under ~/.openclaw/watchdog/ — mechanical halves the agent
# never sees (linux/src/crux-*.sh; each header explains its failure class):
#
#   crux-thinking-watchdog.sh  (*/5)   Auto-recovers the main session when it
#     wedges on cascading "Invalid `signature` in `thinking` block" provider
#     errors — a known OpenClaw bug (openclaw/openclaw#44370, #45010) with no
#     upstream fix as of 2026.6.11. Detection is strict (errorMessage records
#     only, newest stopReason must be an error) with a 30-min cooldown and a
#     daily reset cap.
#
#   crux-auth-watchdog.sh      (*/5)   Halt-and-notify when the provider rejects
#     EVERY call for a non-transient reason (revoked/rotated key, model
#     permission, exhausted credit): {{AUTH_WATCHDOG_THRESHOLD|4}} consecutive
#     auth-class failed turns → stop the gateway, write
#     ~/.openclaw/watchdog/auth-halt, page the operator over Telegram. Retrying
#     cannot fix that class; one run burned six days of dead heartbeats before
#     anyone noticed. Dormant while the marker exists. Recovery: put the new
#     key in ~/.openclaw/.env, then either re-run `openclaw gateway install`
#     (regenerates ~/.openclaw/gateway.systemd.env from the managed keys —
#     the unit's EnvironmentFile, which is what the running gateway carries)
#     or edit ~/.openclaw/gateway.systemd.env to match; `systemctl --user
#     start openclaw-gateway`; then `rm ~/.openclaw/watchdog/auth-halt`.
#     Revoke keys AFTER stopping the gateway, not before.
#
#   crux-session-snapshot.sh   (*/{{SESSION_SNAPSHOT_MINUTES|10}})   Copies the
#     session store into ~/.openclaw/session-snapshots/ so cron transcripts
#     (deleted by the gateway when the job next runs) and orphaned main
#     generations survive for utils/export-run.sh. The store, not the telemetry
#     plugin, is the run's record (per-call usage and cost, thinking, tool args).
WATCHDOG_DIR="$REAL_HOME/.openclaw/watchdog"
sudo -u "$REAL_USER" mkdir -p "$WATCHDOG_DIR"
for wd in crux-thinking-watchdog crux-auth-watchdog crux-session-snapshot; do
  cp "$SCRIPT_DIR/$wd.sh" "$WATCHDOG_DIR/$wd.sh"
  chown "$REAL_USER:$REAL_USER" "$WATCHDOG_DIR/$wd.sh"
  chmod +x "$WATCHDOG_DIR/$wd.sh"
done

# The watchdog dir is outside the workspace, so the workspace pass never touches
# it; resolve the installed copies with the same three passes (operator value →
# environment → default). A literal {{...}} in an installed script would fall
# back to a hard-coded default at best and break at worst.
resolve_placeholders -q "$WATCHDOG_DIR"
REMAINING_WD=$(grep -ln '{{' "$WATCHDOG_DIR"/*.sh 2>/dev/null || true)
if [ -n "$REMAINING_WD" ]; then
  echo "⚠ Unresolved placeholders in installed watchdog scripts (a token without a default?): $REMAINING_WD"
fi
AUTH_WATCHDOG_THRESHOLD=$(placeholder_value '{{AUTH_WATCHDOG_THRESHOLD|4}}')
SESSION_SNAPSHOT_MINUTES=$(placeholder_value '{{SESSION_SNAPSHOT_MINUTES|10}}')
case "$SESSION_SNAPSHOT_MINUTES" in
  ''|*[!0-9]*|0) echo "⚠ SESSION_SNAPSHOT_MINUTES='$SESSION_SNAPSHOT_MINUTES' is not a positive integer — using 10"; SESSION_SNAPSHOT_MINUTES=10 ;;
esac
if [ "$SESSION_SNAPSHOT_MINUTES" -gt 59 ]; then
  echo "⚠ SESSION_SNAPSHOT_MINUTES=$SESSION_SNAPSHOT_MINUTES exceeds a cron */N minute field (max 59) — using 10"
  SESSION_SNAPSHOT_MINUTES=10
fi
# Snapshot target exists from minute zero so status.sh and export-run.sh find it
# before the first cron tick.
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw/session-snapshots"
chmod 700 "$REAL_HOME/.openclaw/session-snapshots"

# Crontab entries (idempotent: drop any previous line for the job, re-add).
sudo -u "$REAL_USER" bash -c \
  '(crontab -l 2>/dev/null | grep -v crux-thinking-watchdog; echo "*/5 * * * * $HOME/.openclaw/watchdog/crux-thinking-watchdog.sh") | crontab -'
sudo -u "$REAL_USER" bash -c \
  '(crontab -l 2>/dev/null | grep -v crux-auth-watchdog; echo "*/5 * * * * $HOME/.openclaw/watchdog/crux-auth-watchdog.sh") | crontab -'
sudo -u "$REAL_USER" bash -c \
  "(crontab -l 2>/dev/null | grep -v crux-session-snapshot; echo \"*/${SESSION_SNAPSHOT_MINUTES} * * * * \$HOME/.openclaw/watchdog/crux-session-snapshot.sh\") | crontab -"

# ====== FINAL-PASS INJECTOR ======
# Auto-dispatches the standing final-pass instruction when the agent writes
# COMPLETION_REPORT.md at the workspace root (AGENTS.md requirement 9). The
# message body is staged here from FINAL_PASS.md (single source of truth);
# manual operator send remains the fallback (OPERATOR_GUIDE.md).
FINAL_PASS_DIR="$REAL_HOME/.openclaw/final_pass"
sudo -u "$REAL_USER" mkdir -p "$FINAL_PASS_DIR"
FINAL_PASS_SRC="$REAL_HOME/crux-in-a-box-harness/FINAL_PASS.md"
if [ -f "$FINAL_PASS_SRC" ]; then
  # Stage the message body: everything below the first '---' rule (the part
  # above it is operator-facing documentation, not part of the message).
  awk 'flag{print} /^---$/{flag=1}' "$FINAL_PASS_SRC" > "$FINAL_PASS_DIR/message.md"
  chown "$REAL_USER:$REAL_USER" "$FINAL_PASS_DIR/message.md"
  echo "✔ Final-pass message staged from FINAL_PASS.md"
else
  echo "⚠ FINAL_PASS.md not found at $FINAL_PASS_SRC — the injector will log an error and the operator must send the final pass manually"
fi
cp "$SCRIPT_DIR/final-pass-injector.sh" "$FINAL_PASS_DIR/final-pass-injector.sh"
chown "$REAL_USER:$REAL_USER" "$FINAL_PASS_DIR/final-pass-injector.sh"
chmod +x "$FINAL_PASS_DIR/final-pass-injector.sh"
sudo -u "$REAL_USER" bash -c \
  '(crontab -l 2>/dev/null | grep -v final-pass-injector; echo "*/5 * * * * $HOME/.openclaw/final_pass/final-pass-injector.sh") | crontab -'
echo "✔ Final-pass injector installed (cron */5 — triggers on workspace COMPLETION_REPORT.md)"
echo "✔ Thinking-signature watchdog installed (cron */5 min)"
echo "✔ Auth watchdog installed (cron */5 min; ${AUTH_WATCHDOG_THRESHOLD} consecutive auth-class failed turns → gateway stopped + Telegram page; recovery: put the new key in ~/.openclaw/.env, then either re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start openclaw-gateway; then rm ~/.openclaw/watchdog/auth-halt)"
echo "✔ Session-store snapshot installed (cron */${SESSION_SNAPSHOT_MINUTES} min → ~/.openclaw/session-snapshots/; utils/export-run.sh merges the copies at end of run)"

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."

