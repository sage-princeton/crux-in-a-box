#!/usr/bin/env bash
set -euo pipefail

# Delete a run's access to the isolated aux-resources account, and
# everything found there — RDS instances, S3 buckets, EC2 instances,
# non-default Route53 hosted zones, CloudFront distributions, ACM
# certificates, and any crux-app-* IAM roles/instance profiles (plus the
# crux-app-boundary policy that capped them) the agent created for its own
# EC2 instances. Registered domain names are the one exception: Route53
# Domains has no delete API, so those are left registered with auto-renew
# disabled instead. The account is single-tenant at a time, so
# "everything found" and "everything this run created" are the same set.
# No-ops cleanly if this slug never had aux resources provisioned.
#
# Usage: ./teardown-aux-aws-resources.sh [CONFIG_FILE] [--yes]

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
AUX_PROFILE="$(cfg AUX_RESOURCE_PROFILE)"
[[ -n "$REGION" && -n "$SLUG" ]] || die "AWS_REGION and RUN_SLUG must be set in $CONFIG_FILE"

RUN_ROLE="crux-run-$SLUG"
AUX_ROLE="crux-agent-devops"

if [ -n "$PROFILE" ]; then MAIN_PROFILE_ARGS=(--profile "$PROFILE"); else MAIN_PROFILE_ARGS=(); fi
aws_main_()     { aws "${MAIN_PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_main_iam_() { aws "${MAIN_PROFILE_ARGS[@]}" iam "$@"; }

# Nothing to do if this slug never had aux resources provisioned.
if ! aws_main_iam_ get-role --role-name "$RUN_ROLE" >/dev/null 2>&1; then
  ok "No per-workspace role $RUN_ROLE in the main account — aux resources were never provisioned for '$SLUG'. Nothing to do."
  exit 0
fi

[ -n "$AUX_PROFILE" ] \
  || die "$RUN_ROLE exists but AUX_RESOURCE_PROFILE is not set in $CONFIG_FILE — cannot reach the isolated account to tear down its resources."
AUX_PROFILE_ARGS=(--profile "$AUX_PROFILE")
aws_aux_()     { aws "${AUX_PROFILE_ARGS[@]}" --region "$REGION" "$@"; }
aws_aux_iam_() { aws "${AUX_PROFILE_ARGS[@]}" iam "$@"; }
# CloudFront/ACM-for-CloudFront and Route53 Domains are all only reachable
# via the us-east-1 endpoint, regardless of $REGION — an ACM cert can't be
# used by CloudFront unless it's from there, and Route53 Domains has no
# other-region endpoint at all.
aws_aux_useast1_() { aws "${AUX_PROFILE_ARGS[@]}" --region us-east-1 "$@"; }

AUX_ACCOUNT_ID="$(aws_aux_ sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "$AUX_ACCOUNT_ID" && "$AUX_ACCOUNT_ID" != "None" ]] \
  || die "Not authenticated to the isolated account with profile '$AUX_PROFILE'. Run: aws sso login --sso-session <session>"

MAIN_ACCOUNT_ID="$(aws_main_ sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "$MAIN_ACCOUNT_ID" && "$MAIN_ACCOUNT_ID" != "None" ]] \
  || die "Not authenticated to the main account with profile '${PROFILE:-<ambient>}'. Run: aws sso login --sso-session <session>"
[ "$MAIN_ACCOUNT_ID" != "$AUX_ACCOUNT_ID" ] \
  || die "AUX_RESOURCE_PROFILE '$AUX_PROFILE' resolves to the SAME account ($MAIN_ACCOUNT_ID) as the main profile. Refusing to run destructive teardown against the main account — this would delete every RDS instance, S3 bucket, EC2 instance and public Route53 zone there. Check AUX_RESOURCE_PROFILE and AWS_PROFILE in $CONFIG_FILE."

RECORDED_AUX_ACCOUNT_ID="$(cfg AUX_RESOURCE_ACCOUNT_ID)"
if [ -n "$RECORDED_AUX_ACCOUNT_ID" ] && [ "$RECORDED_AUX_ACCOUNT_ID" != "$AUX_ACCOUNT_ID" ]; then
  die "AUX_RESOURCE_PROFILE '$AUX_PROFILE' now resolves to account $AUX_ACCOUNT_ID, but $CONFIG_FILE recorded AUX_RESOURCE_ACCOUNT_ID=$RECORDED_AUX_ACCOUNT_ID at provisioning time. The profile's underlying credentials appear to have changed since then. Refusing to run destructive teardown against a different account than was provisioned."
fi

echo
echo "About to tear down aux AWS resources for '$SLUG' in isolated account $AUX_ACCOUNT_ID:"
echo "  every RDS instance, S3 bucket, EC2 instance, non-default Route53"
echo "  hosted zone, CloudFront distribution, and ACM certificate found in"
echo "  that account (it is single-tenant per run), plus any crux-app-*"
echo "  IAM roles/instance profiles and the crux-app-boundary policy"
echo "  delete IAM role $AUX_ROLE (isolated account) and $RUN_ROLE (main account)"
echo "  any Route53-registered domain name is NOT deleted — there is no"
echo "  cancel/delete API for domain registrations. Auto-renew is disabled"
echo "  instead; the domain stays registered (and billable) until it expires."
echo

if [ "$ASSUME_YES" != 1 ]; then
  printf "Type the slug to confirm: "
  read -r reply
  [ "$reply" = "$SLUG" ] || die "Did not match. Nothing was deleted."
fi

info "RDS instances"
DB_IDS="$(aws_aux_ rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text 2>/dev/null || true)"
if [ -n "$DB_IDS" ] && [ "$DB_IDS" != "None" ]; then
  for db in $DB_IDS; do
    info "Deleting RDS instance $db"
    aws_aux_ rds delete-db-instance --db-instance-identifier "$db" --skip-final-snapshot >/dev/null
    aws_aux_ rds wait db-instance-deleted --db-instance-identifier "$db"
    ok "Deleted $db"
  done
else
  ok "None found"
fi

info "S3 buckets"
BUCKETS="$(aws_aux_ s3api list-buckets --query 'Buckets[].Name' --output text 2>/dev/null || true)"
if [ -n "$BUCKETS" ] && [ "$BUCKETS" != "None" ]; then
  for bucket in $BUCKETS; do
    info "Emptying and deleting bucket $bucket"
    aws "${AUX_PROFILE_ARGS[@]}" s3 rm "s3://$bucket" --recursive >/dev/null
    aws_aux_ s3api delete-bucket --bucket "$bucket" >/dev/null
    ok "Deleted $bucket"
  done
else
  ok "None found"
fi

info "EC2 instances"
INSTANCE_IDS="$(aws_aux_ ec2 describe-instances \
  --filters "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)"
if [ -n "$INSTANCE_IDS" ] && [ "$INSTANCE_IDS" != "None" ]; then
  info "Terminating: $INSTANCE_IDS"
  # shellcheck disable=SC2086
  aws_aux_ ec2 terminate-instances --instance-ids $INSTANCE_IDS >/dev/null
  # shellcheck disable=SC2086
  aws_aux_ ec2 wait instance-terminated --instance-ids $INSTANCE_IDS
  ok "Terminated"
else
  ok "None found"
fi

info "Route53 hosted zones"
# Backticks below are JMESPath literal syntax (false), not shell expansion.
# shellcheck disable=SC2016
ZONE_IDS="$(aws_aux_ route53 list-hosted-zones --query 'HostedZones[?Config.PrivateZone==`false`].Id' --output text 2>/dev/null || true)"
if [ -n "$ZONE_IDS" ] && [ "$ZONE_IDS" != "None" ]; then
  for zone_id in $ZONE_IDS; do
    info "Deleting record sets and hosted zone $zone_id"
    RECORDS="$(aws_aux_ route53 list-resource-record-sets --hosted-zone-id "$zone_id" \
      --query "ResourceRecordSets[?Type != 'NS' && Type != 'SOA']" --output json)"
    if [ "$(printf '%s' "$RECORDS" | jq 'length')" -gt 0 ]; then
      CHANGES="$(printf '%s' "$RECORDS" | jq '{Changes: [.[] | {Action: "DELETE", ResourceRecordSet: .}]}')"
      aws_aux_ route53 change-resource-record-sets --hosted-zone-id "$zone_id" --change-batch "$CHANGES" >/dev/null
    fi
    aws_aux_ route53 delete-hosted-zone --id "$zone_id" >/dev/null
    ok "Deleted zone $zone_id"
  done
else
  ok "None found"
fi

info "Route53 registered domains"
# Route53 Domains has no cancel/delete API — a registered domain cannot be
# un-registered programmatically. Disable auto-renew instead (the closest
# thing to "release" available) and warn: the domain stays registered, and
# billable, until its current registration period expires.
DOMAIN_NAMES="$(aws_aux_useast1_ route53domains list-domains --query 'Domains[].DomainName' --output text 2>/dev/null || true)"
if [ -n "$DOMAIN_NAMES" ] && [ "$DOMAIN_NAMES" != "None" ]; then
  for domain in $DOMAIN_NAMES; do
    if aws_aux_useast1_ route53domains disable-domain-auto-renew --domain-name "$domain" >/dev/null 2>&1; then
      warn "$domain: disabled auto-renew, but it is still registered (and billable) until it expires — Route53 Domains has no delete API"
    else
      warn "$domain: could not disable auto-renew — check it manually in the Route53 console. It is still registered (and billable) until it expires."
    fi
  done
else
  ok "None found"
fi

info "CloudFront distributions"
DIST_IDS="$(aws_aux_useast1_ cloudfront list-distributions --query 'DistributionList.Items[].Id' --output text 2>/dev/null || true)"
if [ -n "$DIST_IDS" ] && [ "$DIST_IDS" != "None" ]; then
  for dist_id in $DIST_IDS; do
    DIST_CONFIG_JSON="$(aws_aux_useast1_ cloudfront get-distribution-config --id "$dist_id")"
    ENABLED="$(printf '%s' "$DIST_CONFIG_JSON" | jq -r '.DistributionConfig.Enabled')"
    if [ "$ENABLED" = "true" ]; then
      info "Disabling distribution $dist_id (must be disabled before it can be deleted)"
      ETAG="$(printf '%s' "$DIST_CONFIG_JSON" | jq -r '.ETag')"
      DISABLED_CONFIG="$(printf '%s' "$DIST_CONFIG_JSON" | jq -c '.DistributionConfig.Enabled = false | .DistributionConfig')"
      aws_aux_useast1_ cloudfront update-distribution --id "$dist_id" \
        --distribution-config "$DISABLED_CONFIG" --if-match "$ETAG" >/dev/null
      info "Waiting for $dist_id to finish deploying (can take ~15 minutes)"
      aws_aux_useast1_ cloudfront wait distribution-deployed --id "$dist_id"
    fi
    DELETE_ETAG="$(aws_aux_useast1_ cloudfront get-distribution --id "$dist_id" --query 'ETag' --output text)"
    aws_aux_useast1_ cloudfront delete-distribution --id "$dist_id" --if-match "$DELETE_ETAG" >/dev/null
    ok "Deleted $dist_id"
  done
else
  ok "None found"
fi

info "ACM certificates (us-east-1 only — see comment near aws_aux_useast1_)"
CERT_ARNS="$(aws_aux_useast1_ acm list-certificates --query 'CertificateSummaryList[].CertificateArn' --output text 2>/dev/null || true)"
if [ -n "$CERT_ARNS" ] && [ "$CERT_ARNS" != "None" ]; then
  for cert_arn in $CERT_ARNS; do
    info "Deleting certificate $cert_arn"
    aws_aux_useast1_ acm delete-certificate --certificate-arn "$cert_arn" >/dev/null
    ok "Deleted $cert_arn"
  done
else
  ok "None found"
fi

info "Agent-created app instance profiles (crux-app-*)"
APP_PROFILE_NAMES="$(aws_aux_iam_ list-instance-profiles \
  --query "InstanceProfiles[?starts_with(InstanceProfileName, 'crux-app-')].InstanceProfileName" \
  --output text 2>/dev/null || true)"
if [ -n "$APP_PROFILE_NAMES" ] && [ "$APP_PROFILE_NAMES" != "None" ]; then
  for profile_name in $APP_PROFILE_NAMES; do
    APP_PROFILE_ROLES="$(aws_aux_iam_ get-instance-profile --instance-profile-name "$profile_name" \
      --query 'InstanceProfile.Roles[].RoleName' --output text 2>/dev/null || true)"
    if [ -n "$APP_PROFILE_ROLES" ] && [ "$APP_PROFILE_ROLES" != "None" ]; then
      for role_name in $APP_PROFILE_ROLES; do
        aws_aux_iam_ remove-role-from-instance-profile \
          --instance-profile-name "$profile_name" --role-name "$role_name" >/dev/null
      done
    fi
    aws_aux_iam_ delete-instance-profile --instance-profile-name "$profile_name" >/dev/null
    ok "Deleted instance profile $profile_name"
  done
else
  ok "None found"
fi

info "Agent-created app roles (crux-app-*)"
APP_ROLE_NAMES="$(aws_aux_iam_ list-roles \
  --query "Roles[?starts_with(RoleName, 'crux-app-')].RoleName" \
  --output text 2>/dev/null || true)"
if [ -n "$APP_ROLE_NAMES" ] && [ "$APP_ROLE_NAMES" != "None" ]; then
  for role_name in $APP_ROLE_NAMES; do
    APP_ROLE_POLICY_NAMES="$(aws_aux_iam_ list-role-policies --role-name "$role_name" \
      --query 'PolicyNames' --output text 2>/dev/null || true)"
    if [ -n "$APP_ROLE_POLICY_NAMES" ] && [ "$APP_ROLE_POLICY_NAMES" != "None" ]; then
      for policy_name in $APP_ROLE_POLICY_NAMES; do
        aws_aux_iam_ delete-role-policy --role-name "$role_name" --policy-name "$policy_name" >/dev/null
      done
    fi
    aws_aux_iam_ delete-role-permissions-boundary --role-name "$role_name" >/dev/null 2>&1 || true
    aws_aux_iam_ delete-role --role-name "$role_name" >/dev/null
    ok "Deleted role $role_name"
  done
else
  ok "None found"
fi

info "Managed policy crux-app-boundary (isolated account)"
BOUNDARY_POLICY_ARN="arn:aws:iam::${AUX_ACCOUNT_ID}:policy/crux-app-boundary"
if aws_aux_iam_ get-policy --policy-arn "$BOUNDARY_POLICY_ARN" >/dev/null 2>&1; then
  # shellcheck disable=SC2016 # backtick is literal JMESPath syntax, not shell expansion
  BOUNDARY_VERSIONS="$(aws_aux_iam_ list-policy-versions --policy-arn "$BOUNDARY_POLICY_ARN" \
    --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text 2>/dev/null || true)"
  if [ -n "$BOUNDARY_VERSIONS" ] && [ "$BOUNDARY_VERSIONS" != "None" ]; then
    for version_id in $BOUNDARY_VERSIONS; do
      aws_aux_iam_ delete-policy-version --policy-arn "$BOUNDARY_POLICY_ARN" --version-id "$version_id" >/dev/null
    done
  fi
  aws_aux_iam_ delete-policy --policy-arn "$BOUNDARY_POLICY_ARN" >/dev/null
  ok "Deleted"
else
  ok "None found"
fi

info "IAM role $AUX_ROLE (isolated account)"
POLICY_ARNS="$(aws_aux_iam_ list-attached-role-policies --role-name "$AUX_ROLE" --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null || true)"
if [ -n "$POLICY_ARNS" ] && [ "$POLICY_ARNS" != "None" ]; then
  for policy_arn in $POLICY_ARNS; do
    aws_aux_iam_ detach-role-policy --role-name "$AUX_ROLE" --policy-arn "$policy_arn" >/dev/null
  done
fi
AUX_INLINE_POLICY_NAMES="$(aws_aux_iam_ list-role-policies --role-name "$AUX_ROLE" --query 'PolicyNames' --output text 2>/dev/null || true)"
if [ -n "$AUX_INLINE_POLICY_NAMES" ] && [ "$AUX_INLINE_POLICY_NAMES" != "None" ]; then
  for policy_name in $AUX_INLINE_POLICY_NAMES; do
    aws_aux_iam_ delete-role-policy --role-name "$AUX_ROLE" --policy-name "$policy_name" >/dev/null
  done
fi
if aws_aux_iam_ delete-role --role-name "$AUX_ROLE" >/dev/null 2>&1; then
  ok "Deleted"
else
  warn "Already gone"
fi

info "IAM role $RUN_ROLE (main account)"
if aws_main_iam_ get-instance-profile --instance-profile-name "$RUN_ROLE" >/dev/null 2>&1; then
  aws_main_iam_ remove-role-from-instance-profile --instance-profile-name "$RUN_ROLE" --role-name "$RUN_ROLE" >/dev/null
  aws_main_iam_ delete-instance-profile --instance-profile-name "$RUN_ROLE" >/dev/null
fi
MAIN_POLICY_NAMES="$(aws_main_iam_ list-role-policies --role-name "$RUN_ROLE" --query 'PolicyNames' --output text 2>/dev/null || true)"
if [ -n "$MAIN_POLICY_NAMES" ] && [ "$MAIN_POLICY_NAMES" != "None" ]; then
  for policy_name in $MAIN_POLICY_NAMES; do
    aws_main_iam_ delete-role-policy --role-name "$RUN_ROLE" --policy-name "$policy_name" >/dev/null
  done
fi
if aws_main_iam_ delete-role --role-name "$RUN_ROLE" >/dev/null 2>&1; then
  ok "Deleted"
else
  warn "Already gone"
fi

echo
ok "Aux AWS resources for '$SLUG' are gone."
