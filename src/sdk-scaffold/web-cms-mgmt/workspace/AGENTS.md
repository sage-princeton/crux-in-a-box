# CITP maintenance standing context

Maintain the migrated Drupal main site and WordPress blog now served by Payload
on retained AWS resources. The original Princeton sites are read-only references.
Never modify them or send real form/subscription submissions without approval.
Re-read this file after compaction. Work with the editable source identified in
HANDOFF.md. Do not replace this harness or its state with the migration scaffold.

First verify the S3-restored manifest and full Git history, install from the
lockfile, rebuild and run the migration's checks. Verify the actual successor's
runtime credential availability and CMS/SSH/database/media/TLS access. Inspect resource inventory,
source-to-release mapping, verification gates, screenshot history and completion
report. Record commands/results/paths in HANDOFF.md and append a handoff event.
Missing source, credentials, durable backups or access are blockers; escalate
without inventing their contents or treating a production bundle as editable source.

Use one stable ID per reactive request or proactive issue. Append its original
Slack message/thread, author, acceptance criteria and scope with cms_event.
Keep its changing working spec under requests/<id>/; record each revision and
decision in the append-only log. Ask focused questions in its Slack thread when
needed. Handle routine create/update/delete content and complex content-model,
navigation or feature changes through the same workflow. Do not drop a request
because another arrives. Check open threads each iteration; channel steering
automatically ingests new top-level messages, not all request-thread replies.

For each request:

1. Capture live **before** images for all affected page types, desktop and mobile,
   using cms_snapshot. Examine them. Record baseline content, navigation,
   functionality, WAVE/accessibility, performance/web-vitals, OWASP/security and
   CMS editorial checks from the migration artifacts. Do not weaken those gates.
2. Delegate a scoped change and tests to engineer. Use a draft/version and native Payload Preview/Draft Preview with versioned drafts; no unapproved live
   schema/data mutation. Record commands and raw outputs in runs/<id>/<iteration>/.
3. Capture **during** screenshots of the actual preview at every implementation
   and feedback iteration. Inspect them against the request and baseline. Re-run
   relevant deterministic and editorial checks, interpret failures, and fix them.
   Share the working preview URL, exact immutable source/release revision, evidence
   and known limitations in the original thread. A local-only preview is not a
   review link. If preview draft/public separation is unavailable, escalate before changing live.
4. Ask an allowlisted human whether to publish the current preview in ordinary language.
   Read their reply in context, clarify ambiguity, then use cms_approval to record
   its actual message ID, your interpretation and the reviewed revision. Feedback or any code,
   content, schema or data change invalidates the earlier approval: publish a new
   preview and obtain approval for its new revision. Approval of an implementation
   plan is not publication approval. No reply means keep waiting.
5. Publish exactly the approved artifact/content version using your own tested and documented
   publication/rollback procedure, with backup and rollback available. Capture live **after**
   screenshots and verify the release, content, forms/navigation, editorial
   workflows, accessibility, performance and security against the accepted
   criteria. Append results, image filepaths, approval message ID, deployed revision,
   rollback point and outcome; send the operator a completion message in the thread.
   Log failed/rolled-back publication as blocked, and complete only after checks pass.

Monitor the live site proactively at a reasonable cadence and after publication:
availability/TLS, broken links and media, forms/navigation, accessibility,
performance regressions, security and CMS maintainability. Use existing scripts
and record baselines, commands, outputs and interpretation. Log each issue as
`proactive`, notify the operator in a dedicated thread with impact/evidence and a
proposed fix, then wait for explicit human agreement in ordinary language before resolving it.
Record the actual message ID, approved scope/revision and interpretation with cms_approval. Then use the same preview/publication workflow.
Read-only diagnosis is allowed while waiting. Do not run intrusive security scans
against external sites or make an unapproved production fix.

REQUESTS.jsonl is the structured append-only history; LOG.md is its human-readable
companion. cms_event, cms_snapshot and cms_approval append both. Never rewrite,
truncate or remove earlier entries or images. Each progress/verification/completion
entry links scripts, raw check logs, preview/release revisions and screenshot paths,
and states what the evidence means and what remains. Logs/check results belong in
the archived workspace; keep credentials out of text, URLs, images and artifacts.
Snapshots are full-page PNGs with URL, viewport, time, request, phase and revision
metadata in runs/snapshots.jsonl; references are appended automatically to both logs.
Record visual interpretation separately; a saved screenshot is not a review.

Do not stop after one request or an empty queue. Use rest when waiting and avoid
repeated Slack status posts. When the queue is empty, rest up to an hour until
the next scheduled health check; new top-level steering can wake the wait. When
an open request is awaiting thread feedback/approval, check that thread every
30–60 seconds and rest between checks. Reuse current verification evidence until
something changes rather than repeatedly running checks while waiting.
Budget exhaustion is a reported incomplete run, not
success. To finish, obtain an explicit natural-language request to stop as a new top-level channel
message, clarify if ambiguous, and record it with cms_approval using its timestamp.
Tell the operator to use a top-level message if asking how to stop; closed request
threads are not automatically polled. Resolve/cancel every open request explicitly. Write
COMPLETION_REPORT.md with request outcomes, remaining issues, resource/secret
references, final release, verification and visual evidence, backup/rollback
status, and successor instructions. Update the archive manifest for this SDK run
with the complete drop-in and required state, and keep all artifacts for teardown.

Use native Payload admin.preview, versions/drafts and authenticated frontend draft mode. Devise, test, document and execute publication and rollback yourself; preserve the currently published content while preparing drafts. Use wave_check for credit-accounted WAVE audits and budget_status for the $100 Langfuse inference allowance. There is no token or turn cap.
