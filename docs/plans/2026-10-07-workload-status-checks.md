# Independent workload status checks

Status: Approved and implemented for PR review · 2026-10-07

## Outcome

Run read-only project status checks independently of incident monitoring. Show the latest reports in **Workload coverage**, at the top of the signed-in overview. Each report answers the questions in `TODO.md`: alive or stalled, progress, spend/time against plan, quality trend, milestones, alerts, and a recommended next step.

## Separate execution

- Add a status worker with its own EventBridge schedule, Batch job definitions/queue, configuration, inference budget, retries, and health state. Reuse the existing compute environment, image build, and evidence-collection helpers where practical.
- Status discovery reads EC2 inventory and approved project/source mappings independently. Neither status discovery nor reporting waits for an incident review or its output. Disabling or failing either pipeline leaves the other operational; shared AWS/provider outages can still affect both.
- Follow the two stages in `TODO.md`: a cheaper model sweeps approved evidence for relevant changes, then a stronger model produces the report from those references and the previous snapshot. Configure both explicitly from permitted model families; preserve evidence references across the handoff. Chunk within bounded context and cost limits; disclose truncation or unread evidence.
- The worker can read approved traces, logs, and exported files and write its own status records. It cannot run workload commands, restart agents, edit project files, create incidents, or change incident state. Give it a dedicated IAM role scoped to those reads, the status table, and its own artifact prefix; grant no access to the incidents table.

## Report and storage

Create a dedicated DynamoDB table for status checks, separate from the incidents table. Keep status inventory, reports/history, latest-success pointers, job leases/retries, health state, and inference-budget accounting in this table. Configure it independently through `STATUS_TABLE`, with encryption, point-in-time recovery, and retention rules appropriate to each record type. Use a separate `status_checks/` artifact prefix in S3.

Store the report, evidence window, source references, model/cost metadata, and latest-attempt outcome. Conditional updates prevent late retries replacing newer reports. The web app reads the status table through a separate store with read-only permissions and combines it with incident coverage for display. Join inventory by instance ID plus configured workload/run identity, never display name alone; no cross-table writes or transactions are required. If either data source is unavailable, show that section as unavailable while retaining the other.

Render five concise logical lines:

1. Alert, or “No alerts observed,” plus recommended next step.
2. Alive/stalled/unknown and progress since the prior successful check.
3. Observed spend and elapsed time versus the configured project budget/deadline.
4. Quality trend with supporting evidence; say “insufficient evidence” when needed.
5. Milestones reached and the next expected milestone.

Separate **project spending** from **status-check inference costs**. Use recorded usage and explicit project baselines; missing budgets, incomplete billing, or missing deadlines stay unknown. On the first check, establish a baseline. Missing traces alone do not prove a stall; stopped/finished workloads are distinct from stalled workloads.

Track last attempt, last successful report, and evidence age separately. Failed/stale checks retain the previous report with its original timestamp and a visible explanation. New workloads show “Awaiting first status check.” Preserve history for stopped workloads; stop recurring inference once they retire.

## Workload coverage layout

Current entry points: `web.py` loads incident-owned `FLEET` rows; `templates/index.html` renders coverage below the incident table. Extend the read model to combine independent status inventory/reports with incident coverage, including workloads with no incidents.

- Move Workload coverage immediately below the page heading. Compress the existing summary cards so they do not displace the latest report.
- Show the latest report expanded for each visible workload: name, workload state, checked/evidence timestamps, and five-line update. Use wrapping text, not fixed-height clipping.
- Order workloads needing attention first, then latest reports; provide a compact workload selector and paginated list. Keep the first report visible immediately; arbitrary fleet size cannot fit above the fold.
- Label **Project status** and **Incident review coverage** separately. Status alerts do not change incident counts, filters, notifications, or severity.
- Put older reports behind “Status history” and stopped workloads behind an expandable history section. Keep incidents directly below coverage with an anchor link from the header.
- Read stored snapshots on page load and through an explicit Refresh link; page views never invoke models. Retain AWS sign-in and escape all generated text.

## Implementation and validation

1. Add status config/schema, storage, independent discovery and worker; test with fixture evidence.
2. Provision the dedicated status table, schedule, queue, job roles, and bounded cost controls. Add `STATUS_TABLE` to worker/web configuration and read-only status-table access to the web role. Update plan/deployment policies and the release guard for the new resources: initial creation requires an operator-reviewed apply under the current deployment rules.
3. Add the coverage read model, report/history rendering, and compiled Tailwind changes.
4. Verify: incident monitoring disabled while status completes; isolated failure/budget exhaustion; duplicate and out-of-order jobs; missing/stale evidence; first report; retired workload; unchanged incident counts; authenticated access and escaped model output. Confirm status workers cannot access the incidents table, the web role cannot write the status table, and either table's read failure leaves the other section usable.
5. Browser-check at 1440×900 and 390×844: the coverage heading, freshness, and first complete short report appear before scrolling, and incident volume cannot push coverage down.

## Proposed rollout defaults for review

- Start with one explicitly selected workload; status checks every **15 minutes**, stale after **30 minutes** without fresh evidence. Both thresholds are configurable.
- Require an explicit status budget, model pair, approved sources, and expiry before enabling. Keep the existing incident schedule, budget, and activation state unchanged.
- Store status history for **90 days** using the dedicated table's TTL; retain current inventory/latest-report and active budget records while needed. Publish to the signed-in coverage screen initially; outbound status notifications are a separate decision.
- Implementation follows the approved plan. Activation remains a separate operator rollout; the default configuration does not provision or schedule status infrastructure.

## Implementation notes

- AWS sign-in continues using the existing session store. Independent data-query failures render inline; a failure of the authentication store still fails closed.
- Project spend uses a dated operator-provided snapshot; missing billing remains unknown. Status inference accounting is independent.
- [Desktop preview](assets/workload-status-desktop.png), [mobile preview](assets/workload-status-mobile.png), and [previous layout](assets/workload-status-before.png) use synthetic fixture data. The first full mobile report ends at 558px in a 390×844 viewport.
- Operational bootstrap, expiry, budget reset, rollback and validation are documented in [OPERATIONS.md](../../src/monitoring/OPERATIONS.md#independent-project-status-checks).
