#!/usr/bin/env bash
# Retain all findings; block releases with fixable high/critical vulnerabilities.
set -euo pipefail
image_ref="${1:?image reference required}"
report_file="${2:?JSON report path required}"
mkdir -p "$(dirname "$report_file")"
trivy image --scanners vuln --format json --output "$report_file" "$image_ref"
trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image_ref"
