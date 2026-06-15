#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# setup-device.sh
# ==========================================================================
# Run this on your LOCAL machine (macOS / Linux) to provision and bootstrap
# an AWS EC2 instance with a full GUI desktop ready for CRUX experiments.
#
# Prerequisites on the invoking machine:
#   - AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
#   - ssh / ssh-keygen available
#   - jq installed (brew install jq / apt install jq)
#
# What this script does:
#   1. Creates (or reuses) an EC2 key-pair and security group.
#   2. Launches an Ubuntu 22.04 instance (configurable).
#   3. Waits for the instance to be reachable via SSH.
#   4. Copies the linux/ directory to the instance.
#   5. Runs the remote bootstrap (start.sh) which installs a desktop
#      environment, VNC server, openclaw, monitoring, services, etc.
#   6. Prints connection details (SSH + VNC).
# ==========================================================================

# ====== USAGE ======
usage() {
  cat <<USAGE
Usage: $0 --telegram-bot-name <NAME> --telegram-owner-id <ID> --anthropic-model <MODEL> --anthropic-api-key <KEY> --placeholder-map <FILE> [--instance-suffix <SUFFIX>]

Required:
  --telegram-bot-name <NAME>     Bot name as defined in telegram_bots.json
                                   (e.g. cruxlinuxtest). The token is looked up automatically.
  --telegram-owner-id <ID>       Telegram user ID for commands.ownerAllowFrom
  --anthropic-model <MODEL>      Anthropic model ID (e.g. anthropic/claude-opus-4-6)
  --anthropic-api-key <KEY>      Anthropic API key
  --placeholder-map <FILE>       Workspace placeholders file (KEY=VALUE, one per line).
                                   Copy placeholders.txt.example and fill it in.

Optional:
  --instance-suffix <SUFFIX>     Suffix appended to the instance name
                                   (e.g. --instance-suffix 2 → crux-in-a-box-2).
                                   Allows running multiple instances in parallel.

Optional (override via env vars):
  AWS_REGION                     AWS region (default: us-east-1)
  CRUX_INSTANCE_TYPE             EC2 instance type (default: t3.xlarge)
  CRUX_AMI_ID                    AMI ID (default: latest Ubuntu 22.04)
  CRUX_KEY_NAME                  EC2 key pair name (default: crux-in-a-box)
  CRUX_SG_NAME                   Security group name (default: crux-in-a-box-sg)
  CRUX_INSTANCE_NAME             Instance Name tag (default: crux-in-a-box)
  CRUX_DISK_SIZE_GB              Root volume size in GB (default: 80)
USAGE
  exit 1
}

# ====== PARSE ARGS ======
TELEGRAM_BOT_NAME=""
TELEGRAM_OWNER_ID=""
ANTHROPIC_MODEL=""
ANTHROPIC_API_KEY=""
PLACEHOLDERS=""
INSTANCE_SUFFIX=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-suffix)
      [ -z "${2:-}" ] && { echo "Error: --instance-suffix requires a value" >&2; usage; }
      INSTANCE_SUFFIX="$2"
      shift 2
      ;;
    --telegram-bot-name)
      [ -z "${2:-}" ] && { echo "Error: --telegram-bot-name requires a value" >&2; usage; }
      TELEGRAM_BOT_NAME="$2"
      shift 2
      ;;
    --telegram-owner-id)
      [ -z "${2:-}" ] && { echo "Error: --telegram-owner-id requires a value" >&2; usage; }
      TELEGRAM_OWNER_ID="$2"
      shift 2
      ;;
    --anthropic-model)
      [ -z "${2:-}" ] && { echo "Error: --anthropic-model requires a value" >&2; usage; }
      ANTHROPIC_MODEL="$2"
      shift 2
      ;;
    --anthropic-api-key)
      [ -z "${2:-}" ] && { echo "Error: --anthropic-api-key requires a value" >&2; usage; }
      ANTHROPIC_API_KEY="$2"
      shift 2
      ;;
    --placeholder-map)
      [ -z "${2:-}" ] && { echo "Error: --placeholder-map requires a file path" >&2; usage; }
      [ -f "$2" ] || { echo "Error: placeholder map file not found: $2" >&2; exit 1; }
      while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and blank lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        # Strip inline comments
        line="${line%%#*}"
        # Trim whitespace
        line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        [[ "$line" == *=* ]] || { echo "Error: invalid line in placeholder map (expected KEY=VALUE): $line" >&2; exit 1; }
        if [ -n "$PLACEHOLDERS" ]; then
          PLACEHOLDERS="${PLACEHOLDERS}|||${line}"
        else
          PLACEHOLDERS="$line"
        fi
      done < "$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

[ -z "$TELEGRAM_BOT_NAME" ] && { echo "Error: --telegram-bot-name is required" >&2; usage; }
[ -z "$TELEGRAM_OWNER_ID" ] && { echo "Error: --telegram-owner-id is required" >&2; usage; }
[ -z "$ANTHROPIC_MODEL" ] && { echo "Error: --anthropic-model is required (e.g. anthropic/claude-opus-4-6)" >&2; usage; }
[ -z "$ANTHROPIC_API_KEY" ] && { echo "Error: --anthropic-api-key is required" >&2; usage; }
[ -z "$PLACEHOLDERS" ] && { echo "Error: --placeholder-map is required (copy placeholders.txt.example)" >&2; usage; }

# ====== RESOLVE TELEGRAM BOT TOKEN ======
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
TELEGRAM_BOTS_FILE="$SCRIPT_DIR/../telegram_bots.json"
[ -f "$TELEGRAM_BOTS_FILE" ] \
  || { echo "Error: telegram_bots.json not found at $TELEGRAM_BOTS_FILE" >&2; exit 1; }

TELEGRAM_BOT_TOKEN=$(jq -r --arg name "$TELEGRAM_BOT_NAME" '.[$name] // empty' "$TELEGRAM_BOTS_FILE")
[ -n "$TELEGRAM_BOT_TOKEN" ] \
  || { echo "Error: bot name '$TELEGRAM_BOT_NAME' not found in $TELEGRAM_BOTS_FILE" >&2
       echo "  Available bots: $(jq -r 'keys | join(", ")' "$TELEGRAM_BOTS_FILE")" >&2
       exit 1; }

# ====== CONFIGURATION (override via env vars) ======
REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${CRUX_INSTANCE_TYPE:-t3.xlarge}"
AMI_ID="${CRUX_AMI_ID:-}"
KEY_NAME="${CRUX_KEY_NAME:-crux-in-a-box}"
SG_NAME="${CRUX_SG_NAME:-crux-in-a-box-sg}"
INSTANCE_NAME="${CRUX_INSTANCE_NAME:-crux-in-a-box}"
if [ -n "$INSTANCE_SUFFIX" ]; then
  INSTANCE_NAME="${INSTANCE_NAME}-${INSTANCE_SUFFIX}"
fi
IAM_ROLE_NAME="${CRUX_IAM_ROLE:-crux-in-a-box-role}"
INSTANCE_PROFILE_NAME="${CRUX_INSTANCE_PROFILE:-crux-in-a-box-profile}"
SSH_USER="ubuntu"
DISK_SIZE_GB="${CRUX_DISK_SIZE_GB:-80}"
VNC_PORT=5901

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" &>/dev/null || die "'$1' is required but not found."
}

# ====== PREFLIGHT ======
require_cmd aws
require_cmd jq
require_cmd ssh
require_cmd scp

aws sts get-caller-identity &>/dev/null \
  || die "AWS CLI is not authenticated. Run 'aws configure' first."

info "Region: $REGION | Instance type: $INSTANCE_TYPE | Disk: ${DISK_SIZE_GB}GB"

# ====== 1. KEY PAIR ======
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"

if aws ec2 describe-key-pairs --key-names "$KEY_NAME" \
     --region "$REGION" &>/dev/null; then
  ok "Key pair '$KEY_NAME' already exists"
else
  info "Creating key pair '$KEY_NAME'..."
  aws ec2 create-key-pair \
    --key-name "$KEY_NAME" --region "$REGION" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  ok "Key pair created → $KEY_FILE"
fi

[ -f "$KEY_FILE" ] \
  || die "Key file $KEY_FILE not found. Delete the AWS key pair and re-run."

# ====== 2. SECURITY GROUP ======
SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" \
  --region "$REGION" \
  --query 'SecurityGroups[0].GroupId' \
  --output text 2>/dev/null || true)

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  info "Creating security group '$SG_NAME'..."
  SG_ID=$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "CRUX in a box - SSH + VNC" \
    --region "$REGION" \
    --query 'GroupId' --output text)

  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" --region "$REGION" \
    --protocol tcp --port 22 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" --region "$REGION" \
    --protocol tcp --port "$VNC_PORT" --cidr 0.0.0.0/0

  ok "Security group created: $SG_ID"
else
  ok "Security group '$SG_NAME' already exists: $SG_ID"
fi

# ====== 2b. IAM ROLE + INSTANCE PROFILE (PowerUserAccess) ======
# Gives the instance permanent AWS credentials via IMDS — no keys to expire.
ROLE_EXISTS=$(aws iam get-role --role-name "$IAM_ROLE_NAME" \
  --query 'Role.RoleName' --output text 2>/dev/null || true)

if [ "$ROLE_EXISTS" = "$IAM_ROLE_NAME" ]; then
  ok "IAM role '$IAM_ROLE_NAME' already exists"
else
  info "Creating IAM role '$IAM_ROLE_NAME'..."
  aws iam create-role \
    --role-name "$IAM_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' \
    --description "CRUX-in-a-box EC2 role - PowerUserAccess" \
    --query 'Role.Arn' --output text >/dev/null
  ok "IAM role created: $IAM_ROLE_NAME"
fi

# Ensure PowerUserAccess is attached (idempotent)
aws iam attach-role-policy \
  --role-name "$IAM_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess" 2>/dev/null || true
ok "PowerUserAccess policy attached to $IAM_ROLE_NAME"

# Instance profile
PROFILE_EXISTS=$(aws iam get-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME" \
  --query 'InstanceProfile.InstanceProfileName' --output text 2>/dev/null || true)

if [ "$PROFILE_EXISTS" = "$INSTANCE_PROFILE_NAME" ]; then
  ok "Instance profile '$INSTANCE_PROFILE_NAME' already exists"
else
  info "Creating instance profile '$INSTANCE_PROFILE_NAME'..."
  aws iam create-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --role-name "$IAM_ROLE_NAME"
  # IAM is eventually consistent — the profile needs a moment before EC2 can use it
  info "Waiting for instance profile to propagate..."
  sleep 10
  ok "Instance profile created: $INSTANCE_PROFILE_NAME"
fi

# ====== 3. RESOLVE AMI ======
if [ -z "$AMI_ID" ]; then
  info "Resolving latest Ubuntu 22.04 AMI..."
  AMI_ID=$(aws ec2 describe-images \
    --region "$REGION" --owners 099720109477 \
    --filters \
      "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
      "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)
fi

[ "$AMI_ID" = "None" ] || [ -z "$AMI_ID" ] \
  && die "Could not resolve an Ubuntu 22.04 AMI in $REGION"
ok "AMI: $AMI_ID"

# ====== 4. LAUNCH INSTANCE ======
EXISTING_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters \
    "Name=tag:Name,Values=$INSTANCE_NAME" \
    "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text 2>/dev/null || true)

if [ "$EXISTING_ID" != "None" ] && [ -n "$EXISTING_ID" ]; then
  warn "WARNING: An EC2 instance '$INSTANCE_NAME' is already running ($EXISTING_ID)."
  warn "Continuing will re-provision this existing instance (SSH + bootstrap)."
  warn "If you want a separate instance instead, re-run with --instance-suffix <SUFFIX>."
  printf "\033[1;33m   Continue with existing instance %s? [y/N]: \033[0m" "$EXISTING_ID"
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    INSTANCE_ID="$EXISTING_ID"
    ok "Re-using existing instance $INSTANCE_ID"
  else
    die "Aborted. To launch a parallel instance, re-run with --instance-suffix <SUFFIX>."
  fi
else
  info "Launching EC2 instance..."
  INSTANCE_ID=$(aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$INSTANCE_PROFILE_NAME" \
    --block-device-mappings \
      "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_SIZE_GB},\"VolumeType\":\"gp3\"}}]" \
    --tag-specifications \
      "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  ok "Instance launched: $INSTANCE_ID"
fi

# ====== 5. WAIT FOR INSTANCE ======
info "Waiting for instance to enter 'running' state..."
aws ec2 wait instance-running \
  --instance-ids "$INSTANCE_ID" --region "$REGION"

PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

[ "$PUBLIC_IP" = "None" ] || [ -z "$PUBLIC_IP" ] \
  && die "Instance has no public IP. Check your VPC/subnet settings."
ok "Instance running at $PUBLIC_IP"

# ====== 6. WAIT FOR SSH ======
info "Waiting for SSH to become available (this can take 1-2 min)..."
MAX_RETRIES=30
for i in $(seq 1 $MAX_RETRIES); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
       -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" "echo ok" &>/dev/null; then
    break
  fi
  [ "$i" -eq "$MAX_RETRIES" ] \
    && die "SSH did not become available after $MAX_RETRIES attempts."
  sleep 10
done
ok "SSH is up"

# ====== 7. COPY FILES ======
info "Copying linux/ directory to instance..."
scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
  "$SCRIPT_DIR" "${SSH_USER}@${PUBLIC_IP}:~/crux-in-a-box-linux"
ok "Linux files copied"

HARNESS_DIR="$SCRIPT_DIR/../next-run-harness"
if [ -d "$HARNESS_DIR" ]; then
  info "Copying next-run-harness/ to instance..."
  scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
    "$HARNESS_DIR" "${SSH_USER}@${PUBLIC_IP}:~/crux-in-a-box-harness"
  ok "Harness files copied"
else
  warn "next-run-harness/ not found at $HARNESS_DIR — workspace setup will be skipped"
fi

# ====== 8. RUN REMOTE BOOTSTRAP ======
info "Running remote bootstrap (start.sh) — this will take several minutes..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "chmod +x ~/crux-in-a-box-linux/src/start.sh \
   && sudo TELEGRAM_BOT_TOKEN='${TELEGRAM_BOT_TOKEN}' \
          TELEGRAM_OWNER_ID='${TELEGRAM_OWNER_ID}' \
          ANTHROPIC_MODEL='${ANTHROPIC_MODEL}' \
          ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}' \
          PLACEHOLDERS='${PLACEHOLDERS}' \
          bash ~/crux-in-a-box-linux/src/start.sh"
ok "Remote bootstrap complete"

# ====== 9. CONNECTION INFO ======
cat <<EOF

============================================
  CRUX-in-a-box Linux instance is ready!
============================================

  Instance ID : $INSTANCE_ID
  Public IP   : $PUBLIC_IP
  SSH         : ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP}
  VNC         : connect to ${PUBLIC_IP}:${VNC_PORT}
               (password was set during bootstrap)

  To check status:
    ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP} 'bash ~/crux-in-a-box-linux/status.sh'

  To stop the instance:
    aws ec2 stop-instances --instance-ids $INSTANCE_ID --region $REGION

  To terminate the instance:
    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
============================================
EOF

cat <<'EOF'

  ── Telegram setup ──────────────────────────
  DM your bot on Telegram, then approve the
  pairing from the instance:

    openclaw pairing list telegram
    openclaw pairing approve telegram <CODE>
  ─────────────────────────────────────────────
EOF

# ====== 10. RUN STATUS CHECK ======
info "Running status check..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "bash ~/crux-in-a-box-linux/status.sh"
