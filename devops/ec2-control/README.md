# AgentRQ controller

Deploy the shared AgentRQ service used by ACP Scaffolds. Its lifecycle is
independent of Run Boxes. Prerequisites are in the [parent](../README.md).
Prepare the controller configuration and authenticated AWS profile before use.

## Usage

From this directory:

```sh
./make-control-box.sh --help
./make-control-box.sh --dry-run
./make-control-box.sh
```

Configure `placeholders-control.txt` from its example. Before upgrades, stop
client gateways between turns and follow the backup/rollback procedure in
[operations.md](operations.md#upgrade-and-rollback-between-runs).

## Development

From the repository root, check the controller scripts:

```sh
bash -n devops/ec2-control/make-control-box.sh
shellcheck -x -e SC2029,SC2088 devops/ec2-control/*.sh
```

Use a disposable environment to verify controller startup and workspace connection;
keep its lifecycle independent from active Runs.
