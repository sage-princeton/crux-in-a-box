#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# deploy.sh — Deploy the cost-tracking Lambda + API Gateway
# ==========================================================================
# Creates (or updates) an AWS Lambda function fronted by an HTTP API Gateway.
# Prints the invoke URL on success.
#
# Prerequisites:
#   - AWS CLI v2 authenticated with permissions for Lambda, API Gateway, IAM
#   - jq installed
#
# Usage:
#   ./deploy.sh --admin-key <ANTHROPIC_ADMIN_KEY>
# ==========================================================================

# ====== DEFAULTS ======
REGION="${AWS_REGION:-us-east-1}"
FUNCTION_NAME="crux-cost-tracker"
API_NAME="crux-cost-tracker-api"
ROLE_NAME="crux-cost-tracker-role"
ANTHROPIC_ADMIN_KEY=""

# ====== PARSE ARGS ======
usage() {
  cat <<USAGE
Usage: $0 --admin-key <ANTHROPIC_ADMIN_KEY>

Required:
  --admin-key <KEY>        Anthropic Admin API key (sk-ant-admin...)
USAGE
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-key)
      [ -z "${2:-}" ] && { echo "Error: --admin-key requires a value" >&2; usage; }
      ANTHROPIC_ADMIN_KEY="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -z "$ANTHROPIC_ADMIN_KEY" ] && { echo "Error: --admin-key is required" >&2; usage; }

info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }
require_cmd aws
require_cmd jq

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)

# ====== 1. IAM ROLE ======
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" \
  --query 'Role.Arn' --output text 2>/dev/null || true)

if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" = "None" ]; then
  info "Creating IAM role '$ROLE_NAME'..."
  ROLE_ARN=$(aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' \
    --query 'Role.Arn' --output text)
  aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  info "Waiting for IAM role to propagate..."
  sleep 10
  ok "IAM role created: $ROLE_ARN"
else
  ok "IAM role '$ROLE_NAME' already exists: $ROLE_ARN"
fi

# ====== 2. PACKAGE LAMBDA ======
info "Packaging Lambda function..."
TMP_ZIP=$(mktemp /tmp/cost-tracker-XXXX).zip
(cd "$SCRIPT_DIR" && zip -j "$TMP_ZIP" lambda_function.py)
ok "Lambda packaged: $TMP_ZIP"

# ====== 3. CREATE / UPDATE LAMBDA ======
EXISTING_FUNC=$(aws lambda get-function --function-name "$FUNCTION_NAME" \
  --region "$REGION" --query 'Configuration.FunctionName' --output text 2>/dev/null || true)

if [ "$EXISTING_FUNC" = "$FUNCTION_NAME" ]; then
  info "Updating existing Lambda function '$FUNCTION_NAME'..."
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --zip-file "fileb://$TMP_ZIP" \
    --query 'FunctionArn' --output text > /dev/null
  # Wait for the update to complete before changing config
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION" 2>/dev/null || sleep 5
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --environment "Variables={ANTHROPIC_ADMIN_KEY=$ANTHROPIC_ADMIN_KEY}" \
    --timeout 30 \
    --query 'FunctionArn' --output text > /dev/null
  ok "Lambda function updated"
else
  info "Creating Lambda function '$FUNCTION_NAME'..."
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --runtime python3.12 \
    --handler lambda_function.lambda_handler \
    --role "$ROLE_ARN" \
    --zip-file "fileb://$TMP_ZIP" \
    --environment "Variables={ANTHROPIC_ADMIN_KEY=$ANTHROPIC_ADMIN_KEY}" \
    --timeout 30 \
    --query 'FunctionArn' --output text > /dev/null
  ok "Lambda function created"
fi

LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"
rm -f "$TMP_ZIP" "${TMP_ZIP%.zip}"

# ====== 4. HTTP API GATEWAY ======
API_ID=$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text 2>/dev/null || true)

if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  info "Creating HTTP API Gateway '$API_NAME'..."
  API_ID=$(aws apigatewayv2 create-api \
    --name "$API_NAME" \
    --protocol-type HTTP \
    --region "$REGION" \
    --query 'ApiId' --output text)
  ok "API Gateway created: $API_ID"
else
  ok "API Gateway '$API_NAME' already exists: $API_ID"
fi

# Integration
INTEGRATION_ID=$(aws apigatewayv2 get-integrations \
  --api-id "$API_ID" --region "$REGION" \
  --query "Items[?IntegrationUri=='${LAMBDA_ARN}'].IntegrationId | [0]" \
  --output text 2>/dev/null || true)

if [ -z "$INTEGRATION_ID" ] || [ "$INTEGRATION_ID" = "None" ]; then
  info "Creating Lambda integration..."
  INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id "$API_ID" \
    --integration-type AWS_PROXY \
    --integration-uri "$LAMBDA_ARN" \
    --payload-format-version "2.0" \
    --region "$REGION" \
    --query 'IntegrationId' --output text)
  ok "Integration created: $INTEGRATION_ID"
else
  ok "Integration already exists: $INTEGRATION_ID"
fi

# Route: POST /cost
ROUTE_ID=$(aws apigatewayv2 get-routes \
  --api-id "$API_ID" --region "$REGION" \
  --query "Items[?RouteKey=='POST /cost'].RouteId | [0]" \
  --output text 2>/dev/null || true)

if [ -z "$ROUTE_ID" ] || [ "$ROUTE_ID" = "None" ]; then
  info "Creating POST /cost route..."
  aws apigatewayv2 create-route \
    --api-id "$API_ID" \
    --route-key "POST /cost" \
    --target "integrations/$INTEGRATION_ID" \
    --region "$REGION" > /dev/null
  ok "Route created: POST /cost"
else
  ok "Route POST /cost already exists"
fi

# Default stage (auto-deploy)
STAGE_EXISTS=$(aws apigatewayv2 get-stages \
  --api-id "$API_ID" --region "$REGION" \
  --query 'Items[?StageName==`$default`].StageName | [0]' \
  --output text 2>/dev/null || true)

if [ -z "$STAGE_EXISTS" ] || [ "$STAGE_EXISTS" = "None" ]; then
  info "Creating default stage with auto-deploy..."
  aws apigatewayv2 create-stage \
    --api-id "$API_ID" \
    --stage-name '$default' \
    --auto-deploy \
    --region "$REGION" > /dev/null
  ok "Default stage created"
else
  ok "Default stage already exists"
fi

# Lambda invoke permission for API Gateway
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id "apigateway-invoke-${API_ID}" \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*" \
  --region "$REGION" 2>/dev/null || true

INVOKE_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/cost"

cat <<EOF

============================================
  Cost Tracker Lambda deployed!
============================================

  Function : $FUNCTION_NAME
  API ID   : $API_ID
  URL      : $INVOKE_URL

  Test it:
    curl -s -X POST $INVOKE_URL \\
      -H 'Content-Type: application/json' \\
      -d '{"api_key_suffix": "<last 6 chars>"}'

  Use this URL as --cost-tracker-url when running setup-device.sh.
============================================
EOF
