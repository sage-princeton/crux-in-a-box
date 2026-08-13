#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# ami-install.sh  –  runs ON a builder EC2 instance to produce a CRUX AMI
# ==========================================================================
# Called by build-ami.sh. Installs everything that never changes between runs:
#   - System packages (xfce4, tigervnc, Node.js, build tools, gh CLI, AWS CLI)
#   - openclaw + telemetry plugin
#   - pnpm + gogcli
#   - VNC service unit and wrapper
#   - Canonical assets under /opt/crux/ (exec-approvals, watchdog script)
#
# Does NOT:
#   - Start any services (gateway stays down; VNC unit is installed, not started)
#   - Write any secrets, API keys, or per-run config
#   - Touch ~/.openclaw/openclaw.json or ~/.openclaw/.env
#
# Run as root (sudo). REAL_USER is the non-root user whose home gets tools.
# ==========================================================================

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

export DEBIAN_FRONTEND=noninteractive

# ====== DEPENDENCIES ======
apt-get update -y --allow-insecure-repositories
apt-get upgrade -y --allow-unauthenticated
apt-get install -y --allow-unauthenticated \
  build-essential curl git wget unzip jq \
  software-properties-common apt-transport-https

# ====== GUI DESKTOP ======
apt-get install -y --allow-unauthenticated xfce4 xfce4-goodies dbus-x11 x11-xserver-utils
apt-get install -y --allow-unauthenticated tigervnc-standalone-server tigervnc-common

# Set a default login password (for VNC / sudo prompts; same every run)
echo "$REAL_USER:cruxbox1" | chpasswd

# Configure VNC for the real user
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.vnc"

echo "cruxbox1" | sudo -u "$REAL_USER" vncpasswd -f > "$REAL_HOME/.vnc/passwd"
chmod 600 "$REAL_HOME/.vnc/passwd"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.vnc/passwd"

cat > "$REAL_HOME/.vnc/xstartup" <<'XSTARTUP'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
exec startxfce4
XSTARTUP
chmod +x "$REAL_HOME/.vnc/xstartup"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.vnc/xstartup"

# VNC wrapper script
cat > /usr/local/bin/crux-vnc-start <<'WRAPPER'
#!/bin/bash
DISPLAY_NUM="${1:-1}"
PORT=$((5900 + DISPLAY_NUM))
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

# VNC systemd service (installed but NOT enabled — configure.sh enables it)
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
# Note: deliberately NOT enabling vncserver@1 here — configure.sh does that
# after the per-user VNC password is confirmed present.

# ====== WALLPAPER (config skeleton) ======
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
sudo -u "$REAL_USER" bash -c \
  'curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard'

sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"

# ====== TELEMETRY PLUGIN ======
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

# ====== SERVICES: GitHub CLI ======
(type -p wget >/dev/null || apt-get install wget -y) \
  && mkdir -p -m 755 /etc/apt/keyrings \
  && out=$(mktemp) \
  && wget -nv -O"$out" https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  && cat "$out" | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
  && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update --allow-insecure-repositories \
  && apt-get install gh -y --allow-unauthenticated

# ====== SERVICES: AWS CLI v2 ======
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
unzip -qo /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

# ====== SERVICES: gogcli ======
GOGCLI_VERSION="${GOGCLI_VERSION:-v0.28.0}"
GOGCLI_ARCH="$(dpkg --print-architecture)"
GOGCLI_TARBALL="gogcli_${GOGCLI_VERSION#v}_linux_${GOGCLI_ARCH}.tar.gz"
GOGCLI_URL="https://github.com/openclaw/gogcli/releases/download/${GOGCLI_VERSION}/${GOGCLI_TARBALL}"
if curl -fsSL "$GOGCLI_URL" -o /tmp/gogcli.tar.gz; then
  tar xzf /tmp/gogcli.tar.gz -C /tmp gog 2>/dev/null || tar xzf /tmp/gogcli.tar.gz -C /tmp
  install -m 0755 /tmp/gog /usr/local/bin/gog 2>/dev/null \
    || { find /tmp -maxdepth 2 -name gog -type f -exec install -m 0755 {} /usr/local/bin/gog \; ; }
  rm -f /tmp/gogcli.tar.gz /tmp/gog
  echo "✔ gogcli installed: $(gog --version 2>&1 | head -1)"
else
  echo "⚠ Could not download gogcli from $GOGCLI_URL"
fi

# ====== CANONICAL ASSETS under /opt/crux/ ======
# configure.sh references these paths directly, so they don't depend on the
# linux/ directory being SCP'd to the instance first.
mkdir -p /opt/crux
cp "$SCRIPT_DIR/exec-approvals.json" /opt/crux/exec-approvals.json
cp "$SCRIPT_DIR/crux-thinking-watchdog.sh" /opt/crux/crux-thinking-watchdog.sh
chmod 644 /opt/crux/exec-approvals.json
chmod 755 /opt/crux/crux-thinking-watchdog.sh
echo "✔ Canonical assets installed to /opt/crux/"

# ====== CLEANUP ======
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/openclaw-* 2>/dev/null || true

echo ""
echo "✔ AMI install complete. Stop this instance and create an AMI."
