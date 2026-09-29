#!/usr/bin/env bash
set -euo pipefail

# Grant a CRUX run's agent scoped AWS access to provision auxiliary
# resources (Postgres/RDS, S3, EC2, DNS + domain registration, CloudFront,
# ACM) in a separate, pre-existing isolated AWS account, for runs testing
# SaaS-to-self-hosted migration.
# Companion to provision-workspace-aws-resources.sh; run before it so the
# instance launch can pick up the per-workspace instance profile this
# script creates.
#
# Usage:
#   ./provision-aux-aws-resources.sh [--dry-run] [CONFIG_FILE]
#
# Opt in per resource type in placeholders-<slug>.txt (all default off):
#   PROVISION_POSTGRES=1  PROVISION_S3=1  PROVISION_DNS=1  PROVISION_EC2=1
#   PROVISION_CLOUDFRONT=1  PROVISION_ACM=1
# Whenever any flag is on, read-only Cost Explorer access is also granted —
# baseline, not a separate flag, so the agent can see what it's spending.
# When both PROVISION_EC2 and PROVISION_S3 are set, the agent may also
# create its own crux-app-* IAM roles/instance profiles for EC2 instances
# it launches (e.g. so Payload's S3 storage adapter can use instance
# credentials) — every such role is capped by a crux-app-boundary
# permissions boundary the agent cannot alter or remove, and can only be
# passed to EC2.
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
    -h|--help) sed -n '4,27p' "$0"; exit 0 ;;
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
FLAG_CLOUDFRONT="$(cfg PROVISION_CLOUDFRONT)"
FLAG_ACM="$(cfg PROVISION_ACM)"
ANY_FLAG=0
for f in "$FLAG_POSTGRES" "$FLAG_S3" "$FLAG_DNS" "$FLAG_EC2" "$FLAG_CLOUDFRONT" "$FLAG_ACM"; do
  [ "$f" = 1 ] && ANY_FLAG=1
done
[ "$ANY_FLAG" = 1 ] \
  || die "No PROVISION_* flag is set to 1 in $CONFIG_FILE. Set at least one of PROVISION_POSTGRES, PROVISION_S3, PROVISION_DNS, PROVISION_EC2, PROVISION_CLOUDFRONT, PROVISION_ACM to opt in."

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
      ce:GetCostAndUsage / GetCostForecast / GetUsageForecast / GetDimensionValues / GetTags (baseline, not flag-gated)
    managed policies attached (only for enabled flags):
      $( [ "$FLAG_POSTGRES" = 1 ] && echo "AmazonRDSFullAccess (PROVISION_POSTGRES=1)" )
      $( [ "$FLAG_S3" = 1 ] && echo "AmazonS3FullAccess (PROVISION_S3=1)" )
      $( [ "$FLAG_EC2" = 1 ] && echo "AmazonEC2FullAccess (PROVISION_EC2=1)" )
      $( [ "$FLAG_DNS" = 1 ] && echo "AmazonRoute53FullAccess (PROVISION_DNS=1)" )
      $( [ "$FLAG_DNS" = 1 ] && echo "AmazonRoute53DomainsFullAccess (PROVISION_DNS=1, domain registration)" )
      $( [ "$FLAG_CLOUDFRONT" = 1 ] && echo "CloudFrontFullAccess (PROVISION_CLOUDFRONT=1)" )
      $( [ "$FLAG_ACM" = 1 ] && echo "AWSCertificateManagerFullAccess (PROVISION_ACM=1)" )
      $( [ "$FLAG_EC2" = 1 ] && [ "$FLAG_S3" = 1 ] && echo "crux-app-boundary managed policy created/refreshed (caps crux-app-* roles: S3 in this account + CloudWatch Logs)" )
      $( [ "$FLAG_EC2" = 1 ] && [ "$FLAG_S3" = 1 ] && echo "inline policy create-app-roles (crux-app-* roles only, always boundary-capped, pass-role to EC2 only)" )
  config written to $CONFIG_FILE:
    AUX_RESOURCE_ACCOUNT_ID=$AUX_ACCOUNT_ID
    AUX_RESOURCE_ROLE_ARN=$AUX_ROLE_ARN
Nothing was created.
PLAN
  exit 0
fi

# ====== MAIN ACCOUNT: crux-run-$SLUG role + instance profile ======
info "IAM role '$RUN_ROLE' (main account, per-workspace)"
if aws_main_iam_ get-role --role-name "$RUN_ROLE" >/dev/null 2>&1; then
  ok "Role exists"
else
  aws_main_iam_ create-role --role-name "$RUN_ROLE" \
    --description "CRUX run box $SLUG - baseline system access plus aux-resource devops" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  ok "Created role"
fi
aws_main_iam_ put-role-policy --role-name "$RUN_ROLE" --policy-name "read-system-env" \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${REGION}:${MAIN_ACCOUNT_ID}:parameter/crux/system/env\"}]}" >/dev/null
ok "Inline policy: ssm:GetParameter on /crux/system/env"
aws_main_iam_ put-role-policy --role-name "$RUN_ROLE" --policy-name "assume-aux-resource-role" \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:AssumeRole\",\"Resource\":\"$AUX_ROLE_ARN\"}]}" >/dev/null
ok "Inline policy: sts:AssumeRole on $AUX_ROLE_ARN only"

if aws_main_iam_ get-instance-profile --instance-profile-name "$RUN_ROLE" >/dev/null 2>&1; then
  ok "Instance profile exists"
else
  aws_main_iam_ create-instance-profile --instance-profile-name "$RUN_ROLE" >/dev/null
  aws_main_iam_ add-role-to-instance-profile \
    --instance-profile-name "$RUN_ROLE" --role-name "$RUN_ROLE"
  info "Waiting 10s for IAM to propagate"
  sleep 10
  ok "Created instance profile"
fi

# ====== ISOLATED ACCOUNT: crux-agent-devops role ======
info "IAM role '$AUX_ROLE' (isolated account $AUX_ACCOUNT_ID)"
TRUST_POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"AWS\":\"$RUN_ROLE_ARN\"},\"Action\":\"sts:AssumeRole\"}]}"
if aws_aux_iam_ get-role --role-name "$AUX_ROLE" >/dev/null 2>&1; then
  CURRENT_TRUSTED_ARN="$(aws_aux_iam_ get-role --role-name "$AUX_ROLE" \
    --query 'Role.AssumeRolePolicyDocument.Statement[0].Principal.AWS' --output text 2>/dev/null || true)"
  case "$CURRENT_TRUSTED_ARN" in
    *"/crux-run-"*)
      if [ "$CURRENT_TRUSTED_ARN" != "$RUN_ROLE_ARN" ]; then
        warn "$AUX_ROLE currently trusts $CURRENT_TRUSTED_ARN — handing off to $RUN_ROLE_ARN. The isolated account is single-tenant, so the previous run loses its aux-resource access now."
      fi
      ;;
  esac
  aws_aux_iam_ update-assume-role-policy --role-name "$AUX_ROLE" --policy-document "$TRUST_POLICY" >/dev/null
  ok "Role exists; trust policy set to $RUN_ROLE_ARN only"
else
  aws_aux_iam_ create-role --role-name "$AUX_ROLE" \
    --description "CRUX agent devops access, scoped to the currently opted-in run" \
    --assume-role-policy-document "$TRUST_POLICY" >/dev/null
  ok "Created role; trust policy scoped to $RUN_ROLE_ARN"
fi

# Cost visibility is baseline, not opt-in: whenever any aux resource is
# enabled, the agent can also see what it's spending. Cost Explorer's read
# actions don't support resource-level scoping — Resource must be "*".
aws_aux_iam_ put-role-policy --role-name "$AUX_ROLE" --policy-name "read-cost-explorer" \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ce:GetCostAndUsage","ce:GetCostForecast","ce:GetUsageForecast","ce:GetDimensionValues","ce:GetTags"],"Resource":"*"}]}' >/dev/null
ok "Inline policy: read-only Cost Explorer access (ce:Get*) — baseline, not tied to a flag"

# ====== APP ROLES (EC2 + S3 only): let the agent give its own instances an
# IAM role, so e.g. Payload's S3 storage adapter can use instance
# credentials, without letting it escalate to admin in the isolated
# account. Every role it can create is named crux-app-* and permanently
# capped by the crux-app-boundary permissions boundary below, which the
# agent's own grant (further down) deliberately cannot attach, detach,
# or edit — that's what makes the boundary the operator's, not the
# agent's, to change.
BOUNDARY_POLICY_ARN="arn:aws:iam::${AUX_ACCOUNT_ID}:policy/crux-app-boundary"
if [ "$FLAG_EC2" = 1 ] && [ "$FLAG_S3" = 1 ]; then
  info "Managed policy 'crux-app-boundary' (isolated account) — caps any crux-app-* role"
  BOUNDARY_DOC="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"AppMediaInThisAccountOnly\",\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\",\"s3:PutObjectAcl\",\"s3:ListBucket\",\"s3:GetBucketLocation\"],\"Resource\":\"*\",\"Condition\":{\"StringEquals\":{\"aws:ResourceAccount\":\"$AUX_ACCOUNT_ID\"}}},{\"Sid\":\"AppLogs\",\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}"
  if aws_aux_iam_ get-policy --policy-arn "$BOUNDARY_POLICY_ARN" >/dev/null 2>&1; then
    # A policy can hold at most 5 versions; prune the non-default ones before
    # adding a new one, matching the script's rebuild-from-scratch style.
    # shellcheck disable=SC2016 # backtick is literal JMESPath syntax, not shell expansion
    BOUNDARY_OLD_VERSIONS="$(aws_aux_iam_ list-policy-versions --policy-arn "$BOUNDARY_POLICY_ARN" \
      --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text 2>/dev/null || true)"
    if [ -n "$BOUNDARY_OLD_VERSIONS" ] && [ "$BOUNDARY_OLD_VERSIONS" != "None" ]; then
      for version_id in $BOUNDARY_OLD_VERSIONS; do
        aws_aux_iam_ delete-policy-version --policy-arn "$BOUNDARY_POLICY_ARN" --version-id "$version_id" >/dev/null
      done
    fi
    aws_aux_iam_ create-policy-version --policy-arn "$BOUNDARY_POLICY_ARN" \
      --policy-document "$BOUNDARY_DOC" --set-as-default >/dev/null
    ok "Updated to a new default version"
  else
    aws_aux_iam_ create-policy --policy-name crux-app-boundary --policy-document "$BOUNDARY_DOC" \
      --description "Caps what any crux-app-* role the agent creates can ever do" >/dev/null
    ok "Created"
  fi

  info "Inline policy 'create-app-roles' on $AUX_ROLE — crux-app-* only, boundary enforced, pass-role to EC2 only"
  APP_ROLE_POLICY_DOC="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"CreateAppRolesOnlyWithBoundary\",\"Effect\":\"Allow\",\"Action\":[\"iam:CreateRole\",\"iam:PutRolePermissionsBoundary\"],\"Resource\":\"arn:aws:iam::${AUX_ACCOUNT_ID}:role/crux-app-*\",\"Condition\":{\"StringEquals\":{\"iam:PermissionsBoundary\":\"$BOUNDARY_POLICY_ARN\"}}},{\"Sid\":\"ManageAppRoles\",\"Effect\":\"Allow\",\"Action\":[\"iam:GetRole\",\"iam:DeleteRole\",\"iam:TagRole\",\"iam:UpdateAssumeRolePolicy\",\"iam:PutRolePolicy\",\"iam:GetRolePolicy\",\"iam:DeleteRolePolicy\",\"iam:ListRolePolicies\"],\"Resource\":\"arn:aws:iam::${AUX_ACCOUNT_ID}:role/crux-app-*\"},{\"Sid\":\"ManageAppInstanceProfiles\",\"Effect\":\"Allow\",\"Action\":[\"iam:CreateInstanceProfile\",\"iam:DeleteInstanceProfile\",\"iam:GetInstanceProfile\",\"iam:AddRoleToInstanceProfile\",\"iam:RemoveRoleFromInstanceProfile\",\"iam:TagInstanceProfile\"],\"Resource\":\"arn:aws:iam::${AUX_ACCOUNT_ID}:instance-profile/crux-app-*\"},{\"Sid\":\"PassAppRolesToEc2Only\",\"Effect\":\"Allow\",\"Action\":\"iam:PassRole\",\"Resource\":\"arn:aws:iam::${AUX_ACCOUNT_ID}:role/crux-app-*\",\"Condition\":{\"StringEquals\":{\"iam:PassedToService\":\"ec2.amazonaws.com\"}}}]}"
  aws_aux_iam_ put-role-policy --role-name "$AUX_ROLE" --policy-name "create-app-roles" \
    --policy-document "$APP_ROLE_POLICY_DOC" >/dev/null
  ok "Inline policy: create-app-roles"
else
  # Reconcile away: if either flag was on before and one is now off, the
  # agent must lose the ability to create/manage crux-app-* roles.
  aws_aux_iam_ delete-role-policy --role-name "$AUX_ROLE" --policy-name "create-app-roles" >/dev/null 2>&1 || true
fi

info "Reconciling managed policy attachments to the current PROVISION_* flags"
declare -A WANT_POLICIES=()
[ "$FLAG_POSTGRES" = 1 ] && WANT_POLICIES[AmazonRDSFullAccess]=1
[ "$FLAG_S3" = 1 ] && WANT_POLICIES[AmazonS3FullAccess]=1
[ "$FLAG_EC2" = 1 ] && WANT_POLICIES[AmazonEC2FullAccess]=1
[ "$FLAG_DNS" = 1 ] && WANT_POLICIES[AmazonRoute53FullAccess]=1
[ "$FLAG_DNS" = 1 ] && WANT_POLICIES[AmazonRoute53DomainsFullAccess]=1
[ "$FLAG_CLOUDFRONT" = 1 ] && WANT_POLICIES[CloudFrontFullAccess]=1
[ "$FLAG_ACM" = 1 ] && WANT_POLICIES[AWSCertificateManagerFullAccess]=1

ATTACHED="$(aws_aux_iam_ list-attached-role-policies --role-name "$AUX_ROLE" --query 'AttachedPolicies[].PolicyName' --output text)"
for name in $ATTACHED; do
  case "$name" in
    AmazonRDSFullAccess|AmazonS3FullAccess|AmazonEC2FullAccess|AmazonRoute53FullAccess|AmazonRoute53DomainsFullAccess|CloudFrontFullAccess|AWSCertificateManagerFullAccess)
      if [ -z "${WANT_POLICIES[$name]:-}" ]; then
        aws_aux_iam_ detach-role-policy --role-name "$AUX_ROLE" --policy-arn "arn:aws:iam::aws:policy/$name" >/dev/null
        ok "Detached $name (no longer enabled)"
      fi
      ;;
  esac
done
for name in "${!WANT_POLICIES[@]}"; do
  aws_aux_iam_ attach-role-policy --role-name "$AUX_ROLE" --policy-arn "arn:aws:iam::aws:policy/$name" >/dev/null
  ok "Attached $name"
done

# ====== WRITE CONFIG ======
# Portable in-place rewrite: filter out any prior AUX_RESOURCE_ACCOUNT_ID /
# AUX_RESOURCE_ROLE_ARN lines, then append current values. Avoids sed -i,
# whose -i flag syntax differs between BSD (macOS) and GNU sed.
info "Recording AUX_RESOURCE_ACCOUNT_ID / AUX_RESOURCE_ROLE_ARN in $CONFIG_FILE"
TMP_CONFIG="$(mktemp)"
grep -vE '^(AUX_RESOURCE_ACCOUNT_ID|AUX_RESOURCE_ROLE_ARN)=' "$CONFIG_FILE" > "$TMP_CONFIG" || true
{
  cat "$TMP_CONFIG"
  printf '\n# ---- set by provision-aux-aws-resources.sh ----\n'
  printf 'AUX_RESOURCE_ACCOUNT_ID=%s\n' "$AUX_ACCOUNT_ID"
  printf 'AUX_RESOURCE_ROLE_ARN=%s\n' "$AUX_ROLE_ARN"
} > "$CONFIG_FILE"
rm -f "$TMP_CONFIG"
ok "Recorded"

cat <<DONE

$(ok "Aux-resource access provisioned for '$SLUG'")

  main account role       $RUN_ROLE ($MAIN_ACCOUNT_ID)
  isolated account role   $AUX_ROLE ($AUX_ACCOUNT_ID), trusts $RUN_ROLE only
  granted                 $( [ "$FLAG_POSTGRES" = 1 ] && printf 'postgres ' )$( [ "$FLAG_S3" = 1 ] && printf 's3 ' )$( [ "$FLAG_EC2" = 1 ] && printf 'ec2 ' )$( [ "$FLAG_DNS" = 1 ] && printf 'dns ' )$( [ "$FLAG_CLOUDFRONT" = 1 ] && printf 'cloudfront ' )$( [ "$FLAG_ACM" = 1 ] && printf 'acm ' )cost-explorer-read$( [ "$FLAG_EC2" = 1 ] && [ "$FLAG_S3" = 1 ] && printf ' app-roles' )

Launch the instance with provision-workspace-aws-resources.sh — it will use
the $RUN_ROLE instance profile since it now exists for '$SLUG'.
Teardown: ./teardown-aux-aws-resources.sh $(basename "$CONFIG_FILE")
DONE
