#!/usr/bin/env bash
set -euo pipefail

# Terminate the instance for a slug, release its Elastic IP and remove its SSH alias.
# Retain the AgentRQ workspace and shared security groups, key pair, IAM and SSM resources.
# If the box used an --elastic-ip override (ELASTIC_IP_ALLOCATION_ID in CONFIG_FILE),
# that address is disassociated but never released — it's shared across workspaces.
#
# Usage: ./teardown-workspace-aws-resources.sh [CONFIG_FILE] [--yes]

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
[[ -n "$REGION" && -n "$SLUG" ]] || die "AWS_REGION and RUN_SLUG must be set in $CONFIG_FILE"
# Set when this box used an --elastic-ip override: that address is shared
# across workspaces, so it is disassociated (by terminating the instance)
# but never released.
ELASTIC_IP_OVERRIDE="$(cfg ELASTIC_IP_ALLOCATION_ID)"

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
if [ -n "$ELASTIC_IP_OVERRIDE" ]; then
  ALLOC_ID="$ELASTIC_IP_OVERRIDE"
else
  ALLOC_ID="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$SLUG" \
    --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
fi
if [ -n "$ALLOC_ID" ] && [ "$ALLOC_ID" != "None" ]; then
  if [ -n "$ELASTIC_IP_OVERRIDE" ]; then
    echo "  keep Elastic IP      $ALLOC_ID (override; disassociated only, shared across workspaces)"
  else
    echo "  release Elastic IP   $ALLOC_ID"
  fi
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

# Release the Elastic IP after terminating the instance — unless it's a
# shared override, in which case terminating the instance already
# disassociated it and it's left allocated for the next workspace.
if [ -n "$ALLOC_ID" ] && [ "$ALLOC_ID" != "None" ]; then
  if [ -n "$ELASTIC_IP_OVERRIDE" ]; then
    ok "Left Elastic IP $ALLOC_ID allocated (override; not released)"
  else
    info "Releasing Elastic IP $ALLOC_ID"
    aws_ ec2 release-address --allocation-id "$ALLOC_ID" >/dev/null
    ok "Released"
  fi
fi

# Delete /crux/run/<slug>/env if present.
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
