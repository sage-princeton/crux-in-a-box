#!/usr/bin/env bash
set -euo pipefail

# manage-elastic-ips.sh — Manage standalone Elastic IPs, independent of any
# one workspace, for reuse with make-new-workspace.sh's --elastic-ip flag
# (see src/ec2-workspaces/README.md). Allocations made here are tagged
# CruxRole=shared-eip so `list` can tell them apart from the per-workspace
# EIPs provision-workspace-aws-resources.sh allocates and tags to a slug.
#
# Usage:
#   ./manage-elastic-ips.sh list   [--region REGION] [--profile PROFILE] [--all]
#   ./manage-elastic-ips.sh create --name NAME [--region REGION] [--profile PROFILE]
#   ./manage-elastic-ips.sh delete (--allocation-id ID | --name NAME) [--region REGION] [--profile PROFILE] [--yes]
#
# list shows shared EIPs (CruxRole=shared-eip) by default; --all also shows
# per-workspace ones so you can see what's currently in use.
# create allocates a new Elastic IP tagged Name=NAME, CruxRole=shared-eip.
# delete releases one; it refuses if the address is still associated with a
# running instance — disassociate it (or tear down that workspace) first.

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

usage() { sed -n '4,20p' "$0"; }

[ $# -ge 1 ] || { usage; exit 1; }
CMD="$1"; shift
case "$CMD" in
  list|create|delete) ;;
  -h|--help) usage; exit 0 ;;
  *) die "Unknown command: $CMD (expected list, create or delete)" ;;
esac

REGION="us-east-1"; PROFILE=""; NAME=""; ALLOC_ID=""; SHOW_ALL=0; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --region)        REGION="${2:-}"; [ -n "$REGION" ] || die "--region needs a value"; shift 2 ;;
    --profile)       PROFILE="${2:-}"; [ -n "$PROFILE" ] || die "--profile needs a value"; shift 2 ;;
    --name)          NAME="${2:-}"; [ -n "$NAME" ] || die "--name needs a value"; shift 2 ;;
    --allocation-id) ALLOC_ID="${2:-}"; [ -n "$ALLOC_ID" ] || die "--allocation-id needs a value"; shift 2 ;;
    --all)           SHOW_ALL=1; shift ;;
    --yes|-y)        ASSUME_YES=1; shift ;;
    -h|--help)       usage; exit 0 ;;
    *)                die "Unknown flag: $1" ;;
  esac
done

if [ -n "$PROFILE" ]; then PROFILE_ARGS=(--profile "$PROFILE"); else PROFILE_ARGS=(); fi
aws_() { aws "${PROFILE_ARGS[@]}" --region "$REGION" "$@"; }

command -v aws >/dev/null || die "aws CLI not found"
command -v jq  >/dev/null || die "jq not found"
aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "Not authenticated to AWS in $REGION${PROFILE:+ (profile $PROFILE)}."

case "$CMD" in
  list)
    if [ "$SHOW_ALL" = 1 ]; then
      FILTERS=()
    else
      FILTERS=(--filters "Name=tag:CruxRole,Values=shared-eip")
    fi
    ADDRESSES_JSON="$(aws_ ec2 describe-addresses "${FILTERS[@]}" --output json)"
    COUNT="$(printf '%s' "$ADDRESSES_JSON" | jq '.Addresses | length')"
    if [ "$COUNT" -eq 0 ]; then
      ok "No$([ "$SHOW_ALL" = 1 ] || echo " shared") Elastic IPs in $REGION."
      exit 0
    fi
    printf '%-22s %-16s %-22s %-14s %s\n' "ALLOCATION ID" "PUBLIC IP" "NAME" "ROLE" "STATUS"
    printf '%s' "$ADDRESSES_JSON" | jq -r '
      .Addresses[] | [
        .AllocationId,
        .PublicIp,
        ((.Tags // []) | map(select(.Key=="Name")) | .[0].Value // "-"),
        ((.Tags // []) | map(select(.Key=="CruxRole")) | .[0].Value // "-"),
        (if .InstanceId then "in use: " + .InstanceId else "free" end)
      ] | @tsv' | while IFS=$'\t' read -r id ip name role status; do
        printf '%-22s %-16s %-22s %-14s %s\n' "$id" "$ip" "$name" "$role" "$status"
      done
    ;;

  create)
    [ -n "$NAME" ] || die "create needs --name NAME"
    EXISTING="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
      --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
      die "An Elastic IP tagged Name=$NAME already exists ($EXISTING). Pick another name, or use it as-is."
    fi
    info "Allocating Elastic IP tagged Name=$NAME"
    RESULT="$(aws_ ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME},{Key=CruxRole,Value=shared-eip}]" \
      --output json)"
    ALLOC_ID="$(printf '%s' "$RESULT" | jq -r '.AllocationId')"
    PUBLIC_IP="$(printf '%s' "$RESULT" | jq -r '.PublicIp')"
    ok "Allocated $ALLOC_ID ($PUBLIC_IP)"
    echo
    echo "Use it with: ./src/ec2-workspaces/make-new-workspace.sh <slug> --elastic-ip $ALLOC_ID"
    ;;

  delete)
    if [ -z "$ALLOC_ID" ] && [ -n "$NAME" ]; then
      ALLOC_ID="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
        --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
      [[ -n "$ALLOC_ID" && "$ALLOC_ID" != "None" ]] || die "No Elastic IP tagged Name=$NAME in $REGION."
    fi
    [ -n "$ALLOC_ID" ] || die "delete needs --allocation-id ID or --name NAME"

    INFO="$(aws_ ec2 describe-addresses --allocation-ids "$ALLOC_ID" --output json 2>/dev/null || true)"
    [[ -n "$INFO" && "$(printf '%s' "$INFO" | jq '.Addresses | length')" -gt 0 ]] \
      || die "Allocation '$ALLOC_ID' does not exist in $REGION."
    PUBLIC_IP="$(printf '%s' "$INFO" | jq -r '.Addresses[0].PublicIp')"
    INSTANCE_ID="$(printf '%s' "$INFO" | jq -r '.Addresses[0].InstanceId // empty')"
    [ -z "$INSTANCE_ID" ] \
      || die "'$ALLOC_ID' ($PUBLIC_IP) is still associated with instance $INSTANCE_ID. Disassociate it (or tear down that workspace) before releasing."

    echo "About to release Elastic IP $ALLOC_ID ($PUBLIC_IP) in $REGION. This cannot be undone."
    if [ "$ASSUME_YES" != 1 ]; then
      printf "Type the allocation id to confirm: "
      read -r reply
      [ "$reply" = "$ALLOC_ID" ] || die "Did not match. Nothing was deleted."
    fi
    aws_ ec2 release-address --allocation-id "$ALLOC_ID" >/dev/null
    ok "Released $ALLOC_ID ($PUBLIC_IP)"
    ;;
esac
