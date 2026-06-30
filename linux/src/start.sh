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
# REASONING_EFFORT (default "high"). The key below is the one OpenClaw exposes for
# a global default thinking level (.agents.defaults.thinkingDefault, values
# off|minimal|low|medium|high|xhigh|adaptive|max). VERIFY this key name against
# YOUR pinned OpenClaw version: an unknown key is simply ignored, so thinking just
# stays OFF until corrected — it will NOT break the run. (Same fail-soft convention
# as the gate-enforcer caveat.)
THINKING_LEVEL="${REASONING_EFFORT:-high}"
TMP_CONFIG=$(mktemp)
jq --arg model "$ANTHROPIC_MODEL" --arg reasoning "$THINKING_LEVEL" '
  .agents.defaults.model.primary = $model |
  .agents.defaults.thinkingDefault = $reasoning |
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
# tool calls — RunPod GPU pods and the refine.ink review API — so they must
# reach the tool environment (the gateway loads ~/.openclaw/.env into its
# process env, which tool subprocesses inherit).
for kv in "RUNPOD_API_KEY=${RUNPOD_API_KEY:-}" "REFINE_INK_API_KEY=${REFINE_INK_API_KEY:-}"; do
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

# install GitHub CLI
(type -p wget >/dev/null || apt-get install wget -y) \
  && mkdir -p -m 755 /etc/apt/keyrings \
  && out=$(mktemp) \
  && wget -nv -O"$out" https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  && cat "$out" | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
  && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update \
  && apt-get install gh -y

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

# ====== GitHub auth (gh CLI + git over HTTPS) ======
# Authenticate gh non-interactively with the classic PAT passed by
# setup-device.sh. Run as the real user (gh stores creds under ~/.config/gh),
# wire up git's credential helper, and also export the token in ~/.openclaw/.env
# (GITHUB_TOKEN/GH_TOKEN are what gh, git, and most tooling read) so the agent's
# tool subprocesses can push/pull and call the GitHub API.
if [ -n "${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN:-}" ]; then
  if command -v gh >/dev/null 2>&1; then
    if printf '%s' "$GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN" \
         | sudo -u "$REAL_USER" gh auth login --hostname github.com --git-protocol https --with-token; then
      sudo -u "$REAL_USER" gh auth setup-git || true
      echo "✔ gh CLI authenticated and git credential helper configured"
    else
      echo "⚠ gh auth login failed — check the GitHub classic PAT (scope/expiry)."
    fi
  else
    echo "⚠ gh CLI not installed — cannot authenticate GitHub."
  fi

  # Export for the gateway/agent tool environment (gh + git read these).
  for name in GITHUB_TOKEN GH_TOKEN; do
    sed -i "/^${name}=/d" "$OPENCLAW_ENV"
    echo "${name}=${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN}" >> "$OPENCLAW_ENV"
  done
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
  chmod 600 "$OPENCLAW_ENV"
  echo "✔ GITHUB_TOKEN/GH_TOKEN written to ~/.openclaw/.env"
else
  echo "⚠ GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN not provided — gh/git will be unauthenticated."
fi

# Set up gog
# see: https://gogcli.sh/quickstart.html

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

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."

