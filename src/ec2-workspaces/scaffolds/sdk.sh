# shellcheck shell=bash
# The SDK scaffold module: crux_scaffold (src/sdk-scaffold) runs a drop-in directory on an agent SDK.
# It needs no AgentRQ workspace: the run is crux-sdk-run.service, which the operator starts once the
# drop-in's placeholders are resolved. install-run.sh and configure-run.sh source this file as scaffold.sh.

SCAFFOLD_DIR=/opt/crux-sdk-scaffold          # root-owned: the agent cannot edit its own scaffold
SCAFFOLD_PYTHON="$SCAFFOLD_DIR/.venv/bin/python"
DROP_IN_DIR=/srv/crux-run/run-harness
STATE_DIR=/srv/crux-run/state                # loop state and sessions; a restarted run resumes from here

scaffold_install_preflight() {
  [ "$AGENT_PLATFORM" = openai-agents ] || die "The sdk scaffold module runs AGENT_PLATFORM=openai-agents only."
  [ -f "$SCAFFOLD_DIR/requirements.txt" ] || die "Stage src/sdk-scaffold at $SCAFFOLD_DIR before install-run.sh."
}

scaffold_install() {
  info "SDK scaffold venv at $SCAFFOLD_DIR/.venv"
  apt-get install -y -qq python3 >/dev/null
  export UV_CACHE_DIR=/var/cache/crux-uv
  "$RUN_HOME/.local/bin/uv" venv --quiet --allow-existing --python /usr/bin/python3 "$SCAFFOLD_DIR/.venv" \
    || die "Could not create the scaffold venv"
  "$RUN_HOME/.local/bin/uv" pip install --quiet --python "$SCAFFOLD_PYTHON" -r "$SCAFFOLD_DIR/requirements.txt" \
    || die "Installing the scaffold's pinned requirements failed"
  PYTHONPATH="$SCAFFOLD_DIR" "$SCAFFOLD_PYTHON" -m crux_scaffold --help >/dev/null \
    || die "crux_scaffold does not import in its venv"
  for bin in aws node npx; do
    command -v "$bin" >/dev/null 2>&1 || die "$bin is not on PATH after install"
  done
  ok "crux_scaffold imports (Codex is bundled by openai-codex); node $(node -v) for npx-launched MCP servers"
}

scaffold_preflight() {
  [ -x "$SCAFFOLD_PYTHON" ] || die "No scaffold venv at $SCAFFOLD_DIR; install-run.sh builds it."
}

scaffold_read_secrets() {
  :
}

scaffold_agent_env() {
  jq -cn --arg name "$API_KEY_NAME" --arg key "$AGENT_API_KEY" '{($name): $key}'
}

scaffold_extra_env() {
  jq -rn --arg pk "$LANGFUSE_PUBLIC_KEY" --arg sk "$LANGFUSE_SECRET_KEY" --arg url "$LANGFUSE_BASE_URL" \
    --arg slug "$RUN_SLUG" --arg model "$MODEL" --arg effort "$EFFORT" --arg path "$SCAFFOLD_DIR" '
    {LANGFUSE_PUBLIC_KEY: $pk, LANGFUSE_SECRET_KEY: $sk, LANGFUSE_BASE_URL: $url, RUN_SLUG: $slug,
     CRUX_MODEL: $model, CRUX_REASONING_EFFORT: $effort, PYTHONPATH: $path}
    | to_entries[] | .key + "=" + (.value | @json)'
}

scaffold_configure() {
  install -d -m 755 -o "$RUN_USER" -g "$RUN_USER" "$WORK_DIR" "$STATE_DIR"

  # One real model call and one Codex turn, with the unit's exact environment;
  # the probe also requires Langfuse to accept the keys.
  info "Probing the scaffold: one traced model call and one Codex turn"
  install -m 600 /dev/null "$PROBE_LOG"
  if ! systemd-run --quiet --wait --pipe --collect --uid="$RUN_USER" \
       --property=EnvironmentFile="$RUN_ENV_FILE" --working-directory="$WORK_DIR" \
       "$SCAFFOLD_PYTHON" -m crux_scaffold probe --coding-agent codex >"$PROBE_LOG" 2>&1; then
    die "Scaffold probe failed. Inspect $PROBE_LOG with sudo; crux-sdk-run was not installed."
  fi
  ok "$(tail -1 "$PROBE_LOG")"

  # install-run.sh excludes the run from needrestart. A box provisioned before
  # that would let an unattended upgrade restart it mid-iteration.
  grep -qF 'crux-sdk-run' /etc/needrestart/conf.d/crux.conf 2>/dev/null \
    || die "/etc/needrestart/conf.d/crux.conf does not exclude crux-sdk-run, so unattended upgrades can restart the run mid-iteration. Re-run install-run.sh; crux-sdk-run was not installed."

  # Installed, not started. Crashes restart and resume from $STATE_DIR; a config
  # error (2) or a loop that stopped early (3) is left for the operator.
  info "Installing crux-sdk-run.service (not started)"
  cat > /etc/systemd/system/crux-sdk-run.service <<UNIT
[Unit]
Description=CRUX SDK scaffold run ($RUN_SLUG)
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=${RUN_USER}
WorkingDirectory=${DROP_IN_DIR}/workspace
EnvironmentFile=${RUN_ENV_FILE}
ExecStart=${SCAFFOLD_PYTHON} -m crux_scaffold run --drop-in ${DROP_IN_DIR} --state-dir ${STATE_DIR}
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=2 3
SuccessExitStatus=130 143
StandardOutput=journal
StandardError=journal
UNIT
  systemctl daemon-reload
  ok "Installed. Resolve the drop-in's placeholders, then: sudo systemctl start crux-sdk-run"
}
