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
| DynamoDB | `crux-monitoring-ae211` (reviews/budgets), `crux-monitoring-ae211-incidents` (incidents/auth) |
| Terraform state | Account `869937524494`; [backend.hcl](terraform/backend.hcl) |

Public visitors see summaries and status history. All HAL members can view private
observations/notes and close or reopen incidents; Andrew inherits access through
the group. Raw S3 evidence additionally requires AWS permissions. Sessions last
at most one hour; removing a group assignment prevents new sessions. Delete the
user's `SESSION#` records when immediate revocation is required.

## Deploy

Run the checked branch through GitHub Actions:

```sh
gh workflow run monitoring-checks.yml --ref <branch> -f deploy=true
```

The `crux-monitoring` environment permits `main` and `ae-211-ec2-monitoring`.
Remove the review branch when retired. Merging alone does not deploy. GitHub OIDC
assumes `crux-monitoring-deploy`; no static AWS keys are needed. Environment
variables are `MONITORING_AWS_ROLE_ARN` and `MONITORING_CONFIG_BUCKET`.

CI builds immutable images, scans their exact ECR digests, applies locked shared
Terraform state, reconciles the complete app/proxy configuration through SSM, and verifies the public
HTTPS revision. Releases may briefly restart the host. CI rejects infrastructure
creation, replacement, IAM policy edits and unrelated changes; those need a reviewed operator apply.
Never apply stale local tfvars or overwrite shared state with a local copy.

Deployment inputs are `config/deployment.json` and `config/registry.json` in S3.
Secrets stay in SSM SecureStrings `/crux/monitoring/env` and `/crux/monitoring/web`.
Changing web authentication settings requires restarting `crux-incidents.service`.
Rollback by deploying a previously validated, state-compatible commit; data is
not automatically reverted. Do not restore legacy workers that publish raw findings.

## Evidence and monitoring

- Review [the prompt](prompts/reviewer.md) and approve evidence before activation.
  Only scrubbed exports and approved Langfuse content may reach the model provider.
  Reviewer and subject model families must differ; unknown families fail visibly.
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
Python/Terraform tests, Checkov 3.3.19, Trivy 0.75.0 and Bandit 1.9.4.
Source secrets, medium/high Bandit findings and **fixable high/critical** image
vulnerabilities block releases. Scanner failures also fail the job. Full JSON
reports retain all severities and unfixed findings for 14 days in
`monitoring-security-reports` and `deployed-image-security-reports` artifacts.
Checkov findings block CI; its report is `monitoring-terraform-security` (14 days).
Run locally: `checkov -d src/monitoring/terraform --framework terraform --skip-download`.
Resource-local exceptions explain legacy public HTML, SSE-S3 log delivery,
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
