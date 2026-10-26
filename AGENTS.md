# Working on CRUX in a box

Read the root README for the Box, Run, Scaffold, Invoker, Agent and Harness
terminology. Use these terms consistently in code and documentation. The active
Scaffolds are AgentRQ/ACP and Agents SDK. The legacy OpenClaw implementation
was deleted; recover historical code and operational guidance from Git history.

## Boundaries

- `devops/` owns declarative configuration, preflight, Box/resource lifecycle,
  bundle assembly and Scaffold configuration. Validate before mutation, configure
  before starting a Run, and keep resource ownership and teardown policy explicit.
- Use Python-native AWS/HTTP/configuration/packaging operations. Keep apt and
  systemd administration in focused shell scripts; do not wrap shell programs
  in Python as a substitute for extracting their native operations.
- `scaffolds/` owns execution, operator-message interfaces and scaffold-specific
  Run inputs. Follow its nested AGENTS.md. Reuse existing component interfaces.
- `monitoring/` owns observations, costs and incident assessment. Component-specific
  deployment files and tests stay with that component.
- `utilities/` holds reusable support tools. Do not retain deleted implementations
  in an archive directory; Git history is the recovery mechanism.
- Keep software dependencies in package-manager manifests and install from those
  same declarations. Avoid a second set of versions hidden in shell or examples.

## Changes and validation

Use worktrees and small, testable stacked PRs, preferably under 800 changed lines.
Keep mechanical moves separate from behavior changes. Diagnose from actual
artifacts, fix the failure class, and update every active reference to a changed
concept. Repository design conversation notes are local working files, not commits.

Run the component's lint/tests and the shared root checks. Every relevant PR must
also be exercised through `ciab-devenv` using only that PR stack, the production
provisioning path and a named isolated Ubuntu Box. Reprovision when source or
configuration changes. Inspect actual service behavior, traces and work products.
Record revisions, commands, assertions, outcomes, models/services, evidence paths
and gaps in PR descriptions/comments. PR-specific verification stays outside Git;
`ciab-devenv` contains infrastructure and generic fixtures, not feature scenarios.
Export and cite relevant evidence outside the disposable environment after each
PR, including failures and interruptions. Then reset the named environment and
verify that its machine, containers and volumes are absent. Follow ciab-devenv's
AGENTS.md for cleanup; do not accumulate stopped machines or their data.

## Credentials and live Boxes

Always use an explicit AWS profile. If authentication fails, stop cloud operations
and ask the user to reauthenticate; never fall back to other credentials.
Secrets, private config and raw sessions stay outside source snapshots and Git.
Never print credential values. Scan changed files with
`python utilities/security/scan-secrets.py PATH...` before every commit.
Never pull raw sessions or telemetry into the checkout. Historical exports must
be scrubbed on the Box before transfer.

Live Boxes are read-only unless the user authorizes an intervention. Do not
restart an active Run or gateway mid-turn. Preserve existing state and credentials
on reprovisioning, stop before revoking keys, and act only on resources owned by
the selected deployment. Auxiliary resources may outlive the Box; teardown must
honor their declared retention and current ownership.
