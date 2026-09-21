#!/usr/bin/env python3
"""Test the referee-rounds collector and builder.

Fixtures: three reviews in the three RATINGS shapes the harness brief yields
(bold bullets with an Overall label; a table; plain bullets with a bare
"3/6." and the label only on the Recommendation line), timed from a LOG.md.
Real data: the codex-astra export (git-timed rounds 2–12) and the
codex-influence collection (log-timed rounds 1–7) when present.

Run from the repo root: python3 analysis/skills/figure-referee-rounds/test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "_lib"))
from figstyle import find_chrome, png_size  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


R1 = """## 1. VERDICT-DETERMINING ISSUES

1. **MAJOR — The headline claim rests on one model family.**
   **Grounding:** §4.
2. **MODERATE — Prompts are not fully disclosed.**

## 2. WHAT THE PAPER CONTRIBUTES

Text.

## Minor (fix after, never instead of, the issues above)

- Figure 1 label.
- Typo in §3.

## 5. QUESTIONS

1. Does it replicate?
2. What about the second family?
3. Third question?

## 6. RATINGS

- **Soundness: 3/4.** Solid.
- **Presentation: 2/4.** Dense.
- **Contribution: 3/4.** Useful.
- **Overall: 4/6 — Borderline Accept.** On balance.
- **Confidence: 4/5.**

## 7. RECOMMENDATION

Recommendation: Borderline Accept
"""

R2 = """## 1. VERDICT-DETERMINING ISSUES

1. **FATAL — The central claim is not supported.**

## 6. RATINGS

| Axis | Score | Justification |
|---|---|---|
| Soundness | **1/4** | Not supported. |
| Presentation | **3/4** | Clear. |
| Contribution | **2/4** | Incremental. |
| Overall | **2/6 — Reject** | Serious flaw. |
| Confidence | **5/5** | Certain. |

## 7. RECOMMENDATION

Recommendation: Reject
"""

R3 = """1. VERDICT-DETERMINING ISSUES

1. MODERATE — A real concern.
2. MODERATE — Another.
3. MAJOR — A serious one.

6. RATINGS

- Soundness: 3/4. Fine.
- Presentation: 3/4. Fine.
- Contribution: 2/4. Incremental.
- Overall: 3/6. The weaknesses outweigh the contribution.
- Confidence: 4/5.

7. RECOMMENDATION

Recommendation: Borderline Reject
"""

LOG = """# LOG.md

### 2026-01-01 12:00 — Launch
start

### 2026-01-01 15:00 — First review
Read reviews/blind_round_1.md: Borderline Accept.

### 2026-01-01 18:30 — Second and third reviews
blind_round_2 and blind round 3 came back.
"""

with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
    tdp = Path(td)
    rv = tdp / "reviews"
    rv.mkdir()
    (rv / "blind_round_1.md").write_text(R1)
    (rv / "blind_round_2.md").write_text(R2)
    (rv / "blind_round_3.md").write_text(R3)
    (rv / "harmbench_source_audit.md").write_text("not a round\n")
    (tdp / "LOG.md").write_text(LOG)
    out = run([str(HERE / "collect.py"), "--reviews-dir", str(rv), "--log", str(tdp / "LOG.md"),
               "--start", "2026-01-01T12:00:00Z", "--label", "Fixture", "--out", str(tdp / "reviews.json")])
    check(out.returncode == 0, f"collect (fixture) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.load(open(tdp / "reviews.json"))
        r = {x["round"]: x for x in d["rounds"]}
        check(sorted(r) == [1, 2, 3], f"rounds discovered {sorted(r)} != [1, 2, 3]")
        check(d["facet_order"] == ["soundness", "presentation", "contribution"], f"facet order {d['facet_order']}")
        check(r[1]["facets"] == {"soundness": 3, "presentation": 2, "contribution": 3}, f"R1 facets {r[1]['facets']}")
        check(r[1]["overall"] == 4 and r[1]["overall_label"] == "Borderline Accept" and r[1]["confidence"] == 4,
              f"R1 overall {r[1]['overall']} {r[1]['overall_label']!r} conf {r[1]['confidence']}")
        check(r[1]["issues"] == {"fatal": 0, "major": 1, "moderate": 1}, f"R1 issues {r[1]['issues']}")
        check(r[1]["n_minor"] == 2 and r[1]["n_questions"] == 3, f"R1 minor/questions {r[1]['n_minor']}/{r[1]['n_questions']}")
        check(r[2]["facets"] == {"soundness": 1, "presentation": 3, "contribution": 2}, f"R2 (table) facets {r[2]['facets']}")
        check(r[2]["overall"] == 2 and r[2]["overall_label"] == "Reject" and r[2]["confidence"] == 5,
              f"R2 (table) overall {r[2]['overall']} {r[2]['overall_label']!r}")
        check(r[2]["issues"]["fatal"] == 1, f"R2 issues {r[2]['issues']}")
        check(r[3]["facets"] == {"soundness": 3, "presentation": 3, "contribution": 2}, f"R3 (plain) facets {r[3]['facets']}")
        check(r[3]["overall"] == 3 and r[3]["overall_label"] == "Borderline Reject",
              f"R3 bare '3/6.' must take its label from the Recommendation line, got {r[3]['overall_label']!r}")
        check(r[3]["issues"] == {"fatal": 0, "major": 1, "moderate": 2}, f"R3 issues {r[3]['issues']}")
        check(r[1]["time_source"] == "log" and r[1]["hour"] == 3.0, f"R1 time {r[1]['time_source']} {r[1]['hour']}")
        check(r[3]["time_source"] == "log" and r[3]["hour"] == 6.5, f"R3 time {r[3]['time_source']} {r[3]['hour']}")
        check(d["overall_scale"] == 6 and d["facet_scale"] == 4 and d["confidence_scale"] == 5, "scales wrong")
        check(not d["warnings"], f"unexpected warnings {d['warnings']}")
        print(f"[figure-referee-rounds] fixture: {len(d['rounds'])} rounds, facets {d['facet_order']}, "
              f"times {[x['time_source'] for x in d['rounds']]}")
    # times override wins over the log
    (tdp / "times.json").write_text(json.dumps({"2": "2026-01-01T20:00:00Z"}))
    out = run([str(HERE / "collect.py"), "--reviews-dir", str(rv), "--log", str(tdp / "LOG.md"),
               "--times", str(tdp / "times.json"), "--start", "2026-01-01T12:00:00Z"])
    if out.returncode == 0:
        r = {x["round"]: x for x in json.loads(out.stdout)["rounds"]}
        check(r[2]["time_source"] == "times" and r[2]["hour"] == 8.0, "--times must override the log")
    else:
        failures.append(f"collect with --times failed: {out.stderr[-200:]}")
    # render
    try:
        find_chrome()
        png = tdp / "fig.png"
        out = run([str(HERE / "build.py"), "--data", str(tdp / "reviews.json"), "--out", str(png)])
        check(out.returncode == 0, f"build failed: {out.stderr[-400:]}")
        if out.returncode == 0:
            w, h = png_size(str(png))
            check(600 < w < 1400 and 300 < h < 1200, f"unexpected PNG size {w}x{h}")
            print(f"[figure-referee-rounds] render: {w}x{h}px")
    except RuntimeError as e:
        print(f"[figure-referee-rounds] SKIP render: {e}")

# --- real data ------------------------------------------------------------
def find_run(name: str):
    """The run's repo: under runs-export/ in crux-in-a-box, or this repo itself when the skills ship inside an export."""
    for cand in (REPO / "runs-export" / name / "repo", REPO):
        if (cand / "host" / f"{name}.timeline.jsonl.gz").exists():
            return cand
    return None


astra = find_run("codex-astra")
if astra and (astra / "reviews").is_dir():
    out = run([str(HERE / "collect.py"), "--reviews-dir", str(astra / "reviews"),
               "--timeline", str(astra / "host" / "codex-astra.timeline.jsonl.gz")])
    check(out.returncode == 0, f"collect (codex-astra) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.loads(out.stdout)
        r = {x["round"]: x for x in d["rounds"]}
        check(sorted(r) == list(range(2, 13)), f"astra rounds {sorted(r)}")
        check(r[2]["overall"] == 4 and r[2]["overall_label"] == "Borderline Accept", f"astra R2 {r[2]['overall']} {r[2]['overall_label']!r}")
        check(r[6]["facets"] == {"soundness": 3, "presentation": 3, "contribution": 3} and r[6]["overall"] == 3,
              f"astra R6 (table format) {r[6]['facets']} {r[6]['overall']}")
        check(r[7]["facets"]["contribution"] == 2 and r[7]["overall_label"] == "Borderline Reject",
              f"astra R7 (plain format) {r[7]['facets']} {r[7]['overall_label']!r}")
        check(r[11]["issues"] == {"fatal": 0, "major": 1, "moderate": 2}, f"astra R11 issues {r[11]['issues']}")
        check(r[11]["n_questions"] == 4, f"astra R11 questions {r[11]['n_questions']}")
        check(all(x["time_source"] == "git" for x in d["rounds"]), f"astra time sources {[x['time_source'] for x in d['rounds']]}")
        check(r[2]["time"] == "2026-09-12T10:21:02Z", f"astra R2 time {r[2]['time']}")
        check(abs(r[12]["hour"] - 163.9) < 0.1, f"astra R12 hour {r[12]['hour']}")
        check(d["recommendation_counts"] == {"Borderline Reject": 9, "Borderline Accept": 2}, f"astra recos {d['recommendation_counts']}")
        print(f"[figure-referee-rounds] codex-astra: {len(d['rounds'])} rounds, recos {d['recommendation_counts']}")
else:
    print("[figure-referee-rounds] SKIP real data: codex-astra run data not found")

sol = list((REPO / "runs-export" / "codex-influence" / "collect" / "workspace").glob("*/reviews")) if (
    REPO / "runs-export" / "codex-influence" / "collect" / "workspace").is_dir() else []
if sol:
    ws = sol[0].parent
    tl = list((REPO / "runs-export" / "codex-influence" / "collect" / "timeline").glob("*.timeline.jsonl"))
    args = [str(HERE / "collect.py"), "--reviews-dir", str(sol[0]), "--log", str(ws / "LOG.md"),
            "--gitlog", str(ws / "git" / "git-log.txt")]
    if tl:
        args += ["--timeline", str(tl[0])]
    out = run(args)
    check(out.returncode == 0, f"collect (codex-influence) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.loads(out.stdout)
        r = {x["round"]: x for x in d["rounds"]}
        check(sorted(r) == list(range(1, 8)), f"sol rounds {sorted(r)}")
        check(r[1]["overall"] == 3 and r[1]["overall_label"] == "Borderline Reject",
              f"sol R1 bare '3/6.' {r[1]['overall']} {r[1]['overall_label']!r}")
        check(r[7]["facets"]["contribution"] == 2, f"sol R7 facets {r[7]['facets']}")
        check(all(x["time_source"] in ("gitlog", "log") for x in d["rounds"]),
              f"sol time sources {[x['time_source'] for x in d['rounds']]}")
        print(f"[figure-referee-rounds] codex-influence: {len(d['rounds'])} rounds, times {[x['time_source'] for x in d['rounds']]}")
else:
    print("[figure-referee-rounds] SKIP real data: runs-export/codex-influence not present")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[figure-referee-rounds] PASS")
