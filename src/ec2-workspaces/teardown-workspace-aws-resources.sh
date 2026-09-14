#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# teardown-workspace-aws-resources.sh — terminate a run box and remove what it left behind.
#
# Only touches things tagged for this slug. It deliberately does NOT remove
# crux-run-sg, the key pair, crux-system-role/profile or /crux/system/env:
# those are shared with the control box and other run boxes, and deleting
# them would break the next launch. There is nothing per-box in SSM or IAM —
# per-run secrets were scp'd at provision time and deleted on the box after
# configure, so they die with the instance.
#
# Usage: ./teardown-workspace-aws-resources.sh [CONFIG_FILE] [--yes]
# ==========================================================================

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE=""; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1; shift ;;
    -*) die "Unknown flag: $1" ;;
    *) CONFIG_FILE="$1"; shift ;;
  esac
done
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/placeholders-run.txt}"
[ -f "$CONFIG_FILE" ] || die "Config file not found: $CONFIG_FILE"

cfg() { sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*([^#[:space:]]*).*/\1/p" "$CONFIG_FILE" | head -1; }
PROFILE="$(cfg AWS_PROFILE)"; REGION="$(cfg AWS_REGION)"; SLUG="$(cfg RUN_SLUG)"
[ -n "$REGION" ] && [ -n "$SLUG" ] || die "AWS_REGION and RUN_SLUG must be set in $CONFIG_FILE"

if [ -n "$PROFILE" ]; then PROFILE_ARGS=(--profile "$PROFILE"); else PROFILE_ARGS=(); fi
aws_() { aws "${PROFILE_ARGS[@]}" --region "$REGION" "$@"; }

INSTANCE_ID="$(aws_ ec2 describe-instances \
  --filters "Name=tag:Name,Values=$SLUG" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)"

echo
echo "About to tear down run box '$SLUG' in $REGION:"
if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  echo "  terminate instance   $INSTANCE_ID"
else
  echo "  (no live instance tagged Name=$SLUG)"
fi
ALLOC_ID="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$SLUG" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
if [ -n "$ALLOC_ID" ] && [ "$ALLOC_ID" != "None" ]; then
  echo "  release Elastic IP   $ALLOC_ID"
fi
echo "  remove ~/.ssh/config entry for $SLUG"
echo
echo "Keeping (shared): crux-run-sg, the key pair, crux-system-role/profile,"
echo "/crux/system/env, and the control box. Nothing per-box lives in SSM or"
echo "IAM — the scp'd secrets file was deleted on the box after configure."
echo

if [ "$ASSUME_YES" != 1 ]; then
  printf "Type the slug to confirm: "
  read -r reply
  [ "$reply" = "$SLUG" ] || die "Did not match. Nothing was deleted."
fi

if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  info "Terminating $INSTANCE_ID"
  aws_ ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
  aws_ ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"
  ok "Terminated (its root volume had DeleteOnTermination=true)"
fi

# Released AFTER the instance is terminated: an EIP still associated to a live
# instance is free, but an allocated one that is not associated bills by the
# hour. Releasing it is the difference between "torn down" and "still paying".
if [ -n "$ALLOC_ID" ] && [ "$ALLOC_ID" != "None" ]; then
  info "Releasing Elastic IP $ALLOC_ID"
  aws_ ec2 release-address --allocation-id "$ALLOC_ID" >/dev/null
  ok "Released"
fi

# Legacy cleanup: boxes provisioned before the scp-secrets change left a
# per-run parameter at /crux/run/<slug>/env holding the OpenAI key and the
# workspace token. Delete it if it is still there; new boxes never create one.
LEGACY_SSM_PARAM="/crux/run/${SLUG}/env"
if aws_ ssm get-parameter --name "$LEGACY_SSM_PARAM" >/dev/null 2>&1; then
  aws_ ssm delete-parameter --name "$LEGACY_SSM_PARAM" >/dev/null
  ok "Deleted legacy $LEGACY_SSM_PARAM (pre-scp-secrets box)"
fi

SSH_CONFIG="$HOME/.ssh/config"
if [ -f "$SSH_CONFIG" ] && grep -qE "^Host[[:space:]]+$SLUG\$" "$SSH_CONFIG"; then
  python3 - "$SSH_CONFIG" "$SLUG" <<'PY'
import re, sys
path, slug = sys.argv[1:3]
text = open(path).read()
pattern = re.compile(rf"\n*^Host[ \t]+{re.escape(slug)}[ \t]*$.*?(?=^Host[ \t]|\Z)", re.M | re.S)
open(path, "w").write(pattern.sub("\n", text))
PY
  ok "Removed the ~/.ssh/config entry"
fi

echo
ok "Run box '$SLUG' is gone. Nothing is still billing for it."
