#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# start-claude.sh — Claude as a drop-in OpenClaw replacement
# ==========================================================================
# Minimal adaptation of start.sh to use Claude 3.5 Sonnet API instead of OpenClaw.
# Same harness, same tools (gate_artifact.sh, etc), same AGENTS.md.
# Only changes: provisioning + agent entry point.
#
# What stays the same:
#   • Desktop environment (XFCE + VNC)
#   • External tools (git, python, curl, jq)
#   • Harness workspace (next-run-harness/)
#   • Gate enforcement (scripts/gate_artifact.sh)
#   • Telemetry hooks (cost tracking via API response metadata)
#
# What changes:
#   • No OpenClaw gateway service
#   • No OpenClaw config (~/.openclaw/openclaw.json)
#   • No watchdog (Claude API is stable; no thinking-block wedges)
#   • Agent entry point: python3 wrapper instead of `openclaw gateway`
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

# ====== PYTHON + CLAUDE SDK ======
# Install Python 3.11+ with pip
apt-get install -y python3 python3-pip python3-venv

# Install anthropic SDK
python3 -m pip install --upgrade anthropic

echo "✔ Python installed"
echo "✔ Anthropic SDK installed"

# ====== CREATE AGENT DIRECTORIES ======
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.claude-sessions"
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/claude-runs"
chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.claude-sessions" "$REAL_HOME/claude-runs"
echo "✔ Claude session directories created"

# ====== ENVIRONMENT SETUP ======
# Append ANTHROPIC_API_KEY to ~/.bashrc so it's available in the session
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  BASHRC="$REAL_HOME/.bashrc"
  if grep -q "ANTHROPIC_API_KEY" "$BASHRC" 2>/dev/null; then
    sed -i '/^export ANTHROPIC_API_KEY=/d' "$BASHRC"
  fi
  echo "export ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}'" >> "$BASHRC"
  chown "$REAL_USER:$REAL_USER" "$BASHRC"
  echo "✔ ANTHROPIC_API_KEY exported to ~/.bashrc"
fi

# Also append to /etc/environment for system-wide access
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  if grep -q "ANTHROPIC_API_KEY" /etc/environment 2>/dev/null; then
    sed -i '/^ANTHROPIC_API_KEY=/d' /etc/environment
  fi
  echo "ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}'" >> /etc/environment
  echo "✔ ANTHROPIC_API_KEY exported to /etc/environment"
fi

# ====== GOG (GOOGLE WORKSPACE CLI) AUTH ======
# Same as OpenClaw version
if [ -z "${GOG_KEYRING_PASSWORD:-}" ]; then
  echo "Error: GOG_KEYRING_PASSWORD is required but not set (passed by setup-device.sh from the config file)." >&2
  exit 1
fi

if [ -z "${GOG_HOME_TARBALL:-}" ]; then
  echo "Error: GOG_HOME_TARBALL is required but not set (passed by setup-device.sh from the config file)." >&2
  exit 1
fi

if [ ! -f "$GOG_HOME_TARBALL" ]; then
  echo "Error: GOG_HOME_TARBALL file not found: $GOG_HOME_TARBALL" >&2
  exit 1
fi

# Extract gog-home bundle
GOG_HOME="$REAL_HOME/.gog"
mkdir -p "$GOG_HOME"
tar -xzf "$GOG_HOME_TARBALL" -C "$GOG_HOME" --strip-components 1
chown -R "$REAL_USER:$REAL_USER" "$GOG_HOME"

# Set gog env vars for the shell and system
BASHRC="$REAL_HOME/.bashrc"
if grep -q "GOG_HOME=" "$BASHRC" 2>/dev/null; then
  sed -i '/^export GOG_HOME=/d' "$BASHRC"
fi
echo "export GOG_HOME='$GOG_HOME'" >> "$BASHRC"
echo "export GOG_KEYRING_PASSWORD='${GOG_KEYRING_PASSWORD}'" >> "$BASHRC"
chown "$REAL_USER:$REAL_USER" "$BASHRC"

if grep -q "^GOG_HOME=" /etc/environment 2>/dev/null; then
  sed -i '/^GOG_HOME=/d' /etc/environment
fi
echo "GOG_HOME='$GOG_HOME'" >> /etc/environment
echo "GOG_KEYRING_PASSWORD='${GOG_KEYRING_PASSWORD}'" >> /etc/environment

echo "✔ gog CLI authenticated and configured"

# ====== INSTALL CLAUDE AGENT RUNNER ======
# Copy the Claude agent runner script to the user's home
CLAUDE_RUNNER="$REAL_HOME/run-claude-agent.sh"
cat > "$CLAUDE_RUNNER" <<'RUNNER'
#!/bin/bash
set -e

# run-claude-agent.sh — Launch Claude agent in the harness
# Usage: ./run-claude-agent.sh <workspace-dir> <session-id>

WORKSPACE="${1:-.}"
SESSION_ID="${2:-main-$(date +%s)}"

echo "[INIT] Starting Claude agent"
echo "  Workspace: $WORKSPACE"
echo "  Session ID: $SESSION_ID"

# Verify API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Error: ANTHROPIC_API_KEY not set"
  exit 1
fi

# Read launch prompt
PROMPT_FILE="$WORKSPACE/PROMPT.md"
if [ ! -f "$PROMPT_FILE" ]; then
  echo "Error: PROMPT.md not found at $PROMPT_FILE"
  exit 1
fi

# Read system prompt (AGENTS.md)
AGENTS_FILE="$WORKSPACE/workspace/AGENTS.md"
if [ ! -f "$AGENTS_FILE" ]; then
  echo "Error: AGENTS.md not found at $AGENTS_FILE"
  exit 1
fi

# Start multi-turn agent loop
python3 <<'PYTHON'
import os
import sys
from pathlib import Path

# Agent session wrapper
class ClaudeAgentSession:
    def __init__(self, workspace, session_id):
        self.workspace = Path(workspace)
        self.session_id = session_id
        self.session_dir = self.workspace / ".claude-sessions"
        self.session_dir.mkdir(exist_ok=True)
        
        # Import anthropic here (installed via start-claude.sh)
        try:
            import anthropic
        except ImportError:
            print("Error: anthropic SDK not installed")
            sys.exit(1)
        
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Error: ANTHROPIC_API_KEY not set")
            sys.exit(1)
        
        self.client = anthropic.Anthropic(api_key=api_key)
        self.messages = []
    
    def send_message(self, prompt, system):
        """Send message; return response."""
        self.messages.append({"role": "user", "content": prompt})
        
        response = self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            system=system,
            messages=self.messages,
        )
        
        # Extract response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
        
        # Add to history
        self.messages.append({"role": "assistant", "content": text})
        
        return text

# Main agent loop
workspace = Path(sys.argv[1])
session_id = sys.argv[2]

with open(workspace / "PROMPT.md") as f:
    prompt = f.read()

with open(workspace / "workspace" / "AGENTS.md") as f:
    system = f.read()

print(f"\n[AGENT START] {session_id}")
print("[USER PROMPT]")
print(prompt[:200] + "..." if len(prompt) > 200 else prompt)

session = ClaudeAgentSession(workspace, session_id)
response = session.send_message(prompt, system)

print(f"\n[AGENT RESPONSE]")
print(response[:500] + "..." if len(response) > 500 else response)
PYTHON

echo "[AGENT END] Completed"
RUNNER

chmod +x "$CLAUDE_RUNNER"
chown "$REAL_USER:$REAL_USER" "$CLAUDE_RUNNER"
echo "✔ Claude agent runner installed at ~/run-claude-agent.sh"

# ====== SUMMARY ======
echo ""
echo "✔ Claude provisioning complete"
echo ""
echo "To run the agent:"
echo "  cd ~/next-run-harness"
echo "  ~/run-claude-agent.sh . main-1"
echo ""
