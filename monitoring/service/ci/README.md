# Monitoring release automation

Build, scan, plan and deploy the monitoring service. Requires GitHub Actions,
configured AWS OIDC roles, Terraform and Docker. Shared account settings live in
`../terraform/production.auto.tfvars.json`; application settings in `../monitoring.json`.

## Usage

1. Open a PR and inspect the monitoring checks and Terraform plan.
2. Merge the reviewed change; the deployment workflow builds and applies it.
3. Verify `/healthz` reports the merged revision, sign in, and inspect fleet coverage.
4. Confirm workspace snapshots and intended Slack notifications after rollout.

Read [design.md](design.md) before operating the one-time destructive SQL cutover.

## Development

Use the [parent runbook](../README.md#development) for application checks. For
Terraform and workflow changes, run from the repository root:

```sh
actionlint
terraform -chdir=monitoring/service/terraform fmt -check
terraform -chdir=monitoring/service/terraform init -backend=false
terraform -chdir=monitoring/service/terraform validate
terraform -chdir=monitoring/service/terraform test
```

Review the trusted GitHub Terraform plan before merging infrastructure changes.
