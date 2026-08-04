# LOG.md — the run's memory

Append-only. Every iteration ends by adding an entry here (see `AGENTS.md` § How this run works). Because each iteration starts with a fresh context, this file plus git history *is* the run's continuity — write entries for a stranger, not for yourself.

Entry format:

```
  ## <UTC timestamp> — iteration N
  **Done:** what happened, with artifact paths for every claim.
  **State:** running jobs (PID, log path, ETA), open questions, where half-finished work stops.
  **Next:** the single highest-value next action, executable by a stranger.
```
(The header starts at column 0 in real entries; it is indented above only so tooling that scans for `## ` entries skips this example.)

Pre-registration also lives here: before a decisive experiment runs, write the verdict bins (PASS / PARTIAL / FAIL) and what each implies — the decision rule must predate the result.

---
