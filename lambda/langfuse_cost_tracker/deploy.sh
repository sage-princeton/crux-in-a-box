#!/usr/bin/env bash
set -euo pipefail

PROFILE="${AWS_PROFILE:-}"
REGION="${AWS_REGION:-us-east-1}"
FUNCTION_NAME="langfuse-cost-tracker"

usage() {
  cat <<'EOF'
Usage: deploy.sh --profile PROFILE [--region REGION] [--function-name NAME]

AWS_PROFILE may supply the profile; AWS_REGION defaults to us-east-1.
Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and COST_TRACKER_TOKEN in the
environment. LANGFUSE_BASE_URL defaults to https://us.cloud.langfuse.com.
Credentials are never accepted as command-line arguments.
EOF
}
die() { printf '%s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile|--region|--function-name)
      [[ -n "${2:-}" && "$2" != --* ]] || die "$1 requires a value"
      case "$1" in
        --profile) PROFILE="$2" ;;
        --region) REGION="$2" ;;
        --function-name) FUNCTION_NAME="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[[ -n "$PROFILE" ]] || die 'Set --profile or AWS_PROFILE; no default AWS profile is used.'
[[ -n "$REGION" ]] || die 'AWS region must not be empty.'
[[ "$FUNCTION_NAME" =~ ^[a-zA-Z0-9_-]{1,59}$ ]] || die 'Function name must be 1–59 letters, digits, underscores or hyphens.'
for command in aws python3; do
  command -v "$command" >/dev/null || die "$command is required."
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SCRIPT_DIR/lambda_function.py" ]] || die 'lambda_function.py must be beside deploy.sh.'
umask 077
DEPLOY_TMP="$(mktemp -d /tmp/langfuse-cost-tracker.XXXXXX)"
trap 'rm -rf "$DEPLOY_TMP"' EXIT
aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

if ! ACCOUNT_ID="$(aws_cli sts get-caller-identity --query Account --output text 2>"$DEPLOY_TMP/error")"; then
  die "AWS profile '$PROFILE' is not authenticated. Run aws configure sso --use-device-code and retry with --profile '$PROFILE'."
fi
[[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || die 'AWS returned an invalid account identity.'

python3 - "$SCRIPT_DIR" "$DEPLOY_TMP" <<'PY'
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from zipfile import ZipFile

source, temporary = map(Path, sys.argv[1:])
names = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "COST_TRACKER_TOKEN")
missing = [name for name in names if not os.environ.get(name, "").strip()]
if missing:
    sys.exit("Missing environment variables: " + ", ".join(missing))
environment = {name: os.environ[name] for name in names}
base_url = os.environ.get("LANGFUSE_BASE_URL") or "https://us.cloud.langfuse.com"
url = urlsplit(base_url)
if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
    sys.exit("LANGFUSE_BASE_URL must be an HTTPS URL without credentials, query or fragment.")
environment["LANGFUSE_BASE_URL"] = base_url.rstrip("/")
(temporary / "environment.json").write_text(json.dumps({"Variables": environment}))
with ZipFile(temporary / "function.zip", "w") as archive:
    archive.write(source / "lambda_function.py", "lambda_function.py")
PY

# Only a documented missing-resource error means a resource needs creating.
lookup() {
  local missing="$1"
  shift
  if aws_cli "$@" 2>"$DEPLOY_TMP/error"; then return 0; fi
  if grep -Fq "($missing)" "$DEPLOY_TMP/error"; then return 0; fi
  cat "$DEPLOY_TMP/error" >&2
  return 1
}
ROLE_NAME="$FUNCTION_NAME-role"
API_NAME="$FUNCTION_NAME-api"
ROLE_ARN="$(lookup NoSuchEntity iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"
if [[ -z "$ROLE_ARN" || "$ROLE_ARN" == None ]]; then
  ROLE_ARN="$(aws_cli iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --query Role.Arn --output text)"
  aws_cli iam wait role-exists --role-name "$ROLE_NAME"
fi
PARTITION="${ROLE_ARN#arn:}"
PARTITION="${PARTITION%%:*}"
aws_cli iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn "arn:$PARTITION:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

FUNCTION_ARN="$(lookup ResourceNotFoundException lambda get-function --function-name "$FUNCTION_NAME" \
  --query Configuration.FunctionArn --output text)"
CONFIG_ARGS=(--function-name "$FUNCTION_NAME" --runtime python3.12 --handler lambda_function.lambda_handler
  --role "$ROLE_ARN" --environment "file://$DEPLOY_TMP/environment.json" --timeout 30)
if [[ -n "$FUNCTION_ARN" && "$FUNCTION_ARN" != None ]]; then
  aws_cli lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
  aws_cli lambda update-function-code --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$DEPLOY_TMP/function.zip" --query FunctionArn --output text >/dev/null
  aws_cli lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
  aws_cli lambda update-function-configuration "${CONFIG_ARGS[@]}" --query FunctionArn --output text >/dev/null
  aws_cli lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
else
  for attempt in 1 2 3 4 5 6; do
    if FUNCTION_ARN="$(aws_cli lambda create-function "${CONFIG_ARGS[@]}" \
      --zip-file "fileb://$DEPLOY_TMP/function.zip" --query FunctionArn --output text 2>"$DEPLOY_TMP/error")"; then
      break
    fi
    if [[ "$attempt" == 6 ]] || ! grep -Fq '(InvalidParameterValueException)' "$DEPLOY_TMP/error" \
      || ! grep -Fq 'cannot be assumed by Lambda' "$DEPLOY_TMP/error"; then
      cat "$DEPLOY_TMP/error" >&2
      exit 1
    fi
    sleep "$((attempt * 2))"
  done
  aws_cli lambda wait function-active-v2 --function-name "$FUNCTION_NAME"
fi

API_ID="$(aws_cli apigatewayv2 get-apis --query "Items[?Name=='$API_NAME'].ApiId | [0]" --output text)"
if [[ -z "$API_ID" || "$API_ID" == None ]]; then
  API_ID="$(aws_cli apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP --query ApiId --output text)"
fi
INTEGRATION_ID="$(aws_cli apigatewayv2 get-integrations --api-id "$API_ID" \
  --query "Items[?IntegrationUri=='$FUNCTION_ARN'].IntegrationId | [0]" --output text)"
if [[ -z "$INTEGRATION_ID" || "$INTEGRATION_ID" == None ]]; then
  INTEGRATION_ID="$(aws_cli apigatewayv2 create-integration --api-id "$API_ID" --integration-type AWS_PROXY \
    --integration-uri "$FUNCTION_ARN" --payload-format-version 2.0 --query IntegrationId --output text)"
else
  aws_cli apigatewayv2 update-integration --api-id "$API_ID" --integration-id "$INTEGRATION_ID" \
    --integration-uri "$FUNCTION_ARN" --payload-format-version 2.0 --query IntegrationId --output text >/dev/null
fi
ROUTE_ID="$(aws_cli apigatewayv2 get-routes --api-id "$API_ID" \
  --query "Items[?RouteKey=='POST /cost'].RouteId | [0]" --output text)"
if [[ -z "$ROUTE_ID" || "$ROUTE_ID" == None ]]; then
  aws_cli apigatewayv2 create-route --api-id "$API_ID" --route-key 'POST /cost' \
    --target "integrations/$INTEGRATION_ID" --authorization-type NONE --query RouteId --output text >/dev/null
else
  aws_cli apigatewayv2 update-route --api-id "$API_ID" --route-id "$ROUTE_ID" \
    --target "integrations/$INTEGRATION_ID" --authorization-type NONE --query RouteId --output text >/dev/null
fi
STAGE="$(aws_cli apigatewayv2 get-stages --api-id "$API_ID" \
  --query "Items[?StageName=='\$default'].StageName | [0]" --output text)"
if [[ -z "$STAGE" || "$STAGE" == None ]]; then
  aws_cli apigatewayv2 create-stage --api-id "$API_ID" --stage-name "\$default" --auto-deploy --query StageName --output text >/dev/null
else
  aws_cli apigatewayv2 update-stage --api-id "$API_ID" --stage-name "\$default" --auto-deploy --query StageName --output text >/dev/null
fi
PERMISSION_ARGS=(--function-name "$FUNCTION_NAME" --statement-id "apigateway-invoke-$API_ID"
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com
  --source-arn "arn:$PARTITION:execute-api:$REGION:$ACCOUNT_ID:$API_ID/*/POST/cost")
if ! aws_cli lambda add-permission "${PERMISSION_ARGS[@]}" >/dev/null 2>"$DEPLOY_TMP/error"; then
  if ! grep -Fq '(ResourceConflictException)' "$DEPLOY_TMP/error"; then
    cat "$DEPLOY_TMP/error" >&2
    exit 1
  fi
  aws_cli lambda remove-permission --function-name "$FUNCTION_NAME" --statement-id "apigateway-invoke-$API_ID"
  aws_cli lambda add-permission "${PERMISSION_ARGS[@]}" >/dev/null
fi
ENDPOINT="$(aws_cli apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)"
printf 'Deployed %s\nCOST_TRACKER_URL=%s/cost\nRequests require Authorization: Bearer <COST_TRACKER_TOKEN>.\n' "$FUNCTION_NAME" "$ENDPOINT"
