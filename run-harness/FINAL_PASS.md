# FINAL_PASS.md — the final-stage instruction

_This message is dispatched **automatically**: provisioning stages everything below the line, and a box-side cron (`final-pass-injector.sh`) injects it into the main session as a user message when the agent writes `COMPLETION_REPORT.md` at the workspace root. The body below is the single source of truth the injector stages at provision time — edit it here, before provisioning, or restage it on the box._

_Operator: send it by hand only as a fallback — the completion report arrived but no injection followed within ~10 minutes (check `~/.openclaw/final_pass/injector.log`), or the deadline arrived with no completion report at all. Reply in the Telegram thread or use `openclaw agent --session-key agent:main:main --message "..."`. Send everything below the line verbatim; do not tailor it per run._

---

Good — before the project closes, complete the final pass. The deliverable is not finished until all four steps are done and committed (locally — there is no remote).

1. **Cold-read the paper end to end.** Render the PDF and read it as an expert seeing it for the first time. Restructure freely for legibility: the abstract within its word cap and readable by a non-specialist; a Figure 1 that carries the main result on its own; every technical term defined at first use; no internal vocabulary or file paths anywhere; the main body ends within the page cap (the gate locates the references boundary and checks this). Claims, numbers, and evidence are frozen — this pass changes presentation only. Run `FINAL=1 scripts/gate_artifact.sh <pdf>` and fix every failure.

2. **Build the accessible results page.** Create `results.html` at the repo root: a single self-contained HTML page (inline CSS, embedded images, no external resources) presenting the results as accessibly as you can make them. A reader with no machine-learning background should leave knowing the question, what was done, and what was found; an interested expert should find the key figures, the main numbers with their meaning, and pointers into the paper and repository. Plain language first, detail second.

3. **Write the final README.** Rewrite the repo-root `README.md` for a visitor with zero context: what this project is and what it found (two or three sentences, at exactly the strength the evidence supports), the path to the paper, a short annotated repo map, the one-command reproduction of the headline result, and one line per dataset on where it came from.

4. **Commit everything** (locally), then send an updated completion report: what the final pass changed, the paths to the paper, `results.html`, and `README.md`, and the output of the final gate run.
