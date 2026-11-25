# Auxiliary AWS access

Grant one EC2 workspace access to a separate, pre-existing, single-tenant AWS account. The operator needs Python 3, AWS CLI profiles with IAM and EC2 access to each account, and SSH access to the run box.

1. Set `ISOLATED_RUN_IDENTITY=1` in the workspace base config. Run `devops/ec2-workspaces/make-new-workspace.sh` as usual. The box joins the standard run security group and uses the dedicated `crux-run-SLUG` instance role. Its baseline policy reads `/crux/system/env`.
2. In the generated `placeholders-SLUG.txt` set `AUX_RESOURCE_PROFILE` to the auxiliary account’s explicit CLI profile. Keep the main account in `AWS_PROFILE`. Set the required services to 1 using `PROVISION_POSTGRES`, `PROVISION_S3`, `PROVISION_EC2`, `PROVISION_DNS`, `PROVISION_CLOUDFRONT` and `PROVISION_ACM`. The account must already exist. A handoff transfers the auxiliary role’s trust to this workspace and revokes the previous workspace’s ability to assume it. Retained infrastructure continues billing.
3. Before starting a task, run the following from the repository root.

```sh
python3 utilities/aux-aws/provision.py devops/ec2-workspaces/placeholders-SLUG.txt --dry-run
python3 utilities/aux-aws/provision.py devops/ec2-workspaces/placeholders-SLUG.txt
```

The command reconciles the main role’s assume-role policy, auxiliary trust, selected service permissions, Cost Explorer reads, and the existing EC2 SSH allowlist. EC2 plus S3 enables application roles capped by the existing permissions boundary. It records `AUX_RESOURCE_ACCOUNT_ID` and `AUX_RESOURCE_ROLE_ARN` in the per-run config and root-only `/etc/crux-run.env`. Repeat after a partial failure. An existing workspace with these values keeps its profile and permissions on reprovision. An ACP gateway already running after initial box setup loads new environment values on its next start. Before submitting the first task, run `ssh SLUG sudo systemctl restart crux-acp-gateway`. For an SDK run, start its service normally.

Use workspace teardown with `--keep-aux` to preserve the auxiliary deployment for a successor. Teardown removes the workspace's managed SSH ingress before releasing its IP. Without `--keep-aux` the sweep deletes resources in the dedicated account, including EC2, RDS, S3 contents and application roles. Domain registrations remain billable until expiry. Once a successor holds the trust, running teardown with the former owner's config removes its obsolete main-account role and preserves the successor’s resources. Review the confirmation summary before continuing.
