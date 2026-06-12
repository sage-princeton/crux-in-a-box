# locks/ — Frozen Definitions as Code Artifacts

Any definition that must not drift once experiments start — success thresholds, "harmful"/"failure" criteria, evaluation splits, headline metrics — is frozen here as a small JSON file **before the first run that uses it**, and code reads the JSON. Prose cites the lock file; it never restates the value as a hand-typed number.

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
4. If you find prose referencing a lock that doesn't exist on disk: that is a defect — create the lock from the operative code value (marking it locked-retroactively) or fix the prose, same session.
