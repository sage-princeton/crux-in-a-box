#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# snapshot-instance.sh  –  run this on your LOCAL machine
# ==========================================================================
# Takes an EBS snapshot of a CRUX-in-a-box instance's root volume — the
# instance-state preservation mechanism (replaces the retired S3 tarball
# backup script).
#
# The --instance-suffix selects WHICH instance is snapshotted (matching
# create-new-crux-box.sh): crux-in-a-box[-<SUFFIX>]. The script finds that
# instance by its Name tag, snapshots its root volume, prints the snapshot ID
# and AWS console URL, and polls until the snapshot completes.
#
# Ctrl-C is safe: the snapshot continues server-side in AWS — the handler
# re-prints the ID + console URL so you can monitor it there.
#
# Prerequisites on the invoking machine:
#   - AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
#
# Usage:
#   ./snapshot-instance.sh [--instance-suffix <SUFFIX>]
# ==========================================================================

# ====== FIXED CONFIGURATION ======
REGION="us-east-1"

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }

usage() {
  cat <<USAGE
Usage: $0 [--instance-suffix <SUFFIX>]

Optional:
  --instance-suffix <SUFFIX>   Target instance crux-in-a-box-<SUFFIX>
                                 (default: crux-in-a-box). Matches the
                                 --instance-suffix used at launch time.
USAGE
  exit 1
}

# ====== PARSE ARGS ======
INSTANCE_SUFFIX=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-suffix)
      [ -z "${2:-}" ] && { echo "Error: --instance-suffix requires a value" >&2; usage; }
      INSTANCE_SUFFIX="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

# Derive the instance name exactly like create-new-crux-box.sh.
INSTANCE_NAME="crux-in-a-box"
if [ -n "$INSTANCE_SUFFIX" ]; then
  INSTANCE_NAME="${INSTANCE_NAME}-${INSTANCE_SUFFIX}"
fi

# ====== PREFLIGHT ======
require_cmd aws

aws sts get-caller-identity --region "$REGION" &>/dev/null \
  || die "AWS CLI is not authenticated. Run 'aws configure' first."

# ====== 1. LOCATE TARGET INSTANCE ======
info "Locating instance '$INSTANCE_NAME' in $REGION..."
INSTANCE_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters \
    "Name=tag:Name,Values=$INSTANCE_NAME" \
    "Name=instance-state-name,Values=running,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text 2>/dev/null || true)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  die "No running or stopped instance named '$INSTANCE_NAME' found in $REGION."
fi
ok "Found instance: $INSTANCE_ID"

# ====== 2. GET ROOT VOLUME ======
VOLUME_ID=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' \
  --output text)

if [ "$VOLUME_ID" = "None" ] || [ -z "$VOLUME_ID" ]; then
  die "Could not resolve the root volume of $INSTANCE_ID."
fi
ok "Root volume: $VOLUME_ID"

# ====== 3. CREATE SNAPSHOT ======
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT_NAME="${INSTANCE_NAME}-${STAMP}"

info "Creating snapshot of $VOLUME_ID..."
SNAPSHOT_ID=$(aws ec2 create-snapshot \
  --region "$REGION" \
  --volume-id "$VOLUME_ID" \
  --description "crux-in-a-box manual snapshot" \
  --tag-specifications \
    "ResourceType=snapshot,Tags=[{Key=Name,Value=$SNAPSHOT_NAME}]" \
  --query 'SnapshotId' --output text)

CONSOLE_URL="https://${REGION}.console.aws.amazon.com/ec2/home?region=${REGION}#Snapshots:snapshotId=${SNAPSHOT_ID}"

ok "Snapshot started: $SNAPSHOT_ID"
echo "  Console: $CONSOLE_URL"

# ====== 4. POLL TO COMPLETION ======
# Interrupting the poll does NOT cancel the snapshot — it continues in AWS.
on_interrupt() {
  echo ""
  warn "Poll interrupted — the snapshot continues server-side in AWS."
  echo "  Snapshot : $SNAPSHOT_ID"
  echo "  Console  : $CONSOLE_URL"
  exit 0
}
trap on_interrupt INT TERM

info "Waiting for snapshot to complete (poll every 30s; Ctrl-C is safe)..."
while true; do
  STATE=$(aws ec2 describe-snapshots \
    --region "$REGION" --snapshot-ids "$SNAPSHOT_ID" \
    --query 'Snapshots[0].State' --output text 2>/dev/null || echo "unknown")
  PROGRESS=$(aws ec2 describe-snapshots \
    --region "$REGION" --snapshot-ids "$SNAPSHOT_ID" \
    --query 'Snapshots[0].Progress' --output text 2>/dev/null || echo "?")
  case "$STATE" in
    completed)
      ok "Snapshot completed"
      break
      ;;
    error)
      die "Snapshot entered 'error' state. Check the console: $CONSOLE_URL"
      ;;
    *)
      echo "  state=$STATE progress=$PROGRESS"
      sleep 30
      ;;
  esac
done

cat <<EOF

============================================
  Instance snapshot complete
============================================
  Instance : $INSTANCE_NAME ($INSTANCE_ID)
  Volume   : $VOLUME_ID
  Snapshot : $SNAPSHOT_ID
  Console  : $CONSOLE_URL

  To restore: create a volume (or AMI) from the
  snapshot in the AWS console and attach/launch.
============================================
EOF
