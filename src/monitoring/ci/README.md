# Monitoring deployment permissions and recovery

`deployment-policy.json` is the inline `terraform-deployment` policy on
`crux-monitoring-deploy` in account `881004720495`. An administrator installs it;
Terraform and the deployment workflow do not manage this role's own permissions.
Merging a policy edit does **not** update the live role.

The deployment policy grants full administrator permissions (`Action: "*"`,
`Resource: "*"`) without service, resource, tag, or region conditions. This
deliberately allows deployment changes throughout the account, including IAM
and KMS administration, without a per-service allowlist.
The role's trust policy still controls who can assume it. AWS organization
policies, permission boundaries, and resource policies can still limit effective
access; this identity policy does not override explicit denies or cross-account
trust requirements.

## Automatic run enrollment

Production enables `auto_register_runs` in `status.production.json`. Each status
discovery (every 15 minutes, plus Batch startup) registers EC2 instances tagged
`MonitorWithCruxMonitor=1`; both the workspace provisioner and
`linux/create-new-crux-box.sh` apply this tag to new and reused boxes. The value
must be exactly `1`; `CruxRole=run` alone does not enroll status checks. Control,
monitoring web and Batch hosts are excluded. Explicit status targets override
automatic mappings. New automatic targets use the instance ID as their stable
workload identity, so replacing a box with the same name creates separate history.
A unique, nonempty EC2 Name maps to the matching Langfuse environment. Duplicate
or missing names enroll with a coverage gap rather than reading another run's
traces. Observed models must be recognized and different from the configured
reviewer family; missing telemetry remains unknown.

Incident fleet discovery always runs every five minutes once its worker image is
deployed. Terraform retains the deployed registry's explicit targets,
exclusions, models and budget, enables name-based Langfuse discovery, and removes
the registry and scheduler expiry. This repairs the October 2 expiration that
otherwise prevents incident discovery. Both inference budgets remain cumulative
and bounded; enrollment does not reset them. There are no incident activation
switches or schedule end date. Deployment and PR planning discard the retired
`enabled`, `continuous_fleet_monitoring`, and `schedule_end` inputs from older
deployment snapshots.

The incident digest sends “Monitoring has begun” after a running instance has a
queued status check and at least one recorded incident review attempt (including
an idle or failed review). This confirms enrollment, not healthy telemetry or a
successful model assessment. Existing running boxes also receive their first notice
on rollout. A durable, leased acknowledgement suppresses repeat notices for that
instance across discoveries and restarts. Failed Slack deliveries retry; an
ambiguous timeout after Slack accepted a message can still cause a duplicate
because the webhook send and database acknowledgement are separate operations.
Status workers never receive Slack credentials or access to incident state; the
incident notifier can only read the status inventory.

After deployment, verify both discovery schedules are enabled with no end date,
then start a normally provisioned run. Confirm it appears in status and incident
inventory, receives jobs in both queues, and produces one enrollment notice after
its first incident review. No registry edit is needed for that run. The existing
explicit status entries retain their histories.

## Recover the failed status activation

The activation deployment failed on `UpdateTimeToLive` with `kms:Decrypt`
denied. It created `crux-monitoring-ae211-status`, but Terraform marked
`aws_dynamodb_table.status[0]` tainted. No status job definitions or schedule
were created. The empty status inventory causes the website to show
“Project status checks are not registered for this workload.”

Use administrator credentials for account `881004720495`. Wait for any active
monitoring deployment to finish before repairing its state. Run these commands
from the repository root, using the reviewed policy revision:

```sh
aws sts get-caller-identity --query Account --output text
# Must print 881004720495.
aws iam put-role-policy --role-name crux-monitoring-deploy \
  --policy-name terraform-deployment \
  --policy-document file://src/monitoring/ci/deployment-policy.json

aws dynamodb describe-table --region us-east-1 \
  --table-name crux-monitoring-ae211-status \
  --query 'Table.{Status:TableStatus,Key:KeySchema,Attributes:AttributeDefinitions,Encryption:SSEDescription,DeletionProtection:DeletionProtectionEnabled}'
aws dynamodb describe-time-to-live --region us-east-1 \
  --table-name crux-monitoring-ae211-status
```

Verify the existing table is `ACTIVE`, has string `pk`/`sk` keys, uses the
monitoring status KMS key, and retains deletion protection. Compare the live
table ID and key ARN against `terraform state show` below. The failed TTL
configuration can be updated in place; the table does not need replacement.

```sh
terraform -chdir=src/monitoring/terraform init -input=false -lockfile=readonly \
  -backend-config=backend.hcl
terraform -chdir=src/monitoring/terraform state show 'aws_dynamodb_table.status[0]'
# Only after verifying the existing resource and the specific TTL failure:
terraform -chdir=src/monitoring/terraform untaint -lock-timeout=5m \
  'aws_dynamodb_table.status[0]'
```

Untaint preserves the existing table and lets Terraform reconcile TTL and point
in time recovery on the next apply. Do not disable deletion protection, delete
the table, remove it from state, or force its replacement. If the table differs
from the expected resource, investigate before modifying state.

After the fix is merged and the policy/state recovery is complete, run the
normal deployment through `Lint monitor`:

```sh
gh workflow run monitoring-checks.yml --ref main -f deploy=true
```

Confirm the deployment succeeds, then verify
`crux-monitoring-ae211-status` in the same-named Scheduler group is enabled with
`rate(15 minutes)`. After the next interval and Batch startup, confirm discovery
succeeds and the status table's `FLEET` partition contains the current run
`crux-web-pilot3` and the retired runs `pr19-test` and `crux-web-pilot`. The current
run uses its matching Langfuse environment; its observed model is `gpt-6.1-sol`,
so the configured Anthropic reviewers remain independent. Each running target
should receive a check job and publish a result. Refresh the website to confirm
registration and check timestamps. A successful image build or `/healthz` response alone does not
verify status-job execution.
