#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# build-ami.sh  –  run this on your LOCAL machine (like create-new-crux-box.sh)
# ==========================================================================
# Bakes the CRUX-in-a-box base AMI: launches a temporary EC2 instance from
# raw Ubuntu 22.04, runs install.sh on it (software install only — NO secrets
# are ever passed or written), creates an AMI from the instance, waits for it
# to become available, and terminates the builder.
#
# Pass the resulting AMI ID to create-new-crux-box.sh with --ami to skip the
# 10-15 min software install on every run launch.
#
# Prerequisites on the invoking machine:
#   - AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
#   - ssh / scp available
#
# Usage:
#   ./build-ami.sh [--ami-name <NAME>]
#
# Optional:
#   --ami-name <NAME>   Name for the baked AMI
#                         (default: crux-in-a-box-base-YYYYMMDD).
# ==========================================================================

# ====== FIXED CONFIGURATION ======
REGION="us-east-1"
INSTANCE_TYPE="t3.2xlarge"
KEY_NAME="crux-in-a-box"
SG_NAME="crux-in-a-box-sg"
BUILDER_NAME="crux-ami-builder"
SSH_USER="ubuntu"
DISK_SIZE_GB=80
VNC_PORT=5901

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }

usage() {
  cat <<USAGE
Usage: $0 [--ami-name <NAME>]

Bakes the CRUX-in-a-box base AMI (software only, no secrets). Pass the
resulting AMI ID to create-new-crux-box.sh with --ami.

Optional:
  --ami-name <NAME>   Name for the AMI (default: crux-in-a-box-base-YYYYMMDD)
USAGE
  exit 1
}

# ====== PARSE ARGS ======
AMI_NAME="crux-in-a-box-base-$(date -u +%Y%m%d)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ami-name)
      [ -z "${2:-}" ] && { echo "Error: --ami-name requires a value" >&2; usage; }
      AMI_NAME="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

# ====== PREFLIGHT ======
require_cmd aws
require_cmd ssh
require_cmd scp

aws sts get-caller-identity &>/dev/null \
  || die "AWS CLI is not authenticated. Run 'aws configure' first."

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

info "Region: $REGION | Instance type: $INSTANCE_TYPE | AMI name: $AMI_NAME"

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

# ====== 3. RESOLVE BASE AMI (raw Ubuntu 22.04) ======
info "Resolving latest Ubuntu 22.04 AMI..."
BASE_AMI_ID=$(aws ec2 describe-images \
  --region "$REGION" --owners 099720109477 \
  --filters \
    "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text)
[ "$BASE_AMI_ID" = "None" ] || [ -z "$BASE_AMI_ID" ] \
  && die "Could not resolve an Ubuntu 22.04 AMI in $REGION"
ok "Base AMI: $BASE_AMI_ID"

# ====== 4. LAUNCH BUILDER INSTANCE ======
info "Launching temporary builder instance ($BUILDER_NAME)..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$BASE_AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --block-device-mappings \
    "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_SIZE_GB},\"VolumeType\":\"gp3\"}}]" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=$BUILDER_NAME}]" \
  --query 'Instances[0].InstanceId' --output text)
ok "Builder instance launched: $INSTANCE_ID"

info "Waiting for instance to enter 'running' state..."
aws ec2 wait instance-running \
  --instance-ids "$INSTANCE_ID" --region "$REGION"

PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

[ "$PUBLIC_IP" = "None" ] || [ -z "$PUBLIC_IP" ] \
  && die "Builder instance has no public IP. Check your VPC/subnet settings."
ok "Builder running at $PUBLIC_IP"

# ====== 5. WAIT FOR SSH ======
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

# ====== 6. COPY INSTALL FILES (no secrets) ======
# Only src/ is copied — the placeholders files and gog bundle (secrets) are
# per-run inputs consumed by configure.sh and must never enter the AMI.
info "Copying linux/src/ to the builder..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "mkdir -p ~/crux-in-a-box-linux"
scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
  "$SCRIPT_DIR/src" "${SSH_USER}@${PUBLIC_IP}:~/crux-in-a-box-linux/src"
ok "Install files copied"

# ====== 7. RUN INSTALL (bake phase — no env vars passed) ======
info "Running install.sh on the builder — this will take several minutes..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "chmod +x ~/crux-in-a-box-linux/src/install.sh \
     && sudo bash ~/crux-in-a-box-linux/src/install.sh"
ok "Software install complete"

# On install failure, set -e aborts here and the builder instance is left
# running for inspection (ssh -i $KEY_FILE ubuntu@$PUBLIC_IP).

# ====== 8. CREATE AMI ======
info "Creating AMI '$AMI_NAME' from $INSTANCE_ID..."
AMI_ID=$(aws ec2 create-image \
  --region "$REGION" \
  --instance-id "$INSTANCE_ID" \
  --name "$AMI_NAME" \
  --description "CRUX-in-a-box base image (install.sh baked, no secrets)" \
  --no-reboot \
  --query 'ImageId' --output text)
ok "AMI creation started: $AMI_ID"

# ====== 9. WAIT FOR AMI ======
info "Waiting for AMI to become available (poll every 30s)..."
while true; do
  AMI_STATE=$(aws ec2 describe-images \
    --region "$REGION" --image-ids "$AMI_ID" \
    --query 'Images[0].State' --output text 2>/dev/null || echo "unknown")
  case "$AMI_STATE" in
    available) echo ""; ok "AMI is available"; break ;;
    failed|error) echo ""; die "AMI bake failed (state: $AMI_STATE). Builder $INSTANCE_ID left running for inspection." ;;
    *) printf "." ; sleep 30 ;;
  esac
done

# ====== 10. TERMINATE BUILDER ======
info "Terminating builder instance $INSTANCE_ID..."
aws ec2 terminate-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" >/dev/null
ok "Builder terminated"

# ====== 11. DONE ======
cat <<EOF

============================================
  CRUX-in-a-box base AMI is ready!
============================================

  AMI ID   : $AMI_ID
  AMI name : $AMI_NAME
  Region   : $REGION

  Launch a run from it:
    ./create-new-crux-box.sh --ami $AMI_ID placeholders.txt
============================================
EOF
