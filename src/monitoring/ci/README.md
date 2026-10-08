# Monitoring deployment

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
its first incident review. No registry edit is needed for that run. The current run retains its explicit status registration; new runs use the
monitoring tag.
