# Monitoring deployment

Desired settings live in `terraform/production.auto.tfvars.json` and
`monitoring.json`. Releases never reconstruct desired settings from old S3
configuration. `config/release.json` stores only the deployed revision and two
image digests for speculative PR plans.

The deployment workflow builds immutable app and proxy images, scans them, plans
Terraform, retires the old monitoring pipelines, then applies infrastructure. The
existing public address and SAML origin are preserved. The shared app image runs
both web and worker containers. `alembic upgrade head` must succeed before either
container starts; HTTPS readiness verifies PostgreSQL connectivity and the exact
revision, and an unauthenticated page must redirect to sign-in.

## Fresh-data cutover

The reset deliberately preserves no historical monitoring data. `reset.py`:

1. Verifies the AWS account and exact retired monitoring namespace.
2. Disables both old schedules and queues, terminates their jobs, and waits for
   active jobs to stop before deleting artifacts.
3. Removes deletion protection only from the two monitoring-owned DynamoDB
   tables, which Terraform then destroys with the retired Batch, Scheduler and
   notification infrastructure.
4. Deletes every version and delete marker under the retired configuration,
   inventory, reviews and status-check prefixes. New SQL-era collections,
   assessments and whole workspace snapshots are excluded from this cutover.

Alembic creates a fresh RDS PostgreSQL schema. There is no historical import,
compatibility writer, or migration from DynamoDB. Failed releases can be rerun:
the cutover does not erase new snapshots or PostgreSQL data. Remove the cutover
script after the first successful deployment.

## Roles and state

`deployment-policy.json` is the existing `terraform-deployment` inline policy on
`crux-monitoring-deploy`. It intentionally grants `Action: "*"` and `Resource:
"*"`. Terraform does not manage this deployment role's own permissions; the reset
retains that policy.

The runtime role can read inventory, fetch its configuration and RDS-managed
credentials, access private evidence, and invoke the dedicated workspace-copy
SSM document. Its upload role grants only workspace writes, narrowed by a session
policy to one snapshot object. Run hosts need SSM management; the existing
`crux-system-role` receives `AmazonSSMManagedInstanceCore`.

PR plans use the separate read-only `crux-monitoring-plan` role and read-only
cross-account Terraform state role. `plan-policy.json` grants the current RDS,
SSM, EC2, IAM, ECR and S3 read operations and is managed by Terraform. The plan
trust policy permits the main ref and the trusted `Monitor Terraform plan`
workflow; deployment refreshes it to remove the retired bootstrap branch.
Terraform's deployment backend keeps its existing cross-account state role.

After rollout, verify `/healthz` reports the merged revision, sign in and check
that running EC2 instances appear with both assessment results or explicit
coverage gaps. Verify a whole workspace object contains hidden and binary files,
and that Slack acknowledges enrollment. RDS automated backups cover seven days;
monitoring snapshots and collection rows expire after 90 days.
