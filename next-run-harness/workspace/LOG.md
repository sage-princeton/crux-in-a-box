# LOG.md — Research Log

_Append-only. Never edit or delete an entry. Event-keyed (decisions, observations, surprises, dead ends), not calendar-keyed. This is the authoritative trail of why the project looks the way it does — write each entry so a reader with no other context can reconstruct the reasoning._

Entry format:

```
### YYYY-MM-DD HH:MM — [short title]
- **Observed:** what you saw, measured, read, or noticed (with artifact paths).
- **Decided:** what you chose to do or not do.
- **Reason:** why; what alternatives you rejected.
```

One worked example (delete after the first real entry). Harvest entries simply record which pre-registered branch fired, with the verdict read from the artifact, not from any report.

### 2026-01-01 00:00 — [EXAMPLE: experiment launch with pre-registered branches]
- **Observed:** Hypothesis H1 (see REGISTRY.md F-1) is testable cheaply; falsifier script written, verdict bins in its docstring.
- **Decided:** Launched as nohup PID 1234 → `runs/exp_03/out.log` (~12 min ETA). Pre-registered branch protocol: PASS → extend seeds and draft the method claim; PARTIAL/AMBIGUOUS → second falsifier on the orthogonal family before any claim; FAIL → mark F-1 triggered, open Stuck/Pivot if it kills the headline. Harvest cron at ETA+10m, systemEvent references this entry.
- **Reason:** Branch-before-results keeps the harvest turn mechanical and prevents post-hoc verdict bending; the cron makes the turn end in LAUNCH state per the contract.
