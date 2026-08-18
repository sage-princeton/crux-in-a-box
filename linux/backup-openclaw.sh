#!/usr/bin/env bash
set -euo pipefail
# ==========================================================================
# backup-openclaw.sh  –  run this on your LOCAL machine (like setup-device.sh)
# ==========================================================================
# Backs up the ~/.openclaw directory of a CRUX-in-a-box EC2 instance to S3.
#
# The --instance-suffix selects WHICH instance is backed up (matching
# setup-device.sh): crux-in-a-box[-<SUFFIX>]. The script finds that instance
# by its Name tag, SSHes in, tars ~/.openclaw, and uploads the archive to
# S3 from the instance itself (the instance has the IAM role / S3 access via
# IMDS — your laptop does not need AWS S3 permissions for the upload).
#
# Fixed config:
#   Bucket : hal-crux-backups
#   Region : us-east-1
#
# Prerequisites on the invoking machine:
#   - AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
#   - ssh available, and the key at ~/.ssh/crux-in-a-box.pem
#
# Usage:
#   ./backup-openclaw.sh [--instance-suffix <SUFFIX>]
#
# Optional:
#   --instance-suffix <SUFFIX>   Target instance crux-in-a-box-<SUFFIX>
#                                  (default: crux-in-a-box). Also determines
#                                  the S3 prefix and archive filename.
# ==========================================================================

# ====== FIXED CONFIGURATION ======
BUCKET="hal-crux-backups"
REGION="us-east-1"
KEY_NAME="crux-in-a-box"
SSH_USER="ubuntu"
SOURCE_DIR=".openclaw"   # relative to the instance user's home

# ====== HELPERS ======
info()  { printf "\033[1;34m\xe2\x96\xb8 %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m\xe2\x9c\x94 %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m\xe2\x9a\xa0 %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m\xe2\x9c\x98 %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }

usage() {
  cat <<USAGE
Usage: $0 [--instance-suffix <SUFFIX>]

Optional:
  --instance-suffix <SUFFIX>   Target instance crux-in-a-box-<SUFFIX>
                                 (default: crux-in-a-box). Also sets the S3
                                 prefix/filename. Stored in s3://$BUCKET.
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

# Derive the instance name exactly like setup-device.sh.
INSTANCE_NAME="crux-in-a-box"
if [ -n "$INSTANCE_SUFFIX" ]; then
  INSTANCE_NAME="${INSTANCE_NAME}-${INSTANCE_SUFFIX}"
fi
PREFIX="$INSTANCE_NAME"

# ====== PREFLIGHT ======
require_cmd aws
require_cmd ssh

KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
[ -f "$KEY_FILE" ] || die "SSH key not found: $KEY_FILE"

aws sts get-caller-identity --region "$REGION" &>/dev/null \
  || die "AWS CLI is not authenticated. Run 'aws configure' first."

# ====== LOCATE TARGET INSTANCE ======
info "Locating instance '$INSTANCE_NAME' in $REGION..."
INSTANCE_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters \
    "Name=tag:Name,Values=$INSTANCE_NAME" \
    "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text 2>/dev/null || true)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  die "No running instance named '$INSTANCE_NAME' found in $REGION."
fi
ok "Found instance: $INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

if [ "$PUBLIC_IP" = "None" ] || [ -z "$PUBLIC_IP" ]; then
  die "Instance '$INSTANCE_NAME' has no public IP."
fi
ok "Instance IP: $PUBLIC_IP"

# ====== COMPUTE ARCHIVE / S3 TARGET ======
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="openclaw-${INSTANCE_NAME}-${STAMP}.tar.gz"
S3_URI="s3://${BUCKET}/${PREFIX}/${ARCHIVE_NAME}"

# ====== REMOTE BACKUP ======
# Runs on the instance: ensures the bucket exists, tars ~/.openclaw, uploads,
# and verifies. The instance has S3 access via its IAM role (IMDS).
info "Backing up ~/$SOURCE_DIR on $INSTANCE_NAME -> $S3_URI"

REMOTE_SCRIPT=$(cat <<REMOTE
set -euo pipefail
BUCKET="$BUCKET"
REGION="$REGION"
SRC="\$HOME/$SOURCE_DIR"
S3_URI="$S3_URI"
ARCHIVE_NAME="$ARCHIVE_NAME"

command -v aws >/dev/null || { echo "aws CLI not found on instance" >&2; exit 1; }
[ -d "\$SRC" ] || { echo "Source dir not found on instance: \$SRC" >&2; exit 1; }

if ! aws s3api head-bucket --bucket "\$BUCKET" --region "\$REGION" 2>/dev/null; then
  echo "Creating bucket \$BUCKET in \$REGION..."
  aws s3api create-bucket --bucket "\$BUCKET" --region "\$REGION" >/dev/null
  aws s3api wait bucket-exists --bucket "\$BUCKET" --region "\$REGION"
  aws s3api put-public-access-block --bucket "\$BUCKET" --region "\$REGION" --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null 2>&1 || echo "warn: could not set public-access-block"
  aws s3api put-bucket-encryption --bucket "\$BUCKET" --region "\$REGION" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null 2>&1 || echo "warn: could not set default encryption"
fi

TMP="\$(mktemp -d)"
ARCHIVE_PATH="\$TMP/\$ARCHIVE_NAME"
tar czf "\$ARCHIVE_PATH" -C "\$HOME" "$SOURCE_DIR"
echo "archive size: \$(du -h "\$ARCHIVE_PATH" | cut -f1)"
aws s3 cp "\$ARCHIVE_PATH" "\$S3_URI" --region "\$REGION" --sse AES256
aws s3 ls "\$S3_URI" --region "\$REGION" >/dev/null || { echo "Verify failed: object not found at \$S3_URI" >&2; exit 1; }
rm -rf "\$TMP"
echo "REMOTE_BACKUP_OK"
REMOTE
)

REMOTE_OUTPUT=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
  -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" "bash -s" <<<"$REMOTE_SCRIPT") \
  || die "Remote backup failed. Output above."

printf '%s\n' "$REMOTE_OUTPUT" | while IFS= read -r line; do printf '   %s\n' "$line"; done
if ! echo "$REMOTE_OUTPUT" | grep -q "REMOTE_BACKUP_OK"; then
  die "Remote backup did not report success."
fi
ok "Uploaded and verified"

cat <<EOF

============================================
  OpenClaw backup complete
============================================
  Instance : $INSTANCE_NAME ($INSTANCE_ID)
  Source   : ~/$SOURCE_DIR (on the instance)
  Archive  : $ARCHIVE_NAME
  S3 URI   : $S3_URI

  To restore onto an instance (run there):
    aws s3 cp $S3_URI /tmp/$ARCHIVE_NAME --region $REGION
    tar xzf /tmp/$ARCHIVE_NAME -C \$HOME
============================================
EOF
