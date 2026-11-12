# TabPFN SDK research run

This guide provides instructions for reproducing the the PFN harmful-shift research task.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories.

Note: `render_prompts.py` writes `PROMPTS.md`: the drop-in's prompt text, one block
per file, then the setup section the scaffold generates, for reading and rewriting
(for example in Google Docs). The file is not committed; generate a current copy
from `scaffolds/sdk`:

```bash
.venv/bin/python run-harness/tabpfn-full/render_prompts.py
```

## Dependencies

**Prequisites**:
- Any changes you want to be included in the run are committed on `HEAD`(the deployment scripts deploy from `HEAD`.
- The workspace fleet's Langfuse project secrets have already been configured in SSM. See [workspace provisioning](../../../../devops/ec2-workspaces/README.md). That same project will be used for tracing and cost tracking; confirm the selected model has pricing.
- The shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md) is deployed and you have the associated`LANGFUSE_BASE_URL` and `COST_TRACKER_TOKEN`.
- A [Slack app](https://api.slack.com/apps) has been created from `slack-app-manifest.yaml`, installed into a Slack workspace, and`@crux-tabpfn`bot has been invited into a dedicated run channel. The channel ID and the steering User ID has been recorded.

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
    ./devops/ec2-workspaces/make-new-workspace.sh tabpfn-sdk-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/tabpfn-full/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/tabpfn-full/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.

Note: `provision.json` installs LaTeX/latexmk, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh tabpfn-sdk-<unique-suffix>
```

2.  Prepare the workspace by replacing some placeholders in prompts, compiling the paper skeleton against the committed NeurIPS 2026 style (`workspace/paper/neurips_2026.sty`), and validating run configuration:

```bash
cd /srv/crux-run/run-harness
# Channel is set to crux-tab-pfn channel in Max's Workspace; user is set to Max Morgan user ID (change to your user ID to steer)
# --after-ts marks the run start: steering and the agent's Slack tools ignore earlier channel messages.
python3 prepare.py --channel C0CAP03MZNF --operator U0BUSP5CJM6 \
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

## Steering

Steering messages can be sent to the principal investigator agent through the configured Slack channel. The agent will not react or respond to the steering messages, so use Langfuse to assess whether the agent acknowledged the message.

## Pausing and restarting

If you find an issue in the run that cannot be overcome with steering, consider pausing the run, making a fix and resuming later.

> [!WARNING]
> Paused downtime counts toward the sevend day time limit.
> Pausing the harness will shut down any active RunPods and interrupt delegates (RAs, Codex workers). The PI is responsible for resuming both after the run is restarted.

To pause the run, run this from the run workspace:
```bash
sudo systemctl stop crux-sdk-run
```

To resume the run:
```bash
sudo systemctl start crux-sdk-run
journalctl -u crux-sdk-run -f # follow the logs; Ctrl+C if no errors within ~3 minutes
```
