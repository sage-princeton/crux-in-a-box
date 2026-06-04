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


# ====== MONITORING ======
cp "$SCRIPT_DIR/src/monitor.sh" "$REAL_HOME/monitor.sh"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/monitor.sh"
chmod +x "$REAL_HOME/monitor.sh"

# Install scrot for screenshots (Linux equivalent of macOS screencapture)
apt-get install -y scrot

sudo -u "$REAL_USER" bash -c \
  "nohup $REAL_HOME/monitor.sh > /dev/null 2>&1 &"


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
# TODO: update the openclaw.json file's json itself
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway restart || true

# ====== UNRESTRICTED ACCESS ======
# TODO: add the part about overrides


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

# install gogcli
sudo -u "$REAL_USER" bash -c \
  'brew install openclaw/tap/gogcli' || true

# ======

# # Log in to GitHub
# gh auth login

# # Check GitHub status
# gh auth status

# Set up gog
# see: https://gogcli.sh/quickstart.html

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."

