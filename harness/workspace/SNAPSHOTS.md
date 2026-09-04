# SNAPSHOTS.md — Operator Snapshots

_One-way, append-only. There is no chat channel: the operator reads this file. Every {{SNAPSHOT_HOURS|4}} hours append a snapshot (AGENTS.md § The operator), newest last, headed `### YYYY-MM-DD HH:MM — snapshot`. Content: position against plan, what shipped since the last snapshot, decisions taken, resource line (each budget: spent/remaining vs ledger), open blockers. Never end a snapshot with a question or anything awaiting a reply._

_The one thing that may ask for the operator: a critical external resource broken after a documented debugging attempt, or an imminent budget-cap breach (AGENTS.md § The operator, message 1). It goes at the **top** of this file, under a `## NEEDS OPERATOR` heading — what broke, what you tried, what you need, what you are working on meanwhile — and you keep working; a reply, if one comes, arrives as an `inbox/` drop. The completion report is not a snapshot: it goes to `COMPLETION_REPORT.md`._
