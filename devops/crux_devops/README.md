# Deployment contracts

The Invoker reads a versioned JSON Run: its name, selected Box resource,
Scaffold and resource graph. `PYTHONPATH=devops python -m crux_devops plan RUN.json`
validates the contract without network access or mutations. `validate` performs
the same validation without displaying the plan. Install `devops/` as a Python
package to use the `crux` entry point.

A resource has an `id`, registered `type`, provider-specific `config`, optional
`depends_on` IDs, `ownership` (`managed` or `external`) and `teardown` (`delete`
or `retain`). External resources must be retained. Unknown fields, duplicates,
missing dependencies and cycles fail before creation. Scaffold types currently
implemented are `sdk`/`openai-agents` and `agentrq`/`codex` or `claude`; additional
providers can extend these without changing resource lifecycle.

```json
{
  "version": 1,
  "name": "research",
  "box": "box",
  "scaffold": {
    "type": "sdk", "platform": "openai-agents", "model": "gpt-5.4-mini",
    "effort": "low", "drop_in": "scaffolds/sdk/run-harness/slack-roundtrip"
  },
  "resources": [{"id": "box", "type": "aws.ec2", "config": {}}]
}
```

This illustrates the common contract; actual drivers validate their required
configuration before provisioning. A generic plan does not authenticate or
establish that a provider-specific deployment is possible. Secrets belong in
private input files or inherited environment variables, never in Run JSON.

## Driver interface and recovery

`Lifecycle` receives a registry of resource type to Driver. `preflight` must
validate inputs/access without mutation. All preflights run before any ensure.
`ensure` receives the previous receipt, dependency outputs and a stable
`Context.token(resource)` for idempotent creation. The returned JSON receipt
contains identifiers and safe outputs, never credentials. Resource providers
must tag objects with `context.owner`, recover creations by token after a crash,
and recheck ownership before deleting anything. External resources are validated
and resolved, never silently adopted or mutated.

State is atomic, private (0600), locked against concurrent Invokers and bound to
the immutable Run fingerprint. Keep it outside source and preserve it until
cleanup completes. Failures retain the receipt so the operator can retry or
explicitly destroy. No implicit rollback destroys retained research assets.
Teardown runs in reverse dependency order and records retained IDs; it can retry
an interrupted deletion. Once teardown starts, apply cannot recreate resources.

Model auxiliary grants separately from auxiliary assets: a grant can depend on
the Box and be deleted first, while a dataset or bucket declares `retain`.
PR #16 can implement those drivers, including its account boundary and successor
handoff checks, using the same registry. A driver must refuse deletion if the
remote owner changed after a handoff; a stale receipt is not authority. This
contract does not itself grant cross-account access or enumerate cloud assets.

The lifecycle package performs no shell orchestration. AWS, HTTP, archive and
configuration drivers belong in Python. Apt/systemd operations remain focused
Box-side shell programs. Provision/configure and explicit Run start are separate
operations; a successful resource apply alone does not mean a Run has started.

## AWS resources

`crux preflight RUN.json --state /private/path/state.json` checks provider inputs
and access without creating resources. `create` provisions the resource graph;
`destroy` uses the same Run and receipt to remove managed resources. Neither
operation starts an Agent. Use an explicit `profile`, `region` and 12-digit
`account` in each AWS resource's config. Authentication/account mismatch fails;
there is no ambient/default-profile fallback. Reauthenticate the declared
profile if its session expires.

| Type | Additional config | Behavior |
| --- | --- | --- |
| `aws.ec2` | `image_id`, `instance_type`, `subnet_id`, `security_group_ids`, `key_name`, `instance_profile`; optional `disk_gb`, `iops`, `throughput` | Creates an encrypted gp3 Ubuntu Box with IMDSv2, monitoring/ownership tags and a stable creation token; waits for EC2 health. |
| External `aws.ec2` | `instance_id` | Resolves a running Box without starting or adopting it; requires retention. |
| `aws.elastic_ip` | none | Allocates an address tagged with deployment ownership; recovers it by tag after interruption. |
| External `aws.elastic_ip` | `allocation_id` | Resolves an existing address without adopting or releasing it. |
| `aws.address_attachment` | `box`, `address` resource IDs; both in `depends_on` | Attaches the address to a managed Box in the same account/region, refuses to steal an existing association, and detaches before teardown of dependencies. |

Shared VPC/subnet, security groups, SSH key and instance profile must already
exist. This resource layer does not alter fleet IAM or controller configuration.
The selected image must contain Ubuntu and be compatible with the instance type.
Use a new Run/state file for a new deployment after teardown; keep the original
Run document with its private receipt until cleanup succeeds. An operator or
successor changing resource ownership causes deletion to fail for inspection.

Separate resources let an existing allowlisted IP survive Box teardown, while
its attachment is removed first. Future auxiliary-role drivers can use the same
pattern for grants, retained assets and ownership transfer. AWS API semantics
are implemented through [boto3](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/run_instances.html),
including EC2 client tokens; no AWS CLI shell program is invoked.

## Bundle and configuration

The [Box runbook](../ec2-workspaces/README.md) covers `bundle`, `provision`,
`configure`, `health` and `start`, including SSH trust, credential delivery and
configuration probes. `Invoker` accepts a transport separate from resource
drivers; `SSH` transfers the verified bundle and invokes focused Box-side
apt/systemd scripts. `ciab-devenv` uses the selected production bundle and staging
implementation with OrbStack transport and local connections.

`agentrq.workspace` implements the same resource interface through native HTTPS.
Its config has `url`, `root_token_env` and, for external workspaces, `workspace_id`.
It authenticates before creation, recovers by a stable owner/resource marker,
rejects name collisions and checks ownership again before deletion. Its receipt
stores only ID/URL. `bindings` resolves workspace credentials privately for
configuration; those tokens never enter the bundle or deployment receipt.
