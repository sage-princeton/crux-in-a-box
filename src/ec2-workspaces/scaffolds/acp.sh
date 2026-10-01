# shellcheck shell=bash
# The ACP scaffold module: Codex or Claude Code behind acp-gateway, taking tasks from an AgentRQ workspace.
# install-run.sh and configure-run.sh source this file (copied to the box as scaffold.sh) and call its hooks.
# Function bodies keep the code's original top-level indentation so heredoc output is unchanged.

scaffold_install_preflight() {
  : "${ACP_GATEWAY_VERSION:?}"
  case "$AGENT_PLATFORM" in
    codex) : "${CODEX_VERSION:?}" "${CODEX_ACP_VERSION:?}" ;;
    claude) : "${CLAUDE_VERSION:?}" "${CLAUDE_ACP_VERSION:?}" ;;
    *) die "AGENT_PLATFORM must be codex|claude." ;;
  esac
}

scaffold_install() {
# ====== CODEX CLI ======
# Pinned like the agent packages below. It was previously installed unpinned,
# which is how two boxes in this fleet ended up on 0.153.4 and 0.154.0.
#
# The check compares the INSTALLED VERSION against the pin, not merely whether
# a `codex` exists on PATH. A presence check makes the pin decorative: any box
# that already has some codex — a reused instance, a baked AMI — keeps the
# version it happens to have, and re-running this script never corrects it.
# That matters beyond tidiness, because TRACING_HOOK_TRUSTED_HASH is validated
# against one codex/plugin pairing.
if [ "$AGENT_PLATFORM" = codex ]; then
info "codex CLI @$CODEX_VERSION"
CODEX_HAVE="$(codex --version 2>/dev/null | awk '{print $2}' || true)"
if [ "$CODEX_HAVE" = "$CODEX_VERSION" ]; then
  ok "already at $CODEX_VERSION"
else
  [ -n "$CODEX_HAVE" ] && info "found $CODEX_HAVE, replacing with the pinned $CODEX_VERSION"
  npm install -g "@openai/codex@${CODEX_VERSION}" >/dev/null 2>&1 \
    || die "npm install @openai/codex@${CODEX_VERSION} failed. Does that version exist? npm view @openai/codex versions"
  CODEX_NOW="$(codex --version 2>/dev/null | awk '{print $2}' || true)"
  [ "$CODEX_NOW" = "$CODEX_VERSION" ] \
    || die "Installed @openai/codex@${CODEX_VERSION} but codex reports '${CODEX_NOW:-nothing}'."
  ok "codex $CODEX_NOW"
fi

  AGENT_PACKAGE="@agentclientprotocol/codex-acp"
  AGENT_VERSION="$CODEX_ACP_VERSION"
  AGENT_BIN=codex-acp
  CLI_BIN=codex
else
  info "Claude CLI @$CLAUDE_VERSION"
  CLAUDE_HAVE="$(claude --version 2>/dev/null | awk '{print $1}' || true)"
  if [ "$CLAUDE_HAVE" != "$CLAUDE_VERSION" ]; then
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" >/dev/null 2>&1 \
      || die "Installing the pinned Claude CLI failed."
  fi
  CLAUDE_NOW="$(claude --version 2>/dev/null | awk '{print $1}' || true)"
  [ "$CLAUDE_NOW" = "$CLAUDE_VERSION" ] || die "Claude CLI does not match the requested pin."
  AGENT_PACKAGE="@agentclientprotocol/claude-agent-acp"
  AGENT_VERSION="$CLAUDE_ACP_VERSION"
  AGENT_BIN=claude-agent-acp
  CLI_BIN=claude
fi

# ====== PINNED AGENT PACKAGES ======
info "$AGENT_BIN@$AGENT_VERSION and acp-gateway@$ACP_GATEWAY_VERSION"
npm install -g \
  "$AGENT_PACKAGE@$AGENT_VERSION" \
  "@agentrq/acp-gateway@${ACP_GATEWAY_VERSION}" >/dev/null 2>&1 \
  || die "npm install of the pinned agent packages failed"
ok "installed"

# ====== VERIFY ======
info "Verifying"
for bin in aws node npm "$CLI_BIN" "$AGENT_BIN" acp-gateway; do
  command -v "$bin" >/dev/null 2>&1 || die "$bin is not on PATH after install"
done
ok "aws, node, npm, $CLI_BIN, $AGENT_BIN, acp-gateway all resolve"
ok "acp-gateway $(acp-gateway --help 2>&1 | head -1)"
}

scaffold_preflight() {
  : "${CONTROL_MCP_BASE:?}"
  if [ "$AGENT_PLATFORM" = codex ]; then
    : "${TRACING_PLUGIN_VERSION:?}" "${TRACING_HOOK_TRUSTED_HASH:?}"
  else
    [ -f "$SCRIPT_DIR/langfuse_hook.py" ] || die "Claude Langfuse hook was not copied alongside configure-run.sh."
  fi
}

scaffold_read_secrets() {
  WORKSPACE_ID="$(get AGENTRQ_WORKSPACE_ID)"  || die "AGENTRQ_WORKSPACE_ID missing from $RUN_SECRETS_PATH"
  WORKSPACE_TOKEN="$(get AGENTRQ_WORKSPACE_TOKEN)" || die "AGENTRQ_WORKSPACE_TOKEN missing from $RUN_SECRETS_PATH"
  SECRET_COUNT=$((SECRET_COUNT + 2))
}

scaffold_agent_env() {
  jq -cn --arg platform "$AGENT_PLATFORM" --arg provider "$MODEL_PROVIDER" \
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
  } else {($name): $key} end'
}

scaffold_extra_env() {
  if [ "$AGENT_PLATFORM" = claude ]; then
    printf 'CLAUDE_CODE_EXECUTABLE=/usr/bin/claude\n'
  fi
}

scaffold_configure() {
CODEX_DIR="$RUN_HOME/.codex"

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

# Set the tracing environment and user ID to the run slug.
cat > "$CODEX_DIR/config.toml" <<TOML
personality = "pragmatic"
model = "$CODEX_MODEL"
model_reasoning_effort = "$CODEX_REASONING_EFFORT"
model_provider = "${MODEL_PROVIDER/direct/openai}"

[features]
hooks = true

[plugins."tracing@codex-observability-plugin"]
enabled = true

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
# Pinned to the upstream tag for TRACING_PLUGIN_VERSION, like the codex pins in
# install-run.sh: TRACING_HOOK_TRUSTED_HASH matches one plugin version, and an
# unpinned marketplace installs whatever upstream last released.
info "codex observability plugin @$TRACING_PLUGIN_VERSION"
PLUGIN_SOURCE=https://github.com/langfuse/codex-observability-plugin.git
PLUGIN_REF="v$TRACING_PLUGIN_VERSION"
PLUGIN_ENTRY="$RUN_HOME/.codex/plugins/cache/codex-observability-plugin/tracing/$TRACING_PLUGIN_VERSION/dist/index.mjs"

# Check that the hook file exists; plugin status can reflect configuration only.
if [ ! -f "$PLUGIN_ENTRY" ]; then
  # Codex refuses to re-add a marketplace from another ref, so drop any earlier one.
  su - "$RUN_USER" -c \
    'codex plugin marketplace remove codex-observability-plugin' >/dev/null 2>&1 || true
  su - "$RUN_USER" -c \
    "codex plugin marketplace add $PLUGIN_SOURCE --ref $PLUGIN_REF" \
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
'codex plugin list' may still say 'installed'. Check that upstream has tag
$PLUGIN_REF (TRACING_PLUGIN_VERSION), then diagnose with:
    sudo -u $RUN_USER codex plugin marketplace remove codex-observability-plugin
    sudo -u $RUN_USER codex plugin marketplace add $PLUGIN_SOURCE --ref $PLUGIN_REF
    sudo -u $RUN_USER codex plugin add tracing@codex-observability-plugin"
  ok "installed and unpacked"
else
  ok "already installed ($PLUGIN_ENTRY present)"
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
# Run a paid Codex probe and require a Stop-hook event.
info "Verifying the Stop hook fires (one real codex turn)"
HOOK_STATUS=0
HOOK_OUT="$(su "$RUN_USER" -c \
  "cd '$WORK_DIR' && \
   timeout 180 codex exec --skip-git-repo-check 'Say exactly: HOOK-PROBE' </dev/null 2>&1")" || HOOK_STATUS=$?
if [ "$HOOK_STATUS" != 0 ]; then
  install -m 600 /dev/null "$PROBE_LOG"
  printf '%s\n' "$HOOK_OUT" > "$PROBE_LOG"
  die "Codex probe failed (exit $HOOK_STATUS). Inspect $PROBE_LOG with sudo for the provider error; gateway was not started."
fi

if printf '%s' "$HOOK_OUT" | grep -q 'hook: Stop'; then
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
# Run the gateway as a service with automatic restart.
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
EnvironmentFile=${RUN_ENV_FILE}
ExecStart=/usr/bin/acp-gateway -- $ACP_COMMAND
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable crux-acp-gateway >/dev/null
systemctl restart crux-acp-gateway
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
}
