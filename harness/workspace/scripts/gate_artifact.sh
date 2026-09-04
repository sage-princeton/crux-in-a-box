#!/usr/bin/env bash
# gate_artifact.sh — mechanical deliverable checks.
#
# Run before every review round:        scripts/gate_artifact.sh <pdf> [<src-dir>]
# Run during the final pass:            FINAL=1 scripts/gate_artifact.sh <pdf> [<src-dir>]
#
# Cooperative: run it and honor the exit code. Exit 0 = all active checks pass;
# failures print GATE FAIL lines and exit 1. These checks are mechanical form
# checks only — quality is judged by the isolated reviewers (AGENTS.md § Reviews),
# never by this script, and reviewers are never told about these checks.
#
# Three of the final-pass checks are switchable per run — float captions, the
# replication package, external reviews — because whether they apply depends on
# what the operator provisioned, not on the paper. Each is on or off via a
# REQUIRE_* flag whose default is set at configure time; a switched-off check
# prints a `gate skip [name]` line so the record shows it was not run rather
# than that it passed. The same-named env var overrides the configured default.
#
# Run from the workspace root (the project repo root): the FINAL checks resolve
# reviews/external/, replication/, results.html, and README.md relative to the
# current dir.
set -u

FAIL=0
gate() { # gate <name> <0-ok|1-fail> <detail>
  if [ "$2" -ne 0 ]; then echo "GATE FAIL [$1]: $3"; FAIL=1; else echo "gate ok  [$1]"; fi
}
skip() { # skip <name> <why> — the check did not run; say so, do not count it as a pass
  echo "gate skip [$1]: $2"
}
flag_on() { # flag_on <value> — 1/on/true/yes (any case) means on; anything else is off
  case "$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z')" in
    1|on|true|yes) return 0 ;;
    *) return 1 ;;
  esac
}

PDF="${1:?usage: [FINAL=1] gate_artifact.sh <path-to-pdf> [<source-dir>]}"
SRC="${2:-paper}"

# --- 1. PDF exists and fits the page budget ------------------------------
# PAGE_BUDGET caps the MAIN BODY; references/appendices get BACKMATTER_ALLOWANCE
# on top, because pdfinfo can only count total pages. The main-body cap itself
# is verified by eye at the final pass — this check only catches runaway length.
PAGE_BUDGET="{{PAGE_BUDGET|9}}"
BACKMATTER_ALLOWANCE="{{BACKMATTER_ALLOWANCE|15}}"
case "$PAGE_BUDGET" in (*[!0-9]*) PAGE_BUDGET=9 ;; esac
case "$BACKMATTER_ALLOWANCE" in (*[!0-9]*) BACKMATTER_ALLOWANCE=15 ;; esac
PAGE_CEILING=$((PAGE_BUDGET + BACKMATTER_ALLOWANCE))
if [ ! -f "$PDF" ]; then
  gate "pdf-exists" 1 "$PDF not found"
else
  gate "pdf-exists" 0 ""
  if command -v pdfinfo >/dev/null 2>&1; then
    PAGES=$(pdfinfo "$PDF" | awk '/^Pages:/ {print $2}')
    gate "page-budget" $([ "${PAGES:-999}" -le "$PAGE_CEILING" ] && echo 0 || echo 1) \
         "total pages=$PAGES > ceiling=$PAGE_CEILING (main body ≤$PAGE_BUDGET + backmatter ≤$BACKMATTER_ALLOWANCE); the main-body cap is checked by eye at the final pass"
  fi
fi

# --- 2. Unresolved placeholders ------------------------------------------
PLACEHOLDERS='\[CITE:|TODO|TKTK|% MISSING|XXX|\?\?\?'
HITS=$(grep -rnE "$PLACEHOLDERS" "$SRC" --include='*.tex' --include='*.md' 2>/dev/null | grep -v 'gate_artifact' | head -20)
gate "placeholders" $([ -z "$HITS" ] && echo 0 || echo 1) "unresolved placeholders:
$HITS"

# --- 3. Internal vocabulary (reads as an internal document) --------------
INTERNAL_VOCAB='AGENTS\.md|PLAN\.md|LOG\.md|HEARTBEAT|SNAPSHOTS\.md|COMPLETION_REPORT|review_blind|budget_status|resource ledger|work in flight'
HITS=$(grep -rnE "$INTERNAL_VOCAB" "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-vocab" $([ -z "$HITS" ] && echo 0 || echo 1) "internal vocabulary in deliverable:
$HITS"

# --- 4. Deanonymization --------------------------------------------------
# Whole-word match on the resolved agent/operator names. Names that are common
# English words (e.g. a generic default like "operator") would false-positive
# on ordinary prose, so those are skipped — pick distinctive names if you want
# this check to bite.
HITS=""
for NAME in '{{AGENT_NAME}}' '{{OPERATOR_NAME}}'; do
  case "$(printf '%s' "$NAME" | tr 'A-Z' 'a-z')" in
    crux|operator|agent|assistant|user|admin) continue ;;
  esac
  H=$(grep -rniE "\b$NAME\b" "$SRC" --include='*.tex' 2>/dev/null | head -10)
  [ -n "$H" ] && HITS="$HITS
$H"
done
gate "deanonymize" $([ -z "$HITS" ] && echo 0 || echo 1) "deanonymizing strings:
$HITS"

# --- 5. Leaked internal paths --------------------------------------------
HITS=$(grep -rnE '(^|[^a-zA-Z])(runs/|work/|code/scripts/|reviews/|scripts/telemetry)' "$SRC" --include='*.tex' 2>/dev/null | head -20)
gate "internal-paths" $([ -z "$HITS" ] && echo 0 || echo 1) "internal file paths in deliverable:
$HITS"

# =========================================================================
# FINAL=1 — the final-pass checks (presentation, external reviews, README,
# accessible results page). Not always-on: a skeleton or early draft would
# fail them spuriously.
# =========================================================================
if [ "${FINAL:-0}" = "1" ]; then

  # The switchable checks. Configured defaults; the env var of the same name
  # overrides (FINAL=1 REQUIRE_EXTERNAL_REVIEWS=1 scripts/gate_artifact.sh …).
  REQUIRE_FLOAT_CAPTIONS="${REQUIRE_FLOAT_CAPTIONS:-{{REQUIRE_FLOAT_CAPTIONS|1}}}"
  REQUIRE_REPLICATION_PACKAGE="${REQUIRE_REPLICATION_PACKAGE:-{{REQUIRE_REPLICATION_PACKAGE|1}}}"
  REQUIRE_EXTERNAL_REVIEWS="${REQUIRE_EXTERNAL_REVIEWS:-{{REQUIRE_EXTERNAL_REVIEWS|0}}}"

  # (a) Abstract exists and fits the word cap.
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
      "abstract is ${ABS_WORDS} words > cap=${ABSTRACT_WORD_CAP} — rewrite to 5-6 plain sentences; losing nuance here is fine"
  fi

  # (b) No ALL-CAPS emphasis in prose (>=6 consecutive capitals is almost
  # never a real acronym; the allowlist holds the known legitimate ones,
  # including the target venue's name uppercased).
  VENUE_UC=$(printf '%s' "{{VENUE|NeurIPS}}" | tr 'a-z' 'A-Z' | tr -cd 'A-Z')
  ALLCAPS_ALLOW="${VENUE_UC:-NEURIPS}|GITHUB|LICENSE|DATASET[S]?:"
  HITS=$(grep -rnE '\b[A-Z]{6,}\b' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -v ':[[:space:]]*%' | grep -vE "$ALLCAPS_ALLOW" | head -20)
  gate "allcaps-prose" $([ -z "$HITS" ] && echo 0 || echo 1) "ALL-CAPS emphasis in prose (restate calmly at the strength the evidence supports):
$HITS"

  # (b2) Main-body boundary: the page where the references begin must fall
  # within PAGE_BUDGET+1 (references may start at the top of the page after
  # the main body ends). This is the mechanical check on the main-body cap
  # that the always-on total-page ceiling cannot make.
  if command -v pdftotext >/dev/null 2>&1 && [ -f "$PDF" ]; then
    MAXP="${PAGES:-40}"; [ "$MAXP" -gt 40 ] 2>/dev/null && MAXP=40
    REF_PAGE=""
    P=1
    while [ "$P" -le "$MAXP" ]; do
      if pdftotext -f "$P" -l "$P" -layout "$PDF" - 2>/dev/null \
         | grep -qiE '^[[:space:]]*([0-9]+[[:space:]]+)?(References|Bibliography)[[:space:]]*$'; then
        REF_PAGE=$P; break
      fi
      P=$((P+1))
    done
    if [ -n "$REF_PAGE" ]; then
      gate "main-body-pages" $([ "$REF_PAGE" -le $((PAGE_BUDGET + 1)) ] && echo 0 || echo 1) \
        "references begin on page $REF_PAGE — the main body appears to exceed the ${PAGE_BUDGET}-page cap (it must end by page $PAGE_BUDGET; references may start on page $((PAGE_BUDGET+1)))"
    else
      gate "main-body-pages" 1 "no References/Bibliography heading found in the first $MAXP pages — cannot locate the main-body boundary; verify the main-body page cap by eye and fix the references heading"
    fi
  fi

  # (c) At least one figure in the main body (appendix figures don't count).
  N_FIG=$(grep -rlE '\\begin\{figure' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -viE 'appendix|supplement' | wc -l | tr -d ' ')
  gate "figure-in-body" $([ "${N_FIG:-0}" -ge 1 ] && echo 0 || echo 1) \
    "no figure environment in any main-body .tex — build Figure 1 from cached results; readers spend much of their attention on figures"

  # (c2) Every float carries a caption. A table or figure the reader cannot
  # interpret without hunting through the body text is one they will skip.
  # Comment lines are dropped before matching so a commented-out float does
  # not count; the environment names are matched with bracket expressions so
  # the same program runs under any awk.
  if flag_on "$REQUIRE_FLOAT_CAPTIONS"; then
    HITS=$(find "$SRC" -type f -name '*.tex' -exec awk '
      FNR == 1 { inenv = 0 }
      {
        l = $0
        sub(/^[ \t]*%.*/, "", l)
        if (l ~ /\\begin[{](figure|table|sidewaysfigure|sidewaystable|longtable|threeparttable)[*]?[}]/) {
          inenv = 1; cap = 0; sf = FILENAME; sl = FNR
        }
        if (inenv && l ~ /\\caption/) cap = 1
        if (inenv && l ~ /\\end[{](figure|table|sidewaysfigure|sidewaystable|longtable|threeparttable)[*]?[}]/) {
          if (!cap) printf "%s:%d: float opened here has no \\caption\n", sf, sl
          inenv = 0
        }
      }' {} + 2>/dev/null | head -20)
    gate "float-captions" $([ -z "$HITS" ] && echo 0 || echo 1) "float without a caption — every table and figure must be readable from its caption alone (what the units are, what the sample is, what the reader should see):
$HITS"
  else
    skip "float-captions" "REQUIRE_FLOAT_CAPTIONS=$REQUIRE_FLOAT_CAPTIONS"
  fi

  # (d) External review artifacts were actually collected — one per reviewer.
  # Only when the external reviewers are provisioned for this run
  # (AGENTS.md § Reviews); otherwise the requirement does not apply.
  if flag_on "$REQUIRE_EXTERNAL_REVIEWS"; then
    N_EXT=$(ls reviews/external/ 2>/dev/null | wc -l | tr -d ' ')
    gate "external-reviews" $([ "$N_EXT" -ge 2 ] && echo 0 || echo 1) \
         "reviews/external/ holds $N_EXT artifact(s); both external reviewers are required before completion (AGENTS.md § Reviews). If a reviewer platform is genuinely broken, the outage and the attempt must be in LOG.md and the completion report"
  else
    skip "external-reviews" "REQUIRE_EXTERNAL_REVIEWS=$REQUIRE_EXTERNAL_REVIEWS (external reviewers not provisioned for this run)"
  fi

  # (d2) The replication package: a directory, a README describing it, and a
  # master script that regenerates every exhibit. No particular deposit format
  # is required, only that a stranger can run it.
  if flag_on "$REQUIRE_REPLICATION_PACKAGE"; then
    RD="replication"
    if [ ! -d "$RD" ]; then
      gate "replication-package" 1 "no $RD/ directory at the repo root — the package a reader can run is part of the deliverable, not an optional extra"
    else
      gate "replication-package" 0 ""
      RREADME=$(find "$RD" -maxdepth 2 -type f -iname 'README*' 2>/dev/null | head -1)
      if [ -z "$RREADME" ] || [ ! -s "$RREADME" ]; then
        gate "replication-readme" 1 "no non-empty README under $RD/ — it must name every input dataset with its source and vintage, state which inputs cannot be redistributed and how to obtain them, list the software versions, give the run order, and state the expected runtime"
      else
        case "$RREADME" in
          *.pdf|*.PDF) gate "replication-readme" 0 "" ;;
          *)
            RR_WORDS=$(wc -w < "$RREADME" | tr -d ' ')
            gate "replication-readme" $([ "${RR_WORDS:-0}" -ge 100 ] && echo 0 || echo 1) \
              "$RREADME is ${RR_WORDS} words — too thin to describe the package (inputs with source and vintage, restricted-access inputs and how to get them, software versions, run order, expected runtime)"
            ;;
        esac
      fi
      MASTER=$(find "$RD" -maxdepth 2 -type f \
        \( -iname 'run_all*' -o -iname 'runall*' -o -iname 'master*' -o -iname 'make_all*' \
           -o -iname 'reproduce*' -o -iname 'replicate*' \
           -o -iname 'Makefile' -o -iname 'main.sh' -o -iname 'main.py' -o -iname 'main.R' \) \
        2>/dev/null | head -1)
      gate "replication-master-script" $([ -n "$MASTER" ] && [ -s "$MASTER" ] && echo 0 || echo 1) \
        "no non-empty master script under $RD/ (looked for run_all*, master*, make_all*, reproduce*, replicate*, Makefile, main.{sh,py,R}) — one entry point must regenerate every table and figure in the paper, in order, from the staged inputs"
    fi
  else
    skip "replication-package" "REQUIRE_REPLICATION_PACKAGE=$REQUIRE_REPLICATION_PACKAGE"
    skip "replication-readme" "REQUIRE_REPLICATION_PACKAGE=$REQUIRE_REPLICATION_PACKAGE"
    skip "replication-master-script" "REQUIRE_REPLICATION_PACKAGE=$REQUIRE_REPLICATION_PACKAGE"
  fi

  # (e) The accessible results page exists and is self-contained.
  H="results.html"
  if [ ! -s "$H" ]; then
    gate "results-html" 1 "no $H at repo root — build the accessible results page (final pass, step 2)"
  else
    gate "results-html" 0 ""
    H_WORDS=$(wc -w < "$H" | tr -d ' ')
    gate "results-html-substantive" $([ "${H_WORDS:-0}" -ge 200 ] && echo 0 || echo 1) \
      "$H is ${H_WORDS} words — too thin to present the results accessibly"
    EXT=$(grep -nE '(src|href)=["'\'']https?://' "$H" 2>/dev/null | grep -viE 'href=["'\'']https?://(arxiv|github|doi)' | head -5)
    gate "results-html-selfcontained" $([ -z "$EXT" ] && echo 0 || echo 1) "$H loads external resources (inline CSS and embed images instead):
$EXT"
  fi

  # (f) Final README: exists, substantive, has a reproduction section, clean.
  R="README.md"
  if [ ! -s "$R" ]; then
    gate "readme-exists" 1 "no $R at repo root — write the final README (final pass, step 3)"
  else
    gate "readme-exists" 0 ""
    R_WORDS=$(wc -w < "$R" | tr -d ' ')
    gate "readme-substantive" $([ "${R_WORDS:-0}" -ge 150 ] && echo 0 || echo 1) \
      "README is ${R_WORDS} words — too thin to orient a cold visitor (needs: what+finding, paper path, repo map, repro command, data provenance)"
    gate "readme-repro" $(grep -iqE 'reproduc|repro' "$R" && echo 0 || echo 1) \
      "README has no reproduction section — a visitor must be able to rerun the headline result from a fresh clone"
    HITS=$( { grep -nE "$INTERNAL_VOCAB" "$R"; grep -nE '\b[A-Z]{6,}\b' "$R" | grep -vE "$ALLCAPS_ALLOW"; } 2>/dev/null | head -10)
    gate "readme-vocabulary" $([ -z "$HITS" ] && echo 0 || echo 1) "internal vocabulary or ALL-CAPS in README:
$HITS"
  fi
fi

exit $FAIL
