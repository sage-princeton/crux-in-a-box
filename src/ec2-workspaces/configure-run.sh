#!/usr/bin/env bash
set -euo pipefail

# Configure a workspace as root. Copy the secrets file again before each run.
#
# Per-run secrets arrive at RUN_SECRETS_PATH and are deleted after reading.
# Shared Langfuse credentials come from SYSTEM_SSM_PARAM via crux-system-role.
#
# Required environment: AWS_REGION, RUN_SECRETS_PATH, SYSTEM_SSM_PARAM, RUN_SLUG
# and the selected platform's model and effort, plus what the scaffold module
# (scaffold.sh beside this script) requires. AGENT_PLATFORM defaults to codex.
# Place agent-config.sh and scaffold.sh beside this script.

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${AWS_REGION:?}" "${RUN_SECRETS_PATH:?}" "${SYSTEM_SSM_PARAM:?}" "${RUN_SLUG:?}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=src/ec2-workspaces/agent-config.sh
source "$SCRIPT_DIR/agent-config.sh"
cfg() { local key="$1"; printf '%s' "${!key:-}"; }
# shellcheck source=src/ec2-workspaces/scaffolds/acp.sh
source "$SCRIPT_DIR/scaffold.sh"
load_agent_config settings
scaffold_preflight
# Every config/credential file starts private, including during its creation.
umask 077

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"
WORK_DIR=/srv/crux-run          # the run's root; the agent process's working directory
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
SECRET_COUNT=1
scaffold_read_secrets
RUN_API_KEYS="$(run_api_keys_json "$RUN_SECRETS_PATH")"
rm -f "$RUN_SECRETS_PATH"
ok "Read $SECRET_COUNT per-run value$([ "$SECRET_COUNT" = 1 ] || echo s) plus $(printf '%s' "$RUN_API_KEYS" | jq -r 'keys | length') optional API key(s) (not echoed); deleted $RUN_SECRETS_PATH"

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

# ====== RUN ENVIRONMENT ======
# The agent process's environment. systemd reads this root-only file;
# credential values are quoted, not shell code.
AGENT_ENV="$(scaffold_agent_env)"
RUN_ENV_FILE=/etc/crux-run.env
{
  printf '%s' "$AGENT_ENV" | jq -r 'to_entries[] | .key + "=" + (.value | @json)'
  printf '%s' "$RUN_API_KEYS" | jq -r 'to_entries[] | .key + "=" + (.value | @json)'
  printf 'PATH=%s/.local/bin:/usr/local/bin:/usr/bin:/bin\nHOME=%s\n' "$RUN_HOME" "$RUN_HOME"
  scaffold_extra_env
} > "$RUN_ENV_FILE"
chmod 600 "$RUN_ENV_FILE"

scaffold_configure
