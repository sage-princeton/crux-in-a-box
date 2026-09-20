#!/usr/bin/env bash
set -euo pipefail

# Grant a CRUX run's agent scoped AWS access to provision auxiliary
# resources (Postgres/RDS, S3, EC2, DNS) in a separate, pre-existing
# isolated AWS account, for runs testing SaaS-to-self-hosted migration.
# Companion to provision-workspace-aws-resources.sh; run before it so the
# instance launch can pick up the per-workspace instance profile this
# script creates.
#
# Usage:
#   ./provision-aux-aws-resources.sh [--dry-run] [CONFIG_FILE]
#
# Opt in per resource type in placeholders-<slug>.txt (all default off):
#   PROVISION_POSTGRES=1  PROVISION_S3=1  PROVISION_DNS=1  PROVISION_EC2=1
# AUX_RESOURCE_PROFILE names the AWS CLI profile/credentials for the
# isolated account. See README.md.

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ====== PARSE ARGS ======
CONFIG_FILE=""; DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '4,17p' "$0"; exit 0 ;;
    -*) die "Unknown flag: $1" ;;
    *) [ -z "$CONFIG_FILE" ] || die "Only one config file"; CONFIG_FILE="$1"; shift ;;
  esac
done
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/placeholders-run.txt}"
[ -f "$CONFIG_FILE" ] || die "Config file not found: $CONFIG_FILE"

# ====== LOAD CONFIG ======
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
cfg() { printf '%s' "${CFG[$1]:-}"; }

MISSING=()
for k in AWS_REGION RUN_SLUG; do
  [ -n "${CFG[$k]:-}" ] || MISSING+=("$k")
done
[ ${#MISSING[@]} -eq 0 ] || die "Missing required key(s) in $CONFIG_FILE: ${MISSING[*]}"

REGION="${CFG[AWS_REGION]}"
SLUG="${CFG[RUN_SLUG]}"
PROFILE="${CFG[AWS_PROFILE]:-}"

FLAG_POSTGRES="$(cfg PROVISION_POSTGRES)"
FLAG_S3="$(cfg PROVISION_S3)"
FLAG_DNS="$(cfg PROVISION_DNS)"
FLAG_EC2="$(cfg PROVISION_EC2)"
ANY_FLAG=0
for f in "$FLAG_POSTGRES" "$FLAG_S3" "$FLAG_DNS" "$FLAG_EC2"; do
  [ "$f" = 1 ] && ANY_FLAG=1
done
[ "$ANY_FLAG" = 1 ] \
  || die "No PROVISION_* flag is set to 1 in $CONFIG_FILE. Set at least one of PROVISION_POSTGRES, PROVISION_S3, PROVISION_DNS, PROVISION_EC2 to opt in."

AUX_PROFILE="$(cfg AUX_RESOURCE_PROFILE)"
[ -n "$AUX_PROFILE" ] \
  || die "AUX_RESOURCE_PROFILE is not set in $CONFIG_FILE. It must name the AWS CLI profile for the isolated account these resources are granted in."

RUN_ROLE="crux-run-$SLUG"
AUX_ROLE="crux-agent-devops"

if [ -n "$PROFILE" ]; then MAIN_PROFILE_ARGS=(--profile "$PROFILE"); else MAIN_PROFILE_ARGS=(); fi
AUX_PROFILE_ARGS=(--profile "$AUX_PROFILE")
aws_main_()     { aws "${MAIN_PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_main_iam_() { aws "${MAIN_PROFILE_ARGS[@]}" iam "$@"; }
aws_aux_()      { aws "${AUX_PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_aux_iam_()  { aws "${AUX_PROFILE_ARGS[@]}" iam "$@"; }

# ====== PREFLIGHT ======
info "Preflight"
for b in aws jq; do command -v "$b" >/dev/null || die "$b not found"; done
MAIN_ACCOUNT_ID="$(aws_main_ sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "$MAIN_ACCOUNT_ID" && "$MAIN_ACCOUNT_ID" != "None" ]] \
  || die "Not authenticated to the main account with profile '$PROFILE'. Run: aws sso login --sso-session <session>"
AUX_ACCOUNT_ID="$(aws_aux_ sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "$AUX_ACCOUNT_ID" && "$AUX_ACCOUNT_ID" != "None" ]] \
  || die "Not authenticated to the isolated account with profile '$AUX_PROFILE'. Run: aws sso login --sso-session <session>"
[ "$MAIN_ACCOUNT_ID" != "$AUX_ACCOUNT_ID" ] \
  || die "AUX_RESOURCE_PROFILE '$AUX_PROFILE' resolves to the same account ($MAIN_ACCOUNT_ID) as the main profile. It must be a separate, isolated account."
ok "Main account $MAIN_ACCOUNT_ID, isolated account $AUX_ACCOUNT_ID"

RUN_ROLE_ARN="arn:aws:iam::${MAIN_ACCOUNT_ID}:role/${RUN_ROLE}"
AUX_ROLE_ARN="arn:aws:iam::${AUX_ACCOUNT_ID}:role/${AUX_ROLE}"

if [ "$DRY_RUN" = 1 ]; then
  cat <<PLAN
[dry-run] Would, for slug '$SLUG':
  main account ($MAIN_ACCOUNT_ID):
    IAM role + instance profile   $RUN_ROLE
      ssm:GetParameter on /crux/system/env (baseline, same as crux-system-role)
      sts:AssumeRole on $AUX_ROLE_ARN only
  isolated account ($AUX_ACCOUNT_ID):
    IAM role   $AUX_ROLE   trusts $RUN_ROLE_ARN only
    managed policies attached (only for enabled flags):
      $( [ "$FLAG_POSTGRES" = 1 ] && echo "AmazonRDSFullAccess (PROVISION_POSTGRES=1)" )
      $( [ "$FLAG_S3" = 1 ] && echo "AmazonS3FullAccess (PROVISION_S3=1)" )
      $( [ "$FLAG_EC2" = 1 ] && echo "AmazonEC2FullAccess (PROVISION_EC2=1)" )
      $( [ "$FLAG_DNS" = 1 ] && echo "AmazonRoute53FullAccess (PROVISION_DNS=1)" )
  config written to $CONFIG_FILE:
    AUX_RESOURCE_ACCOUNT_ID=$AUX_ACCOUNT_ID
    AUX_RESOURCE_ROLE_ARN=$AUX_ROLE_ARN
Nothing was created.
PLAN
  exit 0
fi

die "Real (non-dry-run) provisioning is not implemented yet."
