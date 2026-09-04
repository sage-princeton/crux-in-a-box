# FINAL_PASS.md — the final-stage instruction

_This message is dispatched **automatically**: the loop injects everything below the line into the session as a user message, exactly once, on the first of three triggers — the agent writes `COMPLETION_REPORT.md` at the workspace root (requirement 9); `{{FINAL_WINDOW_MINUTES|60}}` minutes of clock remain with no completion report; or spend reaches `{{COST_STOP_FRACTION|0.95}}` of the API budget with no completion report. The last two are the automatic form of the manual fallback (the deadline arriving with no report) and carry a one-line preface saying so. After the agent's reply the loop runs `FINAL=1 scripts/gate_artifact.sh paper/paper.pdf paper` itself and hands any failure back as a message, up to `{{FINAL_GATE_RETRIES|2}}` times; the run ends when the gate passes or the retries are spent._

_The body below the line is the single source of truth — `ops/configure.sh` resolves this file into `run/<name>/` and the loop reads it from there. Edit it here, before configuring; do not tailor it per run. There is nothing to send by hand._

---

Good — before the project closes, complete the final pass. The deliverable is not finished until all four steps are done and committed (locally — there is no remote).

1. **Cold-read the paper end to end.** Render the PDF and read it as an expert seeing it for the first time. Restructure freely for legibility: the abstract within its word cap and readable by a non-specialist; a Figure 1 that carries the main result on its own; every technical term defined at first use; no internal vocabulary or file paths anywhere; the main body ends within the page cap (the gate locates the references boundary and checks this). Claims, numbers, and evidence are frozen — this pass changes presentation only. Run `FINAL=1 scripts/gate_artifact.sh paper/paper.pdf paper` — the same command the harness runs when your reply ends — and fix every failure.

2. **Build the accessible results page.** Create `results.html` at the repo root: a single self-contained HTML page (inline CSS, embedded images, no external resources) presenting the results as accessibly as you can make them. A reader with no background in this field should leave knowing the question, what was done, and what was found; an interested expert should find the key figures, the main numbers with their meaning, and pointers into the paper and repository. Plain language first, detail second.

3. **Write the final README.** Rewrite the repo-root `README.md` for a visitor with zero context: what this project is and what it found (two or three sentences, at exactly the strength the evidence supports), the path to the paper, a short annotated repo map, the one-command reproduction of the headline result, and one line per dataset on where it came from.

4. **Commit everything** (locally), then write the updated completion report into `COMPLETION_REPORT.md`: what the final pass changed, the paths to the paper, `results.html`, and `README.md`, and the output of the final gate run.

Do all four steps within this turn: the harness runs the final gate when your reply ends, and the run closes once it passes.
