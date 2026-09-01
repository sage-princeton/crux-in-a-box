#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# install.sh  –  runs ON the Linux (Ubuntu 24.04 LTS) EC2 instance
# ==========================================================================
# BAKE PHASE: installs all software — desktop environment, VNC, openclaw,
# telemetry, service CLIs (gh, aws, gog), and the thinking-signature watchdog.
# The openclaw gateway service is intentionally NOT installed here (that must
# happen after config exists — see the GATEWAY section); configure.sh installs +
# starts it per run. Consumes NO secrets and NO per-run configuration, so the
# result can be baked into an AMI (see linux/build-ami.sh).
#
# Per-run configuration (Telegram, model, API keys, gog auth, GitHub auth,
# harness workspace, gateway start) lives in configure.sh.
#
# Expected to be run as root (or via sudo) by create-new-crux-box.sh (the
# fresh-Ubuntu fallback path) or build-ami.sh (the AMI bake path).
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
# Install a lightweight XFCE desktop and TigerVNC so the instance has a GUI
apt-get install -y --allow-unauthenticated xfce4 xfce4-goodies dbus-x11 x11-xserver-utils
apt-get install -y --allow-unauthenticated tigervnc-standalone-server tigervnc-common

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
# Install OpenClaw at a pinned version, skipping onboarding. The official
# installer accepts OPENCLAW_VERSION (latest, next, or an exact version);
# without it you get whatever shipped most recently.
#
# We pin because new releases change the config schema and CLI behavior, and
# an unpinned bake inherits those changes blind. When 2026.8.2 landed it
# removed heartbeat.skipWhenBusy (making our config invalid and blocking the
# gateway install), turned agents.list into the keyed agents.entries map, and
# added consent prompts to 'plugins install' that abort non-interactive runs.
#
# configure.sh writes config keys that match THIS version. If you bump the
# pin, review configure.sh's schema in the same change.
OPENCLAW_PINNED_VERSION="2026.8.2"
sudo -u "$REAL_USER" bash -c \
  "curl -fsSL https://openclaw.ai/install.sh | OPENCLAW_VERSION=$OPENCLAW_PINNED_VERSION bash -s -- --no-onboard"
# Hard-verify the pin took: a mismatched version means the version-sensitive
# config keys configure.sh writes later may be silently ignored or rejected.
OPENCLAW_INSTALLED_VERSION=$(sudo -u "$REAL_USER" bash -lc 'openclaw --version' 2>/dev/null | head -1 || echo unknown)
case "$OPENCLAW_INSTALLED_VERSION" in
  *"$OPENCLAW_PINNED_VERSION"*)
    echo "✔ OpenClaw installed and pinned: $OPENCLAW_INSTALLED_VERSION" ;;
  *)
    echo "✘ OpenClaw version mismatch: pinned $OPENCLAW_PINNED_VERSION but installed '$OPENCLAW_INSTALLED_VERSION' — the installer ignored the pin or the release was pulled; do not bake, config keys are version-sensitive" >&2
    exit 1 ;;
esac

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
# is built inline by configure.sh's TELEMETRY CONFIG step (it only needs the two
# host-side switches plus the box-specific path and rotation — everything else
# is the plugin's own default). The fixes the harness depends on (a real
# not-started fallback, redaction on by default, agent context deltas instead of
# the full history every turn, llm.usage from the internal diagnostic bus) live
# in the pinned commit. The pin is applied BEFORE the build and a commit that is
# not in the upstream clone aborts provisioning instead of shipping HEAD. No
# secrets and no per-run config here — safe to bake into the AMI.
TELEMETRY_REPO_URL="https://github.com/schwartzadev/openclaw-telemetry-hal"
TELEMETRY_REPO_DIR="$REAL_HOME/openclaw-telemetry-hal"
# Merge of schwartzadev/openclaw-telemetry-hal#1 on main.
TELEMETRY_UPSTREAM_COMMIT="dc51714fa4cd1e8ec5b34e08d2a044a36941e200"
case "$TELEMETRY_UPSTREAM_COMMIT" in
  *[!0-9a-f]*|'') echo "✘ telemetry-hal: TELEMETRY_UPSTREAM_COMMIT must hold one git sha, got '$TELEMETRY_UPSTREAM_COMMIT'" >&2; exit 1 ;;
esac

# Clone (or reuse) the upstream repo and detach at the pin. Runs as the real user
# (the repo is theirs). --force on the checkout discards any local drift so
# re-running install.sh lands cleanly on the pinned commit.
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
  echo "✘ telemetry-hal: commit $TELEMETRY_UPSTREAM_COMMIT is not in the upstream clone — TELEMETRY_UPSTREAM_COMMIT in install.sh is stale or the fork moved; re-pin deliberately, do not fall back to HEAD" >&2
  exit 1
fi
echo "✔ telemetry-hal pinned at $TELEMETRY_UPSTREAM_COMMIT"
TELEMETRY_PIN
then
  echo "✘ telemetry-hal: pin step failed — provisioning aborted (see the ✘ line above)" >&2
  exit 1
fi

# NOTE: the bash -c script below is a DOUBLE-QUOTED string — comments or text
# with embedded double quotes inside it truncate the script mid-line and the
# remainder executes in THIS root shell - AVOID comments in this.
sudo -u "$REAL_USER" bash -c "
  cd $REAL_HOME
  curl -fsSL https://get.pnpm.io/install.sh | sh -
  export PNPM_HOME=\"$REAL_HOME/.local/share/pnpm\"
  export PATH=\"\$PNPM_HOME:\$PNPM_HOME/bin:\$PATH\"
  cd $TELEMETRY_REPO_DIR
  pnpm install
  pnpm run build
  $REAL_HOME/.npm-global/bin/openclaw plugins install . --link --force --accept-capabilities
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
TELEMETRY_MANIFEST="$TELEMETRY_REPO_DIR/openclaw.plugin.json"
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
  && apt-get update --allow-insecure-repositories \
  && apt-get install gh -y --allow-unauthenticated

# install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
unzip -qo /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

# install gogcli (Google Workspace CLI) from the GitHub release.
# Homebrew isn't available on this box, so we pull the prebuilt linux binary.
# Pinned to a known-good tag: v0.28.0+ honors GOG_HOME (older versions like
# 0.9.0 ignore it and write to the platform default config dir, which breaks
# the portable-bundle approach).
GOGCLI_VERSION="v0.28.0"
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
  echo "⚠ Could not download gogcli from $GOGCLI_URL — gog will be unavailable (update the pinned GOGCLI_VERSION in install.sh to a valid release tag)."
fi

# ====== OPENCLAW GATEWAY ======
# We deliberately DO NOT run `openclaw gateway install` here. At bake time there
# is no config yet (gateway.mode is set per-run by configure.sh), and running it
# early lays down a broken PLACEHOLDER unit that systemd reports as masked (an
# empty 0-byte unit file) — which `systemctl unmask` cannot repair and which then
# fights configure.sh at every launch. That masked-unit bug is what caused the
# repeated "Unit openclaw-gateway.service is masked" failures.
#
# The gateway install is deferred entirely to configure.sh, which runs it AFTER
# gateway.mode=local + secrets are written, so it generates a correct unit from a
# clean slate. The only bake-time prep is lingering (harmless, no secrets) so the
# per-run --user service can survive without an active login session.
REAL_USER_UID=$(id -u "$REAL_USER")
loginctl enable-linger "$REAL_USER" || true
echo "✔ gateway NOT installed at bake (avoids masked-placeholder unit); configure.sh installs + starts it per run (linger enabled for uid $REAL_USER_UID)"

# ====== WATCHDOGS + FINAL-PASS INJECTOR ======
# NOT installed at bake. The four box-side cron jobs (crux-thinking-watchdog,
# crux-auth-watchdog, crux-session-snapshot, final-pass-injector) are installed
# by configure.sh at run time: their cadences and thresholds come from per-run
# {{AUTH_WATCHDOG_THRESHOLD}} / {{SESSION_SNAPSHOT_MINUTES}} placeholders, the
# auth watchdog pages over the per-run Telegram credentials, and the final-pass
# injector stages its message from the per-run harness workspace. The scripts
# themselves ship in linux/src/ (copied to the box by create-new-crux-box.sh) —
# baking them here would only lay down copies with unresolved {{...}} tokens.

echo ""
echo "✔ CRUX-in-a-box software install complete (bake phase — no secrets on disk)."
echo "  Next: bake an AMI from this instance (build-ami.sh) or run configure.sh for a live run."
