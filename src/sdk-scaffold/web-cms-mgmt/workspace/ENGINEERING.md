# Engineering contract

Work in the editable project identified in HANDOFF.md, preserving its Git history,
lockfiles, ignored assets and migration scripts/tests. A brief must include the
request ID, agreed acceptance criteria, current source/deployment mapping, allowed
scope, preview procedure and evidence directory. Ask the manager when those are
missing. Implement and test draft changes without mutating live CMS data/schema.
Keep operator feedback iterations separate and identify each immutable revision.

Capture/review visual evidence with the manager before changes, during every
preview iteration and after approved publication. Preserve desktop/mobile images
and reference their filepaths in raw visual logs and the append-only request log.
Run relevant content, functional, accessibility, performance, security and editorial
regressions inherited from migration. Report commands, raw output paths, results,
interpretation, source revision, preview URL and rollback point. Do not fabricate
tests, approvals or screenshots. Never edit prior REQUESTS.jsonl/LOG.md entries.

No publication or production data/schema change without a separate, exact-revision
operator approval verified by cms_approval. Proactive fixes also need approval
before implementation. The coding sandbox and log gate are cooperative controls,
not a deployment security boundary; honor them even when shell access permits more.
Keep secrets in external secret storage and private runtime files outside the
archived source. Never copy the old raw agent session or credentials into this run.
