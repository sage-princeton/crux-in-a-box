#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# start.sh  –  runs ON the Linux (Ubuntu 22.04) EC2 instance
# ==========================================================================
# Mirrors mac/start.sh: installs a desktop environment, VNC, openclaw,
# monitoring, telemetry, and service CLIs.
#
# Expected to be run as root (or via sudo) by setup-device.sh.
# ==========================================================================

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

export DEBIAN_FRONTEND=noninteractive


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
sudo -u "$REAL_USER" bash -c "
  cd $REAL_HOME
  git clone https://github.com/schwartzadev/openclaw-telemetry-hal 2>/dev/null || (cd openclaw-telemetry-hal && git pull)
  curl -fsSL https://get.pnpm.io/install.sh | sh -
  export PNPM_HOME=\"$REAL_HOME/.local/share/pnpm\"
  export PATH=\"\$PNPM_HOME:\$PNPM_HOME/bin:\$PATH\"
  cd $REAL_HOME/openclaw-telemetry-hal
  pnpm install
  pnpm run build
  $REAL_HOME/.npm-global/bin/openclaw plugins install --link .
"

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
# stays OFF until corrected — it will NOT break the run. (Same fail-soft convention
# as the gate-enforcer caveat.)
THINKING_LEVEL="${REASONING_EFFORT:-xhigh}"

# Prompt cache retention. "long" maps to Anthropic's 1h cache TTL (vs the 5-min
# default). The heartbeat cadence is 30m, so turns are routinely spaced past the
# 5-min TTL — without this, the large re-read workspace prompt is a full cache
# MISS every turn; "long" keeps it warm across the gaps → cache-read pricing.
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
# (deep thinking + long tool loops) — both pilot boxes hit "Request timed out
# before a response was generated... increase agents.defaults.timeoutSeconds".
# 3600s = 1h per turn; the 30m heartbeat sweeper bounds the cost of a runaway.
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
  .agents.defaults.heartbeat.every = "30m" |
  .agents.defaults.heartbeat.skipWhenBusy = true |
  .agents.defaults.heartbeat.target = "none" |
  .agents.defaults.subagents.maxConcurrent = 5
' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
  && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
echo "✔ Model configured: $ANTHROPIC_MODEL"
echo "✔ Extended thinking configured: thinkingDefault=$THINKING_LEVEL (verify key vs pinned OpenClaw; unknown key => thinking stays off, run unaffected)"
echo "✔ Prompt cache retention: $CACHE_RETENTION (1h TTL — survives the 30m heartbeat gaps)"
echo "✔ Provider request timeout: ${PROVIDER_REQUEST_TIMEOUT_SECONDS}s for '$PROVIDER_ID' (raises the 120s idle watchdog for heavy reasoning)"
echo "✔ Tools profile set to full"
echo "✔ Heartbeat configured: 30m, skipWhenBusy, target=none"
echo "✔ Subagents maxConcurrent: 5"

# Append ANTHROPIC_API_KEY to ~/.openclaw/.env for daemon/gateway use
OPENCLAW_ENV="$REAL_HOME/.openclaw/.env"
# Remove any existing ANTHROPIC_API_KEY line to avoid duplicates
if [ -f "$OPENCLAW_ENV" ]; then
  sed -i '/^ANTHROPIC_API_KEY=/d' "$OPENCLAW_ENV"
fi
echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" >> "$OPENCLAW_ENV"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"
echo "✔ Anthropic API key written to ~/.openclaw/.env"

# Append the agent's tool-call API keys (optional). Unlike ANTHROPIC_API_KEY
# (used by the gateway's model calls), these are consumed by the agent's bash
# tool calls — RunPod GPU pods — so they must reach the tool environment (the
# gateway loads ~/.openclaw/.env into its process env, which tool subprocesses
# inherit).
for kv in "RUNPOD_API_KEY=${RUNPOD_API_KEY:-}"; do
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

# ====== SERVICES ======

# install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
unzip -qo /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

# set up gateway
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway install
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway restart

# ======

# ====== VERSION CONTROL ======
# Local git only: the box has no GitHub credentials and the agent has no remote
# to push to. Commits are save points on this box (workspace AGENTS.md § Git
# discipline). Identity is set so commits from agent tool calls don't fail.
sudo -u "$REAL_USER" git config --global user.name "crux" || true
sudo -u "$REAL_USER" git config --global user.email "crux@localhost" || true
sudo -u "$REAL_USER" git config --global init.defaultBranch main || true
echo "✔ git configured (local only — no remote)"

# ====== HARNESS WORKSPACE ======
# Copy the next-run-harness workspace into the agent's OpenClaw workspace
# and resolve placeholders that are known at provisioning time.
HARNESS_SRC="$REAL_HOME/crux-in-a-box-harness/workspace"
OPENCLAW_WORKSPACE="$REAL_HOME/.openclaw/workspace"

if [ -d "$HARNESS_SRC" ]; then
  sudo -u "$REAL_USER" mkdir -p "$OPENCLAW_WORKSPACE"
  sudo -u "$REAL_USER" cp -r "$HARNESS_SRC"/* "$OPENCLAW_WORKSPACE/"
  chown -R "$REAL_USER:$REAL_USER" "$OPENCLAW_WORKSPACE"

  # Make scripts executable
  chmod +x "$OPENCLAW_WORKSPACE/scripts/"*.sh 2>/dev/null || true

  # --- Step 1: Resolve user-supplied placeholders (from the config file) ---
  # These run first so they take priority over built-in defaults.
  if [ -n "${PLACEHOLDERS:-}" ]; then
    IFS='|||' read -ra PAIRS <<< "$PLACEHOLDERS"
    for PAIR in "${PAIRS[@]}"; do
      [ -z "$PAIR" ] && continue
      KEY="${PAIR%%=*}"
      VALUE="${PAIR#*=}"
      # Replace both {{KEY}} and {{KEY|default}} forms
      find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec sed -i \
        -e "s#{{${KEY}}}#${VALUE}#g" \
        -e "s#{{${KEY}|[^}]*}}#${VALUE}#g" \
        {} +
      echo "✔ Placeholder resolved: ${KEY}=${VALUE}"
    done
  fi

  # --- Step 2: Resolve environment-derived placeholders ---
  # Use '#' as sed delimiter to avoid clashes with '|' in placeholder defaults.
  AGENT_NAME="${AGENT_NAME:-crux}"
  OPERATOR_NAME="${OPERATOR_NAME:-operator}"
  find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec sed -i \
    -e "s#{{AGENT_NAME}}#${AGENT_NAME}#g" \
    -e "s#{{OPERATOR_NAME}}#${OPERATOR_NAME}#g" \
    -e "s#{{OPERATOR_SHORT}}#${OPERATOR_NAME}#g" \
    -e "s#{{WORKSPACE_PATH}}#${OPENCLAW_WORKSPACE}#g" \
    -e "s#{{TELEMETRY_PATH}}#${REAL_HOME}/.openclaw/telemetry/telemetry.jsonl#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 22.04 EC2, amd64#g" \
    -e "s#{{COST_TRACKER_URL}}#${COST_TRACKER_URL:-}#g" \
    -e "s#{{API_KEY_SUFFIX}}#${API_KEY_SUFFIX:-}#g" \
    {} +
  echo "✔ Environment placeholders resolved (AGENT_NAME=$AGENT_NAME, OPERATOR_NAME=$OPERATOR_NAME)"

  # --- Step 3: Auto-populate remaining {{KEY|default}} with their defaults ---
  # Any placeholder with a pipe-delimited default that wasn't resolved above
  # gets replaced with its default value (the part after the |).
  find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec \
    sed -i -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g' {} +
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

# ====== THINKING-SIGNATURE WATCHDOG ======
# Auto-recovers the main session when it wedges on cascading
# "Invalid `signature` in `thinking` block" provider errors — a known OpenClaw
# bug (openclaw/openclaw#44370, #45010) with no upstream fix as of 2026.6.11.
# Detection is strict (errorMessage records only, newest stopReason must be an
# error) with a 30-min cooldown and a daily reset cap.
WATCHDOG_DIR="$REAL_HOME/.openclaw/watchdog"
sudo -u "$REAL_USER" mkdir -p "$WATCHDOG_DIR"
cp "$SCRIPT_DIR/crux-thinking-watchdog.sh" "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
chown "$REAL_USER:$REAL_USER" "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
chmod +x "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
sudo -u "$REAL_USER" bash -c \
  '(crontab -l 2>/dev/null | grep -v crux-thinking-watchdog; echo "*/5 * * * * $HOME/.openclaw/watchdog/crux-thinking-watchdog.sh") | crontab -'
echo "✔ Thinking-signature watchdog installed (cron */5 min)"

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."

