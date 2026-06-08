#!/usr/bin/env bash
set -euo pipefail

# clean-telemetry.sh — Redact sensitive strings from a telemetry JSONL file.
#
# Usage:
#   ./clean-telemetry.sh <input.jsonl> <blacklist.txt>
#
# Output:
#   <input>_CLEAN.jsonl  (same dir as input, original untouched)
#
# NB: The blacklist file should have one sensitive string per line.
# Each line is treated as a fixed string and replaced with [REDACTED].

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <input.jsonl> <blacklist.txt>" >&2
    exit 1
fi

INPUT="$1"
BLACKLIST="$2"

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file not found: $INPUT" >&2
    exit 1
fi

if [[ ! -f "$BLACKLIST" ]]; then
    echo "Error: blacklist file not found: $BLACKLIST" >&2
    exit 1
fi

DIR="$(dirname "$INPUT")"
BASE="$(basename "$INPUT")"
NAME="${BASE%.*}"
OUTPUT="${DIR}/${NAME}_CLEAN.jsonl"

INPUT_SIZE=$(wc -c < "$INPUT" | tr -d ' ')
ENTRY_COUNT=$(grep -c . "$BLACKLIST" || true)

echo "Input:     $INPUT ($INPUT_SIZE bytes)"
echo "Blacklist: $BLACKLIST ($ENTRY_COUNT entries)"
echo "Output:    $OUTPUT"
echo

# Use Python for a single-pass replacement — handles large files and
# special characters without sed escaping headaches.
python3 -c "
import sys, time

input_path = sys.argv[1]
blacklist_path = sys.argv[2]
output_path = sys.argv[3]

# Load blacklist — sort longest-first so longer matches take priority
with open(blacklist_path) as f:
    secrets = [line.rstrip(chr(10)) for line in f if line.strip()]
secrets.sort(key=len, reverse=True)

print(f'Loaded {len(secrets)} secrets (longest: {len(secrets[0])} chars)')
print('Replacing...')

t0 = time.time()
replaced_count = 0

with open(input_path, 'r') as fin, open(output_path, 'w') as fout:
    for i, line in enumerate(fin, 1):
        for secret in secrets:
            if secret in line:
                n = line.count(secret)
                replaced_count += n
                line = line.replace(secret, '[REDACTED]')
        fout.write(line)
        if i % 500 == 0:
            print(f'  ...{i} lines processed', end=chr(13))

elapsed = time.time() - t0
print(f'\nDone in {elapsed:.1f}s -- {replaced_count} replacements across {i} lines')
" "$INPUT" "$BLACKLIST" "$OUTPUT"

OUTPUT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
echo
echo "Output: $OUTPUT ($OUTPUT_SIZE bytes)"
echo

# Verify no blacklisted strings remain
echo "Verifying..."
LEAKS=0
while IFS= read -r secret; do
    [[ -z "$secret" ]] && continue
    COUNT=$(grep -cF "$secret" "$OUTPUT" 2>/dev/null || true)
    if [[ "$COUNT" -gt 0 ]]; then
        echo "⚠️  LEAK: '${secret:0:30}...' still found $COUNT time(s)!" >&2
        LEAKS=$((LEAKS + 1))
    fi
done < "$BLACKLIST"

if [[ "$LEAKS" -eq 0 ]]; then
    echo "✅ Verification passed: no blacklisted strings found in output."
else
    echo "❌ $LEAKS blacklisted string(s) still present in output!" >&2
    exit 1
fi
