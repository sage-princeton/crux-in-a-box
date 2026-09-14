#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# make-control-box.sh — provision the persistent AgentRQ control plane
# ==========================================================================
# Run on your LOCAL machine. Creates (idempotently) the key pair, the two
# security groups, the instance role, an Elastic IP and a data volume, then
# launches the control box and hands off to configure-control.sh on the box.
#
# Access is HTTPS: Caddy fronts AgentRQ with a real Let's Encrypt certificate
# and :443 is restricted to TLS_INGRESS_CIDR. There is no tunnel and no second
# access path — :22 and :443 are gated by the same address, so a changed IP is
# fixed by reopening the security group, not by falling back to SSH.
#
# Usage:
#   ./make-control-box.sh [CONFIG_FILE]                 # provision
#   ./make-control-box.sh --put-secrets <envfile> [CONFIG_FILE]
#                                                       # upload AgentRQ .env
#                                                       # to SSM, then exit
#   ./make-control-box.sh --dry-run [CONFIG_FILE]       # print the plan, touch
#                                                       # nothing
#
# CONFIG_FILE defaults to placeholders-control.txt (see the .example).
# ==========================================================================

# ====== HELPERS ======
info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ====== PARSE ARGS ======
CONFIG_FILE=""
PUT_SECRETS=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --put-secrets) PUT_SECRETS="${2:-}"; [ -n "$PUT_SECRETS" ] || die "--put-secrets needs a file"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     sed -n '3,26p' "$0"; exit 0 ;;
    -*)            die "Unknown flag: $1" ;;
    *)             [ -z "$CONFIG_FILE" ] || die "Only one config file"; CONFIG_FILE="$1"; shift ;;
  esac
done

CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/placeholders-control.txt}"
[ -f "$CONFIG_FILE" ] \
  || die "Config file not found: $CONFIG_FILE (copy placeholders-control.txt.example)"

# ====== LOAD CONFIG (KEY=VALUE) ======
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

# Collect every missing key at once rather than one re-run per key.
MISSING=()
for k in AWS_REGION CONTROL_SLUG OPERATOR_CIDR INSTANCE_TYPE \
         ROOT_DISK_GB DATA_DISK_GB KEY_NAME; do
  [ -n "${CFG[$k]:-}" ] || MISSING+=("$k")
done
[ ${#MISSING[@]} -eq 0 ] || die "Missing required key(s) in $CONFIG_FILE: ${MISSING[*]}"

# AWS_PROFILE is optional: leave it empty to use ambient credentials
# (AWS_ACCESS_KEY_ID/... in the environment, or an instance role).
PROFILE="${CFG[AWS_PROFILE]:-}"
REGION="${CFG[AWS_REGION]}"
SLUG="${CFG[CONTROL_SLUG]}"
OPERATOR_CIDR="${CFG[OPERATOR_CIDR]}"
INSTANCE_TYPE="${CFG[INSTANCE_TYPE]}"
ROOT_DISK_GB="${CFG[ROOT_DISK_GB]}"
DATA_DISK_GB="${CFG[DATA_DISK_GB]}"
KEY_NAME="${CFG[KEY_NAME]}"
# TLS is mandatory: the dashboard is reached over HTTPS and there is no other
# path in. A config file predating this still works — TLS_ENABLED is simply
# ignored now, and the remaining TLS_* keys have workable defaults.
TLS_HOSTNAME="${CFG[TLS_HOSTNAME]:-}"
TLS_EMAIL="${CFG[TLS_EMAIL]:-}"
TLS_INGRESS_CIDR="${CFG[TLS_INGRESS_CIDR]:-$OPERATOR_CIDR}"

case "$OPERATOR_CIDR" in
  */32) ;;
  *) die "OPERATOR_CIDR must be a /32 (got '$OPERATOR_CIDR'). Widening it opens SSH to more than you." ;;
esac
case "$TLS_INGRESS_CIDR" in
  */*) ;;
  *) die "TLS_INGRESS_CIDR must be a CIDR (got '$TLS_INGRESS_CIDR'). Use a /32 for just yourself, or 0.0.0.0/0 to publish." ;;
esac
# A hostname that does not resolve to this box fails the ACME check, and
# Let's Encrypt rate-limits failures. Checked again after the EIP is known.
case "$TLS_HOSTNAME" in
  localhost|*.ec2.internal|*.compute-1.amazonaws.com|*.compute.amazonaws.com)
    die "TLS_HOSTNAME '$TLS_HOSTNAME' is not publicly resolvable, so Let's Encrypt cannot validate it. Leave it empty for the sslip.io default." ;;
esac

CONTROL_SG="crux-control-sg"
RUN_SG="crux-run-sg"
IAM_ROLE="crux-control-role"
IAM_PROFILE="crux-control-profile"
SSM_ENV_PARAM="/crux/control/env"
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
SSH_USER="ubuntu"
AGENTRQ_PORT=2026

if [ -n "$PROFILE" ]; then
  PROFILE_ARGS=(--profile "$PROFILE")
  CRED_DESC="profile '$PROFILE'"
  AUTH_HINT="Run: aws sso login --sso-session <session>"
else
  PROFILE_ARGS=()
  CRED_DESC="ambient environment credentials (no AWS_PROFILE set in $CONFIG_FILE)"
  AUTH_HINT="Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, or set AWS_PROFILE in $CONFIG_FILE"
fi

# IAM is global; everything else is regional.
aws_()     { aws "${PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_iam_() { aws "${PROFILE_ARGS[@]}" iam "$@"; }

# ====== PREFLIGHT ======
info "Preflight"
command -v aws >/dev/null || die "aws CLI not found"
command -v jq  >/dev/null || die "jq not found (brew install jq)"
command -v ssh >/dev/null || die "ssh not found"
aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "Not authenticated with $CRED_DESC. $AUTH_HINT"
ACCOUNT_ID="$(aws_ sts get-caller-identity --query Account --output text)"
ok "Authenticated to account $ACCOUNT_ID in $REGION using $CRED_DESC"

# ====== --put-secrets: upload the AgentRQ .env and exit ======
if [ -n "$PUT_SECRETS" ]; then
  [ -f "$PUT_SECRETS" ] || die "No such file: $PUT_SECRETS"
  grep -q 'CHANGE' "$PUT_SECRETS" 2>/dev/null \
    && die "$PUT_SECRETS still contains a CHANGE... placeholder. Rotate the secrets first."
  info "Uploading $PUT_SECRETS to SSM Parameter Store at $SSM_ENV_PARAM (SecureString)"
  if [ "$DRY_RUN" = 1 ]; then ok "[dry-run] would put-parameter $SSM_ENV_PARAM"; exit 0; fi
  aws_ ssm put-parameter \
    --name "$SSM_ENV_PARAM" --type SecureString --overwrite \
    --value "$(cat "$PUT_SECRETS")" \
    --description "AgentRQ control-plane .env" >/dev/null
  ok "Stored. The instance role reads this at boot; the file never goes in the repo or an AMI."
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  cat <<PLAN
[dry-run] Would create/reuse, in account $ACCOUNT_ID / $REGION:
  key pair          $KEY_NAME               (private key -> $KEY_FILE)
  security group    $CONTROL_SG             22 from $OPERATOR_CIDR; $AGENTRQ_PORT from $RUN_SG
  security group    $RUN_SG                 22 from $OPERATOR_CIDR
  iam role/profile  $IAM_ROLE / $IAM_PROFILE  read-only on $SSM_ENV_PARAM
  instance          $SLUG                   $INSTANCE_TYPE, ${ROOT_DISK_GB}GB root
  ebs volume        ${DATA_DISK_GB}GB gp3   mounted /srv/agentrq
  elastic ip        associated to $SLUG
  ssh config entry  Host $SLUG
  https             caddy + lets encrypt for ${TLS_HOSTNAME:-<dashed-eip>.sslip.io (derived)}
                    443 from $TLS_INGRESS_CIDR, 80 from 0.0.0.0/0 (ACME validation)Nothing billable was created.
PLAN
  exit 0
fi

# ====== KEY PAIR ======
info "Key pair '$KEY_NAME'"
if aws_ ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  ok "Key pair exists"
  [ -f "$KEY_FILE" ] || die "Key pair '$KEY_NAME' exists in AWS but $KEY_FILE is missing locally. \
Delete the key pair and re-run, or restore the .pem — the private key cannot be re-downloaded."
else
  [ -e "$KEY_FILE" ] && die "$KEY_FILE already exists but the AWS key pair does not. Move it aside first."
  aws_ ec2 create-key-pair --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 400 "$KEY_FILE"
  ok "Created key pair; private key at $KEY_FILE (mode 400)"
fi

# ====== VPC / SUBNET ======
info "Default VPC and subnet"
VPC_ID="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
[ "$VPC_ID" != "None" ] && [ -n "$VPC_ID" ] || die "No default VPC in $REGION"
SUBNET_ID="$(aws_ ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  "Name=map-public-ip-on-launch,Values=true" \
  --query 'Subnets[0].SubnetId' --output text)"
[ "$SUBNET_ID" != "None" ] && [ -n "$SUBNET_ID" ] || die "No public subnet in $VPC_ID"
AZ="$(aws_ ec2 describe-subnets --subnet-ids "$SUBNET_ID" \
  --query 'Subnets[0].AvailabilityZone' --output text)"
ok "VPC $VPC_ID, subnet $SUBNET_ID ($AZ)"

# ====== SECURITY GROUPS ======
# Created run-SG first: the control SG references it, so it must exist.
sg_id_of() {
  aws_ ec2 describe-security-groups \
    --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true
}

ensure_sg() {
  local name="$1" desc="$2" id
  id="$(sg_id_of "$name")"
  if [ "$id" = "None" ] || [ -z "$id" ]; then
    id="$(aws_ ec2 create-security-group --group-name "$name" \
      --description "$desc" --vpc-id "$VPC_ID" --query 'GroupId' --output text)"
    ok "Created $name ($id)" >&2
  else
    ok "$name exists ($id)" >&2
  fi
  printf '%s' "$id"
}

# authorize is not idempotent — a duplicate rule is an error, so swallow that
# one case and let anything else surface.
allow() {
  local out
  if out=$(aws_ ec2 authorize-security-group-ingress "$@" 2>&1); then return 0; fi
  case "$out" in
    *InvalidPermission.Duplicate*) return 0 ;;
    *) printf '%s\n' "$out" >&2; return 1 ;;
  esac
}

info "Security groups"
RUN_SG_ID="$(ensure_sg "$RUN_SG" "CRUX ACP run boxes - egress only, SSH break-glass")"
CONTROL_SG_ID="$(ensure_sg "$CONTROL_SG" "CRUX AgentRQ control plane - SSH only, no public web port")"

allow --group-id "$RUN_SG_ID" --protocol tcp --port 22 --cidr "$OPERATOR_CIDR"
allow --group-id "$CONTROL_SG_ID" --protocol tcp --port 22 --cidr "$OPERATOR_CIDR"
# The whole point: AgentRQ's port is reachable from run boxes and nowhere else.
# An SG reference rather than a CIDR keeps this correct as boxes come and go.
allow --group-id "$CONTROL_SG_ID" --protocol tcp --port "$AGENTRQ_PORT" \
      --source-group "$RUN_SG_ID"
# HTTPS, when enabled. :443 is narrow (yours by default), but :80 MUST be open
# to the world: Let's Encrypt validates the HTTP-01 challenge from its own
# servers, so a restricted :80 means no certificate now and no renewal in 90
# days. Caddy answers only the ACME challenge and a redirect there.
allow --group-id "$CONTROL_SG_ID" --protocol tcp --port 443 --cidr "$TLS_INGRESS_CIDR"
allow --group-id "$CONTROL_SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
# Run boxes reach the workspace over :443, not the private :2026. AgentRQ
# ROUTES BY HOST: any request whose Host is not AGENTRQ_DOMAIN gets a 404, so
# the private-DNS path cannot work once AGENTRQ_DOMAIN is the public hostname.
# Dialling the public name is what keeps Host matching — and it means the
# workspace token, which rides in the URL query string, is not sent in plaintext.
allow --group-id "$CONTROL_SG_ID" --protocol tcp --port 443 --source-group "$RUN_SG_ID"
ok "Ingress set: 22 from $OPERATOR_CIDR on both; 443 from $TLS_INGRESS_CIDR and from $RUN_SG; 80 from 0.0.0.0/0 (ACME); $AGENTRQ_PORT on control from $RUN_SG"

# ====== IAM ROLE (read the .env parameter, nothing else) ======
info "IAM role '$IAM_ROLE'"
if aws_iam_ get-role --role-name "$IAM_ROLE" >/dev/null 2>&1; then
  ok "Role exists"
else
  aws_iam_ create-role --role-name "$IAM_ROLE" \
    --description "CRUX AgentRQ control plane - read its own SSM env parameter" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null
  ok "Created role"
fi

# Deliberately narrow: this box has no reason to hold PowerUserAccess.
aws_iam_ put-role-policy --role-name "$IAM_ROLE" \
  --policy-name "read-control-env" \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter${SSM_ENV_PARAM}\"}]}" \
  >/dev/null
ok "Inline policy: ssm:GetParameter on $SSM_ENV_PARAM only"

if aws_iam_ get-instance-profile \
     --instance-profile-name "$IAM_PROFILE" >/dev/null 2>&1; then
  ok "Instance profile exists"
else
  aws_iam_ create-instance-profile \
    --instance-profile-name "$IAM_PROFILE" >/dev/null
  aws_iam_ add-role-to-instance-profile \
    --instance-profile-name "$IAM_PROFILE" --role-name "$IAM_ROLE"
  info "Waiting 10s for IAM to propagate (it is eventually consistent)"
  sleep 10
  ok "Created instance profile"
fi

# ====== AMI (Canonical's published SSM parameter, not a hardcoded id) ======
info "Ubuntu 24.04 AMI"
AMI_ID="$(aws_ ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)"
[ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ] || die "Could not resolve the Ubuntu 24.04 AMI"
ok "AMI $AMI_ID"

# ====== INSTANCE (reuse if one with this Name is already alive) ======
info "Instance '$SLUG'"
INSTANCE_ID="$(aws_ ec2 describe-instances \
  --filters "Name=tag:Name,Values=$SLUG" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)"

if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  warn "Reusing existing instance $INSTANCE_ID tagged Name=$SLUG"
  STATE="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)"
  if [ "$STATE" = "stopped" ]; then
    info "Starting it"
    aws_ ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
  fi
else
  INSTANCE_ID="$(aws_ ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$CONTROL_SG_ID" \
    --subnet-id "$SUBNET_ID" \
    --iam-instance-profile "Name=$IAM_PROFILE" \
    --metadata-options "HttpTokens=required" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${ROOT_DISK_GB},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$SLUG},{Key=CruxRole,Value=control}]" \
    --query 'Instances[0].InstanceId' --output text)"
  ok "Launched $INSTANCE_ID"
fi

info "Waiting for 'running'"
aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
ok "Running"

# ====== DATA VOLUME ======
# Kept out of the block-device mapping and tagged separately so it can outlive
# the instance: the control plane's state is the one thing here that is not
# disposable.
info "Data volume (${DATA_DISK_GB}GB, for /srv/agentrq)"
VOL_ID="$(aws_ ec2 describe-volumes \
  --filters "Name=tag:Name,Values=${SLUG}-data" "Name=status,Values=available,in-use" \
  --query 'Volumes[0].VolumeId' --output text)"

if [ -z "$VOL_ID" ] || [ "$VOL_ID" = "None" ]; then
  VOL_ID="$(aws_ ec2 create-volume --availability-zone "$AZ" \
    --size "$DATA_DISK_GB" --volume-type gp3 \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=${SLUG}-data},{Key=CruxRole,Value=control-state}]" \
    --query 'VolumeId' --output text)"
  aws_ ec2 wait volume-available --volume-ids "$VOL_ID"
  ok "Created $VOL_ID"
else
  ok "Reusing $VOL_ID"
fi

ATTACHED_TO="$(aws_ ec2 describe-volumes --volume-ids "$VOL_ID" \
  --query 'Volumes[0].Attachments[0].InstanceId' --output text)"
if [ "$ATTACHED_TO" != "$INSTANCE_ID" ]; then
  [ "$ATTACHED_TO" = "None" ] || die "$VOL_ID is attached to $ATTACHED_TO, not $INSTANCE_ID"
  aws_ ec2 attach-volume --volume-id "$VOL_ID" \
    --instance-id "$INSTANCE_ID" --device /dev/sdf >/dev/null
  aws_ ec2 wait volume-in-use --volume-ids "$VOL_ID"
  ok "Attached as /dev/sdf"
else
  ok "Already attached"
fi

# ====== ELASTIC IP ======
info "Elastic IP"
ALLOC_ID="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$SLUG" \
  --query 'Addresses[0].AllocationId' --output text)"
if [ -z "$ALLOC_ID" ] || [ "$ALLOC_ID" = "None" ]; then
  ALLOC_ID="$(aws_ ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$SLUG}]" \
    --query 'AllocationId' --output text)"
  ok "Allocated $ALLOC_ID"
else
  ok "Reusing $ALLOC_ID"
fi
aws_ ec2 associate-address --instance-id "$INSTANCE_ID" \
  --allocation-id "$ALLOC_ID" >/dev/null
PUBLIC_IP="$(aws_ ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
  --query 'Addresses[0].PublicIp' --output text)"
PRIVATE_DNS="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PrivateDnsName' --output text)"
PRIVATE_IP="$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)"
ok "Public $PUBLIC_IP / private $PRIVATE_IP ($PRIVATE_DNS)"

# sslip.io resolves <dashed-ip>.sslip.io to the IP in the name, which is what
# buys a trusted certificate with no domain and no DNS account. Derived here
# rather than in the config file because it must track the Elastic IP.
if [ -z "$TLS_HOSTNAME" ]; then
  TLS_HOSTNAME="${PUBLIC_IP//./-}.sslip.io"
  ok "TLS hostname derived from the Elastic IP: $TLS_HOSTNAME"
fi
# Resolve it before asking Caddy to: a name pointing elsewhere burns a
# Let's Encrypt failure, and those are rate-limited.
# python3, not getent: getent does not exist on macOS, and this script runs on
# the operator's laptop. python3 is already required below for the
# ~/.ssh/config rewrite.
RESOLVED="$(python3 -c 'import socket,sys
try: print(socket.gethostbyname(sys.argv[1]))
except OSError: pass' "$TLS_HOSTNAME" 2>/dev/null || true)"
[ -n "$RESOLVED" ] \
  || die "$TLS_HOSTNAME does not resolve. For a custom domain, add an A record to $PUBLIC_IP first."
[ "$RESOLVED" = "$PUBLIC_IP" ] \
  || die "$TLS_HOSTNAME resolves to $RESOLVED, not this box ($PUBLIC_IP). Let's Encrypt would fail the challenge."
ok "$TLS_HOSTNAME resolves to $PUBLIC_IP"

# ====== SSH CONFIG ENTRY ======
# The old create-new-crux-box.sh only printed an IP, so aliases were added by
# hand. bootstrap-workspace.sh and the run-box scripts both expect this alias.
info "~/.ssh/config entry for '$SLUG'"
SSH_CONFIG="$HOME/.ssh/config"
touch "$SSH_CONFIG"; chmod 600 "$SSH_CONFIG"
if grep -qE "^Host[[:space:]]+$SLUG\$" "$SSH_CONFIG"; then
  # Rewrite the block in place so a re-associated EIP does not leave a stale
  # HostName behind.
  python3 - "$SSH_CONFIG" "$SLUG" "$PUBLIC_IP" "$SSH_USER" "$KEY_FILE" <<'PY'
import re, sys
path, slug, ip, user, key = sys.argv[1:6]
text = open(path).read()
block = (f"Host {slug}\n    HostName {ip}\n    User {user}\n"
         f"    IdentityFile {key}\n    StrictHostKeyChecking accept-new\n")
pattern = re.compile(rf"^Host[ \t]+{re.escape(slug)}[ \t]*$.*?(?=^Host[ \t]|\Z)",
                     re.M | re.S)
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
[ "${SSH_UP:-0}" = 1 ] || die "SSH never came up. Check that $OPERATOR_CIDR is still your address: curl -s https://checkip.amazonaws.com"

# ====== BOX-SIDE CONFIGURE ======
info "Copying and running configure-control.sh"
scp -q "$SCRIPT_DIR/configure-control.sh" "$SLUG:/tmp/configure-control.sh"
ssh "$SLUG" "chmod +x /tmp/configure-control.sh && sudo AWS_REGION='$REGION' \
  SSM_ENV_PARAM='$SSM_ENV_PARAM' AGENTRQ_PORT='$AGENTRQ_PORT' \
  PRIVATE_DNS='$PRIVATE_DNS' PRIVATE_IP='$PRIVATE_IP' \
  TLS_HOSTNAME='$TLS_HOSTNAME' TLS_EMAIL='$TLS_EMAIL' \
  /tmp/configure-control.sh"

cat <<DONE

$(ok "Control box ready")

  instance     $INSTANCE_ID ($INSTANCE_TYPE) in $AZ
  ssh          ssh $SLUG
  state        $VOL_ID mounted at /srv/agentrq
  web UI       https://$TLS_HOSTNAME   (443 from $TLS_INGRESS_CIDR)
  run boxes    set CONTROL_MCP_BASE=https://$TLS_HOSTNAME in placeholders-base.txt

If your address changes, both :22 and :443 are gated by it — reopen them with
  aws ec2 authorize-security-group-ingress --group-id $CONTROL_SG_ID \
    --protocol tcp --port 443 --cidr "\$(curl -s https://checkip.amazonaws.com)/32"

Next: the auth hygiene — root login off, JWT secret rotated — see src/ec2-control/README.md.
DONE
