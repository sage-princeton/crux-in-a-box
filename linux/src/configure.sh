#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# configure.sh  –  per-run bootstrap for a CRUX AMI-based instance
# ==========================================================================
# Runs on a pre-built CRUX AMI instance (launched by setup-device.sh with
# CRUX_AMI_ID set). All static software is already installed; this script
# only injects per-run secrets, config, and the harness workspace.
#
# Equivalent to the bottom half of start.sh. Does NOT install any packages.
#
# Called by setup-device.sh as:
#   sudo <ENV_VARS> bash ~/crux-in-a-box-linux/src/configure.sh
#
# Required env vars (same contract as start.sh):
#   DEFAULT_LLM_MODEL, ANTHROPIC_API_KEY or OPENAI_API_KEY,
#   TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID, COST_TRACKER_URL,
#   RUNPOD_API_KEY, REFINE_INK_API_KEY,
#   GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN,
#   GOG_ACCOUNT, GOG_KEYRING_PASSWORD, GOG_HOME_TARBALL,
#   API_KEY_SUFFIX, PLACEHOLDERS
# ==========================================================================

REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

# ====== VNC ======
# The VNC service unit is installed in the AMI but not enabled. Enable it now
# so VNC survives reboots. The password and xstartup are already baked in.
systemctl enable --now vncserver@1
echo "✔ VNC service enabled"

# ====== exec-approvals ======
# Copy from the canonical AMI location (/opt/crux/) — no dependency on the
# linux/ directory being present.
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"
cp /opt/crux/exec-approvals.json "$REAL_HOME/.openclaw/exec-approvals.json"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.openclaw/exec-approvals.json"
echo "✔ exec-approvals.json installed"

# ====== TELEGRAM ======
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  OPENCLAW_CONFIG="$REAL_HOME/.openclaw/openclaw.json"
  sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"
  if [ ! -f "$OPENCLAW_CONFIG" ]; then
    echo '{}' > "$OPENCLAW_CONFIG"
    chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
  fi
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

# ====== AI Provider and Model ======
if [ -z "${DEFAULT_LLM_MODEL:-}" ]; then
  echo "Error: DEFAULT_LLM_MODEL is required" >&2
  exit 1
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY or OPENAI_API_KEY is required" >&2
  exit 1
fi

OPENCLAW_CONFIG="$REAL_HOME/.openclaw/openclaw.json"
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"
if [ ! -f "$OPENCLAW_CONFIG" ]; then
  echo '{}' > "$OPENCLAW_CONFIG"
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
fi

THINKING_LEVEL="${REASONING_EFFORT:-xhigh}"
CACHE_RETENTION="${CACHE_RETENTION:-long}"
PROVIDER_ID="${DEFAULT_LLM_MODEL%%/*}"
PROVIDER_REQUEST_TIMEOUT_SECONDS="${PROVIDER_REQUEST_TIMEOUT_SECONDS:-600}"
AGENT_TURN_TIMEOUT_SECONDS="${AGENT_TURN_TIMEOUT_SECONDS:-3600}"

TMP_CONFIG=$(mktemp)
jq --arg model "$DEFAULT_LLM_MODEL" --arg reasoning "$THINKING_LEVEL" \
   --arg cache "$CACHE_RETENTION" --arg provider "$PROVIDER_ID" \
   --argjson ptimeout "$PROVIDER_REQUEST_TIMEOUT_SECONDS" '
  .gateway.mode = "local" |
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
echo "✔ Model configured: $DEFAULT_LLM_MODEL"
echo "✔ Extended thinking: thinkingDefault=$THINKING_LEVEL"
echo "✔ Prompt cache retention: $CACHE_RETENTION"
echo "✔ Provider request timeout: ${PROVIDER_REQUEST_TIMEOUT_SECONDS}s for '$PROVIDER_ID'"
echo "✔ Tools profile: full | Heartbeat: 30m | Subagents: 5"

# ====== API KEYS → ~/.openclaw/.env ======
OPENCLAW_ENV="$REAL_HOME/.openclaw/.env"
if [ -f "$OPENCLAW_ENV" ]; then
  sed -i '/^ANTHROPIC_API_KEY=/d' "$OPENCLAW_ENV"
  sed -i '/^OPENAI_API_KEY=/d' "$OPENCLAW_ENV"
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" >> "$OPENCLAW_ENV"
  echo "✔ Anthropic API key written to ~/.openclaw/.env"
fi
if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY=${OPENAI_API_KEY}" >> "$OPENCLAW_ENV"
  echo "✔ OpenAI API key written to ~/.openclaw/.env"
fi

for kv in "RUNPOD_API_KEY=${RUNPOD_API_KEY:-}" "REFINE_INK_API_KEY=${REFINE_INK_API_KEY:-}"; do
  name="${kv%%=*}"; val="${kv#*=}"
  if [ -z "$val" ]; then
    echo "⚠ ${name} not provided — skipping"
    continue
  fi
  sed -i "/^${name}=/d" "$OPENCLAW_ENV"
  echo "${name}=${val}" >> "$OPENCLAW_ENV"
  echo "✔ ${name} written to ~/.openclaw/.env"
done
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"

# ====== gog auth ======
if [ -z "${GOG_KEYRING_PASSWORD:-}" ]; then
  echo "Error: GOG_KEYRING_PASSWORD is required" >&2
  exit 1
fi
GOG_TARBALL_PATH="${REAL_HOME}/gog-home.tar.gz"
if [ ! -f "$GOG_TARBALL_PATH" ]; then
  echo "Error: gog auth bundle not found at $GOG_TARBALL_PATH" >&2
  exit 1
fi
GOG_HOME_DIR="$REAL_HOME/.openclaw/gogcli"
sudo -u "$REAL_USER" mkdir -p "$GOG_HOME_DIR"
chmod 700 "$GOG_HOME_DIR"
sudo -u "$REAL_USER" tar xzf "$GOG_TARBALL_PATH" -C "$GOG_HOME_DIR"
chown -R "$REAL_USER:$REAL_USER" "$GOG_HOME_DIR"
rm -f "$GOG_TARBALL_PATH"
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

# ====== GATEWAY ======
# All config is written above. One install + one restart — no intermediate starts.
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway install
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" gateway restart

# ====== GitHub auth ======
if [ -n "${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN:-}" ]; then
  if command -v gh >/dev/null 2>&1; then
    if printf '%s' "$GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN" \
         | sudo -u "$REAL_USER" gh auth login --hostname github.com --git-protocol https --with-token; then
      sudo -u "$REAL_USER" gh auth setup-git || true
      echo "✔ gh CLI authenticated"
    else
      echo "⚠ gh auth login failed — check the PAT."
    fi
  fi
  for name in GITHUB_TOKEN GH_TOKEN; do
    sed -i "/^${name}=/d" "$OPENCLAW_ENV"
    echo "${name}=${GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN}" >> "$OPENCLAW_ENV"
  done
  chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
  chmod 600 "$OPENCLAW_ENV"
  echo "✔ GITHUB_TOKEN/GH_TOKEN written to ~/.openclaw/.env"
else
  echo "⚠ GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN not provided."
fi

# ====== HARNESS WORKSPACE ======
HARNESS_SRC="$REAL_HOME/crux-in-a-box-harness/workspace"
OPENCLAW_WORKSPACE="$REAL_HOME/.openclaw/workspace"

if [ -d "$HARNESS_SRC" ]; then
  sudo -u "$REAL_USER" mkdir -p "$OPENCLAW_WORKSPACE"
  sudo -u "$REAL_USER" cp -r "$HARNESS_SRC"/* "$OPENCLAW_WORKSPACE/"
  chown -R "$REAL_USER:$REAL_USER" "$OPENCLAW_WORKSPACE"
  chmod +x "$OPENCLAW_WORKSPACE/scripts/"*.sh 2>/dev/null || true

  # Step 1: user-supplied placeholders
  if [ -n "${PLACEHOLDERS:-}" ]; then
    IFS='|||' read -ra PAIRS <<< "$PLACEHOLDERS"
    for PAIR in "${PAIRS[@]}"; do
      [ -z "$PAIR" ] && continue
      KEY="${PAIR%%=*}"
      VALUE="${PAIR#*=}"
      find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec sed -i \
        -e "s#{{${KEY}}}#${VALUE}#g" \
        -e "s#{{${KEY}|[^}]*}}#${VALUE}#g" \
        {} +
      echo "✔ Placeholder resolved: ${KEY}=${VALUE}"
    done
  fi

  # Step 2: environment-derived placeholders
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
  echo "✔ Environment placeholders resolved"

  # Step 3: remaining {{KEY|default}} → default
  find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec \
    sed -i -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g' {} +
  echo "✔ Remaining defaults auto-populated"
  echo "✔ Harness workspace copied to $OPENCLAW_WORKSPACE"

  REMAINING=$(grep -rn '{{' "$OPENCLAW_WORKSPACE" --include='*.md' --include='*.sh' --include='*.py' 2>/dev/null | grep -v 'grep for {{' | head -20 || true)
  if [ -n "$REMAINING" ]; then
    echo ""
    echo "⚠ Unresolved placeholders (resolve manually before launch):"
    echo "$REMAINING"
  fi
else
  echo "⚠ Harness workspace not found at $HARNESS_SRC — skipping"
fi

# ====== THINKING-SIGNATURE WATCHDOG ======
WATCHDOG_DIR="$REAL_HOME/.openclaw/watchdog"
sudo -u "$REAL_USER" mkdir -p "$WATCHDOG_DIR"
cp /opt/crux/crux-thinking-watchdog.sh "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
chown "$REAL_USER:$REAL_USER" "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
chmod +x "$WATCHDOG_DIR/crux-thinking-watchdog.sh"
sudo -u "$REAL_USER" bash -c \
  '(crontab -l 2>/dev/null | grep -v crux-thinking-watchdog; echo "*/5 * * * * $HOME/.openclaw/watchdog/crux-thinking-watchdog.sh") | crontab -'
echo "✔ Thinking-signature watchdog installed (cron */5 min)"

echo ""
echo "✔ CRUX AMI instance configured and ready."
