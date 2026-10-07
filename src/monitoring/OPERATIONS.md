# Monitoring operations

Passive AWS Batch reviews report suspicious activity; they do not block workload
actions or install agents on targets. Missing evidence means incomplete coverage,
not a safety verdict. Running instances appear first; historical instances remain
visible. “No longer present” describes the instance, not whether an incident is closed.

## Access and resources

| Resource | Location |
| --- | --- |
| Incident log | https://34-193-109-221.sslip.io |
| Deployment account | `881004720495` — CRUX - Operations and Experiments |
| Identity Center | `805370850700`; **CRUX incident management** app; **All HAL** group |
| Web host | `i-02d24c35065046688`, service `crux-incidents.service`; SSM administration |
| Artifact/config bucket | `crux-monitoring-ae211-881004720495-us-east-1` |
| DynamoDB | `crux-monitoring-ae211-incidents` (reviews, budgets, incidents and auth) |
| Terraform state | Account `869937524494`; [backend.hcl](terraform/backend.hcl) |

All incident pages require AWS sign-in through **All HAL**. Members can view
observations/notes and close or reopen incidents; Andrew inherits access through
the group. Only SAML endpoints, CSS and the content-free readiness probe are anonymous.
S3 blocks public ACLs and policies; even the legacy HTML object requires AWS access. Raw S3 evidence additionally requires AWS permissions. Sessions last
at most one hour; removing a group assignment prevents new sessions. Delete the
user's `SESSION#` records when immediate revocation is required.

## Deploy

Pushes to `main`, including PR merges, automatically deploy after both monitoring
and Terraform checks pass. PR checks do not deploy. Newer merges do not cancel
an in-progress deployment; deployments remain serialized.

To deploy a checked branch manually through GitHub Actions:

```sh
gh workflow run monitoring-checks.yml --ref <branch> -f deploy=true
```

The `crux-monitoring` environment permits `main` and `ae-211-ec2-monitoring`.
Remove the review branch when retired. GitHub OIDC
assumes `crux-monitoring-deploy`; no static AWS keys are needed. Environment
variables are `MONITORING_AWS_ROLE_ARN` and `MONITORING_CONFIG_BUCKET`.

CI builds immutable images, scans their exact ECR digests, applies locked shared
Terraform state, reconciles the complete app/proxy configuration through SSM, and verifies the public
HTTPS revision and anonymous redirects to sign-in. Releases may briefly restart the host. CI rejects infrastructure
creation, replacement, IAM policy edits and unrelated changes; those need a reviewed operator apply.
Never apply stale local tfvars or overwrite shared state with a local copy.

Deployment inputs are `config/deployment.json` and `config/registry.json` in S3.
Secrets stay in SSM SecureStrings `/crux/monitoring/env` and `/crux/monitoring/web`.
Changing web authentication settings requires restarting `crux-incidents.service`.
Rollback by deploying a previously validated, state-compatible commit; data is
not automatically reverted. Do not restore legacy workers that publish raw findings.

## PR infrastructure plans

`monitoring-plan.yml` runs a live, speculative plan for same-repository PRs to
`main`. Its trusted workflow and helpers come from the base branch; only Terraform
comes from the PR commit. Fork and Dependabot PRs need a maintainer-owned branch
for AWS planning. The `Monitoring Terraform plan` commit check reports failure
when a plan cannot run. Resource changes create/update one PR comment; a later
no-op clears that comment without creating a new one.

The `crux-monitoring-plan` OIDC role reads infrastructure/configuration only;
`crux-monitoring-plan-state` reads the single Princeton state object. Neither can
apply or write state. Plans use deployed image inputs and `-lock=false`; releases
replan under the deployment lock. Artifacts/comments contain resource addresses
and actions only, never raw plans, state or attribute values.

GitHub issues a `pull_request` OIDC subject for `pull_request_target` runs. The
planning trust policy accepts it only with `ref=refs/heads/main` and the trusted
plan workflow name. Ordinary PR-controlled workflows use a PR merge ref and are
not trusted. Both `Monitoring PR plan` and `Monitor Terraform plan` are accepted
during the workflow rename; apply `ci/plan-trust.json` to the live role before
rerunning existing PR checks, since they use the workflow already on `main`.

Before this workflow reaches `main`, pushes to the review branch plan PR #21.
Once registered, rerun it with
`gh workflow run monitoring-plan.yml --ref ae-211-ec2-monitoring -f pull_request=21`.
Remove its temporary feature-branch push trigger and OIDC trust when retired.
Role policies/trust are versioned under `ci/plan-*.json`; repository variables are
`MONITORING_PLAN_ROLE_ARN` and `MONITORING_CONFIG_BUCKET`.

## Evidence and monitoring

### Independent project status checks

Project status uses a separate DynamoDB table (`<name>-status`), KMS key, Batch
queue/job roles, schedule, budget and failure queue. It reads approved evidence
directly and does not depend on incident reviews or write incident records.
The compute environment, image repository and private evidence bucket are shared.
Status artifacts live under `status_checks/`; the worker has no incident-table
access. The web role can only read the status table. AWS sign-in/session storage
still uses the existing incident table: an authentication-store outage fails
closed, while independent incident/status data-query failures are shown inline.

Status reports appear first in Workload coverage. Refresh reads stored snapshots;
it does not run models. History is private. Failed checks keep the previous
successful report with its original timestamps. Evidence older than the configured
threshold is visibly stale, even if a check just ran. Workload state is based on
independent inventory and is marked unconfirmed if that inventory is stale.

Configuration is `config/status.json` in the existing private configuration bucket.
Start from `status.json.example`; set an explicit expiry, budget, approved target,
and sweep/summary model pair before enabling it. Both models must be explicit
`anthropic/` or `openai/` models in the same family, different from the declared
and observed subject families. Status uses its own `/crux/status/...` SecureString,
containing only the applicable `MONITORING_OPENROUTER_API_KEY`, Langfuse credentials
and named read-only SFTP keys used by the shared collectors. No Slack credential
is needed. Approve CloudWatch access separately through
`status_evidence_log_group_arns`.

Each `targets` entry is keyed by an EC2 instance ID and contains:

```json
{
  "workload_id": "experiment-2026-10",
  "authorization": "Read the approved project evidence and report status only.",
  "subject_families": ["openai"],
  "langfuse": {"session_id": "VERIFIED-SESSION-ID"},
  "project": {"budget_usd": 100, "started_at": 0, "deadline": 0}
}
```

Use a new `workload_id` for each project/run on a reused instance. Sources use the
same explicit `langfuse`, `sftp` and `logs` formats as `registry.json.example`.
Replace zero timestamps or omit unknown baselines. Optional `spent_usd` plus
`spend_as_of` supplies a recorded project-spend snapshot; the UI labels its date.
The checker never substitutes inference costs or overlapping trace totals for
project spending. Missing spending, budgets, deadlines and quality evidence stay
unknown. A stalled judgment requires evidence beyond missing traces.

Default cadence is 900 seconds, staleness 1800 seconds. Sweeps read at most eight
24 KiB chunks; the summary call is limited to 64 KiB including instructions/schema.
Unread evidence and omitted originals are disclosed. Calls reserve a conservative
cost using provider prices before inference, at most $1 per call, against the
separate configured lifetime budget (maximum $500). Ambiguous failures are not
refunded. Recorded model usage is retained separately from the project report.
Provider 401/402/403 responses block further status inference in the status-table
`HEALTH` / `inference` record; correct the credential/billing issue before an
operator removes that record. Changing a schedule or config never resets budgets.

Initial rollout:

1. Merge the feature and let the existing main release publish compatible images.
   `status_provisioned=false` is the default; this does not create resources or
   enable inference during the ordinary release.
2. Prepare the dedicated SecureString and a disabled status config for one approved
   workload. Use deployed inputs and shared Terraform state; add
   `status_provisioned=true`, `status_registry_file`, and
   `status_secrets_parameter_arn` (plus its KMS key ARN if customer-managed).
3. Review and apply the bootstrap plan as an operator. It creates the table/key,
   queue, roles, disabled schedule, failure queue/alarm, S3 config/retention and web
   read permissions. Apply the updated `ci/plan-policy.json` and
   `ci/deployment-policy.json` to their existing CI roles as part of this reviewed
   setup. CI still refuses resource creation, replacement and IAM edits.
4. Persist the new inputs in `config/deployment.json` with portable file names
   (`registry.json`, `status.json`). Subsequent plan/deploy helpers fetch both
   configs; ordinary releases update the status job definitions and web config.
   Reconcile the web service so `STATUS_TABLE` names the new table.
5. Set the selected models, finite budget and future expiry. Enable status in its
   config and apply the reviewed schedule/config change. Keep incident enablement,
   expiry, budget and schedule unchanged. Observe one workload for two intervals.

Healthy signals: `status-discover`/`status-check` Batch jobs succeed; logs contain
`"outcome": "completed"`; the status table's `FLEET` latest-success and evidence
timestamps advance; Workload coverage shows the five-line report above incidents.
The `<name>-status-operations-pending` alarm and its separate queue expose job or
schedule failures. On repeated failures, stale evidence or unexpected costs,
disable `enabled` in status config and disable its scheduler through an operator
apply; preserve the table/history and do not reset budget reservations. Status
history/artifacts expire after 90 days; latest snapshots and active budget state
are retained. Queued checks use current authorization and cannot switch to another
workload ID. Discovery requeues expired leases for up to 24 hours, bounded to three
attempts per window; newer reports cannot be replaced by old jobs.

Manual operator diagnostics (same configuration and role as the Batch job):

```sh
python status_worker.py --help
python status_worker.py discover
python status_worker.py check <instance-id> <workload-id> <completed-window-epoch>
```

These commands write status state and may spend the configured inference budget;
they never run workload commands. Web reads and refreshes remain read-only.

### Incident reviews

- Review [the prompt](prompts/reviewer.md) and approve evidence before activation.
  Only scrubbed exports and approved Langfuse content may reach the model provider.
  Only explicit `anthropic/` and `openai/` reviewers are accepted. Reviewer and
  subject families must differ; unknown families fail visibly.
- SFTP requires a dedicated `crux-inspect` user, root-owned chroot/exports,
  `ForceCommand internal-sftp -R`, disabled shell/forwarding/password login, and a
  pinned host key. Verify reads succeed and writes, deletion, shell and forwarding
  fail before setting `boundary_verified=true`. Never expose research home directories.
- Langfuse currently uses a read/write key with GET-only worker behavior; this is
  **not enforced read-only access**. TODO: provide read-only Langfuse credentials.
- Discovery runs every five minutes. Retired fleet targets get 30 minutes for late
  evidence; queued/retrying windows retain their approved source mapping. Unchanged fleet digests are suppressed;
  ambiguous Slack delivery can still duplicate a message. Inspect Batch failures,
  the operations queue and CloudWatch alarms when reviews stop.
- Authentication/billing errors set `HEALTH#reviewer.blocked`. Fix credentials or
  provider budgets, then remove that health row to resume. Dedicated API-key and
  provider workspace limits both matter; inference limits do not cap AWS costs.
- The current schedule expires **2026-10-02 19:33:56 UTC**. Registry expiry also
  stops work; CI does not extend either. Evidence is retained for 90 days.

SFTP exports use JSON/JSONL records (prefer stable event IDs) or complete text lines;
appending records preserves existing incident identities.

Incident IDs use workload, detector and source-event identity. Repeated reviews
preserve manual closure; reopening is not a new discovery. Status changes and
history use conditional atomic writes. Incidents and audit events have no TTL.

## Authentication troubleshooting

Use the site's `/auth/metadata` as audience, `/auth/callback` as ACS and
`/auth/login` as start URL. In Identity Center map Subject to `${user:email}` with
format `emailAddress`, **and** `email` to `${user:email}` with format `basic`.
Subject alone produces an empty AttributeStatement that strict SAML rejects.
Keep the application's own IdP metadata/certificate in the web SecureString.

Keep `Referrer-Policy: same-origin`: `no-referrer` makes native POSTs send
`Origin: null`, which is correctly rejected. Verify close, reopen and sign-out
using browser forms. SAML diagnostics log categories, not assertions or tokens.

## Security checks

Every PR builds and scans the worker, web app and patched Caddy proxy, and runs
Python/Terraform tests, Ruff 0.16.10 lint/format, Checkov 3.3.19, Trivy 0.75.0 and Bandit 1.9.4.
Source secrets, medium/high Bandit findings and **fixable high/critical** image
vulnerabilities block releases. Scanner failures also fail the job. Full JSON
reports retain all severities and unfixed findings for 14 days in
`monitoring-security-reports` and `deployed-image-security-reports` artifacts.
Checkov findings block CI; its report is `monitoring-terraform-security` (14 days).
Run locally: `checkov -d src/monitoring/terraform --framework terraform --skip-download`.
Resource-local exceptions explain existing SSE-S3 evidence/log encryption,
existing ECR encryption, single-region storage and the no-NAT worker subnet.
Data/log KMS keys rotate annually and are protected from Terraform destruction.
Job/access logs retain one year; owned VPCs also get flow logs and an empty default
security group. Existing shared VPCs remain owner-managed. Docker hardening is advisory.

The 1 October scan found no source secrets, no medium/high Bandit findings and no
fixable high/critical image vulnerabilities after patching Debian packages and
Flask and removing pip/ensurepip from runtime images. Eight unique high-severity
Debian CVEs remain unfixed, plus a low Paramiko finding. Rebuild when fixes arrive;
a passing gate does not mean vulnerability-free. Follow-ups: login rate limiting,
HTTPS egress restrictions and eventual ECR/customer-key migration.
Install dependencies by rebuilding images, not modifying running containers.

## Model costs

The 5 October audit found 1,038 saved review records: 15 Sonnet 4.6 calls and
1,023 without inference. Recorded API charges total $0.593118; average $0.03954,
maximum $0.07839. Average input/output: 5,903 / 1,455 tokens; maximum input:
14,680 tokens. These are saved response charges, not a complete provider invoice.
At the same average, a continuously active five-minute reviewer costs about
$11.39 per instance/day; idle windows avoid inference.

Each call is limited to 128 KiB of input including prompt/schema allowance, 6,000
output tokens, and a conservative $1 reservation using the highest listed price
tier. Oversized evidence becomes a visible coverage failure; it is never silently
truncated. The existing $100 global reservation limit and dedicated provider key
cap still apply. The current $3.69995 reserved is not actual spend; ambiguous calls
are not refunded. The registry/schedule expired on 2 October and remain expired.

Operational records use `sk=OPERATION` in the shared table; incident/auth keys
remain unchanged. The old table's on-demand backup
`crux-monitoring-ae211-pre-consolidation-20261005` is retained.
Never deploy a pre-consolidation worker without restoring its table and reconciling
new operational rows, or budgets and notification acknowledgments could regress.
