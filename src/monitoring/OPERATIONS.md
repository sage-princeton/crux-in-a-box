# Passive EC2 monitoring

The reviewer runs as an unprivileged, tool-free container in AWS Batch on EC2.
EventBridge schedules discovery every five minutes. Discovery inventories EC2
instance IDs and states. With `fleet` enabled, it inventories nonterminated instances in the configured AWS region,
excluding configured names such as `crux-control` and `crux-monitor-worker`, the
pinned controller instance ID, and controller-role instances. Configured name
exclusions also apply to historical instance lists. Running workloads receive reviews.
Slack includes only running instances. The HTML retains all incident history and
groups stopped and historical instances under a collapsed disclosure. Explicit target
entries can supply approved exports or a session mapping. Without `fleet`, only
explicitly registered targets receive reviews.
This does not install agents, enable network sensors, change security groups on
targets, or enforce actions. Existing CloudWatch streams can supply host, Flow Log,
and DNS evidence. Unavailable sources are coverage gaps, never a clean bill of health.
The synchronous action-check service and inspect-orchestrator integration remain
separate work; neither is represented as deployed protection.

## Prompt and evidence approval

Review [the exact prompt](prompts/reviewer.md) before activation. The first review
maps the apparent workload from approved evidence; subsequent reviews receive the
previous profile and suggested source IDs. Recommendations change attention within
the existing evidence set, never access permissions. Reviewer models must be from
a different recognized family than the observed and operator-declared subject
families. Unknown families produce a visible coverage failure.

Only approved, already scrubbed exports belong in the source configuration.
The additional credential redaction is not a guarantee that arbitrary transcripts
are safe. Raw transcript directories, environment files, private keys, hidden
grading material, and recursive filesystem scans are not permitted inputs.
Langfuse content must likewise be approved for disclosure to the reviewer provider.

Each attempt stores `evidence.json`, `prompt.json`, the model's `response.json` when
available, `model.json`, `report.json`, a human-readable `report.md`, and
`manifest.json` under an immutable attempt prefix in the private versioned S3 bucket.
The Markdown report summarizes findings, possible explanations, and coverage gaps;
it explicitly distinguishes a failed review from a review with no findings.
The manifest records object version IDs and SHA-256 digests. The prompt is the exact
system instruction; evidence is the exact user payload. Intermediate artifacts
survive later review failures. Retention is 90 days.

Fleet Slack notifications contain one bullet per running instance, with the new
incident count in bold: `• crux-web-pilot: 3 incidents based on 12 reviews, *1 new incident :warning:* (last updated: 2026-10-01 15:30 ET)`.
When public HTML is enabled, the message ends with “Open the public incident log.”
Zero new incidents use plain text without the warning emoji.
The slug comes from the EC2 Name tag; duplicate names receive an instance-ID
suffix. Incident counts are distinct historical issues, including monitoring
problems. Review counts include completed, unavailable, and skipped attempts;
the HTML separates those outcomes so zero findings cannot imply a safety verdict.
“New” means added since the last acknowledged fleet message. Counts advance once
per review window, including on retries. The timestamp is the latest review-window
end in Eastern Time, or the inventory observation time for an unreviewed instance.

Discovery queues reviews and one fleet digest every five minutes. The digest
summarizes the latest persisted results; jobs still running appear in a later
digest. Unchanged health, incident counts, and inventory do not send another
message merely because more reviews finished. A new incident, health transition,
or running inventory change sends the active fleet snapshot. Slack formatting uses
only sanitized instance slugs, counts, timestamps, and the configured log URL. Slack acknowledgment and DynamoDB
cannot form one transaction: an ambiguous response can still cause duplicate
delivery. Legacy explicit-target deployments retain per-review numbered alerts.

`fleet.langfuse_by_name` reads only the Langfuse environment matching a unique EC2
Name tag, with a bounded time window and a response-side environment check. It
never enables collection on the host. Missing or ambiguous mappings remain
coverage gaps. An oversized Langfuse page is retried without inputs/outputs; the
saved sources and report explicitly mark content-level review incomplete. If even
bounded collection fails, the review is unavailable, not idle.
When task-specific authorization is absent, that context is a
limitation rather than evidence of wrongdoing. Stale-only evidence skips
inference without declaring the target safe.

## Read-only access boundary

The monitoring IAM roles can describe EC2, read explicitly allowed CloudWatch logs,
and write only monitoring state/artifacts. They cannot send SSM commands, change
instances, attach volumes, or alter target security groups. The discovery role
cannot read credentials; the reviewer cannot submit jobs.

SFTP requires an independently verified target-side boundary:

* A dedicated `crux-inspect` user, no sudo membership, password authentication,
  interactive shell, forwarding, PTY, or user startup commands.
* Root-owned chroot `/srv/crux-inspection`; only already scrubbed copies inside
  `/exports` are exposed. No bind mounts of a research home directory.
* A root-owned sshd `Match User crux-inspect` rule with
  `ForceCommand internal-sftp -R`, `ChrootDirectory /srv/crux-inspection`,
  `DisableForwarding yes`, `PermitTTY no`, `PermitUserRC no`,
  `PasswordAuthentication no`, and `AuthenticationMethods publickey`.
* The inspection key is separate from operator credentials. Its authorized-key
  entry uses `restrict`; sshd restrictions enforce read-only access even if a
  client attempts writes. The reviewer pins an out-of-band-verified Ed25519 host
  key, connects only to the EC2 API's private address, and never executes a command.

Before setting `boundary_verified=true`, demonstrate that read succeeds and write,
remove, mkdir, rename, shell execution, and forwarding fail against the new test
target. A false flag is an operator assertion, not an automated attestation.
Reads are limited to eight approved paths, 64 KiB per file, ten API pages, and
512 KiB of combined evidence per review. There is no directory traversal or
recursive discovery. Reads still consume CPU/I/O and SSH may update authentication
logs/access times: read-only authority does not mean literally zero host impact.
No target setup is automatically applied to existing resources.

An isolated monitoring VPC is the default. It cannot reach private targets in
another VPC. For SFTP, select existing subnets with an already valid route and
target ingress, or use a newly created test target. Terraform creates only the
monitoring security group and its egress rules; it does not repair existing routes
or target ingress implicitly.

## DynamoDB contract

One on-demand table, encrypted at rest, with point-in-time recovery:

| Key | Contents | Purpose |
| --- | --- | --- |
| `REVIEW#instance-id#window-end` | Lease owner/expiry, status, S3 prefix, timestamps | A conditional update elects one active reviewer. Completed windows are not rerun. |
| `TARGET#instance-id` | Latest workload profile, suggested source IDs, window end | Later reviews reuse context. Conditional updates prevent older windows overwriting newer profiles. |
| `NOTICE#fleet` | Last acknowledged incident counts, health, and inventory fingerprint | One fleet digest; new counts advance only after Slack acknowledgment. |
| `FLEET#instance-id` | Name/state, distinct review windows, completed/failed/skipped counts | Retries do not increase review totals; latest health cannot move backward. |
| `NOTICE#instance-id` | Delivery lease, latest health fingerprint/window, last acknowledged review | Serializes notifications, suppresses repeated health-only alerts, and identifies recovery. Older windows cannot roll health state backward; actual findings still notify. |
| `BUDGET#inference` | Reserved micro-USD | Atomic reservations bound inference across concurrent jobs and retries. Ambiguous charges are never refunded. |

The `status-updated` index lets discovery find unfinished reviews. It is eventually
consistent; the base-table conditional claim is the authority. A 20-minute lease
outlives the 15-minute Batch attempt timeout, so a killed job's lease can be safely
reclaimed. Reports become `pending_notification` only after S3 writes finish. A
retry sends the saved report without paying for another model call. Only delivered
rows receive a 90-day TTL. TTL is asynchronous cleanup, not a lock or scheduler.
Large evidence and reports stay in S3 to avoid DynamoDB's item-size limit.
Five claimed attempts exhaust a window's automatic retries; the last Batch failure
is retained in the operations queue. Inference authentication/billing failures
(HTTP 401/402/403) set `HEALTH#reviewer.blocked`. Repair credentials/billing and
remove that health row as an operator to resume inference. OpenRouter workspace
budgets are separate from the API key limit: a valid key with remaining allowance
can still receive HTTP 403 when its workspace budget is exhausted. Provider error
diagnostics are redacted and saved as `error.json`; the actionable reason is retained
in the health row. Other reviews retain evidence and reports without additional
paid model requests or repeated alerts. `REVIEW.notification` distinguishes sent
from suppressed notifications; `done` means processing is finished in either case.

## Incident history

The incident log is `reviews/incidents/index.html` in the artifact bucket.
It is private by default. Setting `public_incident_log=true` publishes only this
HTML file over HTTPS; its incident summaries become public, while full reports,
evidence, object versions, and bucket listing remain private. Terraform outputs
the public URL. Download it and open the file in a browser; search, filters, and expanded details
work offline. Evidence links require AWS access. Every saved review updates this
snapshot, including reviews whose Slack notification was suppressed.

One row groups the same finding category, evidence text, and source IDs for a
target; changed evidence remains separate. Monitoring outages, inactivity, and
coverage limitations each have one row per target. Rows retain first/last seen and
the number of distinct review windows. Replaying a window does not increase its
count. Import history oldest first; arrivals more than 90 days behind an incident's
latest observation are ignored to bound replay state. Aggregate incident rows do
not expire. Only monitoring health is resolved automatically; absence in a later
review does not establish that an agent finding was fixed.

## Deployment

Keep state, tfvars, registry, and credential files private and gitignored. The
Princeton remote state backend is a separate ticket; do not run CI apply with
ephemeral local state. The workflow stub deliberately refuses deployment until
remote state, OIDC, and an approval environment are configured.

1. Refresh AWS authentication and verify the account using STS. Prepare the
   non-secret registry from `registry.json.example`, with explicit target IDs,
   authorization, reviewer model IDs, and `expires_at` (initial validation: 24h).
2. Put a dedicated, capped OpenRouter monitoring key and Slack incoming webhook
   in an SSM **SecureString** such as `/crux/monitoring/env`, using the JSON example.
   Supply its ARN to Terraform. Store it in the existing account's Parameter Store;
   do not overwrite `/crux/system/env` or expose workload credentials to reviewers.
   Terraform references the existing parameter by ARN; it never owns the secret
   value. Importing shared values into state is intentionally unnecessary.
3. Supply `MONITORING_LANGFUSE_*` credentials only if using Langfuse, in the
   dedicated monitoring secret. The accepted initial approach uses a project
   read/write key with bounded GET-only worker code. This is read-only behavior,
   not enforced read-only access: a compromised worker could use the key to write.
   Prefer a separately revocable key; reusing a key couples revocation to its other
   consumers. Do not grant the reviewer access to the shared system secret.
   **TODO: create a way to read-only access LangFuse.** See the
   [decision and alternatives](../../docs/research/ae-211-langfuse-access-options.md).
4. `terraform init -backend-config=backend.hcl`, `terraform plan -out=monitoring.tfplan`, then inspect the plan:
   only new monitoring resources and its registry object should change. First
   apply with `enabled=false`, `image_digest=""` to create the ECR repository and
   monitoring infrastructure (compute minimum is zero).
5. Commit checked source, build `Dockerfile` for `linux/amd64`, push it to the
   output ECR repository with a commit-SHA tag, and obtain its digest. Set
   `image_digest` and `revision` to that digest and commit. Apply while disabled.
6. Run the container's `python worker.py preflight` with the review job role and
   environment. This reads AWS/configuration only; it sends no Slack message or
   inference request. Confirm the read-only boundary on a new test workspace,
   collect real AgentRQ turns, and independently verify their artifacts and traces.
7. After operator review of prompt/access/state, enable the schedule. Set
   `schedule_end` to the registry's expiry. Inspect Batch results, S3 artifacts,
   DynamoDB delivery rows, and Slack receipt. Queued jobs after expiry do no work.

Batch scales between zero and four requested vCPUs (AWS may temporarily exceed
the maximum by one instance). Set a provider-enforced spend limit on the dedicated
OpenRouter key as well as the application's inference budget. The inference
reservation is not an AWS infrastructure spending cap. The operations queue and
CloudWatch alarm retain terminal Batch/Scheduler failures when the webhook itself
is unavailable. Arrange human ownership of this alarm before unattended operation;
the stack does not silently install a second notification service.

Useful source contracts: [OpenSSH read-only SFTP](https://man.openbsd.org/sftp-server.8),
[DynamoDB TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html),
[Langfuse Public API](https://langfuse.com/docs/api-and-data-platform/features/public-api).
# Stateful incident website

The Flask/Jinja site runs on a separate `crux-incident-web` EC2 with Caddy and
Gunicorn. Public pages expose a fixed summary allowlist; raw findings, evidence
references, operator identities, and notes require sign-in. S3 evidence still
uses the viewer's AWS access. Open/closed state does not indicate review health.

Set `web_enabled` and an immutable `web_image_digest` to provision the site.
The `/crux/monitoring/web` SecureString contains JSON with `origin`, `bucket`,
and `idp` (`entityId`, `singleSignOnService.url`, and `x509cert`). Register the
site as an Identity Center custom SAML application: audience `/auth/metadata`,
ACS `/auth/callback`, and start URL `/auth/login`, all under that HTTPS origin.
Map Subject to `${user:email}` with format `emailAddress`. Also map `email` to
`${user:email}` with format `basic`: with only Subject mapped, AWS emits an empty
`AttributeStatement`, which fails strict SAML schema validation. Assign only
operators to the application. Load IdP metadata from that completed application;
different applications have different issuer URLs and certificates. AWS controls
the sign-in method and MFA policy. The app accepts signed, requested assertions; portal launches must go
through the start URL. There is no shared password or access-key login form.
Without IdP configuration, public pages work and sign-in returns unavailable.
Sessions expire after at most one hour; removing an assignment prevents new
sessions. Delete the user's `SESSION#` records for immediate session revocation.

The deployed **CRUX incident management** application is assigned to the
**All HAL** Identity Center group. Andrew's membership was verified before his
individual application assignment was removed. Manage operator access through
that group; all assigned members can view private incident details and change
incident status. Raw S3 evidence additionally requires AWS permissions.
The deployment account `881004720495` is named
**CRUX - Operations and Experiments** (formerly **CRUX #2 Testing**).

Build the web image with `Dockerfile.web`. Terraform user-data bootstraps a new
host; changing its image variable does not restart an existing instance.
For updates, use SSM on the web host to update the immutable image and revision
in `/opt/crux-incidents/start`, then restart `crux-incidents.service`. Restart
that service after changing SAML settings so all Gunicorn workers reload them.
No SSH ingress is required. Worker roles cannot write login or session records.

Keep `Referrer-Policy: same-origin` on operator pages. `no-referrer` makes native
form submissions send `Origin: null`, which the origin check rejects. Validate
close, reopen and sign-out with actual browser forms; a synthetic HTTP client
that supplies Origin explicitly does not exercise this browser behaviour.

Before enabling `incident_state_enabled`, pause discovery, let existing jobs
finish, and run `migrate_incidents.py --table <incident-table>` with the existing
monitoring environment. It imports saved reports, preserves known resolutions,
and seeds Slack's cumulative count baseline. Re-running the import preserves
operator changes. Then deploy the new worker image, enable stateful ingestion,
and resume discovery. The old public S3 HTML becomes a link to the live site.
Do not roll back to a worker that publishes raw findings into that public HTML.

Incident correlation version 1 uses workload ID (EC2 ID unless explicitly
configured), a fixed detector ID, and one primary source event: Langfuse
observation ID, CloudWatch event ID, or SFTP path plus content digest. A changed
file snapshot is a distinct source revision. Historical reports without a
unique primary event retain a legacy source-set anchor, rather than guessing
which event caused the finding. Original wording remains in private observations.
Close/reopen changes use conditional version checks and an atomic audit event.
Repeated reviews preserve manual status. Reopening changes the open count but
not the cumulative discovery count. Incidents and audit records have no TTL;
the existing S3 evidence retention still applies.

## Deployment pipeline and credentials

Run **Monitoring checks** with `deploy=true` on an allowed branch to deploy the
tested commit. Pull requests run the same behavior, image, and Terraform checks
without AWS credentials. Deployment is explicit; merging alone does not deploy.
Deployments are serialized and are not cancelled when another run is requested.

The GitHub environment `crux-monitoring` permits `main` and the current
`ae-211-ec2-monitoring` review branch. Remove the review branch when it is retired.
Its two variables are `MONITORING_AWS_ROLE_ARN` and
`MONITORING_CONFIG_BUCKET`. GitHub OIDC assumes `crux-monitoring-deploy` in the
deployment account; the trust requires audience `sts.amazonaws.com` and subject
`repo:sage-princeton@292236392/crux-in-a-box@1314254060:environment:crux-monitoring`.
The immutable owner/repository IDs must match the repository's OIDC settings.
No long-lived AWS
keys belong in GitHub secrets. The role's permissions are recorded in
[`ci/deployment-policy.json`](ci/deployment-policy.json); it can administer the
incident web host through SSM, so keep deployment access limited to operators.

Runtime secrets stay in `/crux/monitoring/env` and `/crux/monitoring/web` as SSM
SecureStrings. The worker and web instance roles read their respective parameters
at runtime. CI does not retrieve their values. Changing web authentication
configuration requires restarting `crux-incidents.service`; redeploying also
reloads it. Worker jobs load the monitoring parameter when they start.

Terraform state uses the versioned, private state-account bucket and KMS key in
[`terraform/backend.hcl`](terraform/backend.hcl), with S3 lockfiles. The backend
role trusts the exact deployment role and the current CRUX administrator role.
If Identity Center recreates that administrator role, update the trust explicitly.
Never initialize this deployment against a new empty state or copy an old local
state over the shared state.

Non-secret deployment inputs live at `config/deployment.json` and
`config/registry.json` in the monitoring bucket. CI builds commit-tagged immutable
images, plans against the locked shared state, and permits only updates to the
existing release resources. Creation, replacement, or unrelated infrastructure
changes require an operator-reviewed apply. It then updates the web host's boot
script through SSM, restarts the service, and checks the public HTTPS revision.
A failed health check fails the deployment; it does not automatically revert
incident data. Redeploy a previously validated compatible commit for rollback.
The registry and schedule retain their explicit expiry; CI does not extend it.
