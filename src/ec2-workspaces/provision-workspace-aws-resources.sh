#!/usr/bin/env bash
set -euo pipefail

# Provision a Codex or Claude workspace instance from the local machine.
#
# Usage:
#   ./provision-workspace-aws-resources.sh --secrets <json> [CONFIG_FILE]
#   ./provision-workspace-aws-resources.sh --put-system-secrets <json> [CONFIG_FILE]
#   ./provision-workspace-aws-resources.sh --dry-run [CONFIG_FILE]
#   ./provision-workspace-aws-resources.sh --handshake [CONFIG_FILE]
#
# Per-run JSON: provider API key, AGENTRQ_WORKSPACE_ID, AGENTRQ_WORKSPACE_TOKEN.
# It is copied over SSH and deleted on the instance after configuration.
# Shared JSON: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL.
# Upload shared credentials to /crux/system/env with --put-system-secrets.
# Instances read them through crux-system-role.
#
# Optional config key ELASTIC_IP_ADDRESS reuses an existing Elastic IP (given
# as its public IP, not its allocation id) instead of allocating one tagged to
# this slug. Set it in CONFIG_FILE, or in make-new-workspace.sh's base config
# so it flows through to every box built from it; that config file is the only
# place it's set — teardown-workspace-aws-resources.sh never releases it.
# See README.md and the example files for configuration.

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ====== PARSE ARGS ======
CONFIG_FILE=""; RUN_SECRETS_FILE=""; PUT_SYSTEM_SECRETS=""; DRY_RUN=0; HANDSHAKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --secrets)            RUN_SECRETS_FILE="${2:-}"; [ -n "$RUN_SECRETS_FILE" ] || die "--secrets needs a file"; shift 2 ;;
    --put-system-secrets) PUT_SYSTEM_SECRETS="${2:-}"; [ -n "$PUT_SYSTEM_SECRETS" ] || die "--put-system-secrets needs a file"; shift 2 ;;
    --put-secrets)        die "--put-secrets is gone: per-run secrets are scp'd now. Use --secrets <json> on the provision run." ;;
    --dry-run)            DRY_RUN=1; shift ;;
    --handshake)          HANDSHAKE=1; shift ;;
    -h|--help)            sed -n '4,23p' "$0"; exit 0 ;;
    -*)                   die "Unknown flag: $1" ;;
    *)                    [ -z "$CONFIG_FILE" ] || die "Only one config file"; CONFIG_FILE="$1"; shift ;;
  esac
done

# shellcheck source=src/ec2-workspaces/agent-config.sh
source "$SCRIPT_DIR/agent-config.sh"

CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/placeholders-run.txt}"
[ -f "$CONFIG_FILE" ] \
  || die "Config file not found: $CONFIG_FILE (copy placeholders-run.txt.example)"

# ====== LOAD CONFIG ======
declare -A CFG=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$line" ] && continue
  case "$line" in *=*) ;; *) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  key="$(printf '%s' "$key" | sed -e 's/[[:space:]]*$//')"
  val="$(printf '%s' "$val" | sed -e 's/^[[:space:]]*//')"
  CFG["$key"]="$val"
done < "$CONFIG_FILE"

cfg() { printf '%s' "${CFG[$1]:-}"; }
load_agent_config all

MISSING=()
for k in AWS_REGION RUN_SLUG OPERATOR_CIDR INSTANCE_TYPE \
         ROOT_DISK_GB KEY_NAME ACP_GATEWAY_VERSION; do
  [ -n "${CFG[$k]:-}" ] || MISSING+=("$k")
done
[ ${#MISSING[@]} -eq 0 ] || die "Missing required key(s) in $CONFIG_FILE: ${MISSING[*]}"

PROFILE="${CFG[AWS_PROFILE]:-}"
REGION="${CFG[AWS_REGION]}"
SLUG="${CFG[RUN_SLUG]}"
# Read the current controller address from AWS; report stale config values.
CONTROL_DNS="${CFG[CONTROL_PRIVATE_DNS]:-}"
CONTROL_NAME="${CFG[CONTROL_SSH_ALIAS]:-crux-control}"
# Require the controller HTTPS URL; its hostname must match AGENTRQ_DOMAIN.
CONTROL_MCP_BASE="${CFG[CONTROL_MCP_BASE]:-}"
[ -n "$CONTROL_MCP_BASE" ] \
  || die "CONTROL_MCP_BASE is not set in $CONFIG_FILE. It must be the control box's public https base, e.g. https://<dashed-eip>.sslip.io — make-control-box.sh prints it."
OPERATOR_CIDR="${CFG[OPERATOR_CIDR]}"
INSTANCE_TYPE="${CFG[INSTANCE_TYPE]}"
ROOT_DISK_GB="${CFG[ROOT_DISK_GB]}"
USE_ELASTIC_IP="${CFG[USE_ELASTIC_IP]:-true}"
case "$USE_ELASTIC_IP" in
  true|false) ;;
  *) die "USE_ELASTIC_IP must be true|false." ;;
esac
# Use the same gp3 throughput and IOPS as the OpenClaw provisioner.
# Additional capacity supports initial reads while EBS loads snapshot blocks.
ROOT_IOPS="${CFG[ROOT_IOPS]:-6000}"
ROOT_THROUGHPUT="${CFG[ROOT_THROUGHPUT]:-250}"
KEY_NAME="${CFG[KEY_NAME]}"
# Optional: reuse an existing Elastic IP (given as its address) instead of
# allocating one per slug. CONFIG_FILE is the only place this is set — see
# the header comment above; never released by teardown.
ELASTIC_IP_ADDRESS="${CFG[ELASTIC_IP_ADDRESS]:-}"
[ -z "$ELASTIC_IP_ADDRESS" ] || [ "$USE_ELASTIC_IP" = true ] \
  || die "ELASTIC_IP_ADDRESS is set but USE_ELASTIC_IP=false in $CONFIG_FILE — that override can't apply without an Elastic IP."
CODEX_MODEL="${CFG[CODEX_MODEL]:-}"
CODEX_REASONING_EFFORT="${CFG[CODEX_REASONING_EFFORT]:-}"
CODEX_VERSION="${CFG[CODEX_VERSION]:-}"
CODEX_ACP_VERSION="${CFG[CODEX_ACP_VERSION]:-}"
ACP_GATEWAY_VERSION="${CFG[ACP_GATEWAY_VERSION]}"
CLAUDE_MODEL="${CFG[CLAUDE_MODEL]:-}"
CLAUDE_EFFORT="${CFG[CLAUDE_EFFORT]:-}"
CLAUDE_VERSION="${CFG[CLAUDE_VERSION]:-}"
CLAUDE_ACP_VERSION="${CFG[CLAUDE_ACP_VERSION]:-}"
TRACING_PLUGIN_VERSION="${CFG[TRACING_PLUGIN_VERSION]:-}"
TRACING_HOOK_TRUSTED_HASH="${CFG[TRACING_HOOK_TRUSTED_HASH]:-}"

# Only validated, selected-platform values cross the remote shell boundary.
if [ "$AGENT_PLATFORM" = claude ]; then
  CODEX_MODEL=; CODEX_REASONING_EFFORT=; CODEX_VERSION=; CODEX_ACP_VERSION=
  TRACING_PLUGIN_VERSION=; TRACING_HOOK_TRUSTED_HASH=
else
  CLAUDE_MODEL=; CLAUDE_EFFORT=; CLAUDE_VERSION=; CLAUDE_ACP_VERSION=
fi

# A comma-separated list, each entry optionally CIDR=LABEL. This script does
# not create SSH rules — make-control-box.sh owns crux-run-sg's ingress — so it
# only sanity-checks the shape, and every entry must still be a single /32.
_ifs_save="$IFS"; IFS=','
for _entry in $OPERATOR_CIDR; do
  _cidr="${_entry%%=*}"
  _cidr="$(printf '%s' "$_cidr" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -n "$_cidr" ] || continue
  case "$_cidr" in
    */32) ;;
    *) die "'$_cidr' in OPERATOR_CIDR must be a /32. Use several comma-separated /32 entries for several people." ;;
  esac
done
IFS="$_ifs_save"
if [ -n "$CONTROL_DNS" ]; then
  case "$CONTROL_DNS" in
    localhost|127.*|*.compute-1.amazonaws.com|*.compute.amazonaws.com)
      die "CONTROL_PRIVATE_DNS looks public or local ('$CONTROL_DNS'). It must be the control box's PRIVATE DNS name (ip-x-x-x-x.ec2.internal) — the security group only permits the VPC path." ;;
  esac
fi
case "$CONTROL_MCP_BASE" in
  http://*|https://*) ;;
  *) die "CONTROL_MCP_BASE must start with http:// or https:// (got '$CONTROL_MCP_BASE')." ;;
esac
case "$CONTROL_MCP_BASE" in
  */) die "CONTROL_MCP_BASE must not end in a slash (got '$CONTROL_MCP_BASE') — the MCP path is appended to it." ;;
esac
RUN_SG="crux-run-sg"
SYSTEM_IAM_ROLE="crux-system-role"
SYSTEM_IAM_PROFILE="crux-system-profile"
SYSTEM_SSM_PARAM="/crux/system/env"
BOX_SECRETS_PATH="/tmp/crux-run-secrets.json"
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
SSH_USER="ubuntu"
AGENTRQ_PORT=2026

if [ -n "$PROFILE" ]; then
  PROFILE_ARGS=(--profile "$PROFILE"); CRED_DESC="profile '$PROFILE'"
  AUTH_HINT="Run: aws sso login --sso-session <session>"
else
  PROFILE_ARGS=(); CRED_DESC="ambient environment credentials"
  AUTH_HINT="Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, or set AWS_PROFILE in $CONFIG_FILE"
fi
aws_()     { aws "${PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_iam_() { aws "${PROFILE_ARGS[@]}" iam "$@"; }

# ====== PREFLIGHT ======
info "Preflight"
for b in aws jq ssh; do command -v "$b" >/dev/null || die "$b not found"; done
aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "Not authenticated with $CRED_DESC. $AUTH_HINT"
ACCOUNT_ID="$(aws_ sts get-caller-identity --query Account --output text)"
ok "Authenticated to account $ACCOUNT_ID in $REGION using $CRED_DESC"

# ====== ELASTIC IP OVERRIDE ======
# Resolve the override address to its allocation id up front, and fail fast
# if it doesn't exist or is already live on another workspace's instance —
# stealing it silently would break that box. ELASTIC_IP_OVERRIDE_ALLOC_ID is
# reused by the ELASTIC IP section below so this lookup only happens once.
ELASTIC_IP_OVERRIDE_ALLOC_ID=""
if [ -n "$ELASTIC_IP_ADDRESS" ] && [ -z "$PUT_SYSTEM_SECRETS" ] && [ "$DRY_RUN" != 1 ]; then
  info "Elastic IP override $ELASTIC_IP_ADDRESS"
  EIP_JSON="$(aws_ ec2 describe-addresses --public-ips "$ELASTIC_IP_ADDRESS" \
    --query 'Addresses[0].[AllocationId,InstanceId]' --output text 2>/dev/null || true)"
  [ -n "$EIP_JSON" ] || die "ELASTIC_IP_ADDRESS '$ELASTIC_IP_ADDRESS' does not exist in $REGION."
  ELASTIC_IP_OVERRIDE_ALLOC_ID="$(printf '%s' "$EIP_JSON" | awk '{print $1}')"
  EIP_INSTANCE_ID="$(printf '%s' "$EIP_JSON" | awk '{print $2}')"
  if [ -n "$EIP_INSTANCE_ID" ] && [ "$EIP_INSTANCE_ID" != "None" ]; then
    # shellcheck disable=SC2016 # backtick is literal JMESPath syntax, not command substitution
    EIP_INSTANCE_NAME="$(aws_ ec2 describe-instances --instance-ids "$EIP_INSTANCE_ID" \
      --query 'Reservations[0].Instances[0].Tags[?Key==`Name`].Value | [0]' --output text 2>/dev/null || true)"
    [ "$EIP_INSTANCE_NAME" = "$SLUG" ] \
      || die "ELASTIC_IP_ADDRESS '$ELASTIC_IP_ADDRESS' is already associated with instance $EIP_INSTANCE_ID (tagged Name=${EIP_INSTANCE_NAME:-<none>}). Tear that workspace down first, or use a different address."
  fi
  ok "Elastic IP address $ELASTIC_IP_ADDRESS (allocation $ELASTIC_IP_OVERRIDE_ALLOC_ID) is available for $SLUG"
fi

# ====== WHERE IS THE CONTROL BOX? ======
# Look up the controller private IP from AWS. Pattern-based ec2.internal
# DNS resolution can return an address with no running instance.
if [ -z "$PUT_SYSTEM_SECRETS" ] && [ "$DRY_RUN" != 1 ]; then
  info "Locating the control box (tag Name=$CONTROL_NAME)"
  CONTROL_INFO="$(aws_ ec2 describe-instances \
    --filters "Name=tag:Name,Values=$CONTROL_NAME" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].[PrivateDnsName,PrivateIpAddress]' --output text 2>/dev/null || true)"
  CONTROL_LIVE_DNS="$(printf '%s' "$CONTROL_INFO" | awk '{print $1}')"
  CONTROL_PRIVATE_IP="$(printf '%s' "$CONTROL_INFO" | awk '{print $2}')"
  [[ -n "$CONTROL_PRIVATE_IP" && "$CONTROL_PRIVATE_IP" != "None" ]] \
    || die "No running instance tagged Name=$CONTROL_NAME. Provision the control box first: ../ec2-control/make-control-box.sh"
  if [ -n "$CONTROL_DNS" ] && [ "$CONTROL_DNS" != "$CONTROL_LIVE_DNS" ]; then
    warn "CONTROL_PRIVATE_DNS in $(basename "$CONFIG_FILE") says $CONTROL_DNS,"
    warn "but the live control box is $CONTROL_LIVE_DNS — using the live one."
    warn "Update the config file; the stale name still resolves, so it fails silently later."
  fi
  CONTROL_DNS="$CONTROL_LIVE_DNS"
  ok "Control box at $CONTROL_DNS ($CONTROL_PRIVATE_IP)"
fi

# ====== --put-system-secrets: shared Langfuse config, uploaded once ======
# One SSM parameter for the whole fleet, plus the one shared IAM role/profile
# every run instance boots with. Per-run secrets never touch SSM — they are
# scp'd during provisioning and deleted from the box after configure.
if [ -n "$PUT_SYSTEM_SECRETS" ]; then
  [ -f "$PUT_SYSTEM_SECRETS" ] || die "No such file: $PUT_SYSTEM_SECRETS"
  jq -e . "$PUT_SYSTEM_SECRETS" >/dev/null 2>&1 || die "$PUT_SYSTEM_SECRETS is not valid JSON"
  for k in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
    v="$(jq -re --arg k "$k" '.[$k] // empty' "$PUT_SYSTEM_SECRETS")" \
      || die "$PUT_SYSTEM_SECRETS is missing required key: $k"
    # A placeholder that reaches SSM fails on every future box, far from here.
    case "$v" in *CHANGE*|*REPLACE*|*xxx*|"") die "$k still looks like a placeholder" ;; esac
  done
  if [ "$DRY_RUN" = 1 ]; then
    ok "[dry-run] would ensure $SYSTEM_IAM_ROLE/$SYSTEM_IAM_PROFILE and put-parameter $SYSTEM_SSM_PARAM"
    exit 0
  fi

  info "IAM role '$SYSTEM_IAM_ROLE' (shared by all run boxes)"
  if aws_iam_ get-role --role-name "$SYSTEM_IAM_ROLE" >/dev/null 2>&1; then
    ok "Role exists"
  else
    aws_iam_ create-role --role-name "$SYSTEM_IAM_ROLE" \
      --description "CRUX run boxes - read the shared system SSM parameter" \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    ok "Created role"
  fi
  # Deliberately narrow: the fleet's only AWS privilege is reading this one
  # shared parameter. Per-run secrets arrive over scp, not IAM.
  aws_iam_ put-role-policy --role-name "$SYSTEM_IAM_ROLE" --policy-name "read-system-env" \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter${SYSTEM_SSM_PARAM}\"}]}" >/dev/null
  ok "Inline policy: ssm:GetParameter on $SYSTEM_SSM_PARAM only"

  if aws_iam_ get-instance-profile --instance-profile-name "$SYSTEM_IAM_PROFILE" >/dev/null 2>&1; then
    ok "Instance profile exists"
  else
    aws_iam_ create-instance-profile --instance-profile-name "$SYSTEM_IAM_PROFILE" >/dev/null
    aws_iam_ add-role-to-instance-profile \
      --instance-profile-name "$SYSTEM_IAM_PROFILE" --role-name "$SYSTEM_IAM_ROLE"
    info "Waiting 10s for IAM to propagate"
    sleep 10
    ok "Created instance profile"
  fi

  info "Uploading $PUT_SYSTEM_SECRETS to SSM at $SYSTEM_SSM_PARAM (SecureString)"
  aws_ ssm put-parameter --name "$SYSTEM_SSM_PARAM" --type SecureString --overwrite \
    --value "$(cat "$PUT_SYSTEM_SECRETS")" \
    --description "CRUX system-wide config (Langfuse), shared by all run boxes" >/dev/null
  ok "Stored. Every run box reads this at configure time via $SYSTEM_IAM_ROLE."
  exit 0
fi

# ====== --handshake: prove ACP works, no AgentRQ involved ======
# Check ACP independently of the AgentRQ network connection.
if [ "$HANDSHAKE" = 1 ]; then
  info "ACP handshake against $SLUG (no AgentRQ, no workspace)"
  ssh "$SLUG" "cd /srv/crux-run && acp-gateway --agent-info -- $ACP_COMMAND" 2>&1
  exit $?
fi

HARNESS_DIR="$SCRIPT_DIR/../../run-harness"
[ -d "$HARNESS_DIR/workspace" ] \
  || die "run-harness/workspace is missing from the repository checkout."

if [ "$DRY_RUN" = 1 ]; then
  if [ "$USE_ELASTIC_IP" != true ]; then
    EIP_PLAN_LINE="  elastic ip        disabled (USE_ELASTIC_IP=false) — uses the instance's ephemeral public IP"
  elif [ -n "$ELASTIC_IP_ADDRESS" ]; then
    EIP_PLAN_LINE="  elastic ip        override $ELASTIC_IP_ADDRESS    associated as-is (not released on teardown)"
  else
    EIP_PLAN_LINE="  elastic ip        associated to $SLUG      (stable address across stop/start)"
  fi
  cat <<PLAN
[dry-run] Would create/reuse, in account $ACCOUNT_ID / $REGION:
  security group    $RUN_SG                  22 from $OPERATOR_CIDR (break-glass only)
  instance profile  $SYSTEM_IAM_PROFILE      shared; read-only on $SYSTEM_SSM_PARAM
                                             (must exist: --put-system-secrets creates it)
  instance          $SLUG                    $INSTANCE_TYPE, ${ROOT_DISK_GB}GB gp3 root
                                             ${ROOT_IOPS} IOPS / ${ROOT_THROUGHPUT} MB/s
  ssh config entry  Host $SLUG
  harness           run-harness/ -> /srv/crux-run/run-harness (staged for run setup)
$EIP_PLAN_LINE
  secrets           ${RUN_SECRETS_FILE:-<--secrets file>} -> scp to $BOX_SECRETS_PATH,
                                             deleted there after configure
  dials             $CONTROL_MCP_BASE
  agent             $AGENT_PLATFORM, $MODEL, effort $EFFORT
  pins              $ACP_COMMAND@$(cfg "${AGENT_PLATFORM^^}_ACP_VERSION"), acp-gateway@$ACP_GATEWAY_VERSION
teardown-workspace-aws-resources.sh releases the Elastic IP: an allocated-but-unassociated EIP bills by
the hour, so leaking one is the easy way to pay for a box you deleted. An ELASTIC_IP_ADDRESS override is
never released by teardown, so it can be reused by the next workspace.
Nothing billable was created.
PLAN
  exit 0
fi

# ====== PER-RUN SECRETS FILE (required for a real provision) ======
# Validate per-run secrets before launching an instance.
[ -n "$RUN_SECRETS_FILE" ] \
  || die "A provision run needs --secrets <json> (copy run-secrets.json.example). It is scp'd to the box and deleted there after configure."
[ -f "$RUN_SECRETS_FILE" ] || die "No such file: $RUN_SECRETS_FILE"
jq -e . "$RUN_SECRETS_FILE" >/dev/null 2>&1 || die "$RUN_SECRETS_FILE is not valid JSON"
validate_agent_key "$RUN_SECRETS_FILE"
validate_run_api_keys "$RUN_SECRETS_FILE"
for k in AGENTRQ_WORKSPACE_ID AGENTRQ_WORKSPACE_TOKEN; do
  v="$(jq -re --arg k "$k" '.[$k] // empty' "$RUN_SECRETS_FILE")" \
    || die "$RUN_SECRETS_FILE is missing required key: $k"
  case "$v" in *CHANGE*|*REPLACE*|*xxx*|"") die "$k still looks like a placeholder" ;; esac
done
ok "Per-run secrets file $RUN_SECRETS_FILE looks complete ($API_KEY_NAME and workspace credentials, not echoed)"
if [ "$AGENT_PLATFORM" = claude ]; then
  [ -f "$SCRIPT_DIR/../../agentrq/claude/.claude/hooks/langfuse_hook.py" ] \
    || die "The vendored Claude Langfuse hook is missing."
fi

# ====== SYSTEM PARAMETER MUST ALREADY EXIST ======
# Require the shared SSM parameter before launching an instance.
info "System secrets at $SYSTEM_SSM_PARAM"
aws_ ssm get-parameter --name "$SYSTEM_SSM_PARAM" >/dev/null 2>&1 \
  || die "$SYSTEM_SSM_PARAM does not exist. Upload it once first: ./provision-workspace-aws-resources.sh --put-system-secrets run-system-secrets.json"
aws_iam_ get-instance-profile --instance-profile-name "$SYSTEM_IAM_PROFILE" >/dev/null 2>&1 \
  || die "Instance profile $SYSTEM_IAM_PROFILE does not exist. --put-system-secrets creates it."
ok "Parameter and $SYSTEM_IAM_PROFILE present"

# ====== KEY PAIR ======
info "Key pair '$KEY_NAME'"
aws_ ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1 \
  || die "Key pair '$KEY_NAME' does not exist. Provision the control box first — it creates it."
[ -f "$KEY_FILE" ] || die "$KEY_FILE is missing locally; the private key cannot be re-downloaded."
ok "Present"

# ====== VPC / SUBNET ======
# Same VPC as the control box, or the SG-to-SG rule cannot apply.
info "Default VPC and subnet"
VPC_ID="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
[[ "$VPC_ID" != "None" && -n "$VPC_ID" ]] || die "No default VPC in $REGION"
SUBNET_ID="$(aws_ ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  "Name=map-public-ip-on-launch,Values=true" --query 'Subnets[0].SubnetId' --output text)"
[[ "$SUBNET_ID" != "None" && -n "$SUBNET_ID" ]] || die "No public subnet in $VPC_ID"
ok "VPC $VPC_ID, subnet $SUBNET_ID"

# ====== SECURITY GROUP ======
# Use crux-run-sg, created by make-control-box.sh for controller access.
info "Security group '$RUN_SG'"
RUN_SG_ID="$(aws_ ec2 describe-security-groups \
  --filters "Name=group-name,Values=$RUN_SG" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
[[ -n "$RUN_SG_ID" && "$RUN_SG_ID" != "None" ]] \
  || die "$RUN_SG does not exist. Run src/ec2-control/make-control-box.sh first: it creates both SGs, and crux-control-sg's :2026 rule references this one."
ok "$RUN_SG_ID"

# No per-box IAM: the instance boots with the shared crux-system-profile
# (verified in preflight), whose only privilege is reading /crux/system/env.
# Per-run secrets never touch AWS — they are scp'd below and deleted on-box.

# ====== AMI ======
info "Ubuntu 24.04 AMI"
AMI_ID="$(aws_ ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)"
[[ -n "$AMI_ID" && "$AMI_ID" != "None" ]] || die "Could not resolve the Ubuntu 24.04 AMI"
ok "AMI $AMI_ID"

# ====== INSTANCE ======
info "Instance '$SLUG'"
INSTANCE_ID="$(aws_ ec2 describe-instances \
  --filters "Name=tag:Name,Values=$SLUG" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)"

if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  warn "Reusing existing instance $INSTANCE_ID tagged Name=$SLUG"
  STATE="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)"
  [ "$STATE" = "stopped" ] && { info "Starting it"; aws_ ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null; }
else
  INSTANCE_ID="$(aws_ ec2 run-instances \
    --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
    --security-group-ids "$RUN_SG_ID" --subnet-id "$SUBNET_ID" \
    --associate-public-ip-address \
    --iam-instance-profile "Name=$SYSTEM_IAM_PROFILE" \
    --metadata-options "HttpTokens=required" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${ROOT_DISK_GB},\"VolumeType\":\"gp3\",\"Iops\":${ROOT_IOPS},\"Throughput\":${ROOT_THROUGHPUT},\"DeleteOnTermination\":true}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$SLUG},{Key=CruxRole,Value=run}]" \
    --query 'Instances[0].InstanceId' --output text)"
  ok "Launched $INSTANCE_ID"
fi

info "Waiting for 'running'"
aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
ok "Running"

# ====== ELASTIC IP ======
# Allocate a stable public IP for the SSH alias, unless an existing one was
# handed in via ELASTIC_IP_ADDRESS (resolved and validated above).
info "Elastic IP"
if [ "$USE_ELASTIC_IP" = true ]; then
  if [ -n "$ELASTIC_IP_ADDRESS" ]; then
    ALLOC_ID="$ELASTIC_IP_OVERRIDE_ALLOC_ID"
    ok "Using override $ELASTIC_IP_ADDRESS ($ALLOC_ID; not allocated by this script; teardown will not release it)"
  else
    ALLOC_ID="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$SLUG" \
      --query 'Addresses[0].AllocationId' --output text)"
    if [ -z "$ALLOC_ID" ] || [ "$ALLOC_ID" = "None" ]; then
      ALLOC_ID="$(aws_ ec2 allocate-address --domain vpc \
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$SLUG},{Key=CruxRole,Value=run}]" \
        --query 'AllocationId' --output text)"
      ok "Allocated $ALLOC_ID"
    else
      ok "Reusing $ALLOC_ID"
    fi
  fi
  aws_ ec2 associate-address --instance-id "$INSTANCE_ID" \
    --allocation-id "$ALLOC_ID" >/dev/null
  PUBLIC_IP="$(aws_ ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
    --query 'Addresses[0].PublicIp' --output text)"
else
  PUBLIC_IP="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
  [[ -n "$PUBLIC_IP" && "$PUBLIC_IP" != "None" ]] \
    || die "Instance has no public IPv4 address; use an Elastic IP or a subnet with public IP assignment."
  warn "Using instance public IP; after stop/start, rerun provisioning to refresh the SSH alias."
fi
PRIVATE_IP="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)"
ok "Public $PUBLIC_IP / private $PRIVATE_IP"

# ====== SSH CONFIG ENTRY ======
info "~/.ssh/config entry for '$SLUG'"
SSH_CONFIG="$HOME/.ssh/config"
touch "$SSH_CONFIG"; chmod 600 "$SSH_CONFIG"
if grep -qE "^Host[[:space:]]+$SLUG\$" "$SSH_CONFIG"; then
  # Rewrite in place: an ephemeral box gets a new IP every launch, so a stale
  # HostName here is the most likely reason a re-run "hangs" on SSH.
  python3 - "$SSH_CONFIG" "$SLUG" "$PUBLIC_IP" "$SSH_USER" "$KEY_FILE" <<'PY'
import re, sys
path, slug, ip, user, key = sys.argv[1:6]
text = open(path).read()
block = (f"Host {slug}\n    HostName {ip}\n    User {user}\n"
         f"    IdentityFile {key}\n    StrictHostKeyChecking accept-new\n")
pattern = re.compile(rf"^Host[ \t]+{re.escape(slug)}[ \t]*$.*?(?=^Host[ \t]|\Z)", re.M | re.S)
open(path, "w").write(pattern.sub(block, text))
PY
  ok "Updated existing entry"
else
  cat >> "$SSH_CONFIG" <<ENTRY

Host $SLUG
    HostName $PUBLIC_IP
    User $SSH_USER
    IdentityFile $KEY_FILE
    StrictHostKeyChecking accept-new
ENTRY
  ok "Appended entry"
fi

# ====== WAIT FOR SSH ======
# ====== STALE HOST KEY ======
# Remove the cached host key for a replacement instance.
# StrictHostKeyChecking=accept-new rejects changed keys.
info "Clearing any stale host key for $PUBLIC_IP"
ssh-keygen -R "$PUBLIC_IP" >/dev/null 2>&1 || true
ssh-keygen -R "$SLUG" >/dev/null 2>&1 || true
ok "known_hosts is clean for this address"

info "Waiting for SSH on $SLUG"
for i in $(seq 1 40); do
  if ssh -o ConnectTimeout=5 -o BatchMode=yes "$SLUG" true 2>/dev/null; then
    ok "SSH is up (after ~$((i*5))s)"; SSH_UP=1; break
  fi
  sleep 5
done
[ "${SSH_UP:-0}" = 1 ] \
  || die "SSH never came up after ~200s. In order of likelihood:
  - your address changed: OPERATOR_CIDR is $OPERATOR_CIDR, you are $(curl -s --max-time 5 https://checkip.amazonaws.com 2>/dev/null || echo '<could not check>')
  - the instance is still booting (rare past 200s)
  - a host key mismatch is being refused — this script clears known_hosts for
    $PUBLIC_IP first, so this should not happen; verify by hand with
    ssh -v $SLUG"

info "Staging run-harness/ on $SLUG"
ssh "$SLUG" 'sudo install -d -o ubuntu -g ubuntu /srv/crux-run /srv/crux-run/run-harness'
git -C "$SCRIPT_DIR/../.." archive HEAD run-harness \
  | ssh "$SLUG" 'tar -x -C /srv/crux-run'
ok "Harness staged at /srv/crux-run/run-harness; resolve run settings before launch"

# ====== INSTALL (software, bakeable) ======
info "install-run.sh — software"
scp -q "$SCRIPT_DIR/install-run.sh" "$SLUG:/tmp/install-run.sh"
ssh "$SLUG" "chmod +x /tmp/install-run.sh && sudo \
  AGENT_PLATFORM='$AGENT_PLATFORM' \
  CLAUDE_VERSION='$CLAUDE_VERSION' CLAUDE_ACP_VERSION='$CLAUDE_ACP_VERSION' \
  CODEX_VERSION='$CODEX_VERSION' \
  CODEX_ACP_VERSION='$CODEX_ACP_VERSION' ACP_GATEWAY_VERSION='$ACP_GATEWAY_VERSION' \
  /tmp/install-run.sh"

# ====== PRIVATE PATH FOR THE PUBLIC HOSTNAME ======
# Resolve the public controller hostname to its private IP. This preserves
# the TLS hostname and AgentRQ Host routing while using private traffic
# covered by the security-group reference.
MCP_HOST="$(printf '%s' "$CONTROL_MCP_BASE" | sed -E 's#^https?://##; s#[:/].*$##')"
if [ "$MCP_HOST" != "$CONTROL_DNS" ]; then
  info "Pinning $MCP_HOST to the control box's private address on $SLUG"
  # The address comes from the AWS lookup, not from resolving a name on the
  # box: ec2.internal names resolve by pattern, so a wrong one looks right.
  ssh "$SLUG" "set -e
    sudo sed -i '/[[:space:]]$MCP_HOST\$/d' /etc/hosts
    echo '$CONTROL_PRIVATE_IP $MCP_HOST' | sudo tee -a /etc/hosts >/dev/null
    echo '  pinned $MCP_HOST -> $CONTROL_PRIVATE_IP'" \
    || die "Could not pin $MCP_HOST on $SLUG"
  ok "$MCP_HOST resolves to the private address on $SLUG"
fi

# ====== REACHABILITY GATE ======
# Check controller reachability before starting the gateway.
info "Can the run box reach the control box at $CONTROL_MCP_BASE?"
if ssh "$SLUG" "curl -fsS -o /dev/null --max-time 8 '${CONTROL_MCP_BASE}/'" 2>/dev/null; then
  ok "$CONTROL_MCP_BASE answers"
else
  die "Run box cannot reach $CONTROL_MCP_BASE. Check, in order:
  - crux-control-sg permits the port from $RUN_SG ($RUN_SG_ID) — :443 for an
    https base, :$AGENTRQ_PORT for the private http one
  - a 404 here means AgentRQ is up but ROUTING BY HOST: AGENTRQ_DOMAIN on the
    control box does not match the hostname in CONTROL_MCP_BASE
  - the control box's container is bound to the address you are dialling"
fi

# ====== PER-RUN SECRETS (scp, deleted on-box after configure) ======
# The file rides the same SSH channel as the scripts. configure-run.sh reads
# it, writes the values where they live (mode-600 files), then deletes it —
# so it exists on the box only for the duration of the configure step.
info "Copying per-run secrets to $SLUG:$BOX_SECRETS_PATH"
scp -q "$RUN_SECRETS_FILE" "$SLUG:$BOX_SECRETS_PATH"
ssh "$SLUG" "chmod 600 '$BOX_SECRETS_PATH'"
ok "Copied (mode 600; configure-run.sh deletes it)"

# ====== CONFIGURE (secrets, per-run) ======
info "configure-run.sh — config and gateway"
scp -q "$SCRIPT_DIR/configure-run.sh" "$SCRIPT_DIR/agent-config.sh" "$SLUG:/tmp/"
if [ "$AGENT_PLATFORM" = claude ]; then
  scp -q "$SCRIPT_DIR/../../agentrq/claude/.claude/hooks/langfuse_hook.py" "$SLUG:/tmp/langfuse_hook.py"
fi
ssh "$SLUG" "chmod +x /tmp/configure-run.sh && sudo AWS_REGION='$REGION' \
  RUN_SECRETS_PATH='$BOX_SECRETS_PATH' SYSTEM_SSM_PARAM='$SYSTEM_SSM_PARAM' \
  RUN_SLUG='$SLUG' AGENT_PLATFORM='$AGENT_PLATFORM' MODEL_PROVIDER='$MODEL_PROVIDER' \
  CLAUDE_MODEL='$CLAUDE_MODEL' CLAUDE_EFFORT='$CLAUDE_EFFORT' \
  CODEX_MODEL='$CODEX_MODEL' CODEX_REASONING_EFFORT='$CODEX_REASONING_EFFORT' \
  CONTROL_MCP_BASE='$CONTROL_MCP_BASE' \
  TRACING_PLUGIN_VERSION='$TRACING_PLUGIN_VERSION' \
  TRACING_HOOK_TRUSTED_HASH='$TRACING_HOOK_TRUSTED_HASH' \
  /tmp/configure-run.sh"

cat <<DONE

$(ok "Run box ready")

  instance   $INSTANCE_ID ($INSTANCE_TYPE) at $PUBLIC_IP
  ssh        ssh $SLUG
  agent      $AGENT_PLATFORM, $MODEL, effort $EFFORT
  harness    /srv/crux-run/run-harness (unconfigured scaffold)
  dials      $CONTROL_MCP_BASE
  logs       ssh $SLUG 'journalctl -u crux-acp-gateway -f'
  langfuse   environment=$SLUG

Send the workspace a task from the AgentRQ dashboard and it should be answered
by this box. Teardown: ./teardown-workspace-aws-resources.sh
DONE
