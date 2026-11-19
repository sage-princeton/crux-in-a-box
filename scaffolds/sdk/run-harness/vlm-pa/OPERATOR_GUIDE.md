# VLM-PA SDK research run

This guide covers provisioning and running the seven-day VLM-PA research task.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories. `INPUTS.md`
records what the run is given and what changed from the TabPFN full run.

The seven-day run has separate agent allowances and provider billing backstops:

| Purpose | Agent/harness allowance | Provider backstop |
|---|---|---|
| Inference (PI, delegates, reviews, compaction) | $15,000 | Non-resetting $30,000 OpenRouter key cap |
| RunPod GPU | $3,000 | None; the harness meters the run's tagged pods |
| Experiments | $100 | Non-resetting $200 OpenRouter key cap |

The higher key caps provide headroom for in-flight work and delayed accounting;
they do not authorize the agents to spend beyond their run allowances. The inference
allowance is `budget.max_cost_usd` and the RunPod allowance is
`resources.gpu.limits.cost_usd` in `scaffold.toml`. Finalization starts at $13,500
of measured inference spend, $2,700 of RunPod spend, or when 16.8 hours remain,
whichever happens first.

Every file in this directory is staged on the run machine at
`/srv/crux-run/run-harness`, where the agents' shells can read it. Keep the source
paper, its method and its results out of this directory.

## Dependencies

**Prerequisites**:
- Any changes you want to be included in the run are committed on `HEAD` (the deployment scripts deploy from `HEAD`).
- The workspace fleet's Langfuse project secrets have already been configured in SSM. See [workspace provisioning](../../../../devops/ec2-workspaces/README.md). That same project will be used for tracing and cost tracking; confirm the selected model has pricing.
- The shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md) is deployed and you have the associated `LANGFUSE_BASE_URL` and `COST_TRACKER_TOKEN`.
- A [Slack app](https://api.slack.com/apps) has been created from `slack-app-manifest.yaml`, installed into a Slack workspace, and the `@crux-vlm-pa` bot has been invited into a dedicated run channel. The channel ID has been recorded. Any person in that channel can steer the run.

**Dependency setup:**

1. Authenticate AWS explicitly:
   ```bash
   aws configure sso --use-device-code --profile crux-2-acct-admin-access
   ```
   Alternatively, you could set the appropriate AWS environment variables for authentication to the CRUX operations and experiments account.

2. Create a RunPod key that can list/create/delete pods and read billing. Record it
   as `RUNPOD_API_KEY` in `secrets.json`.

3. Create a Hugging Face token whose only permission is read access to public
   repos. Accept the access conditions of any gated datasets or models the run may
   need on that account first; the agents cannot. Record it as `HF_TOKEN` in
   `secrets.json`, and revoke it after the run.

4. Set the dedicated inference key's OpenRouter limit to a **non-resetting $30,000**
   and record the key as `OPENROUTER_API_KEY` in `secrets.json`. Keep the harness's
   inference allowance at $15,000 in `scaffold.toml`.

5. Create a fresh, dedicated OpenRouter key with a **non-resetting $200 limit** for
   experimental model calls, distinct from the agent's `OPENROUTER_API_KEY`. Record it
   as `OPENROUTER_EXPERIMENT_API_KEY` in `secrets.json`. Retain the example's
   `OPENROUTER_EXPERIMENT_BUDGET_USD=100`, `OPENROUTER_EXPERIMENT_KEY_CAP_USD=200`
   and shared absolute `OPENROUTER_EXPERIMENT_STATE_DIR=/srv/crux-run/state/experiments`
   values, and set `OPENROUTER_EXPERIMENT_BILLING_MODE` as described below; these also
   go in secrets.json so they get bundled into the service environment. The key's
   lifetime usage counts toward the allowance, so never reuse a key from another run.

### Experiment billing route

The experiment client compares every response's billing route with
`OPENROUTER_EXPERIMENT_BILLING_MODE` and refuses a mismatch:

- `credits` (the example's value): every call must be paid from OpenRouter credits.
  A call routed through a BYOK provider key fails, and once the experiment key shows
  any BYOK usage the client refuses all further experiment calls.
- `byok`: every call must route through a BYOK provider key, and the key's limit must
  include BYOK usage (`include_byok_in_limit`). A call paid from credits fails.

BYOK provider keys are configured per OpenRouter account or organization, so they
apply to every key in it.
Before launch, choose one route for every provider the run may call:

- for `credits`, create the experiment key in an organization with no BYOK provider keys;
- for `byok`, configure BYOK keys for every such provider in the key's organization.

In `byok` mode the client paces spend on the key's OpenRouter meter, which values
BYOK calls at OpenRouter's list price. Where a provider currently charges less than
that list price, the allowance buys proportionally fewer calls than its face value.
OpenRouter's BYOK fee is charged to account credits on top.

## Provisioning

1. Copy `placeholders.txt.example` to `placeholders.txt`.
2. Copy `secrets.json.example` to `secrets.json` and fill its values from the values recorded during prerequisite/dependency setup.
3. From the repository root, choose a unique run slug and run:
    ```bash
    ./devops/ec2-workspaces/make-new-workspace.sh vlm-pa-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/vlm-pa/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/vlm-pa/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.

Note: `provision.json` installs LaTeX/latexmk, `cm-super` fonts, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh vlm-pa-<unique-suffix>
```

2. Prepare the workspace by replacing some placeholders in prompts, compiling the paper skeleton against the committed NeurIPS 2026 style (`workspace/paper/neurips_2026.sty`), and validating run configuration and the experiment allowance:

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

Confirm the paper skeleton compiles, the scaffold check reports the expected
configuration with a $15,000 inference allowance and a $3,000 RunPod limit, and the
experiment utility reports `allocation_usd=100`, `provider_limit_usd=200` and the
intended billing mode. Confirm the inference key's $30,000 cap in OpenRouter
separately. Experiment submissions pause at the $100 run allowance even while the
provider key has credit.

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
