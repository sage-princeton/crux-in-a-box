#!/usr/bin/env bash
# provision-box.sh — launch a bare EC2 box and provision the Codex harness on it.
#
# Run on your LOCAL machine. One command: AWS launch (Ubuntu 22.04, SSH-only) →
# rsync the harness → remote setup-codex.sh. Unlike linux/setup-device.sh this
# does NOT install OpenClaw / VNC / gog / Telegram — it is the Codex-loop path.
#
#   ./provision-box.sh <placeholders-codex-*.txt> <name-suffix>
#   ./provision-box.sh ../linux/placeholders-codex-myrun.txt myrun
#
# Prereqs on THIS machine: aws CLI (authenticated — `aws sts get-caller-identity`),
# jq, ssh, scp, rsync. Override defaults via env: AWS_REGION, CODEX_INSTANCE_TYPE
# (m5.2xlarge), CODEX_DISK_GB (100), CODEX_KEY_NAME, CODEX_SG_NAME.
#
# It does NOT start the run — that's launch.sh on the box, so the clock starts
# when you say so. Reruns with the same suffix re-provision the same instance.
set -euo pipefail

info(){ printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

CONFIG="${1:-}"; SUFFIX="${2:-}"
[ -n "$CONFIG" ] && [ -n "$SUFFIX" ] || die "usage: provision-box.sh <placeholders-codex-*.txt> <name-suffix>"
[ -f "$CONFIG" ] || die "config file not found: $CONFIG"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for c in aws jq ssh scp rsync; do command -v "$c" >/dev/null || die "'$c' not found on PATH"; done
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "AWS CLI not authenticated (token expired?). Refresh creds, then rerun. 'aws sts get-caller-identity' must succeed."

# Validate the config's own required keys the same way setup-codex.sh will,
# so a bad config fails here (seconds) rather than after a box is billing.
get_cfg(){ awk -F= -v k="$1" '
  /^[[:space:]]*#/ || /^[[:space:]]*$/ {next}
  $1==k {v=substr($0,index($0,"=")+1); sub(/[[:space:]]+#.*$/,"",v);
         gsub(/^[[:space:]]+|[[:space:]]+$/,"",v); print v; exit}' "$CONFIG"; }
MISSING=""
for k in OPENAI_API_KEY RESEARCH_QUESTION RESEARCH_CONTEXT API_BUDGET_USD DEADLINE_HOURS; do
  [ -n "$(get_cfg "$k")" ] || MISSING="$MISSING $k"
done
[ -z "$MISSING" ] || die "config missing required keys:$MISSING"

REGION="${AWS_REGION:-us-east-1}"
# m5.2xlarge (8 vCPU / 32GB, non-burstable). Burstable 16GB-class instances are
# too small: agents run memory-heavy local experiments, and a large in-memory
# process can OOM the box and take sshd down with it. setup-codex.sh also adds
# a swap backstop.
INSTANCE_TYPE="${CODEX_INSTANCE_TYPE:-m5.2xlarge}"
DISK_GB="${CODEX_DISK_GB:-100}"
SG_NAME="${CODEX_SG_NAME:-codex-run-sg}"
NAME="codex-${SUFFIX}"
SSH_USER="ubuntu"
info "region=$REGION type=$INSTANCE_TYPE disk=${DISK_GB}G name=$NAME"

# ── Key pair: reuse the repo's existing pem if present locally, else make a codex one ──
KEY_NAME="${CODEX_KEY_NAME:-}"
if [ -z "$KEY_NAME" ]; then
  if aws ec2 describe-key-pairs --key-names crux-in-a-box --region "$REGION" >/dev/null 2>&1 \
     && [ -f "$HOME/.ssh/crux-in-a-box.pem" ]; then
    KEY_NAME="crux-in-a-box"
  else
    KEY_NAME="codex-run"
  fi
fi
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" >/dev/null 2>&1; then
  [ -f "$KEY_FILE" ] || die "AWS key pair '$KEY_NAME' exists but $KEY_FILE is missing locally. Set CODEX_KEY_NAME to a fresh name to create one."
  ok "key pair '$KEY_NAME' (reusing $KEY_FILE)"
else
  info "creating key pair '$KEY_NAME'..."
  aws ec2 create-key-pair --key-name "$KEY_NAME" --region "$REGION" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"; ok "key pair created → $KEY_FILE"
fi

# ── Security group: SSH-only (no VNC — the Codex box is headless) ──
SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
  --region "$REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  info "creating security group '$SG_NAME' (inbound SSH only)..."
  SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "Codex run box - SSH only" --region "$REGION" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --region "$REGION" \
    --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null
  ok "security group $SG_ID"
else
  ok "security group '$SG_NAME' ($SG_ID)"
fi

# ── AMI: latest Ubuntu 22.04 amd64 ──
AMI_ID="${CODEX_AMI_ID:-$(aws ec2 describe-images --region "$REGION" --owners 099720109477 \
  --filters 'Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*' \
            'Name=state,Values=available' \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)}"
[ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ] || die "could not resolve an Ubuntu 22.04 AMI in $REGION"
ok "AMI $AMI_ID"

# ── Launch (or reuse an existing same-named instance) ──
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)
if [ "$INSTANCE_ID" != "None" ] && [ -n "$INSTANCE_ID" ]; then
  warn "instance '$NAME' already running ($INSTANCE_ID) — re-provisioning it"
else
  info "launching instance..."
  INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_GB},\"VolumeType\":\"gp3\"}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=codex-crux}]" \
    --query 'Instances[0].InstanceId' --output text)
  ok "launched $INSTANCE_ID"
fi

info "waiting for 'running'..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
[ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "None" ] || die "instance has no public IP (VPC/subnet?)"
ok "running at $PUBLIC_IP"

info "waiting for SSH..."
for i in $(seq 1 30); do
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
    -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" 'echo ok' >/dev/null 2>&1 && break
  [ "$i" -eq 30 ] && die "SSH never came up"; sleep 10
done
ok "SSH up"

# ── Ship the harness (template bundled inside so no sibling dir is needed) ──
TEMPLATE="$REPO_ROOT/next-run-harness/workspace/templates/paper_template.zip"
if [ -f "$TEMPLATE" ]; then
  mkdir -p "$SCRIPT_DIR/workspace/templates"
  cp -f "$TEMPLATE" "$SCRIPT_DIR/workspace/templates/paper_template.zip"
  ok "paper template bundled"
else
  warn "paper template not found at $TEMPLATE — put one in workspace/templates/ before launch"
fi

info "rsyncing harness to ~/codex-run-harness ..."
rsync -az --delete \
  --exclude '.git' --exclude 'loop/state' --exclude 'loop/logs' \
  -e "ssh -o StrictHostKeyChecking=no -i $KEY_FILE" \
  "$SCRIPT_DIR/" "${SSH_USER}@${PUBLIC_IP}:~/codex-run-harness/"
scp -o StrictHostKeyChecking=no -i "$KEY_FILE" \
  "$CONFIG" "${SSH_USER}@${PUBLIC_IP}:~/codex-run-harness/placeholders-codex.txt"
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  'chmod 600 ~/codex-run-harness/placeholders-codex.txt'
ok "harness + config on box"

# ── Remote provision (installs codex/tectonic/etc., resolves placeholders) ──
info "running setup-codex.sh on the box (several minutes)..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  'cd ~/codex-run-harness && chmod +x setup-codex.sh && sudo ./setup-codex.sh placeholders-codex.txt'
ok "provisioned"

SSH_ALIAS="codex-${SUFFIX}"
cat <<EOF

============================================================
  Codex box ready — NOT yet launched (clock starts on launch)
============================================================
  Instance : $INSTANCE_ID   IP: $PUBLIC_IP
  SSH      : ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP}

  Add to ~/.ssh/config for convenience:
    Host $SSH_ALIAS
      HostName $PUBLIC_IP
      User ${SSH_USER}
      IdentityFile $KEY_FILE

  Sanity-check, then LAUNCH (starts the deadline clock):
    ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP} 'less ~/crux-codex/workspace/AGENTS.md'   # question reads right, no {{...}}
    ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP} '~/crux-codex/loop/launch.sh'

  Watch:
    ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP} -t 'tmux attach -t crux-codex-loop'

  Stop / terminate the box:
    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
============================================================
EOF
