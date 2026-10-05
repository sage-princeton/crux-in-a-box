#!/usr/bin/env bash
set -euo pipefail

# Configure a workspace as root. Copy the secrets file again before each run.
#
# Per-run secrets arrive at RUN_SECRETS_PATH and are deleted after reading.
# Shared Langfuse credentials come from SYSTEM_SSM_PARAM via crux-system-role.
#
# Required environment: AWS_REGION, RUN_SECRETS_PATH, SYSTEM_SSM_PARAM,
# RUN_SLUG, CONTROL_MCP_BASE and the selected platform's model and effort.
# AGENT_PLATFORM defaults to codex; Codex also requires its tracing pin and hash.
# Optional CODEX_TRACE_MODE: stop-hook (default, the tracing plugin's Stop hook)
# or live (codex-live-trace.py streams each observation as it completes).
# Place agent-config.sh beside this script; Codex also requires
# codex-flush-turns.py, codex-live-trace.py and live_trace/, and Claude requires langfuse_hook.py.

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${AWS_REGION:?}" "${RUN_SECRETS_PATH:?}" "${SYSTEM_SSM_PARAM:?}" \
  "${RUN_SLUG:?}" "${CONTROL_MCP_BASE:?}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=src/ec2-workspaces/agent-config.sh
source "$SCRIPT_DIR/agent-config.sh"
cfg() { local key="$1"; printf '%s' "${!key:-}"; }
load_agent_config settings
if [ "$AGENT_PLATFORM" = codex ]; then
  : "${TRACING_PLUGIN_VERSION:?}" "${TRACING_HOOK_TRUSTED_HASH:?}"
  [ -f "$SCRIPT_DIR/codex-flush-turns.py" ] || die "codex-flush-turns.py was not copied alongside configure-run.sh."
  CODEX_TRACE_MODE="${CODEX_TRACE_MODE:-stop-hook}"
  case "$CODEX_TRACE_MODE" in
    stop-hook) ;;
    live)
      if [ ! -f "$SCRIPT_DIR/codex-live-trace.py" ] || [ ! -f "$SCRIPT_DIR/live_trace/__init__.py" ]; then
        die "codex-live-trace.py and live_trace/ were not copied alongside configure-run.sh."
      fi ;;
    *) die "CODEX_TRACE_MODE must be stop-hook|live (got '$CODEX_TRACE_MODE')." ;;
  esac
  command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH; the gateway's trace flush needs it."
else
  [ -f "$SCRIPT_DIR/langfuse_hook.py" ] || die "Claude Langfuse hook was not copied alongside configure-run.sh."
fi
# Every config/credential file starts private, including during its creation.
umask 077

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"
WORK_DIR=/srv/crux-run          # acp-gateway's cwd; holds .mcp.json
CODEX_DIR="$RUN_HOME/.codex"
PROBE_LOG=/var/log/crux-agent-probe.log

# ====== PER-RUN SECRETS FROM THE SCP'D FILE ======
# Delivered over the same SSH channel that delivered this script; deleted as
# soon as the values are held in shell variables. They persist only in the
# mode-600 files written below.
info "Reading per-run secrets from $RUN_SECRETS_PATH"
[ -f "$RUN_SECRETS_PATH" ] \
  || die "$RUN_SECRETS_PATH not found. provision-workspace-aws-resources.sh scps it before running this script; a manual re-run needs it scp'd again."
SECRETS="$(cat "$RUN_SECRETS_PATH")"

get() { printf '%s' "$SECRETS" | jq -re --arg k "$1" '.[$k] // empty'; }

validate_agent_key "$RUN_SECRETS_PATH"
validate_run_api_keys "$RUN_SECRETS_PATH"
AGENT_API_KEY="$(get "$API_KEY_NAME")" || die "$API_KEY_NAME missing from $RUN_SECRETS_PATH"
WORKSPACE_ID="$(get AGENTRQ_WORKSPACE_ID)"  || die "AGENTRQ_WORKSPACE_ID missing from $RUN_SECRETS_PATH"
WORKSPACE_TOKEN="$(get AGENTRQ_WORKSPACE_TOKEN)" || die "AGENTRQ_WORKSPACE_TOKEN missing from $RUN_SECRETS_PATH"
RUN_API_KEYS="$(run_api_keys_json "$RUN_SECRETS_PATH")"
rm -f "$RUN_SECRETS_PATH"
ok "Read 3 per-run values plus per-run API keys [$(printf '%s' "$RUN_API_KEYS" | jq -r 'keys | join(", ")')] (values not echoed); deleted $RUN_SECRETS_PATH"

# ====== SYSTEM-WIDE SECRETS FROM PARAMETER STORE ======
# Shared by every run box; read via the instance's crux-system-role, whose
# only privilege is GetParameter on this one path.
info "Fetching system config from SSM $SYSTEM_SSM_PARAM"
SYS="$(aws ssm get-parameter --region "$AWS_REGION" --name "$SYSTEM_SSM_PARAM" \
  --with-decryption --query 'Parameter.Value' --output text)" \
  || die "Could not read $SYSTEM_SSM_PARAM. Upload it once: provision-workspace-aws-resources.sh --put-system-secrets <file>"

sys() { printf '%s' "$SYS" | jq -re --arg k "$1" '.[$k] // empty'; }

LANGFUSE_PUBLIC_KEY="$(sys LANGFUSE_PUBLIC_KEY)" || die "LANGFUSE_PUBLIC_KEY missing from $SYSTEM_SSM_PARAM"
LANGFUSE_SECRET_KEY="$(sys LANGFUSE_SECRET_KEY)" || die "LANGFUSE_SECRET_KEY missing from $SYSTEM_SSM_PARAM"
LANGFUSE_BASE_URL="$(sys LANGFUSE_BASE_URL)"
LANGFUSE_BASE_URL="${LANGFUSE_BASE_URL:-https://us.cloud.langfuse.com}"
ok "Read 3 system values (not echoed)"

# ====== WORK DIR AND .mcp.json ======
# acp-gateway finds its workspace by searching for .mcp.json in the cwd and up
# to three directories above, so the gateway's WorkingDirectory must be here.
info "Work dir $WORK_DIR"
mkdir -p "$WORK_DIR"
MCP_URL="${CONTROL_MCP_BASE}/mcp/${WORKSPACE_ID}?token=${WORKSPACE_TOKEN}"
jq -n --arg id "$WORKSPACE_ID" --arg url "$MCP_URL" \
  '{mcpServers: {($id): {type: "http", url: $url}}}' > "$WORK_DIR/.mcp.json"
chown -R "$RUN_USER:$RUN_USER" "$WORK_DIR"
# The URL embeds the workspace token, so this is a credential file.
chmod 600 "$WORK_DIR/.mcp.json"
ok "Wrote .mcp.json -> ${CONTROL_MCP_BASE}/mcp/${WORKSPACE_ID}?token=<redacted> (mode 600)"

# ====== GATEWAY ENVIRONMENT ======
# systemd reads this root-only file; credential values are quoted, not shell code.
AGENT_ENV="$(jq -cn --arg platform "$AGENT_PLATFORM" --arg provider "$MODEL_PROVIDER" \
  --arg name "$API_KEY_NAME" --arg key "$AGENT_API_KEY" --arg model "$MODEL" '
  if $platform == "claude" and $provider == "openrouter" then {
    ANTHROPIC_BASE_URL: "https://openrouter.ai/api",
    ANTHROPIC_AUTH_TOKEN: $key,
    ANTHROPIC_API_KEY: "",
    ANTHROPIC_DEFAULT_FABLE_MODEL: $model,
    ANTHROPIC_DEFAULT_OPUS_MODEL: $model,
    ANTHROPIC_DEFAULT_SONNET_MODEL: $model,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: $model,
    CLAUDE_CODE_SUBAGENT_MODEL: $model
  } else {($name): $key} end')"
check_run_api_key_clashes "$RUN_API_KEYS" "$AGENT_ENV"
GW_ENV=/etc/crux-run.env
{
  printf '%s' "$AGENT_ENV" | jq -r 'to_entries[] | .key + "=" + (.value | @json)'
  printf '%s' "$RUN_API_KEYS" | jq -r 'to_entries[] | .key + "=" + (.value | @json)'
  printf 'PATH=%s/.local/bin:/usr/local/bin:/usr/bin:/bin\nHOME=%s\n' "$RUN_HOME" "$RUN_HOME"
  if [ "$AGENT_PLATFORM" = claude ]; then
    printf 'CLAUDE_CODE_EXECUTABLE=/usr/bin/claude\n'
  fi
} > "$GW_ENV"
chmod 600 "$GW_ENV"

# Provisioning settings are a snapshot; each generation separately records
# the model reported by the agent transcript.
TRACE_METADATA="$(jq -cn --arg workspace "$WORKSPACE_ID" --arg slug "$RUN_SLUG" \
  --arg platform "$AGENT_PLATFORM" --arg model "$MODEL" --arg effort "$EFFORT" \
  '{workspaceId: $workspace, runSlug: $slug, agentPlatform: $platform,
    configuredModel: $model, configuredEffort: $effort}')"

if [ "$AGENT_PLATFORM" = codex ]; then
# ====== CODEX CONFIG ======
info "codex config"
mkdir -p "$CODEX_DIR"

# Use the home-directory tracing config for sessions in any working directory.
jq -n --arg pk "$LANGFUSE_PUBLIC_KEY" --arg sk "$LANGFUSE_SECRET_KEY" \
      --arg url "$LANGFUSE_BASE_URL" --arg env "$RUN_SLUG" --argjson metadata "$TRACE_METADATA" \
  '{enabled: true, public_key: $pk, secret_key: $sk, base_url: $url,
    environment: $env, user_id: $env, metadata: $metadata,
    tags: [("workspace:" + $metadata.workspaceId), ("run:" + $metadata.runSlug),
           ("platform:" + $metadata.agentPlatform)]}' > "$CODEX_DIR/langfuse.json"
chmod 600 "$CODEX_DIR/langfuse.json"

# Set the tracing environment and user ID to the run slug. Live mode replaces the
# plugin's Stop hook, so the plugin is disabled there.
PLUGIN_ENABLED=true
if [ "$CODEX_TRACE_MODE" = live ]; then
  PLUGIN_ENABLED=false
fi
cat > "$CODEX_DIR/config.toml" <<TOML
personality = "pragmatic"
model = "$CODEX_MODEL"
model_reasoning_effort = "$CODEX_REASONING_EFFORT"
model_provider = "${MODEL_PROVIDER/direct/openai}"

[features]
hooks = true

[plugins."tracing@codex-observability-plugin"]
enabled = $PLUGIN_ENABLED

# Pin trusted_hash to authorize the Stop hook on unattended instances.
[hooks.state."tracing@codex-observability-plugin:hooks/hooks.json:stop:0:0"]
trusted_hash = "$TRACING_HOOK_TRUSTED_HASH"
enabled = true

[projects."$WORK_DIR"]
trust_level = "trusted"
TOML

if [ "$MODEL_PROVIDER" = openrouter ]; then
  cat >> "$CODEX_DIR/config.toml" <<'TOML'

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
wire_api = "responses"
supports_websockets = false

[model_providers.openrouter.auth]
command = "printenv"
args = ["OPENROUTER_API_KEY"]
TOML
  export OPENROUTER_API_KEY="$AGENT_API_KEY"
fi

chown -R "$RUN_USER:$RUN_USER" "$CODEX_DIR"
ok "Wrote langfuse.json (environment=$RUN_SLUG) and config.toml (model=$CODEX_MODEL, effort=$CODEX_REASONING_EFFORT)"

# ====== OBSERVABILITY PLUGIN ======
# Install the plugin package before enabling its hooks.
# Pinned to TRACING_PLUGIN_VERSION, like the codex pins in install-run.sh:
# TRACING_HOOK_TRUSTED_HASH matches one plugin version. Upstream's marketplace
# (from v0.4.0 on) names the npm package without a version, so even a tag-pinned
# copy installs whatever upstream last published. A local marketplace with the
# same name pins the npm version and keeps the tracing@codex-observability-plugin id.
info "codex observability plugin @$TRACING_PLUGIN_VERSION"
PLUGIN_PACKAGE=@langfuse/codex-observability-plugin
PLUGIN_MARKETPLACE="$CODEX_DIR/pinned-marketplaces/codex-observability-plugin"
PLUGIN_ENTRY="$RUN_HOME/.codex/plugins/cache/codex-observability-plugin/tracing/$TRACING_PLUGIN_VERSION/dist/index.mjs"

# Check that the hook file exists; plugin status can reflect configuration only.
if [ ! -f "$PLUGIN_ENTRY" ]; then
  # Codex reads a local marketplace in place, so it must outlive this script.
  mkdir -p "$PLUGIN_MARKETPLACE/.agents/plugins"
  jq -n --arg pkg "$PLUGIN_PACKAGE" --arg version "$TRACING_PLUGIN_VERSION" \
    '{name: "codex-observability-plugin", interface: {displayName: "Langfuse"},
      plugins: [{name: "tracing", category: "Coding",
                 source: {source: "npm", package: $pkg, version: $version},
                 policy: {installation: "AVAILABLE", authentication: "ON_INSTALL"}}]}' \
    > "$PLUGIN_MARKETPLACE/.agents/plugins/marketplace.json"
  chown -R "$RUN_USER:$RUN_USER" "$CODEX_DIR/pinned-marketplaces"

  # Codex refuses to re-add a marketplace from another source, so drop any earlier one.
  su - "$RUN_USER" -c \
    'codex plugin marketplace remove codex-observability-plugin' >/dev/null 2>&1 || true
  su - "$RUN_USER" -c \
    "codex plugin marketplace add '$PLUGIN_MARKETPLACE'" \
    >/dev/null 2>&1 || true

  for attempt in 1 2; do
    [ -f "$PLUGIN_ENTRY" ] && break
    # A half-installed plugin makes `add` a no-op, so clear it before retrying.
    if [ "$attempt" = 2 ]; then
      su - "$RUN_USER" -c \
        'codex plugin remove tracing@codex-observability-plugin' >/dev/null 2>&1 || true
    fi
    su - "$RUN_USER" -c 'codex plugin add tracing@codex-observability-plugin' >/dev/null 2>&1 || true
  done

  [ -f "$PLUGIN_ENTRY" ] || die "The observability plugin did not unpack to $PLUGIN_ENTRY.
Without it codex runs normally and emits NO Langfuse traces — note that
'codex plugin list' may still say 'installed'. Check that npm has
$PLUGIN_PACKAGE@$TRACING_PLUGIN_VERSION (npm view $PLUGIN_PACKAGE versions), then diagnose with:
    sudo -u $RUN_USER codex plugin marketplace remove codex-observability-plugin
    sudo -u $RUN_USER codex plugin marketplace add $PLUGIN_MARKETPLACE
    sudo -u $RUN_USER codex plugin add tracing@codex-observability-plugin"
  ok "installed and unpacked"
else
  ok "already installed ($PLUGIN_ENTRY present)"
fi

# Codex fires Stop only when a turn ends, so a turn whose gateway is killed is
# never uploaded. The gateway unit runs this flush around every stop and start.
# The run user executes it; umask 077 would make a new directory root-only.
install -d -m 755 /usr/local/lib/crux
FLUSH_SCRIPT=/usr/local/lib/crux/codex-flush-turns.py
install -m 755 "$SCRIPT_DIR/codex-flush-turns.py" "$FLUSH_SCRIPT"
FLUSH="/usr/bin/python3 $FLUSH_SCRIPT --plugin $PLUGIN_ENTRY"
GATEWAY_START_FLUSH="$FLUSH --reason gateway-start"
GATEWAY_STOP_FLUSH="$FLUSH --reason gateway-stop"

# Live mode replaces the plugin's Stop hook with a service that sends each
# observation once it is complete, so a running turn shows up within a pass.
# The gateway unit then ends a killed turn through the same exporter.
if [ "$CODEX_TRACE_MODE" = live ]; then
  LIVE_SCRIPT=/usr/local/lib/crux/codex-live-trace.py
  install -m 755 "$SCRIPT_DIR/codex-live-trace.py" "$LIVE_SCRIPT"
  rm -rf /usr/local/lib/crux/live_trace
  install -d -m 755 /usr/local/lib/crux/live_trace
  install -m 644 "$SCRIPT_DIR"/live_trace/*.py /usr/local/lib/crux/live_trace/
  LIVE="/usr/bin/python3 $LIVE_SCRIPT"
  GATEWAY_START_FLUSH="$LIVE --once --finalize"
  GATEWAY_STOP_FLUSH="$LIVE --once --finalize"
fi

# ====== CODEX LOGIN ======
if [ "$MODEL_PROVIDER" = direct ]; then
# Run Codex login to write the API key to ~/.codex/auth.json.
info "codex login (writes ~/.codex/auth.json)"
AUTH_JSON="$CODEX_DIR/auth.json"
if ! printf '%s' "$AGENT_API_KEY" \
     | su "$RUN_USER" -c "cd '$RUN_HOME' && codex login --with-api-key" >/dev/null 2>&1; then
  die "codex login --with-api-key failed. Check the OPENAI_API_KEY in the per-run secrets file."
fi
[ -f "$AUTH_JSON" ] || die "codex login reported success but $AUTH_JSON does not exist."
chown "$RUN_USER:$RUN_USER" "$AUTH_JSON"
chmod 600 "$AUTH_JSON"
# 2>&1, not 2>/dev/null: codex reports login status on stderr, so discarding it
# prints a blank line that reads like a failed login.
ok "$(su "$RUN_USER" -c 'codex login status' 2>&1 | tail -1)"
fi

# ====== PROVE THE HOOK ACTUALLY FIRES ======
# Run a paid Codex probe and require its turn to be traced: a Stop-hook event,
# or in live mode an export by the live exporter.
info "Verifying tracing with one real codex turn ($CODEX_TRACE_MODE)"
HOOK_STATUS=0
HOOK_OUT="$(su "$RUN_USER" -c \
  "cd '$WORK_DIR' && \
   timeout 180 codex exec --skip-git-repo-check 'Say exactly: HOOK-PROBE' </dev/null 2>&1")" || HOOK_STATUS=$?
if [ "$HOOK_STATUS" != 0 ]; then
  install -m 600 /dev/null "$PROBE_LOG"
  printf '%s\n' "$HOOK_OUT" > "$PROBE_LOG"
  die "Codex probe failed (exit $HOOK_STATUS). Inspect $PROBE_LOG with sudo for the provider error; gateway was not started."
fi

if [ "$CODEX_TRACE_MODE" = live ]; then
  LIVE_OUT="$(su - "$RUN_USER" -c "$LIVE --once" 2>&1)" \
    || die "The live trace exporter could not send the probe's turn to Langfuse:
$LIVE_OUT"
  printf '%s' "$LIVE_OUT" | grep -qE 'sent [1-9][0-9]* observations' \
    || die "The live trace exporter found nothing to send for the probe's turn under $CODEX_DIR/sessions:
$LIVE_OUT"
  ok "Live exporter sent the probe's turn — traces will reach Langfuse as environment=$RUN_SLUG"
elif printf '%s' "$HOOK_OUT" | grep -q 'hook: Stop'; then
  ok "Stop hook fired — traces will reach Langfuse as environment=$RUN_SLUG"
else
  printf '\n%s\n' "$HOOK_OUT" | tail -20
  die "codex ran but never fired the Stop hook, so NOTHING will be traced.
Almost always one of:
  - trusted_hash in TRACING_HOOK_TRUSTED_HASH does not match plugin
    version $TRACING_PLUGIN_VERSION (codex ignores untrusted hooks silently)
  - the plugin did not unpack to $PLUGIN_ENTRY
Compare against a machine where tracing works:
    grep -A3 'hooks.state' ~/.codex/config.toml"
fi

# The flush must parse the rollout this codex just wrote; --check uploads nothing.
if [ "$CODEX_TRACE_MODE" = stop-hook ]; then
info "Verifying the gateway's trace flush reads this codex's rollouts"
FLUSH_OUT="$(su - "$RUN_USER" -c "$FLUSH --reason provision-check --check" 2>&1)" \
  || die "The trace flush failed, so turns killed with the gateway will not reach Langfuse:
$FLUSH_OUT"
printf '%s' "$FLUSH_OUT" | grep -qE 'checked [1-9][0-9]* rollouts' \
  || die "The trace flush found none of the probe's rollouts under $CODEX_DIR/sessions:
$FLUSH_OUT"
ok "$(printf '%s' "$FLUSH_OUT" | tail -1)"
fi

else
# ====== CLAUDE CONFIG AND STANDALONE TRACING ======
# The adapter loads user settings. Keep the same explicit model/effort for
# both its SDK process and the CLI probe, including max via the env setting.
CLAUDE_DIR="$RUN_HOME/.claude"
HOOK_PATH="$CLAUDE_DIR/hooks/langfuse_hook.py"
STATE_DIR="$WORK_DIR/.claude/state"
mkdir -p "$CLAUDE_DIR/hooks" "$STATE_DIR"
cp "$SCRIPT_DIR/langfuse_hook.py" "$HOOK_PATH"
jq -n --arg model "$MODEL" --arg effort "$EFFORT" \
  --argjson agent_env "$AGENT_ENV" --arg pk "$LANGFUSE_PUBLIC_KEY" \
  --arg sk "$LANGFUSE_SECRET_KEY" --arg url "$LANGFUSE_BASE_URL" \
  --arg slug "$RUN_SLUG" --arg state "$STATE_DIR" --arg hook "$HOOK_PATH" \
  --arg uv "$RUN_HOME/.local/bin/uv" --arg metadata "$TRACE_METADATA" \
  '{
    model: $model,
    env: ($agent_env + {
      CLAUDE_CODE_EFFORT_LEVEL: $effort,
      TRACE_TO_LANGFUSE: "true",
      LANGFUSE_PUBLIC_KEY: $pk,
      LANGFUSE_SECRET_KEY: $sk,
      LANGFUSE_BASE_URL: $url,
      LANGFUSE_TRACING_ENVIRONMENT: $slug,
      CC_LANGFUSE_METADATA: $metadata,
      CC_LANGFUSE_STATE_DIR: $state
    }),
    hooks: {Stop: [{hooks: [{
      type: "command",
      command: (($uv | @sh) + " run --quiet --script " + ($hook | @sh)),
      timeout: 120
    }]}]}
  }' > "$CLAUDE_DIR/settings.json"
# No Langfuse plugin: its isMeta filter drops AgentRQ prompts. The standalone
# hook is the same vendored implementation used by agentrq/claude/.
chmod 600 "$CLAUDE_DIR/settings.json"
chown -R "$RUN_USER:$RUN_USER" "$CLAUDE_DIR" "$WORK_DIR/.claude"
ok "Wrote Claude settings (model=$MODEL, effort=$EFFORT) and standalone Stop hook"

# Provisioning must fail when the model cannot answer or the hook cannot
# process a transcript. Inspect only new hook log bytes, never a stale success.
HOOK_LOG="$STATE_DIR/langfuse_hook.log"
HOOK_OFFSET=0
[ ! -f "$HOOK_LOG" ] || HOOK_OFFSET="$(wc -c < "$HOOK_LOG")"
info "Verifying Claude and its Stop hook (one real Claude turn)"
install -m 600 /dev/null "$PROBE_LOG"
if ! HOOK_OUT="$(su - "$RUN_USER" -c \
  "cd '$WORK_DIR' && timeout 180 claude -p --output-format stream-json --verbose --max-turns 1 \
   --tools '' --strict-mcp-config --mcp-config '{\"mcpServers\":{}}' \
   -- 'Say exactly: HOOK-PROBE' </dev/null" 2>>"$PROBE_LOG")"; then
  printf '%s\n' "$HOOK_OUT" >> "$PROBE_LOG"
  die "Claude probe failed. Inspect $PROBE_LOG with sudo for the provider error; gateway was not started."
fi
# Gateway streams can emit thinking after text, leaving result.result empty.
# Require both a successful result and the marker in an assistant text event.
printf '%s' "$HOOK_OUT" | jq -se '
  ([.[] | select(.type == "result")] | last | .is_error == false) and
  any(.[] | select(.type == "assistant") | .message.content[]?;
    .type == "text" and (.text | contains("HOOK-PROBE")))' >/dev/null \
  || die "Claude probe did not return a successful result; gateway was not started."
HOOK_NEW="$(tail -c "+$((HOOK_OFFSET + 1))" "$HOOK_LOG" 2>/dev/null || true)"
if ! printf '%s' "$HOOK_NEW" | grep -qE 'Processed [1-9][0-9]* turns' \
   || printf '%s' "$HOOK_NEW" | grep -q 'emit_turn failed'; then
  die "Claude answered but its Langfuse hook did not process the turn. Inspect $HOOK_LOG; gateway was not started."
fi
ok "Claude answered and the standalone Stop hook processed its transcript"
fi

# ====== GATEWAY SERVICE ======
# install-run.sh excludes the gateway from needrestart. A box provisioned
# before that would let an unattended upgrade restart it mid-run.
[ -f /etc/needrestart/conf.d/crux.conf ] \
  || die "/etc/needrestart/conf.d/crux.conf is missing, so unattended upgrades can restart the gateway mid-run. Re-run install-run.sh; gateway was not started."

# Run the gateway as a service with automatic restart.
# ExecStopPost runs after every exit, including a SIGKILL or crash; ExecStartPre
# catches turns cut off by a reboot or instance stop. "-" keeps a failed flush
# from failing the unit.
FLUSH_UNIT=""
if [ "$AGENT_PLATFORM" = codex ]; then
  FLUSH_UNIT="ExecStartPre=-$GATEWAY_START_FLUSH
ExecStopPost=-$GATEWAY_STOP_FLUSH"
fi
info "Installing crux-acp-gateway.service"
cat > /etc/systemd/system/crux-acp-gateway.service <<UNIT
[Unit]
Description=CRUX ACP gateway ($AGENT_PLATFORM -> AgentRQ)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${WORK_DIR}
EnvironmentFile=${GW_ENV}
ExecStart=/usr/bin/acp-gateway -- $ACP_COMMAND
$FLUSH_UNIT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

if [ "$AGENT_PLATFORM" = codex ] && [ "$CODEX_TRACE_MODE" = live ]; then
  info "Installing crux-codex-live-trace.service"
  cat > /etc/systemd/system/crux-codex-live-trace.service <<UNIT
[Unit]
Description=CRUX live Codex traces -> Langfuse
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Environment=HOME=${RUN_HOME}
ExecStart=$LIVE --interval 30
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
else
  systemctl disable --now crux-codex-live-trace >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/crux-codex-live-trace.service
fi

systemctl daemon-reload
systemctl enable crux-acp-gateway >/dev/null
systemctl restart crux-acp-gateway
if [ -f /etc/systemd/system/crux-codex-live-trace.service ]; then
  systemctl enable crux-codex-live-trace >/dev/null
  systemctl restart crux-codex-live-trace
fi
ok "Service enabled and started"

# ====== HEALTH ======
# Require an active service; repeated restarts can leave it activating.
info "Checking the gateway stays up"
sleep 8
STATE="$(systemctl is-active crux-acp-gateway || true)"
if [ "$STATE" != "active" ]; then
  printf '\n'
  journalctl -u crux-acp-gateway -n 30 --no-pager || true
  die "Gateway is '$STATE', not active. Logs above."
fi
ok "Gateway active (restart count $(systemctl show -p NRestarts --value crux-acp-gateway))"
if [ -f /etc/systemd/system/crux-codex-live-trace.service ]; then
  [ "$(systemctl is-active crux-codex-live-trace || true)" = active ] || {
    journalctl -u crux-codex-live-trace -n 30 --no-pager || true
    die "crux-codex-live-trace is not active, so running turns will not reach Langfuse. Logs above."
  }
  ok "Live trace exporter active"
fi
