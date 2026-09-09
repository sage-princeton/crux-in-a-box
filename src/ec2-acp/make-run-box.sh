#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# make-run-box.sh — provision an ephemeral codex ACP run box
# ==========================================================================
# Run on your LOCAL machine. Launches the box into crux-run-sg (which is what
# grants it access to the control box's :2026), installs the software, writes
# the per-run config and starts the ACP gateway.
#
# The run box dials the control box. It needs no inbound to do its job; the
# SSH rule is break-glass only.
#
# Usage:
#   ./make-run-box.sh [CONFIG_FILE]                     # provision
#   ./make-run-box.sh --put-secrets <json> [CONFIG]     # upload run secrets
#   ./make-run-box.sh --dry-run [CONFIG_FILE]           # print plan, touch nothing
#   ./make-run-box.sh --handshake [CONFIG_FILE]         # ACP handshake only
#
# The secrets file is JSON with these keys (see --put-secrets output):
#   OPENAI_API_KEY, AGENTRQ_WORKSPACE_ID, AGENTRQ_WORKSPACE_TOKEN,
#   LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
# ==========================================================================

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ====== PARSE ARGS ======
CONFIG_FILE=""; PUT_SECRETS=""; DRY_RUN=0; HANDSHAKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --put-secrets) PUT_SECRETS="${2:-}"; [ -n "$PUT_SECRETS" ] || die "--put-secrets needs a file"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --handshake)   HANDSHAKE=1; shift ;;
    -h|--help)     sed -n '3,30p' "$0"; exit 0 ;;
    -*)            die "Unknown flag: $1" ;;
    *)             [ -z "$CONFIG_FILE" ] || die "Only one config file"; CONFIG_FILE="$1"; shift ;;
  esac
done

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

MISSING=()
for k in AWS_REGION RUN_SLUG CONTROL_PRIVATE_DNS OPERATOR_CIDR INSTANCE_TYPE \
         ROOT_DISK_GB KEY_NAME CODEX_ACP_VERSION ACP_GATEWAY_VERSION \
         TRACING_PLUGIN_VERSION TRACING_HOOK_TRUSTED_HASH; do
  [ -n "${CFG[$k]:-}" ] || MISSING+=("$k")
done
[ ${#MISSING[@]} -eq 0 ] || die "Missing required key(s) in $CONFIG_FILE: ${MISSING[*]}"

PROFILE="${CFG[AWS_PROFILE]:-}"
REGION="${CFG[AWS_REGION]}"
SLUG="${CFG[RUN_SLUG]}"
CONTROL_DNS="${CFG[CONTROL_PRIVATE_DNS]}"
OPERATOR_CIDR="${CFG[OPERATOR_CIDR]}"
INSTANCE_TYPE="${CFG[INSTANCE_TYPE]}"
ROOT_DISK_GB="${CFG[ROOT_DISK_GB]}"
KEY_NAME="${CFG[KEY_NAME]}"
CODEX_ACP_VERSION="${CFG[CODEX_ACP_VERSION]}"
ACP_GATEWAY_VERSION="${CFG[ACP_GATEWAY_VERSION]}"
TRACING_PLUGIN_VERSION="${CFG[TRACING_PLUGIN_VERSION]}"
TRACING_HOOK_TRUSTED_HASH="${CFG[TRACING_HOOK_TRUSTED_HASH]}"

case "$OPERATOR_CIDR" in
  */32) ;;
  *) die "OPERATOR_CIDR must be a /32 (got '$OPERATOR_CIDR')." ;;
esac
case "$CONTROL_DNS" in
  localhost|127.*|*.compute-1.amazonaws.com|*.compute.amazonaws.com)
    die "CONTROL_PRIVATE_DNS looks public or local ('$CONTROL_DNS'). It must be the control box's PRIVATE DNS name (ip-x-x-x-x.ec2.internal) — the security group only permits the VPC path." ;;
esac

RUN_SG="crux-run-sg"
IAM_ROLE="crux-run-role"
IAM_PROFILE="crux-run-profile"
SSM_ENV_PARAM="/crux/run/${SLUG}/env"
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

# ====== --put-secrets ======
if [ -n "$PUT_SECRETS" ]; then
  [ -f "$PUT_SECRETS" ] || die "No such file: $PUT_SECRETS"
  jq -e . "$PUT_SECRETS" >/dev/null 2>&1 || die "$PUT_SECRETS is not valid JSON"
  for k in OPENAI_API_KEY AGENTRQ_WORKSPACE_ID AGENTRQ_WORKSPACE_TOKEN \
           LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
    v="$(jq -re --arg k "$k" '.[$k] // empty' "$PUT_SECRETS")" \
      || die "$PUT_SECRETS is missing required key: $k"
    # A placeholder that reaches the box fails at gateway start, far from here.
    case "$v" in *CHANGE*|*REPLACE*|*xxx*|"") die "$k still looks like a placeholder" ;; esac
  done
  info "Uploading $PUT_SECRETS to SSM at $SSM_ENV_PARAM (SecureString)"
  if [ "$DRY_RUN" = 1 ]; then ok "[dry-run] would put-parameter $SSM_ENV_PARAM"; exit 0; fi
  aws_ ssm put-parameter --name "$SSM_ENV_PARAM" --type SecureString --overwrite \
    --value "$(cat "$PUT_SECRETS")" --description "CRUX run box $SLUG config" >/dev/null
  ok "Stored. The instance role reads this at boot."
  exit 0
fi

# ====== --handshake: prove ACP works, no AgentRQ involved ======
# Deliberately separable from the full round trip: this answers "can we speak
# ACP to codex on that box" without also depending on the VPC path to AgentRQ.
if [ "$HANDSHAKE" = 1 ]; then
  info "ACP handshake against $SLUG (no AgentRQ, no workspace)"
  ssh "$SLUG" 'cd /srv/crux-run && acp-gateway --agent-info -- codex-acp' 2>&1
  exit $?
fi

if [ "$DRY_RUN" = 1 ]; then
  cat <<PLAN
[dry-run] Would create/reuse, in account $ACCOUNT_ID / $REGION:
  security group    $RUN_SG                  22 from $OPERATOR_CIDR (break-glass only)
  iam role/profile  $IAM_ROLE / $IAM_PROFILE   read-only on $SSM_ENV_PARAM
  instance          $SLUG                    $INSTANCE_TYPE, ${ROOT_DISK_GB}GB root
  ssh config entry  Host $SLUG
  elastic ip        associated to $SLUG      (stable address across stop/start)
  dials             $CONTROL_DNS:$AGENTRQ_PORT
  pins              codex-acp@$CODEX_ACP_VERSION, acp-gateway@$ACP_GATEWAY_VERSION
teardown.sh releases the Elastic IP: an allocated-but-unassociated EIP bills by
the hour, so leaking one is the easy way to pay for a box you deleted.
Nothing billable was created.
PLAN
  exit 0
fi

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
[ "$VPC_ID" != "None" ] && [ -n "$VPC_ID" ] || die "No default VPC in $REGION"
SUBNET_ID="$(aws_ ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  "Name=map-public-ip-on-launch,Values=true" --query 'Subnets[0].SubnetId' --output text)"
[ "$SUBNET_ID" != "None" ] && [ -n "$SUBNET_ID" ] || die "No public subnet in $VPC_ID"
ok "VPC $VPC_ID, subnet $SUBNET_ID"

# ====== SECURITY GROUP ======
# crux-run-sg is created by make-control-box.sh, because crux-control-sg's
# :2026 rule references it. Requiring it here rather than creating a second
# one keeps a single SG as the thing that grants control-plane access.
info "Security group '$RUN_SG'"
RUN_SG_ID="$(aws_ ec2 describe-security-groups \
  --filters "Name=group-name,Values=$RUN_SG" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
[ -n "$RUN_SG_ID" ] && [ "$RUN_SG_ID" != "None" ] \
  || die "$RUN_SG does not exist. Run src/ec2-control/make-control-box.sh first: it creates both SGs, and crux-control-sg's :2026 rule references this one."
ok "$RUN_SG_ID"

# ====== IAM ROLE ======
info "IAM role '$IAM_ROLE'"
if aws_iam_ get-role --role-name "$IAM_ROLE" >/dev/null 2>&1; then
  ok "Role exists"
else
  aws_iam_ create-role --role-name "$IAM_ROLE" \
    --description "CRUX ACP run box - read its own SSM config parameter" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  ok "Created role"
fi
# Scoped to this box's own parameter: one run box cannot read another's keys.
aws_iam_ put-role-policy --role-name "$IAM_ROLE" --policy-name "read-run-env" \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter${SSM_ENV_PARAM}\"}]}" >/dev/null
ok "Inline policy: ssm:GetParameter on $SSM_ENV_PARAM only"

if aws_iam_ get-instance-profile --instance-profile-name "$IAM_PROFILE" >/dev/null 2>&1; then
  ok "Instance profile exists"
else
  aws_iam_ create-instance-profile --instance-profile-name "$IAM_PROFILE" >/dev/null
  aws_iam_ add-role-to-instance-profile \
    --instance-profile-name "$IAM_PROFILE" --role-name "$IAM_ROLE"
  info "Waiting 10s for IAM to propagate"
  sleep 10
  ok "Created instance profile"
fi

# ====== AMI ======
info "Ubuntu 24.04 AMI"
AMI_ID="$(aws_ ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)"
[ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ] || die "Could not resolve the Ubuntu 24.04 AMI"
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
    --iam-instance-profile "Name=$IAM_PROFILE" \
    --metadata-options "HttpTokens=required" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${ROOT_DISK_GB},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$SLUG},{Key=CruxRole,Value=run}]" \
    --query 'Instances[0].InstanceId' --output text)"
  ok "Launched $INSTANCE_ID"
fi

info "Waiting for 'running'"
aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
ok "Running"

# ====== ELASTIC IP ======
# Every box gets a stable address. Without one, a stop/start hands the box a
# new public IP and silently invalidates the ~/.ssh/config entry — which
# presents as an SSH hang, not as an obviously wrong address.
info "Elastic IP"
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
aws_ ec2 associate-address --instance-id "$INSTANCE_ID" \
  --allocation-id "$ALLOC_ID" >/dev/null
PUBLIC_IP="$(aws_ ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
  --query 'Addresses[0].PublicIp' --output text)"
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
info "Waiting for SSH on $SLUG"
for i in $(seq 1 40); do
  if ssh -o ConnectTimeout=5 -o BatchMode=yes "$SLUG" true 2>/dev/null; then
    ok "SSH is up (after ~$((i*5))s)"; SSH_UP=1; break
  fi
  sleep 5
done
[ "${SSH_UP:-0}" = 1 ] \
  || die "SSH never came up. Check $OPERATOR_CIDR is still your address: curl -s https://checkip.amazonaws.com"

# ====== INSTALL (software, bakeable) ======
info "install-run.sh — software"
scp -q "$SCRIPT_DIR/install-run.sh" "$SLUG:/tmp/install-run.sh"
ssh "$SLUG" "chmod +x /tmp/install-run.sh && sudo \
  CODEX_ACP_VERSION='$CODEX_ACP_VERSION' ACP_GATEWAY_VERSION='$ACP_GATEWAY_VERSION' \
  /tmp/install-run.sh"

# ====== REACHABILITY GATE ======
# Checked before configuring the gateway: if the VPC path is shut, the gateway
# would come up and fail to reach its workspace, which is a much harder
# failure to read than this one line.
info "Can the run box reach the control box over the VPC?"
if ssh "$SLUG" "curl -fsS -o /dev/null --max-time 8 http://${CONTROL_DNS}:${AGENTRQ_PORT}/" 2>/dev/null; then
  ok "$CONTROL_DNS:$AGENTRQ_PORT answers"
else
  die "Run box cannot reach $CONTROL_DNS:$AGENTRQ_PORT. Check that crux-control-sg allows $AGENTRQ_PORT from $RUN_SG ($RUN_SG_ID), that the control box's container is bound to its PRIVATE ip (not just 127.0.0.1), and that CONTROL_PRIVATE_DNS is right."
fi

# ====== CONFIGURE (secrets, per-run) ======
info "configure-run.sh — config and gateway"
scp -q "$SCRIPT_DIR/configure-run.sh" "$SLUG:/tmp/configure-run.sh"
ssh "$SLUG" "chmod +x /tmp/configure-run.sh && sudo AWS_REGION='$REGION' \
  SSM_ENV_PARAM='$SSM_ENV_PARAM' RUN_SLUG='$SLUG' \
  CONTROL_PRIVATE_DNS='$CONTROL_DNS' AGENTRQ_PORT='$AGENTRQ_PORT' \
  TRACING_PLUGIN_VERSION='$TRACING_PLUGIN_VERSION' \
  TRACING_HOOK_TRUSTED_HASH='$TRACING_HOOK_TRUSTED_HASH' \
  /tmp/configure-run.sh"

cat <<DONE

$(ok "Run box ready")

  instance   $INSTANCE_ID ($INSTANCE_TYPE) at $PUBLIC_IP
  ssh        ssh $SLUG
  dials      $CONTROL_DNS:$AGENTRQ_PORT
  logs       ssh $SLUG 'journalctl -u crux-acp-gateway -f'
  langfuse   environment=$SLUG

Send the workspace a task from the AgentRQ web UI (via src/ec2-control/connect.sh)
and it should be answered by this box. Teardown: ./teardown.sh
DONE
