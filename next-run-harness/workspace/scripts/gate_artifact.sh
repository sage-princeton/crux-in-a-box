#!/usr/bin/env bash
# gate_artifact.sh — mechanical deliverable gates: cooperative, and a critical few.
# Run before each blind-review round and at every PLAN.md milestone gate.
# Exit 0 = all active gates pass. Any failure prints GATE FAIL lines, exits 1.
#
# FORM/HYGIENE gates run always; the two SHIP substance gates read the ISOLATED
# critic's own artifact and the budget lock DIRECTLY — never an agent-typed
# certification string, so there is no self-typed token in the loop. The gates are
# cooperative: the agent is trusted to run this and honor the exit code
# (OPERATOR_GUIDE.md § enforcement). Add gates only via a Tier-2 memo.
set -u

FAIL=0
gate() { # gate <name> <0-ok|1-fail> <detail>
  if [ "$2" -ne 0 ]; then echo "GATE FAIL [$1]: $3"; FAIL=1; else echo "gate ok  [$1]"; fi
}

PDF="${1:?usage: gate_artifact.sh <path-to-pdf> [<source-dir>]}"
SRC="${2:-paper}"

# --- 1. Artifact exists and page budget ---------------------------------
PAGE_BUDGET="{{PAGE_BUDGET|9}}"   # main-body page limit from BRIEF.md
case "$PAGE_BUDGET" in (*[!0-9]*) PAGE_BUDGET=9 ;; esac   # unresolved placeholder -> safe default
if [ ! -f "$PDF" ]; then
  gate "pdf-exists" 1 "$PDF not found"
else
  gate "pdf-exists" 0 ""
  if command -v pdfinfo >/dev/null 2>&1; then
    PAGES=$(pdfinfo "$PDF" | awk '/^Pages:/ {print $2}')
    # TODO(task-specific): if refs/appendix are exempt, count main-body pages.
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

# --- 5b. Presentation gates (Presentation Overhaul + Ship milestones) -----
# Mechanical checks for the documented revision-round failure: prose that rots
# into an over-long abstract, ALL-CAPS emphasis, and a figureless main body.
# These run under REQUIRE_PRESENTATION=1 (not always-on: a skeleton or early
# draft would fail them spuriously). The cold-read acceptance test in
# playbooks/writing.md § Presentation overhaul is the judgment half; these are
# the grep half. Reviewers are never told about these (reviewers grade,
# scripts verify).
if [ "${REQUIRE_PRESENTATION:-0}" = "1" ]; then
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
      "abstract is ${ABS_WORDS} words > cap=${ABSTRACT_WORD_CAP} — rewrite to the 5-6 sentence-role spec (playbooks/writing.md); losing nuance here is fine"
  fi
  # (b) No ALL-CAPS emphasis in prose. >=6 consecutive capitals is almost never
  # a real acronym; the allowlist below holds the known legit ones. Extending
  # the allowlist is a Tier-2 memo, same as any gate change.
  ALLCAPS_ALLOW='NEURIPS|GITHUB|LICENSE|DATASET[S]?:|HTTPS?'
  HITS=$(grep -rnE '\b[A-Z]{6,}\b' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -v ':[[:space:]]*%' | grep -vE "$ALLCAPS_ALLOW" | head -20)
  gate "allcaps-prose" $([ -z "$HITS" ] && echo 0 || echo 1) "ALL-CAPS emphasis in deliverable prose (restate calmly at the strength the registry licenses):
$HITS"
  # (c) At least one figure in the MAIN BODY (appendix figures don't count).
  N_FIG=$(grep -rlE '\\begin\{figure' "$SRC" --include='*.tex' 2>/dev/null \
    | grep -viE 'appendix|supplement' | wc -l | tr -d ' ')
  gate "figure-in-body" $([ "${N_FIG:-0}" -ge 1 ] && echo 0 || echo 1) \
    "no figure environment in any main-body .tex — readers spend ~30% of attention on figures (playbooks/writing.md effort allocation); build Figure 1 per playbooks/figures.md from cached artifacts"
fi

# --- 6. External-review milestone gate (final milestone only) ------------
if [ "${REQUIRE_EXTERNAL_REVIEWS:-0}" = "1" ]; then
  N_EXT=$(ls reviews/external/ 2>/dev/null | wc -l | tr -d ' ')
  gate "external-reviews" $([ "$N_EXT" -ge 1 ] && echo 0 || echo 1) \
       "no external review artifacts in reviews/external/ (the brief budgets them; using them is a milestone, not optional)"
fi

# --- 7. Evidence adequacy (final/ship gate) ------------------------------
# The headline must be powered, not a single cell. Read the ISOLATED ship-time
# power critic's OWN file directly (playbooks/review.md §2d) — no agent-typed
# REGISTRY cert line. The critic writes its verdict and the seed/cell counts it
# found against the delivered artifacts; the gate honors that artifact and checks
# the counts against locks/evidence_floors.json. A missing critic file or a
# non-ADEQUATE verdict fails — the agent must run the critic, not type a token.
# A negative/impossibility headline faces the SAME floor as a positive one.
if [ "${REQUIRE_EVIDENCE_ADEQUATE:-0}" = "1" ]; then
  SEED_FLOOR=3; CELL_FLOOR=2
  if [ -f locks/evidence_floors.json ]; then
    _sf=$(python3 -c 'import json;print(json.load(open("locks/evidence_floors.json")).get("seed_floor",3))' 2>/dev/null)
    _cf=$(python3 -c 'import json;print(json.load(open("locks/evidence_floors.json")).get("cell_floor",2))' 2>/dev/null)
    case "${_sf:-}" in (''|*[!0-9]*) : ;; (*) SEED_FLOOR="$_sf" ;; esac
    case "${_cf:-}" in (''|*[!0-9]*) : ;; (*) CELL_FLOOR="$_cf" ;; esac
  fi
  PC="reviews/power_critic_ship.md"
  if [ ! -f "$PC" ] || [ ! -s "$PC" ]; then
    gate "evidence-adequate" 1 "no ship-time power critic at $PC — spawn the isolated power/evidence-adequacy critic (review.md §2d) against the delivered artifacts before shipping"
  else
    if grep -iqE '(verdict|recommendation)[[:space:]]*:[[:space:]]*adequate' "$PC"; then
      gate "evidence-adequate:verdict" 0 ""
    else
      gate "evidence-adequate:verdict" 1 "$PC verdict is not ADEQUATE (e.g. UNDERPOWERED / SINGLE-POINT / SCALE-INCONSISTENT) — power up the headline or route to Stuck/Pivot; it does not ship underpowered (BRIEF.md evidence floor)"
    fi
    SEEDS=$(grep -oiE 'seeds[=: ]+[0-9]+' "$PC" | grep -oE '[0-9]+' | head -1)
    CELLS=$(grep -oiE '(load-bearing-)?cells[=: ]+[0-9]+' "$PC" | grep -oE '[0-9]+' | head -1)
    gate "evidence-adequate:seeds" $([ "${SEEDS:-0}" -ge "$SEED_FLOOR" ] 2>/dev/null && echo 0 || echo 1) \
      "power critic reports seeds=${SEEDS:-unstated} < floor=$SEED_FLOOR (locks/evidence_floors.json) — the critic must state 'seeds=<N>' and meet the floor"
    gate "evidence-adequate:cells" $([ "${CELLS:-0}" -ge "$CELL_FLOOR" ] 2>/dev/null && echo 0 || echo 1) \
      "power critic reports cells=${CELLS:-unstated} < floor=$CELL_FLOOR (locks/evidence_floors.json) — the critic must state 'cells=<M>' and meet the floor"
  fi
fi

# --- 8. Ship authorization (final/ship gate) — light under-spend backstop -
# Blocks an early below-bar ship while budget+time remain. Cooperative and light —
# the felt-budget heuristic (AGENTS.md § Resources; HEARTBEAT.md burn-rate) does
# the real work; this is only the backstop. Passes if ANY holds:
#   (a) reviews/final_review.md records Weak Accept or higher (success); OR
#   (b) locks/budget.json + telemetry show a cap genuinely (near-)exhausted; OR
#   (c) LOG.md carries an honest ship / under-spend justification memo.
# Only below-bar + budget-remaining + no-memo fails. "Unknown budget" does NOT
# hard-block — the memo path is always open to an honest, logged call.
if [ "${REQUIRE_SHIP_AUTHORIZATION:-0}" = "1" ]; then
  BARMET=1
  if [ -f reviews/final_review.md ] \
     && grep -iqE '(recommendation|verdict)[[:space:]]*:.*accept' reviews/final_review.md \
     && ! grep -iqE '(recommendation|verdict)[[:space:]]*:.*(reject|borderline)' reviews/final_review.md; then
    BARMET=0
  fi
  SHIPMEMO=1
  if [ -f LOG.md ] && grep -iqE '(ship.{0,20}justif|justif.{0,20}ship|below.bar ship|under.?spend)' LOG.md; then SHIPMEMO=0; fi
  UNDERSPENT="unknown"
  if [ -f locks/budget.json ]; then
    UNDERSPENT=$(python3 - <<'PY' 2>/dev/null || echo unknown
import json, subprocess, sys
from datetime import datetime, timezone
try:
    b = json.load(open("locks/budget.json"))
    dls = str(b.get("deadline_iso", "")); lns = str(b.get("launch_iso", "")); ab = b.get("api_budget")
    if not dls or not lns or "FILL_AT_HOUR_0" in (dls, lns) or ab in (None, 0, ""):
        print("unknown"); sys.exit(0)
    dl = datetime.fromisoformat(dls); ln = datetime.fromisoformat(lns)
    if dl.tzinfo is None: dl = dl.replace(tzinfo=timezone.utc)
    if ln.tzinfo is None: ln = ln.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    rt = (dl - now).total_seconds() / max(1.0, (dl - ln).total_seconds())
    tf = float(b.get("underspend_time_floor", 0.5)); bf = float(b.get("underspend_budget_floor", 0.4))
    out = subprocess.run([sys.executable, "scripts/telemetry_costs.py"], capture_output=True, text=True, timeout=40)
    rb = 1.0 - float(out.stdout.strip().lstrip("$")) / float(ab)
    print("yes" if (rt > tf and rb > bf) else "no")
except Exception:
    print("unknown")
PY
)
  fi
  if [ "$BARMET" -eq 0 ]; then
    gate "ship-authorization" 0 ""
  elif [ "$SHIPMEMO" -eq 0 ]; then
    gate "ship-authorization" 0 ""   # honest below-bar / under-spend call, logged in LOG.md
  elif [ "$UNDERSPENT" = "no" ]; then
    gate "ship-authorization" 0 ""   # a cap is genuinely (near-)exhausted
  else
    gate "ship-authorization" 1 "below-bar ship with budget/time apparently remaining (under-spent=$UNDERSPENT) and no ship-justification memo in LOG.md. Deploy the budget on stronger evidence and keep working (BRIEF.md success bar; HEARTBEAT.md under-spend), reach Weak Accept+ in reviews/final_review.md, or log an honest ship/under-spend justification memo."
  fi
fi

# --- 9. Final README (ship gate) ------------------------------------------
# The repo's front door for a cold visitor — written LAST, after the
# Presentation Overhaul, committed as the run's final commit
# (playbooks/writing.md § The final README). Mechanical half only; the
# cold-reader acceptance lives in the playbook.
if [ "${REQUIRE_README:-0}" = "1" ]; then
  R="README.md"
  if [ ! -s "$R" ]; then
    gate "readme-exists" 1 "no $R at repo root — write the final README (playbooks/writing.md § The final README) before the completion report"
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
