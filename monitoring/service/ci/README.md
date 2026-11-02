# Monitoring release automation

Build, scan, plan and deploy the monitoring service. Requires GitHub Actions,
configured AWS OIDC roles, Terraform and Docker. Shared account settings live in
`../terraform/production.auto.tfvars.json`; application settings in `../monitoring.json`.

1. Open a PR and inspect the monitoring checks and Terraform plan.
2. Merge the reviewed change; the deployment workflow builds and applies it.
3. Verify `/healthz` reports the merged revision, sign in, and inspect fleet coverage.
4. Confirm workspace snapshots and intended Slack notifications after rollout.

Use the [parent runbook](../README.md) for local checks. Read
[design.md](design.md) before operating the one-time destructive SQL cutover.
