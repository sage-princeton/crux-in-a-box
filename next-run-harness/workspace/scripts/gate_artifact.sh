#!/usr/bin/env bash
# gate_artifact.sh — mechanical deliverable gates.
# Run before every blind review round and at every PLAN.md milestone gate.
# Exit 0 = all gates pass. Any failure prints GATE FAIL lines and exits 1.
#
# The agent extends this file during the run (e.g. registered-number checks),
# but gates may only be ADDED, never removed or weakened, except via a Tier-2 memo.
set -u

FAIL=0
gate() { # gate <name> <0-ok|1-fail> <detail>
  if [ "$2" -ne 0 ]; then echo "GATE FAIL [$1]: $3"; FAIL=1; else echo "gate ok  [$1]"; fi
}

PDF="${1:?usage: gate_artifact.sh <path-to-pdf> [<source-dir>]}"
SRC="${2:-paper}"

# --- 1. Artifact exists and page budget ---------------------------------
PAGE_BUDGET="{{PAGE_BUDGET|9}}"   # main-body page limit from BRIEF.md
if [ ! -f "$PDF" ]; then
  gate "pdf-exists" 1 "$PDF not found"
else
  gate "pdf-exists" 0 ""
  if command -v pdfinfo >/dev/null 2>&1; then
    PAGES=$(pdfinfo "$PDF" | awk '/^Pages:/ {print $2}')
    # TODO(task-specific): if refs/appendix are exempt from the budget, count
    # main-body pages instead (e.g. via pdftotext and the References heading).
    gate "page-budget" $([ "${PAGES:-999}" -le "$PAGE_BUDGET" ] && echo 0 || echo 1) \
         "pages=$PAGES > budget=$PAGE_BUDGET"
  fi
fi

# --- 2. Unresolved placeholders -----------------------------------------
PLACEHOLDERS='\[CITE:|TODO|TKTK|% MISSING|XXX|\?\?\?'
HITS=$(grep -rnE "$PLACEHOLDERS" "$SRC" --include='*.tex' --include='*.md' 2>/dev/null | grep -v 'gate_artifact' | head -20)
gate "placeholders" $([ -z "$HITS" ] && echo 0 || echo 1) "unresolved placeholders:
$HITS"

# --- 3. Author-internal vocabulary (reads-as-internal-document) ----------
# These greps live HERE, not in reviewer prompts. Never tell a reviewer about them.
INTERNAL_VOCAB='closure ledger|untouchable|MAJOR-[0-9]|F-[0-9]+\.[0-9]|SHIP/DEFER|Principle #|Status: LIVE|changelog:'
HITS=$(grep -rnE "$INTERNAL_VOCAB" "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-vocab" $([ -z "$HITS" ] && echo 0 || echo 1) "author-internal vocabulary in deliverable:
$HITS"

# --- 4. Deanonymization ---------------------------------------------------
DEANON='{{AGENT_NAME}}|{{OPERATOR_NAME}}|{{GITHUB_ORG_OR_USER}}'
HITS=$(grep -rniE "$DEANON" "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "deanonymize" $([ -z "$HITS" ] && echo 0 || echo 1) "deanonymising strings:
$HITS"

# --- 5. Leaked internal paths --------------------------------------------
HITS=$(grep -rnE '(runs/exp_|code/scripts/|paper/section_|LOG\.md|REGISTRY\.md|locks/)' "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-paths" $([ -z "$HITS" ] && echo 0 || echo 1) "internal file paths in deliverable:
$HITS"

# --- 6. External-review milestone gate (final milestone only) ------------
if [ "${REQUIRE_EXTERNAL_REVIEWS:-0}" = "1" ]; then
  N_EXT=$(ls reviews/external/ 2>/dev/null | wc -l | tr -d ' ')
  gate "external-reviews" $([ "$N_EXT" -ge 1 ] && echo 0 || echo 1) \
       "no external review artifacts in reviews/external/ (brief budgets them; using them is gated, not optional)"
fi

# --- 7. Registered-number consistency (agent-extended) --------------------
# TODO(agent, during run): for each row in REGISTRY.md § Audited numbers, run
# its re-derivation script and check the prose value matches. This is the
# canonical number sweep; it runs before every review round and milestone gate.

exit $FAIL
