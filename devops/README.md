# DevOps

The Invoker validates inputs, creates a Box, assembles and executes a configuration
bundle, verifies the Scaffold, then starts the Run explicitly. Shared operations
live here; scaffold-specific installation hooks stay small. The root README
contains the common terminology.

- [Run Boxes](ec2-workspaces/README.md): EC2 lifecycle and Scaffold configuration.
- [AgentRQ controller](ec2-control/README.md): shared controller installation.
- `aws/`: reusable AWS administration utilities.
- `layout.json`: versioned component paths consumed by `ciab-devenv` and bundle assembly.

Run inputs remain scaffold-specific. SDK inputs are under
`scaffolds/sdk/run-harness/`; Codex/Claude inputs live with their respective
Scaffolds. These repository paths are separate from the installed
`/srv/crux-run/run-harness` directory. The previous root OpenClaw input bundle was deleted.

## Layout migration

| Previous path | Current path |
| --- | --- |
| `src/ec2-workspaces` | `devops/ec2-workspaces` |
| `src/ec2-control` | `devops/ec2-control` |
| `src/monitoring` | `monitoring/service` |
| `src/sdk-scaffold` | `scaffolds/sdk` |
| SDK `examples/` | SDK `run-harness/` |
| `agentrq/{codex,claude}` | `scaffolds/{codex,claude}/config` |
| workspace Codex exporters | `monitoring/tracing/codex` |
| Claude tracing hook | `monitoring/tracing/claude` |
| `tests/codex-observability` | `monitoring/tracing/tests` |
| `tests/workspace-provisioning` | `devops/ec2-workspaces/tests` |
| Langfuse cost Lambda | `monitoring/costs/langfuse` |
| `utils/scan-secrets.py` | `utilities/security/scan-secrets.py` |
| `utils/manage-elastic-ips.sh` | `devops/aws/manage-elastic-ips.sh` |
| `utils/bootstrap-gog.sh` | `utilities/bootstrap/bootstrap-gog.sh` |
| `linux/`, root `run-harness/`, OpenClaw export tools/provider cost Lambdas | Deleted; recover from Git history |

The old Invoker launcher commands and scanner forward to their new locations for
one migration period. Move new feature files to the owning component when rebasing;
do not extend the forwarding scripts. In particular, PR #16's auxiliary AWS code
belongs under DevOps, PR #20's pilot inputs belong with its selected Scaffold,
and provider changes in PR #73 belong under `scaffolds/sdk`. Existing deployed
paths and systemd identities are unchanged by the repository move.

The paired `ciab-devenv` reads `layout.json` and can still select older PRs without
that manifest. Its fixtures remain in that repository; its configuration adapters
are in `devops/` and report execution in `monitoring/`.

`src/monitoring/terraform` temporarily links to the relocated Terraform directory
so the trusted planning workflow on the default branch can validate this layout
PR before that workflow itself is merged. New automation uses
`monitoring/service/terraform`; the link contains no duplicate implementation.
