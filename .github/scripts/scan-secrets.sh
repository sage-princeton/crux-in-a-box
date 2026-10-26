#!/usr/bin/env bash
set -euo pipefail
: "${BASE_SHA:?}" "${HEAD_SHA:?}"
report="$(mktemp)"
trap 'rm -f "$report"' EXIT
code=0
for mode in verified private-keys; do
  arguments=('--results=verified,unknown')
  if [ "$mode" = private-keys ]; then
    arguments=(--include-detectors=PrivateKey --results=unverified --no-verification)
  fi
  result=0
  docker run --rm --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp \
    -e HOME=/tmp -v "$PWD:/repo:ro" crux-secret-scanner \
    git file:///repo --since-commit "$BASE_SHA" --branch "$HEAD_SHA" \
    "${arguments[@]}" --fail --no-update --json >> "$report" 2>/dev/null || result=$?
  if [ "$result" != 0 ]; then code="$result"; fi
done
python3 - "$report" <<'REPORT'
import json,sys
count=0
for line in open(sys.argv[1]):
    record=json.loads(line)
    count+=1
    source=record.get('SourceMetadata',{}).get('Data',{}).get('Git',{})
    print(json.dumps({key:value for key,value in {'detector':record.get('DetectorName'),
        'verified':record.get('Verified'),'commit':source.get('commit'),'file':source.get('file')}.items()}))
print(f'Secret scan findings: {count}; raw values omitted.')
REPORT
if [ "$code" != 0 ]; then echo "Secret scan failed (exit $code)." >&2; fi
exit "$code"
