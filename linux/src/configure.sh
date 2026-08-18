#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# configure.sh  –  runs ON the Linux (Ubuntu 24.04 LTS) EC2 instance
# ==========================================================================
# PER-RUN PHASE: applies all run-specific configuration on top of a box that
# already has the software installed (either baked into an AMI by install.sh
# via build-ami.sh, or freshly installed by install.sh on raw Ubuntu).
#
# Consumes secrets and run config via env vars passed by
# create-new-crux-box.sh (build_remote_env): Telegram bot token/owner, LLM
# model + API keys, gog auth bundle, GitHub PAT, RunPod/refine.ink keys,
# workspace placeholders, cost-tracker URL. Starts the openclaw gateway last,
# once all config is written (the gateway's systemd unit is already installed by
# install.sh at bake time; this phase only writes secrets and starts it).
#
# Expected to be run as root (or via sudo) by create-new-crux-box.sh.
# Does NOT re-run any apt/software install steps.
# ==========================================================================

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

# ====== VALIDATE REQUIRED SECRETS / CONFIG (before touching anything) ======
if [ -z "${DEFAULT_LLM_MODEL:-}" ]; then
  echo "Error: DEFAULT_LLM_MODEL is required (e.g. anthropic/claude-opus-4-8 or openai/gpt-4o)" >&2
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY or OPENAI_API_KEY is required" >&2
  exit 1
fi

if [ -z "${GOG_KEYRING_PASSWORD:-}" ]; then
  echo "Error: GOG_KEYRING_PASSWORD is required but not set (passed by create-new-crux-box.sh from the config file)." >&2
  exit 1
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


# ====== AI Provider and Model ======
# Requires DEFAULT_LLM_MODEL and one of ANTHROPIC_API_KEY or OPENAI_API_KEY
# (validated above; passed in by create-new-crux-box.sh).

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
CACHE_RETENTION="long"

# Per-request LLM idle watchdog. OpenClaw's default is 120s
# (DEFAULT_LLM_IDLE_TIMEOUT_MS) — too short for heavy reasoning (xhigh/max), whose
# pre-stream thinking pause can exceed 2 min and trip a "model idle timeout".
# models.providers.<id>.timeoutSeconds is the knob that RAISES the watchdog
# (agents.defaults.timeoutSeconds only bounds it DOWN; its 48-min default is fine).
# 600s covers xhigh; raise toward ~900+ if you switch the default to max. Provider
# id = the part before the "/".
PROVIDER_ID="${DEFAULT_LLM_MODEL%%/*}"
PROVIDER_REQUEST_TIMEOUT_SECONDS=600

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
    .agents.defaults.subagents.maxConcurrent = 5 |
    .tools.exec.security = "full" |
    (.agents.list //= []) |
    if any(.agents.list[]; .id == "main") then
      .agents.list = [.agents.list[] | if .id == "main" then .tools.exec.security = "full" else . end]
    else
      .agents.list += [{"id": "main", "tools": {"exec": {"security": "full"}}}]
    end |
    .plugins.entries.codex.config.appServer.approvalPolicy = "never" |
    .plugins.entries.codex.config.appServer.sandbox = "danger-full-access"
' "$OPENCLAW_CONFIG" > "$TMP_CONFIG" \
  && mv "$TMP_CONFIG" "$OPENCLAW_CONFIG"
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_CONFIG"
echo "✔ Model configured: $DEFAULT_LLM_MODEL"
echo "✔ Extended thinking configured: thinkingDefault=$THINKING_LEVEL (verify key vs pinned OpenClaw; unknown key => thinking stays off, run unaffected)"
echo "✔ Prompt cache retention: $CACHE_RETENTION (1h TTL — survives the 30m heartbeat gaps)"
echo "✔ Provider request timeout: ${PROVIDER_REQUEST_TIMEOUT_SECONDS}s for '$PROVIDER_ID' (raises the 120s idle watchdog for heavy reasoning)"
echo "✔ Tools profile set to full"
echo "✔ Heartbeat configured: 30m, skipWhenBusy, target=none"
echo "✔ Subagents maxConcurrent: 5"
echo "✔ Exec security: tools.exec.security=full, agents.list[main].tools.exec.security=full"
echo "✔ Codex app-server: approvalPolicy=never, sandbox=danger-full-access"

# Append the LLM API key to ~/.openclaw/.env for daemon/gateway use.
# Write whichever of ANTHROPIC_API_KEY / OPENAI_API_KEY is set; both if both.
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
chown "$REAL_USER:$REAL_USER" "$OPENCLAW_ENV"
chmod 600 "$OPENCLAW_ENV"

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
# was scp'd by create-new-crux-box.sh into the real user's home; unpack it into
# a fixed GOG_HOME and wire the file-keyring env into ~/.openclaw/.env so gog is
# authenticated with no browser. Missing inputs are a hard error (set -e aborts;
# GOG_KEYRING_PASSWORD was validated at the top).

# create-new-crux-box.sh passes "$HOME/gog-home.tar.gz"; resolve it under the real home.
GOG_TARBALL_PATH="${REAL_HOME}/gog-home.tar.gz"
if [ ! -f "$GOG_TARBALL_PATH" ]; then
  echo "Error: gog auth bundle not found at $GOG_TARBALL_PATH — expected create-new-crux-box.sh to scp it (create it with utils/bootstrap-gog.sh)." >&2
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

# ====== GitHub auth (gh CLI + git over HTTPS) ======
# Authenticate gh non-interactively with the classic PAT passed by
# create-new-crux-box.sh. Run as the real user (gh stores creds under
# ~/.config/gh), wire up git's credential helper, and also export the token in
# ~/.openclaw/.env (GITHUB_TOKEN/GH_TOKEN are what gh, git, and most tooling
# read) so the agent's tool subprocesses can push/pull and call the GitHub API.
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

# ====== HARNESS WORKSPACE ======
# Copy the run-harness workspace into the agent's OpenClaw workspace
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
  AGENT_NAME="crux"
  OPERATOR_NAME="operator"
  find "$OPENCLAW_WORKSPACE" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -exec sed -i \
    -e "s#{{AGENT_NAME}}#${AGENT_NAME}#g" \
    -e "s#{{OPERATOR_NAME}}#${OPERATOR_NAME}#g" \
    -e "s#{{OPERATOR_SHORT}}#${OPERATOR_NAME}#g" \
    -e "s#{{WORKSPACE_PATH}}#${OPENCLAW_WORKSPACE}#g" \
    -e "s#{{TELEMETRY_PATH}}#${REAL_HOME}/.openclaw/telemetry/telemetry.jsonl#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 24.04 LTS EC2, amd64#g" \
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

# ====== GATEWAY ======
# install.sh runs `openclaw gateway install` at bake time, but that happens
# BEFORE gateway.mode/config exists, so it lays down a placeholder unit that
# systemd reports as MASKED — specifically a 0-byte empty regular file at
# ~/.config/systemd/user/openclaw-gateway.service (systemd treats an empty unit
# file as masked, the SAME as a /dev/null symlink). `systemctl --user unmask`
# only clears /dev/null-symlink masks, NOT empty-file masks, so it can't repair
# this — the fix is to REGENERATE the unit now that config is complete.
#
# We've written gateway.mode=local and all secrets above, so `gateway install
# --force` regenerates a proper, non-masked unit. Then enable + start it.
REAL_UID=$(id -u "$REAL_USER")
loginctl enable-linger "$REAL_USER" || true

# Wrap the per-user systemctl/openclaw invocation once — every call needs the
# same runtime dir + bus for the --user manager to be reachable non-interactively.
uctl() {
  sudo -u "$REAL_USER" \
    XDG_RUNTIME_DIR="/run/user/${REAL_UID}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${REAL_UID}/bus" \
    "$@"
}

# Regenerate the unit with config present. --force overwrites the baked
# placeholder (empty/masked) unit with a real one. Remove the stale wants-symlink
# first so the reinstall + enable start from a clean slate.
rm -f "$REAL_HOME/.config/systemd/user/default.target.wants/openclaw-gateway.service" 2>/dev/null || true
uctl systemctl --user unmask openclaw-gateway.service >/dev/null 2>&1 || true
uctl "$REAL_HOME/.npm-global/bin/openclaw" gateway install --force
uctl systemctl --user daemon-reload || true
uctl systemctl --user enable openclaw-gateway.service || true

# Clear any leftover failed/rate-limited state from prior attempts, then start.
# (Not `restart`: a fresh install has nothing running to restart, and start is
# the documented path; reset-failed avoids systemd's "start repeated too quickly"
# rate-limit if configure.sh is re-run.)
uctl systemctl --user reset-failed openclaw-gateway.service >/dev/null 2>&1 || true
uctl systemctl --user restart openclaw-gateway.service

# Verify. The gateway runs one-time startup migrations on first boot that briefly
# hold a lock; give it a few seconds and poll rather than judging on the first
# read, so a slow-but-healthy start isn't misreported as a failure.
gw_ok=""
for _ in 1 2 3 4 5 6; do
  if uctl systemctl --user is-active openclaw-gateway.service >/dev/null 2>&1; then
    gw_ok=1; break
  fi
  sleep 5
done
if [ -n "$gw_ok" ]; then
  echo "✔ openclaw gateway active with full run config"
else
  echo "✘ openclaw gateway failed to start — dumping status + logs:" >&2
  uctl systemctl --user --no-pager status openclaw-gateway.service >&2 || true
  uctl journalctl --user -u openclaw-gateway.service --no-pager -n 30 >&2 || true
  exit 1
fi

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."
