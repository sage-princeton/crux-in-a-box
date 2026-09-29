# Passive EC2 monitoring

The reviewer runs as an unprivileged, tool-free container in AWS Batch on EC2.
EventBridge schedules discovery every five minutes. Discovery inventories EC2
instance IDs and states; only explicitly registered targets receive reviews.
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
available, `model.json`, `report.json`, and
`manifest.json` under an immutable attempt prefix in the private versioned S3 bucket.
The manifest records object version IDs and SHA-256 digests. The prompt is the exact
system instruction; evidence is the exact user payload. Intermediate artifacts
survive later review failures. Retention is 90 days. Slack receives all findings
without a severity threshold, with a short summary, numbered findings, possible
benign explanations, coverage gaps, and a labeled AWS-console link to the notes.
Model-authored text is redacted and rendered as literal text, never active mentions
or links. Failed inference is labeled “Review unavailable — no safety verdict.”
Set a target's optional `display_name` to identify it clearly in Slack.
The link requires the reader's S3 permissions;
it is not a public object or a bearer URL. Duplicate windows/alerts are intentional
for this first noisy iteration. Slack acknowledgement and DynamoDB cannot form one
transaction: an ambiguous response can cause duplicate delivery with the same ID.

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
remove that health row as an operator to resume inference. Other reviews continue
to report the coverage gap without sending additional paid model requests.

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
4. `terraform init`, `terraform plan -out=monitoring.tfplan`, then inspect the plan:
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
