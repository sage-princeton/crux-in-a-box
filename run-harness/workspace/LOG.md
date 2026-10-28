# LOG.md — Migration Log

_Append-only. Never edit or delete an entry. This is the authoritative trail of why the site looks the way it does, and of every verification iteration. Write each entry so that a reader with no other context can reconstruct the reasoning. Point to artifacts by path, and never paste secrets or API keys._

There are two entry types.

The first Decision entry references `runs/startup/mail.md` for the mailbox authentication, inbox read and self-mail send/receive checks, alongside the other dependency checks. Record actual outcomes and sanitized message IDs, never OAuth secrets or confirmation/unsubscribe tokens.

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
- **Visual snapshots:** runs/N/screens/index.json; source/target image paths by page and viewport, with the review in runs/N/rubric.md and its visual-check log. Preserve every iteration's files.
- **Coverage:** complete inventory/import totals and discrepancies; runs/N/accessibility_coverage.csv with WAVE-tested URLs, represented groups, gaps and credits consumed; runs/N/performance_coverage.csv with measured URLs, workload groups, medians and baseline methods. State whether visual review is targeted or complete, with reviewed/total counts and selection rationale in runs/N/rubric.md. Do not report a sampled page as individually tested.
- **Results:** what came back, in numbers, per check
- **Interpretation:** what the results mean: the causes behind the failures,
  which are systematic and which page-specific, and whether any check itself is suspect
- **Criteria:**
  1. URL parity: PASS | FAIL (<n>/<total> pages; artifact)
  2. Content parity: PASS | FAIL (...)
  3. Visual parity (rubric): PASS | FAIL | PARTIAL (reviewed <n>/<total>; runs/N/rubric.md; PARTIAL requires CONTINUE)
  4. Functional parity: PASS | FAIL (...; runs/N/subscription.md or prior confirmed test plus this iteration's form/provider re-check; actual subscription/confirmation outcome)
  5. Public access, restricted admin: PASS | FAIL (...)
  6. Accessibility: PASS | FAIL (representatives tested <n>/<total>; template/variant coverage; desktop/mobile results; runs/N/accessibility_coverage.csv)
  7. Performance: PASS | FAIL (representatives <n>/<total>; workload coverage; mobile/desktop medians; runs/N/performance_coverage.csv)
  8. Security: PASS | FAIL (...)
  9. Maintainability: PASS | FAIL (editorial scenarios <n>/<total>: runs/N/editorial/; CMS review: runs/N/cms_review.md)
- **Verdict:** DONE | CONTINUE
- **Next:** the fixes this iteration calls for, most important first (omit on DONE)
```
