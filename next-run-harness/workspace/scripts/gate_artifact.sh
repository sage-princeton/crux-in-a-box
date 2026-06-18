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
DEANON='{{AGENT_NAME}}|{{OPERATOR_NAME}}'
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

# --- 6.5 Headline substance (drafting-entry gate only) -------------------
# Blocks a tautological / non-falsifiable headline from entering drafting.
# Set REQUIRE_HEADLINE_SUBSTANCE=1 at the skeleton/drafting milestone gate.
# The agent must add ONE line to REGISTRY.md, format (tokens are greppable):
#   HEADLINE-SUBSTANCE: non-vacuous=<yes|no>; not-by-construction=<yes|no|n/a>; certified-by=<subagent/LOG ref>
#   - non-vacuous=yes  ⇔ a runnable experiment could falsify the headline
#     (its pre-registered falsifier is NOT "Vacuously not triggered").
#   - not-by-construction=yes  ⇔ an INDEPENDENT isolated subagent answered
#     "is this claim true by construction given its own definitions?" → NO.
#     Required (not n/a) when the headline is a negative/impossibility result.
if [ "${REQUIRE_HEADLINE_SUBSTANCE:-0}" = "1" ]; then
  CERT=$(grep -nE '^HEADLINE-SUBSTANCE:' REGISTRY.md 2>/dev/null | head -1)
  if [ -z "$CERT" ]; then
    gate "headline-substance" 1 "no HEADLINE-SUBSTANCE certification line in REGISTRY.md — a tautological/non-falsifiable headline cannot enter drafting; resolve via playbooks/decisions.md § Stuck/Pivot"
  else
    gate "headline-substance:non-vacuous" \
      $(echo "$CERT" | grep -qE 'non-vacuous=yes' && echo 0 || echo 1) \
      "headline falsifier is vacuous/non-falsifiable (non-vacuous!=yes) — true by construction; resolve via decisions.md § Stuck/Pivot, do not draft around it"
    # If the locked headline reads as a negative/impossibility result, the
    # not-by-construction subagent certification is mandatory (no n/a).
    NEG=$(grep -niE 'Headline claim' PLAN.md 2>/dev/null \
          | grep -iE 'impossib|cannot|no such|boundary|negative result|by construction|Proposition')
    if [ -n "$NEG" ]; then
      gate "headline-substance:not-tautological" \
        $(echo "$CERT" | grep -qE 'not-by-construction=yes' && echo 0 || echo 1) \
        "negative/impossibility headline requires not-by-construction=yes (independent subagent certified NOT true-by-construction) before drafting"
    fi
  fi
fi

# --- 6.6 Exploration adequacy (drafting-entry gate) ----------------------
# Blocks PROSE drafting until exploration is critic-certified adequate.
# Set REQUIRE_EXPLORATION_ADEQUATE=1 at the dossier milestone and at the
# drafting-entry milestone (alongside REQUIRE_HEADLINE_SUBSTANCE=1). The empty
# target-format skeleton (Milestone 1) does NOT set this — it is a compiling
# shell, not prose. The agent adds ONE line to REGISTRY.md, format:
#   EXPLORATION-ADEQUATE: settled-candidates=<N>; lit-coverage=yes; certified-by=<critic/LOG ref>
#   - settled-candidates=N (N>=2) ⇔ >=2 candidates settled by a CLEAN PASS/FAIL
#     against their pre-registered criterion (a clean FAIL counts). A single-
#     viable-idea run uses 'exhaustion-memo=<id>' instead (critic-affirmed
#     honest convergence — playbooks/exploration.md §4).
#   - lit-coverage=yes ⇔ the isolated sufficiency critic returned ADEQUATE
#     (not THIN-LIT / STOP-EARLY / FABRICATED-OR-VACUOUS).
if [ "${REQUIRE_EXPLORATION_ADEQUATE:-0}" = "1" ]; then
  DOSS="exploration/DOSSIER.md"
  if [ ! -f "$DOSS" ]; then
    gate "exploration-dossier" 1 "no exploration/DOSSIER.md — drafting is gated behind the exploration dossier (playbooks/exploration.md)"
  else
    for H in "## LIT SYNTHESIS" "## HYPOTHESIS PORTFOLIO" "## DIRECTION CHOICE"; do
      if grep -qF "$H" "$DOSS"; then gate "dossier-section:$H" 0 ""; else gate "dossier-section:$H" 1 "dossier missing required section: $H"; fi
    done
  fi
  CERT=$(grep -nE '^EXPLORATION-ADEQUATE:' REGISTRY.md 2>/dev/null | head -1)
  if [ -z "$CERT" ]; then
    gate "exploration-adequate" 1 "no EXPLORATION-ADEQUATE certification in REGISTRY.md — the isolated sufficiency critic (playbooks/exploration.md §4) has not returned ADEQUATE; do not begin prose"
  else
    SETTLED=$(echo "$CERT" | sed -nE 's/.*settled-candidates=([0-9]+).*/\1/p')
    EXH=$(echo "$CERT" | grep -oE 'exhaustion-memo=[A-Za-z0-9_-]+')
    if { [ "${SETTLED:-0}" -ge 2 ] || [ -n "$EXH" ]; }; then
      gate "exploration:breadth" 0 ""
    else
      gate "exploration:breadth" 1 "fewer than 2 candidates settled by a clean PASS/FAIL and no critic-affirmed exhaustion-memo (PLAN.md scout-depth; a clean FAIL counts as settled)"
    fi
    gate "exploration:lit-coverage" \
      $(echo "$CERT" | grep -qE 'lit-coverage=yes' && echo 0 || echo 1) \
      "sufficiency critic flagged THIN-LIT/STOP-EARLY — close the named coverage/exploration gap before drafting; do not draft around it"
  fi
fi

# --- 7. Registered-number consistency (agent-extended) --------------------
# TODO(agent, during run): for each row in REGISTRY.md § Audited numbers, run
# its re-derivation script and check the prose value matches. This is the
# canonical number sweep; it runs before every review round and milestone gate.

exit $FAIL
