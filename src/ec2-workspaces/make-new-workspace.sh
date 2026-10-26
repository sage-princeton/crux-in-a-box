#!/usr/bin/env bash
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/devops/ec2-workspaces/make-new-workspace.sh" "$@"
