#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# configure.sh  –  runs ON the Linux (Ubuntu 24.04 LTS) EC2 instance
# ==========================================================================
# PER-RUN PHASE: applies all run-specific configuration on top of a box that
# already has the base software installed (either baked into an AMI by
# install.sh via build-ami.sh, or freshly installed by install.sh on raw
# Ubuntu). This phase ALSO installs OpenClaw itself at a pinned version (see
# the OPENCLAW section) — the pinned installer proved finnicky at bake time,
# and everything the openclaw CLI touches (~/.openclaw config, the plugin
# registration, the gateway unit) is per-run state anyway; install.sh
# deliberately never runs it.
#
# Consumes secrets and run config via env vars passed by
# create-new-crux-box.sh (build_remote_env): Telegram bot token/owner, LLM
# model + API keys, gog auth bundle, GitHub PAT, RunPod/refine.ink keys,
# workspace placeholders, cost-tracker URL. Starts the openclaw gateway last,
# once all config is written (the gateway's systemd unit is generated here
# too — see the GATEWAY section).
#
# Expected to be run as root (or via sudo) by create-new-crux-box.sh.
# Does NOT re-run any apt/software install steps (the OpenClaw install above
# is the one deliberate exception — see the OPENCLAW section).
# ==========================================================================

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME=$(eval echo "~$REAL_USER")

# ====== PLACEHOLDER RESOLUTION ======
# Operator-tunable values travel as {{KEY|default}} tokens. create-new-crux-box.sh
# forwards every non-secret config key as PLACEHOLDERS ("K=V|||K=V"). The harness
# workspace, the installed watchdog scripts (which live OUTSIDE the workspace) and
# configure.sh's own settings all resolve tokens through the two helpers below, in
# the same order, so one grammar covers all three — a token resolved in one place
# and left literal in another silently becomes a wrong default, which is the
# failure this centralises away.
AGENT_NAME="${AGENT_NAME:-crux}"
OPERATOR_NAME="${OPERATOR_NAME:-operator}"
OPENCLAW_WORKSPACE="$REAL_HOME/.openclaw/workspace"
TELEMETRY_PATH="$REAL_HOME/.openclaw/logs/telemetry.jsonl"

# placeholder_value '{{KEY|default}}' — print the operator's KEY from the config
# file if one was given, else the token's default. For values configure.sh
# consumes itself (jq arguments, crontab cadences) rather than files it edits.
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

# resolve_placeholders [-q] PATH... — resolve the tokens in files in place:
#   Step 1: operator-supplied values from PLACEHOLDERS (they win);
#   Step 2: environment-derived values (AGENT_NAME, OPERATOR_NAME, ...);
#   Step 3: whatever {{KEY|default}} is left takes its default.
# Directories are walked for *.md, *.sh and *.py; a file named explicitly is
# always processed. '#' is the sed delimiter so '|' inside defaults survives.
# -q suppresses the per-key "Placeholder resolved" lines (second callers).
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
    -e "s#{{OPERATOR_SHORT}}#$(sed_replacement_escape "$OPERATOR_NAME")#g" \
    -e "s#{{WORKSPACE_PATH}}#$(sed_replacement_escape "$OPENCLAW_WORKSPACE")#g" \
    -e "s#{{TELEMETRY_PATH}}#$(sed_replacement_escape "$TELEMETRY_PATH")#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 24.04 LTS EC2, amd64#g" \
    -e "s#{{COST_TRACKER_URL}}#$(sed_replacement_escape "${COST_TRACKER_URL:-}")#g" \
    -e "s#{{API_KEY_SUFFIX}}#$(sed_replacement_escape "${API_KEY_SUFFIX:-}")#g" \
    "${files[@]}"

  # --- Step 3: Auto-populate remaining {{KEY|default}} with their defaults ---
  # Any placeholder with a pipe-delimited default that wasn't resolved above
  # gets replaced with its default value (the part after the |).
  sed -i -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g' "${files[@]}"
}

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

# ====== OPENCLAW ======
# Install OpenClaw at a pinned version, skipping onboarding. This runs HERE
# (per-run), not at bake: the pinned installer proved finnicky at bake time,
# and everything the openclaw CLI touches — ~/.openclaw config, the plugin
# registration, the gateway unit — is per-run state anyway. install.sh bakes
# only openclaw-independent software (incl. the telemetry plugin clone+build,
# which we link into openclaw below).
#
# The official installer accepts OPENCLAW_VERSION (latest, next, or an exact
# version); without it you get whatever shipped most recently.
#
# We pin because new releases change the config schema and CLI behavior, and
# an unpinned install inherits those changes blind. When 2026.8.2 landed it
# removed heartbeat.skipWhenBusy (making our config invalid and blocking the
# gateway install), turned agents.list into the keyed agents.entries map, and
# added consent prompts to 'plugins install' that abort non-interactive runs.
#
# The config keys written below match THIS version. If you bump the pin,
# review this file's schema in the same change.
OPENCLAW_PINNED_VERSION="2026.8.2"
sudo -u "$REAL_USER" bash -c \
  "curl -fsSL https://openclaw.ai/install.sh | OPENCLAW_VERSION=$OPENCLAW_PINNED_VERSION bash -s -- --no-onboard"
# Hard-verify the pin took: a mismatched version means the version-sensitive
# config keys written later in this file may be silently ignored or rejected.
OPENCLAW_INSTALLED_VERSION=$(sudo -u "$REAL_USER" bash -lc 'openclaw --version' 2>/dev/null | head -1 || echo unknown)
case "$OPENCLAW_INSTALLED_VERSION" in
  *"$OPENCLAW_PINNED_VERSION"*)
    echo "✔ OpenClaw installed and pinned: $OPENCLAW_INSTALLED_VERSION" ;;
  *)
    echo "✘ OpenClaw version mismatch: pinned $OPENCLAW_PINNED_VERSION but installed '$OPENCLAW_INSTALLED_VERSION' — the installer ignored the pin or the release was pulled; aborting, config keys are version-sensitive" >&2
    exit 1 ;;
esac

# Copy exec-approvals config (unrestricted access for the agent), then migrate
# it: linux/src/exec-approvals.json is in the LEGACY JSON format, and OpenClaw
# 2026.8.x refuses to start the Telegram channel while an unmigrated legacy
# file exists ("Legacy exec approvals exist ... Run openclaw doctor --fix";
# the channel crash-loops inside an otherwise-active gateway, so nothing
# obvious fails at provision time). `doctor --fix` imports the file into the
# shared SQLite state and deletes the JSON. It must run HERE: before the
# gateway exists (doctor needs maintenance access it cannot take from a
# running service) and before the workspace copy (doctor also migrates
# workspace files like HEARTBEAT.md if it sees them — we don't want that).
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.openclaw"
cp "$SCRIPT_DIR/exec-approvals.json" "$REAL_HOME/.openclaw/exec-approvals.json"
chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.openclaw/exec-approvals.json"
sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" doctor --fix --non-interactive || true
# Mechanical check: a successful migration REMOVES the legacy JSON. If it is
# still there, the Telegram channel will crash-loop later — fail loudly now.
if [ -f "$REAL_HOME/.openclaw/exec-approvals.json" ]; then
  echo "✘ openclaw: legacy exec-approvals.json was not migrated (doctor --fix did not import+remove it) — the Telegram channel will refuse to start; inspect 'openclaw doctor' output" >&2
  exit 1
fi
echo "✔ exec approvals migrated into shared SQLite state (legacy JSON imported and removed)"

# Link the telemetry plugin (cloned, pinned and BUILT by install.sh at bake)
# into the freshly installed openclaw. `plugins install --link` writes
# plugins.load.paths and plugins.entries.telemetry-hal.enabled into
# openclaw.json; the TELEMETRY CONFIG section below merges the run config on
# top and leaves plugins.load.paths exactly as this wrote it. A missing or
# unbuilt repo is a hard error — the bake phase did not complete.
TELEMETRY_REPO_DIR="$REAL_HOME/openclaw-telemetry-hal"
if [ ! -f "$TELEMETRY_REPO_DIR/dist/index.js" ]; then
  echo "✘ telemetry-hal: $TELEMETRY_REPO_DIR/dist/index.js is missing — install.sh (bake) did not clone/build the plugin; re-run install.sh before configure.sh" >&2
  exit 1
fi
sudo -u "$REAL_USER" bash -c \
  "cd '$TELEMETRY_REPO_DIR' && '$REAL_HOME/.npm-global/bin/openclaw' plugins install . --link --force --accept-capabilities"
echo "✔ telemetry-hal linked into openclaw (built at bake, linked per run)"

# Install the ClawHub plugins the run config depends on, with capability
# consent. The gateway refuses to start ("Plugin X requires capability
# consent") if config references a plugin that is not installed+consented:
# codex is referenced by the .plugins.entries.codex block written below, and
# perplexity is demanded by the full tools profile. On the old flow these two
# existed only as hidden manual state baked into the AMI — the per-run
# OpenClaw install exposed that. If you add a config block for a new plugin,
# add its install here in the same change.
for hub_plugin in "clawhub:@openclaw/codex" "clawhub:@openclaw/perplexity-plugin"; do
  if ! sudo -u "$REAL_USER" "$REAL_HOME/.npm-global/bin/openclaw" plugins install "$hub_plugin" --accept-capabilities; then
    echo "✘ openclaw: failed to install $hub_plugin — the gateway will refuse to start without it" >&2
    exit 1
  fi
done
echo "✔ ClawHub plugins installed with capability consent: codex, perplexity"

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
   .agents.defaults.heartbeat.target = "none" |
    .agents.defaults.subagents.maxConcurrent = 5 |
    .tools.exec.security = "full" |
    .agents.entries.main.tools.exec.security = "full" |
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
echo "✔ Heartbeat configured: 30m, target=none (busy-skip is built-in; skipWhenBusy key removed in OpenClaw 2026.8.x)"
echo "✔ Subagents maxConcurrent: 5"
echo "✔ Exec security: tools.exec.security=full, agents.entries.main.tools.exec.security=full"
echo "✔ Codex app-server: approvalPolicy=never, sandbox=danger-full-access"

# ====== TELEMETRY CONFIG ======
# install.sh (bake) pins and builds the telemetry-hal plugin and patches its
# manifest for activation; the OPENCLAW section above linked it into the
# per-run openclaw install. `openclaw plugins
# install --link .` writes only plugins.load.paths and
# plugins.entries.telemetry-hal.enabled. That flag makes the gateway LOAD the
# plugin (hooks fire); the plugin's service reads
# plugins.entries.telemetry-hal.config.enabled and returns without starting when
# it is absent — two different switches. With only the first set, the service
# never starts: no rotation, no integrity, no llm.usage from the diagnostic bus,
# and hook events go only to the stamped+redacted fallback file. One run went a
# week with the service off. agent_end / llm_input / llm_output are
# "conversation" hooks: the gateway refuses them for non-bundled plugins unless
# hooks.allowConversationAccess=true sits at the ENTRY level (next to config, not
# inside it — inside config it fails schema validation and disables the whole
# plugin). The block is built inline below — it only needs the two host-side
# switches (enabled + hooks.allowConversationAccess), config.enabled, and the
# box-specific file path and rotation. redact.patterns / redact.enabled are the
# plugin's own defaults (redaction is on by default once the service starts), so
# they are not restated here. plugins.load.paths is left exactly as the install
# wrote it.
TELEMETRY_LOG_DIR="$REAL_HOME/.openclaw/logs"
TELEMETRY_FILE="$TELEMETRY_PATH"   # $REAL_HOME/.openclaw/logs/telemetry.jsonl (PLACEHOLDER RESOLUTION)
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

# FIXME(merge v3): OPENROUTER_API_KEY was added to the tool-key loop below during
# the v3 merge. create-new-crux-box.sh forwards it and AGENTS.md requires it for
# the experiments' LLM budget (the second, never-crossed budget). It was absent
# from main's configure.sh; verify on a live box that it lands in ~/.openclaw/.env.
# FIXME: investigate more here - confirm we treat OPENROUTER_API_KEY as a tool that we give the agent and NOT a way to power the agent
# NB: experiments run in the environment with ANTHROPIC_API_KEY and OPENROUTER_API_KEY
# we just need to make sure that experiments are running with open router and I think we're good to go

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

# ====== git (local commits, no remote) ======
# The agent keeps its work under local git version control (small, frequent
# commits) as the run's own history. No remote is configured and no host
# credentials are provisioned — nothing is pushed anywhere. Plain `git` is
# installed by install.sh; identity for commits is set here so the agent's
# commits are attributable in the local log (status.sh checks for it).
sudo -u "$REAL_USER" git config --global user.name "crux-agent"
sudo -u "$REAL_USER" git config --global user.email "crux-agent@localhost"
sudo -u "$REAL_USER" git config --global init.defaultBranch main
echo "✔ git configured for local commits (no remote, no credentials)"

# ====== HARNESS WORKSPACE ======
# Copy the run-harness workspace into the agent's OpenClaw workspace
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

  # Report any remaining unresolved placeholders (those without defaults).
  REMAINING=$(grep -rEn '\{\{[A-Z_]+(\|[^}]*)?\}\}' "$OPENCLAW_WORKSPACE" --include='*.md' --include='*.sh' --include='*.py' 2>/dev/null | grep -v 'grep for {{' | head -20 || true)
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
#
# These are installed HERE (run phase), not at bake, because their cadences and
# thresholds are per-run placeholders and the auth watchdog pages over the
# per-run Telegram credentials in openclaw.json.
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
echo "✔ Thinking-signature watchdog installed (cron */5 min)"
echo "✔ Auth watchdog installed (cron */5 min; ${AUTH_WATCHDOG_THRESHOLD} consecutive auth-class failed turns → gateway stopped + Telegram page; recovery: put the new key in ~/.openclaw/.env, then either re-run 'openclaw gateway install' (regenerates ~/.openclaw/gateway.systemd.env from the managed keys) or edit ~/.openclaw/gateway.systemd.env to match; systemctl --user start openclaw-gateway; then rm ~/.openclaw/watchdog/auth-halt)"
echo "✔ Session-store snapshot installed (cron */${SESSION_SNAPSHOT_MINUTES} min → ~/.openclaw/session-snapshots/; utils/export-run.sh merges the copies at end of run)"
echo "✔ Final-pass injector installed (cron */5 — triggers on workspace COMPLETION_REPORT.md)"

# ====== GATEWAY ======
# The gateway is installed HERE, not at bake time. Installing it before config
# exists lays down a broken placeholder unit systemd reports as masked (a 0-byte
# unit file) that can't be unmasked and fights every launch. So install.sh skips
# it, and we generate the unit here from a clean slate now that gateway.mode=local
# and all secrets are written above.
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

# Tighten permissions before installing the gateway unit. OpenClaw refuses to
# write its systemd service if ~/.config/systemd/user — or whichever parent of
# it already exists — can be written by group or other (the error is
# SERVICE_DEFINITION_UNKNOWN: [unsafe-permissions]). That's exactly what
# happens on a stock Ubuntu box: the default umask creates ~/.config as 775.
# So: strip group/other write from ~/.config, then create the systemd dirs
# as private (700) so the install has a safe path all the way down.
chmod go-w "$REAL_HOME/.config" 2>/dev/null || true
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.config/systemd/user"
chmod 700 "$REAL_HOME/.config/systemd" "$REAL_HOME/.config/systemd/user"
chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd"

# Generate the unit. --force is defensive: if this box was launched from an older
# AMI that DID bake a (masked/placeholder) unit, --force overwrites it cleanly;
# on a current AMI there's nothing there and it just installs. Clear any stale
# wants-symlink and user-level mask first so we start from a known state.
rm -f "$REAL_HOME/.config/systemd/user/default.target.wants/openclaw-gateway.service" 2>/dev/null || true
uctl systemctl --user unmask openclaw-gateway.service >/dev/null 2>&1 || true
uctl "$REAL_HOME/.npm-global/bin/openclaw" gateway install --force

# --- Restart-policy hardening (fixes the migration crash-loop) ---
# On first boot the gateway runs one-time SQLite startup migrations that take
# ~15-30s and hold a lock. The generated unit ships RestartSec=5, so if the
# process is (re)started while a migration is mid-flight, systemd relaunches it in
# 5s; the new instance sees "startup migrations are already running ... retry
# after <~4 min from now>", exits 1, and this LOOPS — the retry deadline keeps
# getting pushed, so it never converges until that 4-min window happens to pass.
# A drop-in override lengthens RestartSec so a crashed/slow start backs off long
# enough for migrations to finish, and widens the start-rate limit so the unit
# isn't wedged as "start-limit-hit". Drop-ins don't touch the generated unit, so
# a future `gateway install --force` won't clobber this.
OVERRIDE_DIR="$REAL_HOME/.config/systemd/user/openclaw-gateway.service.d"
sudo -u "$REAL_USER" mkdir -p "$OVERRIDE_DIR"
cat > "$OVERRIDE_DIR/10-migration-backoff.conf" <<'OVERRIDE'
[Service]
# Back off long enough that a first-boot migration completes uninterrupted
# instead of being relaunched into an "already running" lock every 5s.
RestartSec=45
# Give a slow migration room before systemd's start timeout fires.
TimeoutStartSec=180

[Unit]
# Allow more attempts over a much wider window so transient first-boot crashes
# don't trip the start-rate limiter and wedge the unit.
StartLimitBurst=10
StartLimitIntervalSec=600
OVERRIDE
chown -R "$REAL_USER:$REAL_USER" "$OVERRIDE_DIR"

uctl systemctl --user daemon-reload || true
uctl systemctl --user enable openclaw-gateway.service || true

# Clear any leftover failed/rate-limited state, then start once.
uctl systemctl --user reset-failed openclaw-gateway.service >/dev/null 2>&1 || true
uctl systemctl --user restart openclaw-gateway.service

# Verify. Poll for up to ~90s: first-boot migrations mean "active" can lag, and
# with the 45s backoff a legitimately-recovering start needs room. We check for a
# STABLE active state (not mid-migration-crash) before declaring success.
gw_ok=""
for _ in $(seq 1 18); do
  state=$(uctl systemctl --user is-active openclaw-gateway.service 2>/dev/null || true)
  if [ "$state" = "active" ]; then
    gw_ok=1; break
  fi
  sleep 5
done
if [ -n "$gw_ok" ]; then
  echo "✔ openclaw gateway active with full run config"
else
  echo "✘ openclaw gateway failed to reach active — dumping status + logs:" >&2
  uctl systemctl --user --no-pager status openclaw-gateway.service >&2 || true
  uctl journalctl --user -u openclaw-gateway.service --no-pager -n 40 >&2 || true
  exit 1
fi

echo ""
echo "✔ Linux CRUX-in-a-box bootstrap complete."
