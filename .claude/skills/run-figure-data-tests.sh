#!/bin/sh
# Run every figure-data skill's test against the checked-in CRUX-2 data and
# the two run workspaces. Run from the repo root:
#   sh .claude/skills/run-figure-data-tests.sh
set -e
cd "$(dirname "$0")/../.."
fail=0
for t in .claude/skills/*/test.py; do
  echo "== $t"
  python3 "$t" || fail=1
  echo
done
if [ "$fail" = 1 ]; then
  echo "SOME FIGURE-DATA SKILL TESTS FAILED"
  exit 1
fi
echo "ALL FIGURE-DATA SKILL TESTS PASS"
