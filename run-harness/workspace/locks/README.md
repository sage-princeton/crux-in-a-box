# locks/ — Frozen Definitions as Code Artifacts

Any definition that must not drift once experiments start — success thresholds, "harmful"/"failure" criteria, evaluation splits, headline metrics, budgets — is frozen here as a small JSON file **before the first run that uses it**, and code reads the JSON. Prose cites the lock file; it never restates the value as a hand-typed number.

Why: a lock that exists only in prose is not a lock — it's a contradiction waiting for a reviewer to find.

Pattern:

```json
// locks/success_threshold.json (example shape)
{
  "name": "success_threshold",
  "value": 0.05,
  "definition": "the externally-defined threshold at which an effect counts as material",
  "locked_at": "<ISO timestamp>",
  "locked_by_log_entry": "<LOG.md entry key>",
  "change_policy": "Tier-2 memo required; changing this re-defines results for all future runs, never past ones"
}
```

Rules:
1. Register every lock in `REGISTRY.md` § Locks.
2. Code imports the value from the JSON; prose cites the file path.
3. Changing a lock is a Tier-2 memo (`playbooks/decisions.md`); the change updates the registry row, never silently edits history.
4. Prose referencing a lock that doesn't exist on disk is a defect — create the lock from the operative code value or fix the prose, same session.

## Gate-read locks shipped with the harness (complete these hour-0)

Two locks are read directly by `scripts/gate_artifact.sh`. They ship as templates with sentinel values; **completing them is an hour-0 duty** (alongside registering the caps in `BRIEF.md`). Until completed, the gate degrades gracefully — it still blocks shipping on the missing power-critic artifact, but the budget cross-check returns "unknown" (which does not hard-block — the under-spend backstop relies on the felt-budget heuristic plus a logged ship justification).

- **`budget.json`** — the budget-deployment target + clock the **ship-authorization backstop** uses. Keys read by code: `api_budget`, `deadline_iso`, `launch_iso`, `underspend_time_floor` (default 0.5), `underspend_budget_floor` (default 0.4). Fill `api_budget`, `deadline_iso`, `launch_iso` hour-0.
- **`evidence_floors.json`** — the minimum seeds / load-bearing cells a shipped headline must rest on. Keys read by code: `seed_floor` (default 3), `cell_floor` (default 2). The isolated power critic states the counts it found; the gate checks them against these floors. A negative/impossibility headline faces the same floor as a positive one.
