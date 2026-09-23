# LOG.md — Migration Log

_Append-only. Never edit or delete an entry. This is the authoritative trail of why the site looks the way it does, and of every verification iteration. Write each entry so that a reader with no other context can reconstruct the reasoning. Point to artifacts by path, and never paste secrets or API keys._

There are two entry types.

**Decision**: for decisions, observations, surprises and dead ends.

```
### YYYY-MM-DD HH:MM — [short title]
- **Observed:** what you saw, measured, read, or noticed (with artifact paths).
- **Decided:** what you chose to do or not do.
- **Reason:** why; what alternatives you rejected.
```

**Verification iteration**: exactly one per iteration (see `AGENTS.md` § Verification).

```
### YYYY-MM-DD HH:MM — Iteration N
- **Target:** <deployed URL> @ <git SHA>
- **Checks run:** one line each: check name · command · runs/N/<output>
- **Results:** what came back, in numbers, per check
- **Interpretation:** what the results mean: the causes behind the failures,
  which are systematic and which page-specific, and whether any check itself is suspect
- **Criteria:**
  1. URL parity: PASS | FAIL (<n>/<total> pages; artifact)
  2. Content parity: PASS | FAIL (...)
  3. Visual parity (rubric): PASS | FAIL (runs/N/rubric.md)
  4. Functional parity: PASS | FAIL (...)
  5. Public access, restricted admin: PASS | FAIL (...)
  6. Accessibility: PASS | FAIL (...)
  7. Performance: PASS | FAIL (...)
  8. Security: PASS | FAIL (...)
- **Verdict:** DONE | CONTINUE
- **Next:** the fixes this iteration calls for, most important first (omit on DONE)
```
