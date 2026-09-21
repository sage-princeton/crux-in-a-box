#!/bin/sh
# Run the shared renderer's test and every figure skill's test: fixtures
# always; the real run data and Chrome rendering when present. Run from
# anywhere — the script cds to the repo root that holds this skills dir:
#   sh analysis/skills/run-figure-tests.sh        (crux-in-a-box)
#   sh .claude/skills/run-figure-tests.sh         (inside an exported run repo)
set -e
cd "$(dirname "$0")/../.."
fail=0
for t in "$(dirname "$0")"/_lib/test.py "$(dirname "$0")"/figure-*/test.py; do
  echo "== $t"
  python3 "$t" || fail=1
  echo
done
if [ "$fail" = 1 ]; then
  echo "SOME FIGURE SKILL TESTS FAILED"
  exit 1
fi
echo "ALL FIGURE SKILL TESTS PASS"
