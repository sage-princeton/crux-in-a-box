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

Standalone addresses use `crux-addresses list|create|delete` (or the forwarding
`devops/aws/manage-elastic-ips.sh`) with explicit `--profile`, `--region` and
`--account`. Creation takes `--name`; deletion takes `--allocation-id` or a unique
`--name`, plus `--yes`. Only detached `CruxRole=shared-eip` allocations can be
deleted; Run-owned addresses remain under their deployment lifecycle. Shared
allocations can be referenced as external resources by a Run or controller.

## Dependency updates

`.github/dependabot.yml` checks weekly on Monday at 09:00 America/New_York.
Version updates are grouped by component/ecosystem; security updates exclude all
packages from grouping and are driven immediately by Dependabot alerts, independent
of the version schedule. Enable dependency graph, Dependabot alerts and security
updates in repository settings. Security groups deliberately match no packages,
so repository-wide grouping cannot combine these patches. No target branch,
cooldown or security-update ignore rule delays supported security fixes.

Production installs consume the manifests Dependabot edits: DevOps Python bootstrap
requirements, SDK requirements/pyproject, Scaffold npm locks, controller Compose,
monitoring Python/npm/Go/Terraform, and Dockerfiles for runtime/CI tools. CI exports
Trivy and Terraform from their declared images; secret scans run their declared
image and omit raw findings from logs. New dependencies belong in these consumed
manifests, not duplicate version strings in shell or workflow inputs.

APT packages are declared in `devops/apt/*.txt`; the same installer supplies Box
and daily Ubuntu audit image packages. The audit exports SPDX and vulnerability
JSON as Actions artifacts and prints fixable high/critical findings for review.
These reports are informational, not a new merge gate. Ubuntu's unattended security
updates remain enabled; agent units remain exempt from disruptive mid-turn restarts.
NodeSource and Caddy repositories are included in the audit. Refresh installed
packages between Runs and inspect the actual Box inventory, since the audit image
is not a snapshot of every deployed Box or kernel.

APT is not a Dependabot ecosystem. Submitting SPDX would not give Dependabot
support for Debian/Ubuntu package fixes, so these artifacts are retained for
inspection rather than advertised as automatic apt updates. Remaining external
inputs include the official AWS CLI v2 installer (agent utility; the Invoker uses
manifest-managed boto3), selected EC2 AMIs, vendor apt repository setup/key URLs,
and operator-declared Run software. Those require upstream/OS review and fresh
provisioning; they do not silently become Dependabot-managed. GitHub-hosted runner
software and runtime major lines remain selected by their respective CI actions.
