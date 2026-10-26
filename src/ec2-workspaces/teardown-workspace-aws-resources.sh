#!/usr/bin/env bash
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/devops/ec2-workspaces/teardown-workspace-aws-resources.sh" "$@"
