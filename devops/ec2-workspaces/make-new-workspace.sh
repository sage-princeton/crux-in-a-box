#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$root/devops${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m crux_devops provision "$@"
