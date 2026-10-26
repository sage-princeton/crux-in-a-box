#!/usr/bin/env bash
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/devops/ec2-control/make-control-box.sh" "$@"
