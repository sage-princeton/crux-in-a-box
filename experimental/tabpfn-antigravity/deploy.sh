#!/usr/bin/env bash
# Launch or locate the tagged study host, then install the committed revision over verified SSH.
# Required operator settings are checked before launching a billable instance.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${AWS_PROFILE:?Set a named AWS profile}" "${RUN_SLUG:?Set the study slug}"
: "${SUBNET_ID:?Set a subnet}" "${SG_ID:?Set a security group}" "${KEY_NAME:?Set an EC2 key pair}"
deploy=("${PYTHON:-python3}" "$here/deploy.py" --profile "$AWS_PROFILE" --region "${AWS_REGION:-us-east-1}" --slug "$RUN_SLUG")
launch=(launch --subnet "$SUBNET_ID" --security-group "$SG_ID" --key-name "$KEY_NAME")
case "${1:-}" in
  --dry-run) "${deploy[@]}" "${launch[@]}" --dry-run ;;
  "")
    : "${SSH_KEY:?Set the SSH identity}" "${VERIFIED_HOSTS:?Set the known_hosts file}"
    : "${AI_STUDIO_API_DEV_KEY:?}" "${OPENROUTER_API_KEY:?}" "${LANGFUSE_BASE_URL:?}" "${LANGFUSE_PUBLIC_KEY:?}" "${LANGFUSE_SECRET_KEY:?}"
    "${deploy[@]}" "${launch[@]}"
    "${deploy[@]}" configure --ssh-key "$SSH_KEY" --known-hosts "$VERIFIED_HOSTS"
    ;;
  *) echo "Usage: deploy.sh [--dry-run]" >&2; exit 2 ;;
esac
