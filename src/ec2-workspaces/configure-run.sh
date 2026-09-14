#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# configure-run.sh — runs ON the run box, as root. PER-RUN CONFIG AND
# SECRETS. Idempotent: safe to re-run after a config change — but a re-run
# needs the per-run secrets file scp'd again, because this script deletes it
# once the values are written where they live.
#
# Deliberately separate from install-run.sh, which is the bakeable half.
#
# Secrets arrive two ways:
#   - per-run (OpenAI key, workspace id/token): a JSON file at
#     RUN_SECRETS_PATH, scp'd by provision-workspace-aws-resources.sh, deleted here after use
#   - system-wide (Langfuse): SSM SYSTEM_SSM_PARAM, read via the shared
#     crux-system-role
#
# Expects in the environment: AWS_REGION, RUN_SECRETS_PATH, SYSTEM_SSM_PARAM,
# RUN_SLUG, CODEX_MODEL, CODEX_REASONING_EFFORT, CONTROL_MCP_BASE,
# TRACING_PLUGIN_VERSION, TRACING_HOOK_TRUSTED_HASH.
# ==========================================================================

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${AWS_REGION:?}" "${RUN_SECRETS_PATH:?}" "${SYSTEM_SSM_PARAM:?}" \
  "${RUN_SLUG:?}" "${CODEX_MODEL:?}" "${CODEX_REASONING_EFFORT:?}" \
  "${CONTROL_MCP_BASE:?}" \
  "${TRACING_PLUGIN_VERSION:?}" "${TRACING_HOOK_TRUSTED_HASH:?}"

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"
WORK_DIR=/srv/crux-run          # acp-gateway's cwd; holds .mcp.json
CODEX_DIR="$RUN_HOME/.codex"

# ====== PER-RUN SECRETS FROM THE SCP'D FILE ======
# Delivered over the same SSH channel that delivered this script; deleted as
# soon as the values are held in shell variables. They persist only in the
# mode-600 files written below.
info "Reading per-run secrets from $RUN_SECRETS_PATH"
[ -f "$RUN_SECRETS_PATH" ] \
  || die "$RUN_SECRETS_PATH not found. provision-workspace-aws-resources.sh scps it before running this script; a manual re-run needs it scp'd again."
SECRETS="$(cat "$RUN_SECRETS_PATH")"

get() { printf '%s' "$SECRETS" | jq -re --arg k "$1" '.[$k] // empty'; }

OPENAI_API_KEY="$(get OPENAI_API_KEY)"      || die "OPENAI_API_KEY missing from $RUN_SECRETS_PATH"
WORKSPACE_ID="$(get AGENTRQ_WORKSPACE_ID)"  || die "AGENTRQ_WORKSPACE_ID missing from $RUN_SECRETS_PATH"
WORKSPACE_TOKEN="$(get AGENTRQ_WORKSPACE_TOKEN)" || die "AGENTRQ_WORKSPACE_TOKEN missing from $RUN_SECRETS_PATH"
rm -f "$RUN_SECRETS_PATH"
ok "Read 3 per-run values (not echoed); deleted $RUN_SECRETS_PATH"

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

# ====== CODEX CONFIG ======
info "codex config"
mkdir -p "$CODEX_DIR"

# ~/.codex/langfuse.json, NOT <cwd>/.codex/. The plugin resolves
# defaults -> ~/.codex/langfuse.json -> <cwd>/.codex/langfuse.json -> env, and
# a cwd-scoped file silently drops every trace produced from anywhere else
# (fail_on_error defaults false, so nothing surfaces). On a single-purpose box
# the home-dir location is the correct one.
jq -n --arg pk "$LANGFUSE_PUBLIC_KEY" --arg sk "$LANGFUSE_SECRET_KEY" \
      --arg url "$LANGFUSE_BASE_URL" --arg env "$RUN_SLUG" \
  '{enabled: true, public_key: $pk, secret_key: $sk, base_url: $url,
    environment: $env, user_id: $env}' > "$CODEX_DIR/langfuse.json"
chmod 600 "$CODEX_DIR/langfuse.json"

# environment/user_id set from the slug so a trace names the box that made it;
# both were empty/"default" on every trace in the local setup.
cat > "$CODEX_DIR/config.toml" <<TOML
personality = "pragmatic"
model = "$CODEX_MODEL"
model_reasoning_effort = "$CODEX_REASONING_EFFORT"

[features]
hooks = true

[plugins."tracing@codex-observability-plugin"]
enabled = true

# trusted_hash is REQUIRED, not decorative. Codex refuses to run a hook it has
# not been told to trust, and refuses silently — no warning, no trace, a
# completely normal-looking run. \`enabled = true\` alone is not enough.
# Interactively a human accepts the hook and codex records this; an unattended
# box has nobody to click, so the value is pinned in placeholders-run.txt.
[hooks.state."tracing@codex-observability-plugin:hooks/hooks.json:stop:0:0"]
trusted_hash = "$TRACING_HOOK_TRUSTED_HASH"
enabled = true

[projects."$WORK_DIR"]
trust_level = "trusted"
TOML

chown -R "$RUN_USER:$RUN_USER" "$CODEX_DIR"
ok "Wrote langfuse.json (environment=$RUN_SLUG) and config.toml (model=$CODEX_MODEL, effort=$CODEX_REASONING_EFFORT)"

# ====== OBSERVABILITY PLUGIN ======
# config.toml above only ENABLES the plugin. Enabling one that was never
# installed is a SILENT NO-OP: codex starts fine, the run works, and no trace
# is ever emitted. The plugin must be fetched from its marketplace first.
# This is what actually produces the Langfuse traces.
info "codex observability plugin"
PLUGIN_ENTRY="$RUN_HOME/.codex/plugins/cache/codex-observability-plugin/tracing/0.3.0/dist/index.mjs"

# CHECK THE ARTIFACT, NOT THE STATUS STRING. `codex plugin list` will happily
# report "installed, enabled" from config state alone while the npm package
# was never unpacked — observed on a first run here. In that state codex
# behaves perfectly and emits no traces at all, so the only trustworthy test
# is whether the hook file exists on disk.
if [ ! -f "$PLUGIN_ENTRY" ]; then
  su - "$RUN_USER" -c \
    'codex plugin marketplace add https://github.com/langfuse/codex-observability-plugin.git' \
    >/dev/null 2>&1 || true

  for attempt in 1 2; do
    [ -f "$PLUGIN_ENTRY" ] && break
    # A half-installed plugin makes `add` a no-op, so clear it before retrying.
    [ "$attempt" = 2 ] && su - "$RUN_USER" -c \
      'codex plugin remove tracing@codex-observability-plugin' >/dev/null 2>&1 || true
    su - "$RUN_USER" -c 'codex plugin add tracing@codex-observability-plugin' >/dev/null 2>&1 || true
  done

  [ -f "$PLUGIN_ENTRY" ] || die "The observability plugin did not unpack to $PLUGIN_ENTRY.
Without it codex runs normally and emits NO Langfuse traces — note that
'codex plugin list' may still say 'installed'. Diagnose with:
    sudo -u $RUN_USER codex plugin remove tracing@codex-observability-plugin
    sudo -u $RUN_USER codex plugin add tracing@codex-observability-plugin"
  ok "installed and unpacked"
else
  ok "already installed ($PLUGIN_ENTRY present)"
fi

# ====== CODEX LOGIN ======
# codex does NOT pick the key up from OPENAI_API_KEY in the environment. It
# reads ~/.codex/auth.json, and with no auth.json it sends no Authorization
# header at all — which surfaces as
#     401 Unauthorized: Missing bearer or basic authentication in header
# i.e. a message that reads like a revoked key rather than a missing login.
# The first box in this fleet worked only because a human had run the login by
# hand; doing it here is what makes a box self-sufficient.
info "codex login (writes ~/.codex/auth.json)"
AUTH_JSON="$CODEX_DIR/auth.json"
if ! printf '%s' "$OPENAI_API_KEY" \
     | su "$RUN_USER" -c "cd '$RUN_HOME' && codex login --with-api-key" >/dev/null 2>&1; then
  die "codex login --with-api-key failed. Check the OPENAI_API_KEY in the per-run secrets file."
fi
[ -f "$AUTH_JSON" ] || die "codex login reported success but $AUTH_JSON does not exist."
chown "$RUN_USER:$RUN_USER" "$AUTH_JSON"
chmod 600 "$AUTH_JSON"
# 2>&1, not 2>/dev/null: codex reports login status on stderr, so discarding it
# prints a blank line that reads like a failed login.
ok "$(su "$RUN_USER" -c 'codex login status' 2>&1 | tail -1)"

# ====== PROVE THE HOOK ACTUALLY FIRES ======
# Every failure in this area has been silent: plugin "installed" but not
# unpacked, hook present but untrusted. Neither shows up as an error — you
# just get no traces, and only notice days later when you go looking. So the
# check is behavioural, not a config inspection: run one real codex turn and
# require codex to report that it ran the Stop hook.
# Costs a few cents and ~15s. Worth it — the alternative is an unmonitored run.
info "Verifying the Stop hook fires (one real codex turn)"
HOOK_OUT="$(su - "$RUN_USER" -c \
  "cd '$WORK_DIR' && export OPENAI_API_KEY='$OPENAI_API_KEY' && \
   timeout 180 codex exec --skip-git-repo-check 'Say exactly: HOOK-PROBE' </dev/null 2>&1" || true)"

if printf '%s' "$HOOK_OUT" | grep -q 'hook: Stop'; then
  ok "Stop hook fired — traces will reach Langfuse as environment=$RUN_SLUG"
elif printf '%s' "$HOOK_OUT" | grep -qE '401 Unauthorized|Missing bearer|invalid_api_key'; then
  # Separated from the tracing diagnosis below because the symptom is the same
  # — no 'hook: Stop' — while the cause is not tracing at all. Reporting a
  # credential failure as a trusted_hash problem sends you to the wrong file.
  printf '\n%s\n' "$HOOK_OUT" | tail -5
  die "The probe turn never reached the model: OpenAI rejected the request (401).
The hook cannot fire because no turn completed, so this is NOT a tracing fault.
Check, in order:
  - OPENAI_API_KEY in the per-run secrets file is current and not revoked
  - $AUTH_JSON exists and holds that key (codex ignores the env var)
    Re-run:  printenv OPENAI_API_KEY | codex login --with-api-key"
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

# ====== GATEWAY ENVIRONMENT ======
# Root-only file rather than inline Environment= lines: systemd unit contents
# are world-readable via `systemctl cat`, and this holds the OpenAI key.
info "Gateway environment file"
GW_ENV=/etc/crux-run.env
cat > "$GW_ENV" <<ENV
OPENAI_API_KEY=${OPENAI_API_KEY}
PATH=${RUN_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
HOME=${RUN_HOME}
ENV
chmod 600 "$GW_ENV"
ok "Wrote $GW_ENV (mode 600, root only)"

# ====== GATEWAY SERVICE ======
# A unit rather than a foreground command so a dropped connection recovers by
# itself; the run is long-lived and nobody is watching the terminal.
info "Installing crux-acp-gateway.service"
cat > /etc/systemd/system/crux-acp-gateway.service <<UNIT
[Unit]
Description=CRUX ACP gateway (codex -> AgentRQ)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${WORK_DIR}
EnvironmentFile=${GW_ENV}
ExecStart=/usr/bin/acp-gateway -- codex-acp
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
# Confirm it is actually up rather than crash-looping: a unit with
# Restart=always reports "activating" indefinitely on a failing binary, which
# reads as success if you only glance at systemctl.
info "Checking the gateway stays up"
sleep 8
STATE="$(systemctl is-active crux-acp-gateway || true)"
if [ "$STATE" != "active" ]; then
  printf '\n'
  journalctl -u crux-acp-gateway -n 30 --no-pager || true
  die "Gateway is '$STATE', not active. Logs above."
fi
ok "Gateway active (restart count $(systemctl show -p NRestarts --value crux-acp-gateway))"
