#!/usr/bin/env bash
# Download a pinned GitHub release; publish the binary only after verification.
set -euo pipefail
version="${1:?Trivy version required}"
expected_sha256="${2:?release archive SHA-256 required}"
binary_dir="${3:?installation directory required}"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
archive="$scratch/trivy.tar.gz"

curl --fail --show-error --location \
  --retry 4 --retry-all-errors --retry-delay 2 --retry-max-time 180 \
  --connect-timeout 15 --max-time 120 \
  "https://github.com/aquasecurity/trivy/releases/download/v${version}/trivy_${version}_Linux-64bit.tar.gz" \
  --output "$archive"
python3 - "$archive" "$expected_sha256" <<'PY'
import hashlib
import sys

with open(sys.argv[1], "rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
if digest != sys.argv[2]:
    raise SystemExit("Trivy release archive SHA-256 mismatch")
PY
tar -xzf "$archive" -C "$scratch" trivy
mkdir -p "$binary_dir"
install -m 755 "$scratch/trivy" "$binary_dir/trivy"
"$binary_dir/trivy" --version
