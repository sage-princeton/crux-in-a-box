# AgentRQ controller

`crux-controller` provisions shared controller infrastructure from a declarative
JSON specification. Install the Invoker with `python3 -m pip install ./devops`.
The old `make-control-box.sh` path forwards the same arguments to this CLI;
sourced shell configuration and automatic SSH-key generation are replaced.

Copy `controller.example.json` outside the checkout. Set the authenticated AWS
profile, account, region, Ubuntu 24.04 AMI, VPC, subnet/availability zone, SSH public
key name and ingress CIDRs. A custom `configuration.hostname` needs a DNS A record
pointing at the declared address; otherwise the public IP's sslip.io name is used.
Keep the private SSH key, verified SSH known-hosts entries, credentials, state and
bundles outside source. No command accepts an unverified SSH host key.

```sh
crux-controller plan /private/controller.json
crux-controller put-secrets /private/controller.json --state /private/controller/state.json --secrets /private/controller.env
crux-controller preflight /private/controller.json --state /private/controller/state.json
crux-controller provision /private/controller.json --state /private/controller/state.json --source "$PWD" --ssh-key /private/controller.pem --known-hosts /private/known_hosts --confirm-idle
crux-controller health /private/controller.json --state /private/controller/state.json --ssh-key /private/controller.pem --known-hosts /private/known_hosts
crux-controller start /private/controller.json --state /private/controller/state.json --ssh-key /private/controller.pem --known-hosts /private/known_hosts
```

The private env file must contain `AGENTRQ_AUTH_ROOT_ACCESS_TOKEN` and any other
AgentRQ settings. It is uploaded directly to the declared SSM SecureString.
The Box fetches that parameter through its narrowly scoped instance role.
`configuration.initialize_data: true` allows formatting a newly managed blank
volume on the first configuration only; existing/external volumes are never
initialized. The EBS serial must match the declared volume ID. Configuration
leaves AgentRQ and Caddy inactive; `start` validates DNS, starts them and waits
for trusted HTTPS. `health` checks configuration and, when started, HTTPS.
Use `create` for resources alone and `configure` to configure an existing receipt.

Controller deployments use the same ownership checks, dependency ordering,
private receipts and retry handling as Runs, without a research Scaffold.
Security groups, a public SSH key import, an SSM-scoped instance role, EBS volumes
and attachments can also be declared by Runs. Box settings accept either existing
IDs/names or declared `security_group_resources`, `key_resource` and
`identity_resource` dependencies. Auxiliary drivers can extend this registry;
finalizers must precede the resources they need in reverse teardown order.

`destroy ... --confirm-idle` stops services, unmounts data, detaches attachments
and deletes owned resources in reverse dependency order. The example retains
its address and data volume. Preserve the receipt and recorded resource IDs;
a replacement deployment can reference them with `ownership: external`,
`teardown: retain` and `allocation_id` / `volume_id`. Changing a live specification
is rejected; use a new state path for replacement. A controller is shared: inspect
its dependent Runs before passing `--confirm-idle`, configuring or destroying it.

## Upgrades and recovery

The running image comes from `compose.yaml`; workspace gateway and adapter versions
come from each Scaffold's npm lockfile. Configuration pulls the declared image
without starting it. Controller SQLite migrations occur at explicit start.
Before an upgrade, stop dependent gateways between turns, stop AgentRQ and back up
its complete data directory (including SQLite WAL files), private env, Compose
files, systemd unit and the current image to protected storage. Validate the new
image against a copy of that database first. Restore image, configuration and
database from the same backup when rolling back; never open a migrated database
with an older image. Rollback discards writes since the backup.

New Run specifications declare `agentrq.workspace` resources for native HTTPS
workspace registration and private credential binding. The former SSH/curl
`bootstrap-workspace.sh` implementation has been removed. Registering a workspace
and configuring a Run do not start its agent.
