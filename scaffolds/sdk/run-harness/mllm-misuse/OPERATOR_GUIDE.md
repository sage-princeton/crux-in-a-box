# MLLM misuse SDK research run

This guide covers provisioning and running the seven-day run on curating
vision-centric malicious-use requests for red-teaming multimodal models.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories. `INPUTS.md`
records what the run is given and the shared full-run baseline.

## Dependencies

**Prerequisites**:
- Any changes you want to be included in the run are committed on `HEAD` (the deployment scripts deploy from `HEAD`).
- The workspace fleet's Langfuse project secrets have already been configured in SSM. See [workspace provisioning](../../../../devops/ec2-workspaces/README.md). That same project will be used for tracing and cost tracking; confirm the selected model has pricing.
- The shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md) is deployed and you have the associated `LANGFUSE_BASE_URL` and `COST_TRACKER_TOKEN`.
- A [Slack app](https://api.slack.com/apps) has been created from `slack-app-manifest.yaml`, installed into a Slack workspace, and the `@crux-mllm-misuse` bot has been invited into a dedicated run channel. The channel ID has been recorded. Any person in that channel can steer the run.

**Dependency setup:**

1. Authenticate AWS explicitly:
   ```bash
   aws configure sso --use-device-code --profile crux-2-acct-admin-access
   ```

2. Create a fresh OpenRouter inference key with a **non-resetting $15,000 limit**
   for `OPENROUTER_API_KEY`. This covers PI, assistants, Codex workers, compaction
   and blind reviewers. Keep it separate from the experiment key below.

3. Create a RunPod key that can list/create/delete pods and read billing. Record the key secret.

4. Copy the shared SerpAPI key (`SERPAPI_KEY` in the operator's `.env`) into
   `secrets.json` as `SERPAPI_KEY`. The agents use its Google Images engine to find
   real-world photos; the account is shared, and the standing context asks the run
   to stay under about 2,000 searches.

5. Create a Hugging Face token whose only permission is read access
   to public repos. Accept the licences of any gated models the
   run may need on that account first; the agents cannot. Record it as `HF_TOKEN`
   in `secrets.json`, and revoke it after the run.

6. Create a fresh, dedicated OpenRouter key with a **non-resetting $1,500 limit** for
   experimental model calls (target models, evaluation judges, data generation),
   distinct from the agent's `OPENROUTER_API_KEY`. Record it as
   `OPENROUTER_EXPERIMENT_API_KEY` in `secrets.json`. Retain the example's
   `OPENROUTER_EXPERIMENT_BUDGET_USD=1500`, `OPENROUTER_EXPERIMENT_KEY_CAP_USD=1500`,
   `OPENROUTER_EXPERIMENT_BILLING_MODE=credits` and shared absolute
   `OPENROUTER_EXPERIMENT_STATE_DIR=/srv/crux-run/state/experiments` values;
   these also go in secrets.json so they get bundled into the service environment.

   The key's lifetime usage counts toward the allowance; never reuse the pilot's
   key or receipts directory for a new run.

### Experiment billing route

This run allocates **$1,500 of OpenRouter credits** to experiments. Keep
`OPENROUTER_EXPERIMENT_BILLING_MODE=credits` and use a dedicated experiment key
in an OpenRouter organization with no BYOK provider keys for the requested
providers. The client checks the billing route on every response and pauses
submissions if it detects BYOK usage; a new API key alone does not isolate it
from the organization's provider routing. Resolve a route mismatch before launch.
The shared client also supports `byok`, but that changes the billing source and
requires an explicit operator decision and a key limit that includes BYOK usage.

The inference, experiment and RunPod allowances are separate. The $500 RunPod
limit includes the run's pod charges; AWS infrastructure is separate. The final
16.8 hours, $1,500 inference and $50 RunPod are reserved for finalization. Exhausted
experiment credits pause new experiment calls while analysis and writing continue.
Concurrent calls and delayed billing can overshoot cooperative limits; the
non-resetting OpenRouter key caps are the provider billing backstops.

## Provisioning

1. Copy `placeholders.txt.example` to `placeholders.txt` and set `AWS_PROFILE`.
2. Copy `secrets.json.example` to `secrets.json` and fill its values from the values recorded during prerequisite/dependency setup.
3. From the repository root, choose a unique run slug and run:
    ```bash
    ./devops/ec2-workspaces/make-new-workspace.sh mllm-misuse-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/mllm-misuse/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/mllm-misuse/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.

Note: `provision.json` installs LaTeX/latexmk, `cm-super` fonts, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh mllm-misuse-<unique-suffix>
```

2. Prepare the workspace by replacing some placeholders in prompts, compiling the paper skeleton against the committed NeurIPS 2026 style (`workspace/paper/neurips_2026.sty`), and validating run configuration:

```bash
cd /srv/crux-run/run-harness
# --channel is the run channel's ID. Any person in the channel can steer.
# --after-ts marks the run start: steering and the agent's Slack tools ignore earlier channel messages.
python3 prepare.py --channel <channel-id> \
  --after-ts "$(date +%s).000000"
cd workspace/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
cd /srv/crux-run/run-harness
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
  --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  -p WorkingDirectory=/srv/crux-run/run-harness/workspace \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold.experiment_api status
```

Confirm the harness parameters are updated, the paper template compiles, the scaffold check
reports expected configuration values, and the experiment utility reports
correct configuration values.

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
> Paused downtime counts toward the seven-day time limit.
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

If the service exits with `model request rejected (HTTP 400)`, a provider refused a
request outright; the state is preserved. Find the rejected call in Langfuse before
restarting, since an unchanged context is likely to be rejected again.
