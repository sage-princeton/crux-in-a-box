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
