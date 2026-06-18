#!/usr/bin/env bash
set -euo pipefail
# ==========================================================================
# backup-openclaw.sh  –  runs ON the CRUX-in-a-box dev EC2 instance
# ==========================================================================
# Tars up the agent's ~/.openclaw directory and uploads a single timestamped
# archive to S3. Intended to capture the full OpenClaw state (config, env,
# credentials, workspace, sessions) before stopping or terminating the box.
#
# Auth: relies on the instance's IAM role (crux-in-a-box-role, PowerUserAccess
# attached by setup-device.sh) for S3 access via IMDS — no AWS keys needed.
#
# Fixed config:
#   Bucket : hal-crux-backups
#   Region : us-east-1
#
# Usage:
#   ./backup-openclaw.sh [--instance-suffix <SUFFIX>]
#
# Optional:
#   --instance-suffix <SUFFIX>   Matches setup-device.sh. The instance name is
#                                  crux-in-a-box[-<SUFFIX>]; the backup is stored
#                                  under that name's prefix in the bucket.
# ==========================================================================

# ====== FIXED CONFIGURATION ======
BUCKET="hal-crux-backups"
REGION="us-east-1"
SOURCE_DIR="$HOME/.openclaw"

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Usage: $0 [--instance-suffix <SUFFIX>]

Optional:
  --instance-suffix <SUFFIX>   Matches setup-device.sh. Instance name becomes
                                 crux-in-a-box-<SUFFIX>; backup is stored under
                                 that prefix in s3://$BUCKET.
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
command -v aws &>/dev/null || die "'aws' CLI not found on this instance."
command -v tar &>/dev/null || die "'tar' not found on this instance."

[ -d "$SOURCE_DIR" ] || die "Source directory not found: $SOURCE_DIR"

aws sts get-caller-identity --region "$REGION" &>/dev/null \
  || die "AWS credentials unavailable. This script expects the instance IAM role (IMDS)."

# ====== ENSURE BUCKET ======
# Create the backup bucket if it doesn't exist yet. NOTE: for us-east-1 the
# create-bucket call must NOT pass a LocationConstraint (AWS rejects it).
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  ok "Bucket exists: $BUCKET"
else
  info "Bucket '$BUCKET' not found — creating in $REGION..."
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null \
    || die "Could not create bucket '$BUCKET'. Check the name is globally unique and IAM permissions."
  aws s3api wait bucket-exists --bucket "$BUCKET" --region "$REGION" \
    || die "Bucket '$BUCKET' was created but did not become available."
  # Block public access and enable default server-side encryption.
  aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
    >/dev/null 2>&1 || warn "Could not set public-access-block on $BUCKET (continuing)."
  aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$REGION" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    >/dev/null 2>&1 || warn "Could not set default encryption on $BUCKET (continuing)."
  ok "Bucket created: $BUCKET"
fi

# ====== BUILD ARCHIVE ======
# tar from the parent dir so the archive contains the basename
# (e.g. '.openclaw/...') rather than absolute paths.
PARENT_DIR="$(cd "$(dirname "$SOURCE_DIR")" && pwd)"
BASE_NAME="$(basename "$SOURCE_DIR")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="${BASE_NAME#.}-${INSTANCE_NAME}-${STAMP}.tar.gz"
ARCHIVE_PATH="$(mktemp -d)/${ARCHIVE_NAME}"

info "Archiving $SOURCE_DIR → $ARCHIVE_PATH"
tar czf "$ARCHIVE_PATH" -C "$PARENT_DIR" "$BASE_NAME" \
  || die "tar failed."

ARCHIVE_SIZE="$(du -h "$ARCHIVE_PATH" | cut -f1)"
ok "Archive created (${ARCHIVE_SIZE})"

# ====== UPLOAD ======
S3_URI="s3://${BUCKET}/${PREFIX}/${ARCHIVE_NAME}"
info "Uploading to $S3_URI (server-side encryption: AES256)"
aws s3 cp "$ARCHIVE_PATH" "$S3_URI" \
  --region "$REGION" \
  --sse AES256 \
  || die "Upload failed. Check the bucket ($BUCKET) and the instance IAM permissions."
ok "Uploaded"

# ====== VERIFY ======
info "Verifying object in S3..."
if aws s3 ls "$S3_URI" --region "$REGION" >/dev/null 2>&1; then
  ok "Verified: $S3_URI"
else
  die "Upload reported success but object not found at $S3_URI"
fi

# ====== CLEANUP ======
rm -f "$ARCHIVE_PATH"
rmdir "$(dirname "$ARCHIVE_PATH")" 2>/dev/null || true

cat <<EOF

============================================
  OpenClaw backup complete
============================================
  Source  : $SOURCE_DIR
  Archive : $ARCHIVE_NAME
  S3 URI  : $S3_URI

  To restore on a new instance:
    aws s3 cp $S3_URI /tmp/$ARCHIVE_NAME --region $REGION
    tar xzf /tmp/$ARCHIVE_NAME -C \$HOME
============================================
EOF
