# Contrastive RL SDK pilot

This guide provides instructions for reproducing the the Contrastive 
Reinforcement Learning (RL) research task.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories.

## Dependencies

**Prequisites**:
- Any changes you want to be included in the run are committed on `HEAD`(the deployment scripts deploy from `HEAD`.
- The workspace fleet's Langfuse project secrets have already been configured in SSM. See [workspace provisioning](../../../../devops/ec2-workspaces/README.md). That same project will be used for tracing and cost tracking; confirm the selected model has pricing.
- The shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md) is deployed and you have the associated`LANGFUSE_BASE_URL` and `COST_TRACKER_TOKEN`.
- A [Slack app](https://api.slack.com/apps) has been created from `slack-app-manifest.yaml`, installed into a Slack workspace, and`@crux-contrastive-rl`bot has been invited into a dedicated run channel. The channel ID and the steering User ID has been recorded.

**Dependency setup:**

1. Authenticate AWS explicitly:
   ```bash
   aws configure sso --use-device-code --profile crux-2-acct-admin-access
   ```
   Alternatively, you could set the appropriate AWS environment variables for authentication to the CRUX operations and experiments account.

2. Create a RunPod key that can list/create/delete pods and read billing has been created. Record the key secret.

## Provisioning

1. Copy `placeholders.txt.example` to `placeholders.txt`.
2. Copy `secrets.json.example` to `secrets.json` and fill its values from the values recorded during prequisite/dependency setup.
3. From the repository root, choose a unique run slug and run:
    ```bash
    ./devops/ec2-workspaces/make-new-workspace.sh contrastive-rl-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/contrastive-rl/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/contrastive-rl/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.


Note: `provision.json` installs LaTeX/latexmk, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh contrastive-rl-<unique-suffix>
```

2.  Prepare the workspace by replacing some placeholders in prompts, fetching/compiling the NeurIPs LaTeX template, and validating run configuration:

```bash
cd /srv/crux-run/run-harness
python3 prepare.py --channel C0CEJP2MP2P \ #crux-contrastive-rl channel in Max's Workspace
  --operator U0BUSP5CJM6 \ # Max Morgan user ID (change to your user ID to steer)
  --after-ts "$(date +%s).000000"
cd workspace/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
cd /srv/crux-run/run-harness
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
  --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
```

3. Start the run and observe the logs.

```bash
sudo systemctl start crux-sdk-run
journalctl -u crux-sdk-run -f # follow the logs; Ctrl+C if no errors within ~3 minutes
```

You should see the agent report its initial milestone in the Slack channel within about ~20 minutes. You can also check OpenRouter for inference spending and Langfuse for streaming traces before then.
