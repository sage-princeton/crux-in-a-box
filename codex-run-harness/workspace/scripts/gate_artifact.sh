#!/usr/bin/env bash
# gate_artifact.sh — mechanical deliverable gates (Codex-loop edition).
# Run by the agent before treating any draft as done, and by the outer-loop
# verifier after every iteration. Exit 0 = all active gates pass; failures
# print GATE FAIL lines and exit 1.
#
# This is the MECHANICAL half of the harness's quality machinery; the
# JUDGMENT half is the isolated referee the loop runs (loop/verify.sh),
# which the agent cannot author. Form/hygiene gates run always;
# REQUIRE_PRESENTATION=1 and REQUIRE_README=1 activate the ship-window gates
# (the loop sets them in POLISH mode). REQUIRE_EXTERNAL_REVIEWS=1 checks that
# a refine.ink (or other external) review artifact was actually collected.
set -u

FAIL=0
gate() { # gate <name> <0-ok|1-fail> <detail>
  if [ "$2" -ne 0 ]; then echo "GATE FAIL [$1]: $3"; FAIL=1; else echo "gate ok  [$1]"; fi
}

PDF="${1:?usage: gate_artifact.sh <path-to-pdf> [<source-dir>]}"
SRC="${2:-paper}"

# --- 1. Artifact exists and page budget ---------------------------------
PAGE_BUDGET="{{PAGE_BUDGET|9}}"   # main-body page limit
case "$PAGE_BUDGET" in (*[!0-9]*) PAGE_BUDGET=9 ;; esac
if [ ! -f "$PDF" ]; then
  gate "pdf-exists" 1 "$PDF not found"
else
  gate "pdf-exists" 0 ""
  if command -v pdfinfo >/dev/null 2>&1; then
    PAGES=$(pdfinfo "$PDF" | awk '/^Pages:/ {print $2}')
    gate "page-budget" $([ "${PAGES:-999}" -le "$PAGE_BUDGET" ] && echo 0 || echo 1) \
         "pages=$PAGES > budget=$PAGE_BUDGET"
  fi
fi

# --- 2. Unresolved placeholders -----------------------------------------
PLACEHOLDERS='\[CITE:|TODO|TKTK|% MISSING|XXX|\?\?\?'
HITS=$(grep -rnE "$PLACEHOLDERS" "$SRC" --include='*.tex' --include='*.md' 2>/dev/null | grep -v 'gate_artifact' | head -20)
gate "placeholders" $([ -z "$HITS" ] && echo 0 || echo 1) "unresolved placeholders:
$HITS"

# --- 3. Run-internal vocabulary (reads-as-internal-document) --------------
INTERNAL_VOCAB='VERIFIER_FEEDBACK|iteration [0-9]+ of|outer loop|LOG\.md|AGENTS\.md|Status: LIVE|changelog:'
HITS=$(grep -rnE "$INTERNAL_VOCAB" "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-vocab" $([ -z "$HITS" ] && echo 0 || echo 1) "run-internal vocabulary in deliverable:
$HITS"

# --- 4. Leaked internal paths --------------------------------------------
HITS=$(grep -rnE '(^|[^a-zA-Z])(runs/|code/scripts/|lit/|loop_state\.json|budget_status)' "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-paths" $([ -z "$HITS" ] && echo 0 || echo 1) "internal file paths in deliverable:
$HITS"

# --- 5. Presentation gates (POLISH window; REQUIRE_PRESENTATION=1) --------
# Mechanical checks for known revision-round failure modes: prose that
# rots into an over-long abstract, ALL-CAPS emphasis, and a figureless main
# body. Not always-on: a skeleton or early draft would fail them spuriously.
if [ "${REQUIRE_PRESENTATION:-0}" = "1" ]; then
  ABSTRACT_WORD_CAP="{{ABSTRACT_WORD_CAP|200}}"
  case "$ABSTRACT_WORD_CAP" in (*[!0-9]*) ABSTRACT_WORD_CAP=200 ;; esac
  ABS_FILE=$(grep -rlE '\\begin\{abstract\}' "$SRC" --include='*.tex' 2>/dev/null | head -1)
  if [ -z "$ABS_FILE" ]; then
    gate "abstract-exists" 1 "no \\begin{abstract} found under $SRC"
  else
    ABS_WORDS=$(sed -n '/\\begin{abstract}/,/\\end{abstract}/p' "$ABS_FILE" \
      | grep -v '^[[:space:]]*%' | sed -e 's/\\begin{abstract}//' -e 's/\\end{abstract}//' \
      | sed 's/\\[a-zA-Z]*//g' | wc -w | tr -d ' ')
    gate "abstract-length" $([ "${ABS_WORDS:-999}" -le "$ABSTRACT_WORD_CAP" ] && echo 0 || echo 1) \
      "abstract is ${ABS_WORDS} words > cap=${ABSTRACT_WORD_CAP} — rewrite to 5-6 sentences; losing nuance here is fine"
  fi
  ALLCAPS_ALLOW='NEURIPS|GITHUB|LICENSE|DATASET[S]?:|HTTPS?'
  HITS=$(grep -rnE '\b[A-Z]{6,}\b' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -v ':[[:space:]]*%' | grep -vE "$ALLCAPS_ALLOW" | head -20)
  gate "allcaps-prose" $([ -z "$HITS" ] && echo 0 || echo 1) "ALL-CAPS emphasis in deliverable prose (restate calmly at the strength the evidence licenses):
$HITS"
  N_FIG=$(grep -rlE '\\begin\{figure' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -viE 'appendix|supplement' | wc -l | tr -d ' ')
  gate "figure-in-body" $([ "${N_FIG:-0}" -ge 1 ] && echo 0 || echo 1) \
    "no figure environment in any main-body .tex — build Figure 1 from cached artifacts; readers spend ~30% of attention on figures"
fi

# --- 6. External-review gate (REQUIRE_EXTERNAL_REVIEWS=1) -----------------
if [ "${REQUIRE_EXTERNAL_REVIEWS:-0}" = "1" ]; then
  N_EXT=$(ls reviews/external/ 2>/dev/null | wc -l | tr -d ' ')
  gate "external-reviews" $([ "$N_EXT" -ge 1 ] && echo 0 || echo 1) \
       "no external review artifacts in reviews/external/ — the refine.ink credit exists to be used (AGENTS.md § External review)"
fi

# --- 7. Final README (REQUIRE_README=1; POLISH window) ---------------------
if [ "${REQUIRE_README:-0}" = "1" ]; then
  R="README.md"
  if [ ! -s "$R" ]; then
    gate "readme-exists" 1 "no $R at repo root — write the final README (polish protocol) before the run ends"
  else
    gate "readme-exists" 0 ""
    R_WORDS=$(wc -w < "$R" | tr -d ' ')
    gate "readme-substantive" $([ "${R_WORDS:-0}" -ge 150 ] && echo 0 || echo 1) \
      "README is ${R_WORDS} words — too thin to orient a cold visitor (needs: what+finding, paper path, repo map, repro command, data provenance)"
    gate "readme-repro" $(grep -iqE 'reproduc|repro' "$R" && echo 0 || echo 1) \
      "README has no reproduction section — a visitor must be able to rerun the headline result from a fresh clone"
    HITS=$( { grep -nE "$INTERNAL_VOCAB" "$R"; grep -nE '\b[A-Z]{6,}\b' "$R" | grep -vE "${ALLCAPS_ALLOW:-NEURIPS|GITHUB|LICENSE|HTTPS?}"; } 2>/dev/null | head -10)
    gate "readme-vocabulary" $([ -z "$HITS" ] && echo 0 || echo 1) "internal vocabulary or ALL-CAPS in README:
$HITS"
  fi
fi

exit $FAIL
