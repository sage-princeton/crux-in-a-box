# MLLM misuse SDK pilot

This guide covers provisioning and running the six-hour pilot on curating
vision-centric malicious-use requests for red-teaming multimodal models.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories. `INPUTS.md`
records what the run is given and what changed from the contrastive RL pilot.

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
   Alternatively, you could set the appropriate AWS environment variables for authentication to the CRUX operations and experiments account.

2. Create a RunPod key that can list/create/delete pods and read billing. Record the key secret.

3. Copy the shared SerpAPI key (`SERPAPI_KEY` in the operator's `.env`) into
   `secrets.json` as `SERPAPI_KEY`. The agents use its Google Images engine to find
   real-world photos; the account is shared, and the standing context asks the run
   to stay under about 2,000 searches.

4. Create a fine-grained Hugging Face token whose only permission is read access
   to the public gated repos the account can access (no private repos, no
   organization permissions, no write). Accept the licences of any gated models the
   run may need on that account first; the agents cannot. Record it as `HF_TOKEN`
   in `secrets.json`, and revoke it after the run.

5. Create a fresh, dedicated OpenRouter key with a **non-resetting $25 limit** for
   experimental model calls (target models, evaluation judges, data generation),
   distinct from the agent's `OPENROUTER_API_KEY`. Record it as
   `OPENROUTER_EXPERIMENT_API_KEY` in `secrets.json`. Retain the example's
   `OPENROUTER_EXPERIMENT_BUDGET_USD=25`, `OPENROUTER_EXPERIMENT_KEY_CAP_USD=25`,
   `OPENROUTER_EXPERIMENT_BILLING_MODE=credits` and shared absolute
   `OPENROUTER_EXPERIMENT_STATE_DIR=/srv/crux-run/state/experiments` values.
   These settings pass through the run secrets bundle into the service environment.

   This key must spend **OpenRouter credits, not BYOK**. Use an OpenRouter workspace
   with no applicable BYOK credentials, or exclude the experiment key from every
   BYOK credential using OpenRouter's [API key filters](https://openrouter.ai/docs/guides/overview/auth/byok#key-filters).
   Do not change the agent-inference key's routing. The client checks billing mode
   and rejects unexpected BYOK usage; setting `credits` alone does not change
   account-side routing. Check routing before making a call. Keep the account funded;
   a key limit does not reserve account funds. Never deliver a management key.

## Provisioning

1. Copy `placeholders.txt.example` to `placeholders.txt` and set `AWS_PROFILE`.
2. Copy `secrets.json.example` to `secrets.json` and fill its values from the values recorded during prerequisite/dependency setup.
3. From the repository root, choose a unique run slug and run:
    ```bash
    ./devops/ec2-workspaces/make-new-workspace.sh mllm-misuse-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/mllm-misuse-pilot/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/mllm-misuse-pilot/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.

Note: `provision.json` installs LaTeX/latexmk, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh mllm-misuse-<unique-suffix>
```

2. Prepare the workspace by replacing some placeholders in prompts, fetching/compiling the NeurIPS LaTeX template, and validating run configuration:

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

Confirm experiment status reports `billing_mode=credits`, a $25 allocation and
provider limit, zero BYOK usage, and fresh remaining credit before starting.
`check` validates configuration without contacting the experiment provider; the
separate `status` command verifies its key cap and usage. Record a small benign
experiment smoke call and check its receipt before scaling up. Call output includes
the answer: redirect it to a private file and inspect only a sanitized summary.

3. Start the run and observe the logs.

```bash
sudo systemctl start crux-sdk-run
journalctl -u crux-sdk-run -f # follow the logs; Ctrl+C if no errors within ~3 minutes
```

You should see the agent report its initial milestone in the Slack channel within about ~20 minutes. You can also check OpenRouter for inference spending and Langfuse for streaming traces before then.

`budget_status` exposes the shared experiment pool under `experiments`; experiment
calls write receipts to `/srv/crux-run/state/experiments/receipts.sqlite3`, separate
from inference tokens and priced Langfuse generations. Preserve this directory
with run state on restart. Do not reset or rebind it to a different key, run slug,
allocation or billing mode. Use the CLI's reconciled spend rather than adding
provider usage to receipts. Experiment exhaustion or unavailable status pauses
new experiment submissions while inference remains available for synthesis.
There is no experiment finalization reserve or resource `limits` entry; the
existing time, inference and RunPod reserves still apply. Concurrent requests and
delayed settlement may overshoot the cooperative allowance; the provider key cap
is the billing backstop.

For an image-bearing experiment, select a vision-capable model/provider and append
`--image-file PATH` to the CLI call for each local PNG, JPEG, WebP or GIF. Images
are attached in order after the text, with a 20 MB encoded request cap (the selected
provider may impose lower limits). Test a benign image in the smoke call and
verify the receipt's `image_count`, `billing_mode=credits`, `upstream_cost_usd=0`
and experiment spend. Keep the answer in a private file. The shared image-input
client is stacked between PR 178 and this pilot; all modalities use the same
credential, headroom checks and receipt store.

## Steering

Steering messages can be sent to the principal investigator agent through the configured Slack channel. The agent will not react or respond to the steering messages, so use Langfuse to assess whether the agent acknowledged the message.

## Pausing and restarting

If you find an issue in the run that cannot be overcome with steering, consider pausing the run, making a fix and resuming later.

> [!WARNING]
> Paused downtime counts toward the six-hour time limit.
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
