#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# build-ami.sh — Build a reusable CRUX base AMI
# ==========================================================================
# Run this on your LOCAL machine (macOS / Linux) when you want to bake a new
# base AMI. Takes ~15 minutes. Run once per openclaw version bump or
# dependency change.
#
# What it does:
#   1. Launches a fresh Ubuntu 22.04 t3.xlarge builder instance
#   2. Copies linux/ to the instance
#   3. Runs src/ami-install.sh (static package + tool install, no secrets)
#   4. Stops the instance
#   5. Creates an AMI: CRUX-base-YYYYMMDD
#   6. Waits for the AMI to be available
#   7. Terminates the builder instance
#   8. Prints the AMI ID
#
# Prerequisites:
#   - AWS CLI v2 authenticated
#   - ssh / scp available
#   - ~/.ssh/crux-in-a-box.pem exists (or set CRUX_KEY_NAME / CRUX_KEY_FILE)
#
# Usage:
#   ./build-ami.sh
#   AWS_REGION=us-west-2 ./build-ami.sh
# ==========================================================================

# ====== CONFIG ======
REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${CRUX_AMI_BUILD_INSTANCE_TYPE:-t3.xlarge}"
KEY_NAME="${CRUX_KEY_NAME:-crux-in-a-box}"
KEY_FILE="${CRUX_KEY_FILE:-$HOME/.ssh/${KEY_NAME}.pem}"
SG_NAME="${CRUX_SG_NAME:-crux-in-a-box-sg}"
DISK_SIZE_GB="${CRUX_DISK_SIZE_GB:-80}"
SSH_USER="ubuntu"
AMI_NAME="CRUX-base-$(date -u +%Y%m%d)"

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }
require_cmd aws
require_cmd jq
require_cmd ssh
require_cmd scp

aws sts get-caller-identity &>/dev/null \
  || die "AWS CLI is not authenticated."

info "Region: $REGION | Instance type: $INSTANCE_TYPE | AMI name: $AMI_NAME"

# ====== 1. KEY PAIR ======
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" &>/dev/null; then
  ok "Key pair '$KEY_NAME' already exists"
else
  info "Creating key pair '$KEY_NAME'..."
  aws ec2 create-key-pair \
    --key-name "$KEY_NAME" --region "$REGION" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  ok "Key pair created → $KEY_FILE"
fi
[ -f "$KEY_FILE" ] || die "Key file $KEY_FILE not found. Delete the AWS key pair and re-run."

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
  ok "Security group created: $SG_ID"
else
  ok "Security group '$SG_NAME' already exists: $SG_ID"
fi

# ====== 3. RESOLVE LATEST UBUNTU 22.04 AMI (always build from stock Ubuntu) ======
info "Resolving latest Ubuntu 22.04 AMI..."
BASE_AMI=$(aws ec2 describe-images \
  --region "$REGION" --owners 099720109477 \
  --filters \
    "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text)
[ -z "$BASE_AMI" ] || [ "$BASE_AMI" = "None" ] && die "Could not resolve Ubuntu 22.04 AMI"
ok "Base AMI: $BASE_AMI"

# ====== 4. CHECK FOR EXISTING AMI WITH SAME NAME ======
EXISTING_AMI=$(aws ec2 describe-images \
  --region "$REGION" --owners self \
  --filters "Name=name,Values=${AMI_NAME}" \
  --query 'Images[0].ImageId' --output text 2>/dev/null || true)

if [ -n "$EXISTING_AMI" ] && [ "$EXISTING_AMI" != "None" ]; then
  warn "AMI '$AMI_NAME' already exists: $EXISTING_AMI"
  printf "\033[1;33m   Overwrite? (A new AMI with -v2 suffix will be created) [y/N]: \033[0m"
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    AMI_NAME="${AMI_NAME}-v2"
    info "Using name: $AMI_NAME"
  else
    die "Aborted. Delete the existing AMI first or wait until tomorrow."
  fi
fi

# ====== 5. LAUNCH BUILDER INSTANCE ======
info "Launching builder instance..."
BUILDER_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$BASE_AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --block-device-mappings \
    "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_SIZE_GB},\"VolumeType\":\"gp3\"}}]" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=crux-ami-builder},{Key=Purpose,Value=AMI-build}]" \
  --query 'Instances[0].InstanceId' --output text)
ok "Builder instance: $BUILDER_ID"

# Ensure we terminate the builder on exit (success or failure)
cleanup() {
  local exit_code=$?
  if [ -n "${BUILDER_ID:-}" ]; then
    warn "Terminating builder instance $BUILDER_ID..."
    aws ec2 terminate-instances --instance-ids "$BUILDER_ID" \
      --region "$REGION" --output text >/dev/null 2>&1 || true
    ok "Builder instance terminated"
  fi
  exit $exit_code
}
# Register cleanup only for failures — on success we terminate after AMI creation
trap 'cleanup' ERR INT TERM

# ====== 6. WAIT FOR INSTANCE ======
info "Waiting for builder to enter 'running' state..."
aws ec2 wait instance-running --instance-ids "$BUILDER_ID" --region "$REGION"

BUILDER_IP=$(aws ec2 describe-instances \
  --instance-ids "$BUILDER_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)
[ -z "$BUILDER_IP" ] || [ "$BUILDER_IP" = "None" ] && die "Builder has no public IP"
ok "Builder running at $BUILDER_IP"

# ====== 7. WAIT FOR SSH ======
info "Waiting for SSH..."
for i in $(seq 1 30); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
       -i "$KEY_FILE" "${SSH_USER}@${BUILDER_IP}" "echo ok" &>/dev/null; then
    break
  fi
  [ "$i" -eq 30 ] && die "SSH did not become available."
  sleep 10
done
ok "SSH is up"

# ====== 8. COPY linux/ TO BUILDER ======
info "Copying linux/ to builder..."
scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
  "$SCRIPT_DIR" "${SSH_USER}@${BUILDER_IP}:~/crux-in-a-box-linux"
ok "Files copied"

# ====== 9. RUN ami-install.sh ======
info "Running ami-install.sh (this takes ~10-15 min)..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${BUILDER_IP}" \
  "chmod +x ~/crux-in-a-box-linux/src/ami-install.sh \
   && sudo bash ~/crux-in-a-box-linux/src/ami-install.sh 2>&1 | tee /tmp/ami-install.log"
ok "ami-install.sh complete"

# ====== 10. STOP INSTANCE (required before create-image) ======
info "Stopping builder instance to prepare for AMI creation..."
aws ec2 stop-instances --instance-ids "$BUILDER_ID" --region "$REGION" --output text >/dev/null
aws ec2 wait instance-stopped --instance-ids "$BUILDER_ID" --region "$REGION"
ok "Builder stopped"

# ====== 11. CREATE AMI ======
info "Creating AMI '$AMI_NAME'..."
NEW_AMI_ID=$(aws ec2 create-image \
  --instance-id "$BUILDER_ID" \
  --name "$AMI_NAME" \
  --description "CRUX base: xfce4, tigervnc, openclaw, telemetry, gh, awscli, gogcli. Built $(date -u +%Y-%m-%d)." \
  --region "$REGION" \
  --tag-specifications \
    "ResourceType=image,Tags=[{Key=Name,Value=${AMI_NAME}},{Key=Purpose,Value=crux-base}]" \
    "ResourceType=snapshot,Tags=[{Key=Name,Value=${AMI_NAME}-snapshot}]" \
  --query 'ImageId' --output text)
ok "AMI creation started: $NEW_AMI_ID"

# ====== 12. WAIT FOR AMI ======
info "Waiting for AMI to become available (this takes a few minutes)..."
aws ec2 wait image-available --image-ids "$NEW_AMI_ID" --region "$REGION"
ok "AMI is available: $NEW_AMI_ID"

# ====== 13. TERMINATE BUILDER ======
# Deregister the error trap — we handle termination explicitly now
trap - ERR INT TERM
info "Terminating builder instance..."
aws ec2 terminate-instances --instance-ids "$BUILDER_ID" --region "$REGION" --output text >/dev/null
ok "Builder terminated"

# ====== DONE ======
cat <<EOF

============================================
  CRUX base AMI built successfully!
============================================

  AMI ID   : $NEW_AMI_ID
  AMI Name : $AMI_NAME
  Region   : $REGION

  To use this AMI, add to your placeholders file:
    CRUX_AMI_ID=$NEW_AMI_ID

  setup-device.sh will then skip the 10-15 min install
  and run configure.sh only (~60-90s).

  To deregister this AMI when it's superseded:
    aws ec2 deregister-image --image-id $NEW_AMI_ID --region $REGION
    # Also delete the associated snapshot:
    aws ec2 describe-images --image-ids $NEW_AMI_ID --region $REGION \\
      --query 'Images[0].BlockDeviceMappings[0].Ebs.SnapshotId' --output text
    # then: aws ec2 delete-snapshot --snapshot-id <id> --region $REGION
============================================
EOF
