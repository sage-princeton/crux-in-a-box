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

# ====== PYTHON (harness research tooling) ======
# The harness's playbooks and gate scripts assume python3 is present for
# experiment code; Claude Code itself is a self-contained binary and needs none
# of this.
apt-get install -y python3 python3-pip python3-venv
echo "✔ Python installed"

# ====== GITHUB CLI ======
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
echo "✔ GitHub CLI installed"

# ====== CLAUDE CODE CLI ======
# Claude Code is the agent runtime — the drop-in for the OpenClaw gateway. It
# brings the tool surface the harness assumes (bash, file edit, web fetch/search,
# subagents, hooks) plus resumable sessions, so run-claude-agent.sh only has to
# hand it the launch prompt.
sudo -u "$REAL_USER" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'

# Resolve the binary: the native installer lands in ~/.local/bin.
CLAUDE_BIN="$REAL_HOME/.local/bin/claude"
if [ ! -x "$CLAUDE_BIN" ]; then
  CLAUDE_BIN=$(sudo -u "$REAL_USER" bash -lc 'command -v claude' || true)
fi
if [ -z "${CLAUDE_BIN:-}" ] || [ ! -x "$CLAUDE_BIN" ]; then
  echo "Error: Claude Code install failed — no 'claude' binary found." >&2
  exit 1
fi
echo "✔ Claude Code installed: $($CLAUDE_BIN --version 2>/dev/null || echo unknown) ($CLAUDE_BIN)"

# ====== MODEL / CREDENTIAL RESOLUTION ======
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY is required (passed by setup-device-claude.sh from the config file)." >&2
  exit 1
fi
if [ -z "${ANTHROPIC_MODEL:-}" ]; then
  echo "Error: ANTHROPIC_MODEL is required (e.g. claude-opus-5)." >&2
  exit 1
fi

# The config file carries OpenClaw's provider-qualified form
# ("anthropic/claude-opus-4-8"); Claude Code wants the bare model ID. Strip any
# "provider/" prefix so one placeholders file drives both bootstraps.
CRUX_MODEL="${ANTHROPIC_MODEL##*/}"
CRUX_EFFORT="${REASONING_EFFORT:-xhigh}"

# The condition Claude re-checks before it is allowed to stop (`/goal`). This is
# what keeps the run going across turns without a heartbeat service: the session
# does not end until the condition is met or the goal is cleared.
CRUX_GOAL_DEFAULT="The research project defined in BRIEF.md is complete and shipped: the terminal Ship milestone in PLAN.md is done, the Presentation Overhaul milestone before it was run and acceptance-tested, scripts/gate_artifact.sh passes on the final artifact, and the completion report has been sent. Until every one of those is true, keep working through the research cycle and the End-of-Turn Contract in AGENTS.md — never stop to wait on the operator."
CRUX_GOAL="${GOAL_CONDITION:-$CRUX_GOAL_DEFAULT}"

CRUX_HOME="$REAL_HOME/.crux"
CRUX_WORKSPACE="$REAL_HOME/crux-workspace"
TELEMETRY_PATH="$CRUX_HOME/telemetry.jsonl"

sudo -u "$REAL_USER" mkdir -p "$CRUX_HOME" "$CRUX_WORKSPACE"
chmod 700 "$CRUX_HOME"

echo "✔ Model resolved: $CRUX_MODEL (effort=$CRUX_EFFORT)"

# ====== AGENT ENVIRONMENT FILE ======
# ~/.crux/env is the analog of ~/.openclaw/.env: the single place secrets and
# run config live. The systemd unit loads it, and run-claude-agent.sh sources it,
# so the agent's bash tool calls inherit the same environment.
CRUX_ENV="$CRUX_HOME/env"
: > "$CRUX_ENV"
{
  echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
  echo "CRUX_MODEL=${CRUX_MODEL}"
  echo "CRUX_EFFORT=${CRUX_EFFORT}"
  echo "CRUX_HOME=${CRUX_HOME}"
  echo "CRUX_WORKSPACE=${CRUX_WORKSPACE}"
  echo "CRUX_TELEMETRY=${TELEMETRY_PATH}"
  echo "CLAUDE_BIN=${CLAUDE_BIN}"
} >> "$CRUX_ENV"

# Tool-call credentials (RunPod GPU pods, refine.ink review API). Optional —
# absent keys just mean the tools that need them fail until they're added here.
for kv in "RUNPOD_API_KEY=${RUNPOD_API_KEY:-}" "REFINE_INK_API_KEY=${REFINE_INK_API_KEY:-}"; do
  name="${kv%%=*}"; val="${kv#*=}"
  if [ -z "$val" ]; then
    echo "⚠ ${name} not provided — skipping (agent tools needing it will fail until it's added to ~/.crux/env)"
    continue
  fi
  echo "${name}=${val}" >> "$CRUX_ENV"
  echo "✔ ${name} written to ~/.crux/env"
done

# The goal condition is multi-line prose, so write it as its own file rather than
# a KEY=VALUE line the runner would have to re-quote.
printf '%s\n' "$CRUX_GOAL" > "$CRUX_HOME/GOAL.md"

chown -R "$REAL_USER:$REAL_USER" "$CRUX_HOME"
chmod 600 "$CRUX_ENV"
echo "✔ Agent environment written to ~/.crux/env"

# ====== GITHUB AUTH (gh CLI + git over HTTPS) ======
if [ -n "${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN:-}" ]; then
  if printf '%s' "$GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN" \
       | sudo -u "$REAL_USER" gh auth login --hostname github.com --git-protocol https --with-token; then
    sudo -u "$REAL_USER" gh auth setup-git || true
    echo "✔ gh CLI authenticated and git credential helper configured"
  else
    echo "⚠ gh auth login failed — check the GitHub classic PAT (scope/expiry)."
  fi

  for name in GITHUB_TOKEN GH_TOKEN; do
    echo "${name}=${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN}" >> "$CRUX_ENV"
  done
  chown "$REAL_USER:$REAL_USER" "$CRUX_ENV"
  chmod 600 "$CRUX_ENV"
  echo "✔ GITHUB_TOKEN/GH_TOKEN written to ~/.crux/env"
else
  echo "⚠ GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN not provided — gh/git will be unauthenticated."
fi

# ====== HARNESS WORKSPACE ======
# Copy next-run-harness/workspace/ into the agent's cwd and resolve the
# placeholders that are known at provisioning time. PROMPT.md is the launch
# message rather than a workspace file, so it lands in ~/.crux/ — but it carries
# the same placeholders and is resolved alongside the workspace.
HARNESS_ROOT="$REAL_HOME/crux-in-a-box-harness"
HARNESS_SRC="$HARNESS_ROOT/workspace"

# Re-provisioning a box with a run already in progress must not clobber the
# agent's working files — LOG.md, PLAN.md and REGISTRY.md are the run's memory.
# A session-id is the marker that a run has started.
if [ -f "$CRUX_HOME/session-id" ]; then
  echo "⚠ A run already exists on this box (session $(cat "$CRUX_HOME/session-id"))."
  echo "  Leaving $CRUX_WORKSPACE untouched — re-copying the harness would destroy"
  echo "  the run's LOG.md/PLAN.md. To start a fresh run, on the box:"
  echo "    sudo systemctl stop crux-agent"
  echo "    rm -rf $CRUX_WORKSPACE $CRUX_HOME/session-id"
  echo "  then re-run this bootstrap."
  SKIP_WORKSPACE=1
else
  SKIP_WORKSPACE=0
fi

if [ "$SKIP_WORKSPACE" = "0" ] && [ -d "$HARNESS_SRC" ]; then
  sudo -u "$REAL_USER" cp -r "$HARNESS_SRC"/* "$CRUX_WORKSPACE/"
  if [ -f "$HARNESS_ROOT/PROMPT.md" ]; then
    sudo -u "$REAL_USER" cp "$HARNESS_ROOT/PROMPT.md" "$CRUX_HOME/PROMPT.md"
  fi
  chown -R "$REAL_USER:$REAL_USER" "$CRUX_WORKSPACE" "$CRUX_HOME"
  chmod +x "$CRUX_WORKSPACE/scripts/"*.sh 2>/dev/null || true

  # The set of files placeholders get resolved in: the workspace tree plus the
  # launch prompt. Note the explicit `if` rather than a trailing `&&` test — as
  # the last command in a function that would return 1 when PROMPT.md is absent,
  # aborting the whole script under `set -e`.
  resolve_in() {
    find "$CRUX_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec sed -i "$@" {} +
    if [ -f "$CRUX_HOME/PROMPT.md" ]; then
      sed -i "$@" "$CRUX_HOME/PROMPT.md"
    fi
  }

  # --- Step 1: Resolve user-supplied placeholders (from the config file) ---
  # These run first so they take priority over built-in defaults.
  if [ -n "${PLACEHOLDERS:-}" ]; then
    IFS='|||' read -ra PAIRS <<< "$PLACEHOLDERS"
    for PAIR in "${PAIRS[@]}"; do
      [ -z "$PAIR" ] && continue
      KEY="${PAIR%%=*}"
      VALUE="${PAIR#*=}"
      resolve_in -e "s#{{${KEY}}}#${VALUE}#g" -e "s#{{${KEY}|[^}]*}}#${VALUE}#g"
      echo "✔ Placeholder resolved: ${KEY}=${VALUE}"
    done
  fi

  # --- Step 2: Resolve environment-derived placeholders ---
  # Use '#' as sed delimiter to avoid clashes with '|' in placeholder defaults.
  AGENT_NAME="${AGENT_NAME:-crux}"
  OPERATOR_NAME="${OPERATOR_NAME:-operator}"
  resolve_in \
    -e "s#{{AGENT_NAME}}#${AGENT_NAME}#g" \
    -e "s#{{OPERATOR_NAME}}#${OPERATOR_NAME}#g" \
    -e "s#{{OPERATOR_SHORT}}#${OPERATOR_NAME}#g" \
    -e "s#{{WORKSPACE_PATH}}#${CRUX_WORKSPACE}#g" \
    -e "s#{{TELEMETRY_PATH}}#${TELEMETRY_PATH}#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 22.04 EC2, amd64#g" \
    -e "s#{{COST_TRACKER_URL}}#${COST_TRACKER_URL:-}#g" \
    -e "s#{{API_KEY_SUFFIX}}#${API_KEY_SUFFIX:-}#g"
  echo "✔ Environment placeholders resolved (AGENT_NAME=$AGENT_NAME, OPERATOR_NAME=$OPERATOR_NAME)"

  # --- Step 3: Auto-populate remaining {{KEY|default}} with their defaults ---
  resolve_in -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g'
  echo "✔ Remaining defaults auto-populated"

  # Deliberately NOT symlinking CLAUDE.md → AGENTS.md: the runner passes AGENTS.md
  # via --append-system-prompt on every invocation, which is both stronger (system
  # prompt, not a context file) and already covers resumed turns. A symlink would
  # only put the constitution in context twice.

  chown -R "$REAL_USER:$REAL_USER" "$CRUX_WORKSPACE" "$CRUX_HOME"
  echo "✔ Harness workspace installed at $CRUX_WORKSPACE"

  REMAINING=$(grep -rn '{{' "$CRUX_WORKSPACE" --include='*.md' --include='*.sh' --include='*.py' 2>/dev/null | grep -v 'grep for {{' | head -20 || true)
  if [ -n "$REMAINING" ]; then
    echo ""
    echo "⚠ Unresolved placeholders (resolve manually or via the config file before launch):"
    echo "$REMAINING"
  fi
elif [ "$SKIP_WORKSPACE" = "0" ]; then
  echo "Error: harness workspace not found at $HARNESS_SRC — nothing to run." >&2
  exit 1
fi

if [ ! -f "$CRUX_HOME/PROMPT.md" ]; then
  echo "Error: PROMPT.md not found at $HARNESS_ROOT/PROMPT.md — no launch message to send." >&2
  exit 1
fi

# ====== AGENT RUNNER ======
# Two invocations against one session, in this order:
#   1. the launch prompt (PROMPT.md) — the agent runs the hour-0 sequence and
#      ends its first turn in a normal End-of-Turn state.
#   2. `/goal <condition>` — this is what replaces OpenClaw's heartbeat. `/goal`
#      installs a session-scoped Stop hook that BLOCKS the turn from ending
#      until the condition holds, and auto-clears once it does, so this single
#      invocation carries the run to completion instead of returning after one
#      turn.
# The order matters: `/goal` also tells the agent to start working toward the
# condition immediately, so setting it first would run the whole project without
# ever reading PROMPT.md.
# On restart the runner resumes the persisted session ID rather than starting a
# new run, so a crash mid-project doesn't lose the transcript.
CLAUDE_RUNNER="$REAL_HOME/run-claude-agent.sh"
cat > "$CLAUDE_RUNNER" <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail

# run-claude-agent.sh — start or resume the CRUX run under Claude Code.
# Normally invoked by the crux-agent systemd service; safe to run by hand.

CRUX_HOME="${CRUX_HOME:-$HOME/.crux}"
set -a
# shellcheck disable=SC1090,SC1091
. "$CRUX_HOME/env"
set +a

WORKSPACE="${CRUX_WORKSPACE:?}"
TELEMETRY="${CRUX_TELEMETRY:-$CRUX_HOME/telemetry.jsonl}"
SESSION_FILE="$CRUX_HOME/session-id"
PROMPT_FILE="$CRUX_HOME/PROMPT.md"
GOAL_FILE="$CRUX_HOME/GOAL.md"

cd "$WORKSPACE"

# Shared flags. bypassPermissions is deliberate: the agent is alone on a
# disposable box and every approval prompt would deadlock a headless run.
COMMON=(
  --model "$CRUX_MODEL"
  --effort "$CRUX_EFFORT"
  --dangerously-skip-permissions
  --output-format stream-json
  --verbose
)

run_turn() {
  # $1 = prompt text, remaining args = session flags
  local prompt="$1"; shift
  "$CLAUDE_BIN" -p "$prompt" \
    "${COMMON[@]}" \
    --append-system-prompt "$(cat "$WORKSPACE/AGENTS.md")" \
    "$@" 2>&1 | tee -a "$TELEMETRY"
}

if [ -s "$SESSION_FILE" ]; then
  SESSION_ID=$(cat "$SESSION_FILE")
  SESSION_FLAG=(--resume "$SESSION_ID")
  echo "[RESUME] session $SESSION_ID"
  OPENING=$(cat <<'NUDGE'
This session was interrupted and has just been resumed. Re-read the End-of-Turn
Contract in AGENTS.md, then read LOG.md and PLAN.md to find where you left off.
Do not restart work that is already done and do not re-plan from scratch —
pick up the current milestone and continue.
NUDGE
)
else
  SESSION_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  printf '%s\n' "$SESSION_ID" > "$SESSION_FILE"
  SESSION_FLAG=(--session-id "$SESSION_ID")
  echo "[INIT] starting CRUX run"
  echo "  workspace:  $WORKSPACE"
  echo "  session:    $SESSION_ID"
  echo "  model:      $CRUX_MODEL (effort $CRUX_EFFORT)"
  OPENING=$(cat "$PROMPT_FILE")
fi

# 1. Opening turn: the launch prompt on a fresh run, a re-orientation nudge on a
#    resumed one. Ends in a normal End-of-Turn state.
echo "[OPEN] sending opening message"
run_turn "$OPENING" "${SESSION_FLAG[@]}"

# 2. Install the goal. Its Stop hook blocks this turn from ending until the
#    condition holds, so this call is the run — it returns when the project is
#    done (or when the turn dies, which systemd retries as a resume).
echo "[GOAL] installing stop condition — this turn runs until the goal is met"
run_turn "/goal $(cat "$GOAL_FILE")" --resume "$SESSION_ID"

echo "[END] goal turn returned (condition met, or the session ended early)"
RUNNER

chmod +x "$CLAUDE_RUNNER"
chown "$REAL_USER:$REAL_USER" "$CLAUDE_RUNNER"
echo "✔ Agent runner installed at ~/run-claude-agent.sh"

# ====== AGENT SERVICE ======
# Mirrors `openclaw gateway install` — the run is a supervised service, not a
# thing someone has to remember to launch. Restart=on-failure means a crashed
# turn resumes the same session (see run-claude-agent.sh); a clean exit means
# the goal was met, and the service correctly stays stopped.
cat > /etc/systemd/system/crux-agent.service <<CRUXUNIT
[Unit]
Description=CRUX research agent (Claude Code)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${REAL_USER}
WorkingDirectory=${CRUX_WORKSPACE}
EnvironmentFile=${CRUX_HOME}/env
ExecStart=${CLAUDE_RUNNER}
Restart=on-failure
RestartSec=60
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
CRUXUNIT

systemctl daemon-reload
systemctl enable --now crux-agent
echo "✔ crux-agent service installed and started"

# ====== SUMMARY ======
echo ""
echo "✔ Claude provisioning complete — the run is live."
echo ""
echo "  workspace:  $CRUX_WORKSPACE"
echo "  model:      $CRUX_MODEL (effort $CRUX_EFFORT)"
echo "  telemetry:  $TELEMETRY_PATH"
echo ""
echo "Watch it:      journalctl -u crux-agent -f"
echo "Turn stream:   tail -f $TELEMETRY_PATH"
echo "Stop the run:  systemctl stop crux-agent"
echo "Start over:    systemctl stop crux-agent && rm $CRUX_HOME/session-id && systemctl start crux-agent"
echo ""
