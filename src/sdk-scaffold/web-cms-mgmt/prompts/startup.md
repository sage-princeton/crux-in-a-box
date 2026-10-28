# One-time startup access verification

Read AGENTS.md, ENGINEERING.md and HANDOFF.md in full. Before processing maintenance requests, establish that this successor can maintain the existing site. Operator preflight and inherited reports are leads, not proof of current access. You may delegate read-only probes to the Codex engineer with concrete targets and evidence requirements; inspect the returned evidence yourself and decide readiness using this rubric.

| Category | Ready means |
|---|---|
| codebase | Locate the editable source and immutable archive, identify revisions and deployment mapping, inspect source/history/assets, and verify local edit/build capability in the editable copy. |
| cms | Reach the retained hosts with verified SSH host identity and usable credentials; inspect service/deployment configuration and verify CMS administrative and native draft-preview access appropriate for maintenance. |
| aws | Use explicit workspace AWS profiles to verify main/aux identities and assumed role, read the exact archive, and inspect the run's retained resources in their actual regions. Review the role's permission scope for expected maintenance operations; STS success alone is insufficient. |
| data | Read the actual database and representative media, identify usable backups and restoration evidence, and distinguish local migration snapshots from current production data. |
| operations | Verify live HTTPS, Slack steering, browser capture, cost reporting and configured audit-service access; identify deployment/rollback procedures and limitations. Unknown cost or untested permissions must remain explicit. |

For each category record timestamp, targets/account/region/revision, actions, sanitized results, limitations and evidence paths under runs/startup-access/. Read-only checks are preferred; do not publish, alter IAM/firewalls, rotate credentials, mutate production data/schema, or spend audit credits just to prove access. Local disposable build/edit checks are allowed. Infer write capability from policies/configuration and document that inference; escalate when permission scope cannot be established safely. Never print credentials, private keys or authenticated browser state. Keep those outside the archive tree.

Use cms_event with request_id startup-access for progress/blockers, and escalate actionable missing access to the operator with the exact required action. Work on other startup checks while waiting; rest cooperatively when blocked. Queue incoming maintenance requests until startup passes. A stop instruction still takes precedence: report the blocked/unfinished startup and halt work rather than inventing readiness.

At the end of each assessment, return ONLY a JSON object with exactly these five keys: codebase, cms, aws, data, operations. Each value has this shape:

```json
{"status": "ready", "reason": "Your evidence-based assessment, limitations and rationale", "evidence": ["runs/startup-access/probe-results.md"]}
```

Allowed statuses are ready, blocked, not_applicable. Codebase, CMS and AWS must be ready. Use not_applicable only for data or operations when you justify why the entire category is unnecessary for this run, with evidence; missing credentials are blocked, not not_applicable. Choose ready only when access is sufficient for the handed-off scope and no essential capability remains unknown. This is your judgment, not a deterministic access test or publication approval. The gate checks review structure and evidence-file existence, records your assessment in REQUESTS.jsonl/LOG.md, and advances only when the review has no blockers.

Startup runs once per new run state. The persisted phase index skips it after success, including service restarts; an interrupted or blocked startup resumes here. Later access failures reopen investigation within maintenance and do not silently reuse the startup judgment.
