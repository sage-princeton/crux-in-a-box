# AgentRQ controller

Deploy the shared AgentRQ service used by ACP Scaffolds. Its lifecycle is
independent of Run Boxes. Prerequisites are in the [parent](../README.md).
Prepare the controller configuration and authenticated AWS profile before use.

From this directory:

```sh
./make-control-box.sh --help
./make-control-box.sh --dry-run
./make-control-box.sh
```

Configure `placeholders-control.txt` from its example. Before upgrades, stop
client gateways between turns and follow the backup/rollback procedure in
[operations.md](operations.md#upgrade-and-rollback-between-runs).
