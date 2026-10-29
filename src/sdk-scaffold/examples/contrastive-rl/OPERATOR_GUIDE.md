# Contrastive RL SDK pilot

Use google/gemini-4-argon, high effort, through OpenRouter. The unattended pilot
has six hours, $100 total agent inference and $30 RunPod. Leave the OpenRouter
key's provider limit unchanged. All agents receive the $100 instruction; the TOML
also applies that budget and reserves the last hour/final 10% for finishing.

## Operator files

placeholders.txt and secrets.json beside this guide are the operator inputs.
Both are ignored and mode 600. The prepared secrets file contains the supplied
OpenRouter and RunPod keys plus the existing cost-tracker URL/token. AWS
credentials stay in the operator environment and are not delivered to the agent.
Shared Langfuse credentials come from SSM /crux/system/env, using the CRUX project.

For another operator, copy the .example files and fill their values. The config
selects this directory with DROP_IN_PATH=src/sdk-scaffold/examples/contrastive-rl.
Omit RUN_SLUG in the base placeholders; make-new-workspace.sh sets it.

## Create the workspace

Commit the drop-in before provisioning; the existing scripts stage HEAD.
Prepare the local working directory once, preserving the entire supplied archive:

    python3 src/sdk-scaffold/examples/contrastive-rl/prepare.py \
      --archive contrastive-reinforcement-learning/CRTR-main.zip

With the intended AWS session exported, run from the repository root:

    src/ec2-workspaces/make-new-workspace.sh contrastive-rl-argon-20261029 \
      --base-config src/sdk-scaffold/examples/contrastive-rl/placeholders.txt \
      --base-secrets src/sdk-scaffold/examples/contrastive-rl/secrets.json --dry-run

Run the same command without --dry-run to provision. It installs the SDK and run
software and copies the prepared WORKSPACE_DIR into the staged drop-in, including
ignored research data. Credentials stay outside that directory. Provisioning
delivers credentials and probes SDK/Codex inference into Langfuse. It leaves
crux-sdk-run stopped.

## Prepare and start

On the new box as ubuntu, validate the staged example:

    cd /srv/crux-run/run-harness/workspace/paper
    latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
    sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
      -p EnvironmentFile=/etc/crux-run.env \
      /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
      --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state

Check the configured model is Argon and the probe traces have token usage and
prices in this run's Langfuse environment. Record prelaunch inference spending in
workspace/PLAN.md so it stays within the $100 total. Then start:

    sudo systemctl start crux-sdk-run
    journalctl -u crux-sdk-run -f

The clock starts at research launch; downtime counts on resume. Source code lives
in workspace/crtr and SOURCE.json records its pin. The existing SDK research loop,
review gates and cleanup come from the TabPFN example; their registered type names
and state/tabpfn paths remain unchanged.

## Evidence and shutdown

Progress and blockers go to PLAN.md, LOG.md and COMPLETION_REPORT.md. Preserve
workspace artifacts and the state directory together on the box. The loop requests
deletion only of RunPod pods marked with this run's exact RUN_SLUG. Retrieve
results before exit and inspect CLEANUP_STATUS.json and the provider console.

Use export_trace.py with the service environment and utils/scan-secrets.py to
extract and scrub SDK sessions plus run-filtered Langfuse observations on the box.
Transfer only scanned outputs into ignored runs-export/. Raw sessions, telemetry
and the literal secret blacklist stay on the box.

After evidence collection, use the existing
src/ec2-workspaces/teardown-workspace-aws-resources.sh with the generated
placeholders-<slug>.txt to remove the workspace resources.
