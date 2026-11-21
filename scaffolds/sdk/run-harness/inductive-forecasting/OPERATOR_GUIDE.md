# Inductive forecasting SDK research run

This guide covers provisioning and running the five-day forecasting research task.
For run configurations, see `./placeholders.txt`, `./secrets.json`, `./scaffold.toml`,
and the referenced `.md` files in this directory and child directories. `INPUTS.md`
records what the run is given and what changed from the VLM-PA run.

The five-day run has separate agent allowances and provider billing backstops:

| Purpose | Agent/harness allowance | Provider backstop |
|---|---|---|
| Inference (PI, delegates, reviews, compaction) | $30,000 | Non-resetting $65,000 OpenRouter key cap |
| RunPod GPU | $1,500 | None; the harness meters the run's tagged pods |
| Experiments | $2,000 | Non-resetting $4,000 OpenRouter key cap |

The higher key caps provide headroom for in-flight work and delayed accounting;
they do not authorize the agents to spend beyond their run allowances. The inference
allowance is `budget.max_cost_usd` and the RunPod allowance is
`resources.gpu.limits.cost_usd` in `scaffold.toml`. Finalization starts at $27,000
of measured inference spend, $1,350 of RunPod spend, or when 12 hours remain,
whichever happens first. Through November, OpenRouter values BYOK Gemini calls at
about 2.1 times what Google charges, which is why the inference cap is more than
twice the allowance. OpenRouter's BYOK fee (5% of that valuation, about $3,000 for a
November run) is charged to OpenRouter credits outside the run allowances.

Spending the inference allowance takes about $250 an hour. At last week's
per-delegate rates, a PI, six research assistants and twenty Codex workers spend
about $215 an hour with every member busy, and about $120 an hour at the half
occupancy earlier runs reached, so expect $15,000 to $25,000 of measured spend.

Every file in this directory is staged on the run machine at
`/srv/crux-run/run-harness`, where the agents' shells can read it. Keep the source
paper, its method, its results, its title and its locations out of this directory.
The paper's URLs live only in the operator-side policy file,
`devops/ec2-workspaces/network-policies/inductive-forecasting.toml`, which
provisioning does not stage.

> [!WARNING]
> The agents read the run's Slack channel through their Slack tools. Never name,
> link or describe the withheld paper in the channel, including in steering
> messages and replies to escalations.

## Dependencies

**Prerequisites**:
- Any changes you want to be included in the run are committed on `HEAD` (the deployment scripts deploy from `HEAD`).
- The workspace fleet's Langfuse project secrets have already been configured in SSM. See [workspace provisioning](../../../../devops/ec2-workspaces/README.md). That same project will be used for tracing and cost tracking; confirm the selected model has pricing.
- The shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md) is deployed and you have the associated `LANGFUSE_BASE_URL` and `COST_TRACKER_TOKEN`.
- The Google project behind the BYOK key has a per-minute token quota for Gemini 4 Carbon on the priority tier that covers about 5M tokens a minute for this run, on top of any other runs on the project.
- The AWS account has 32 vCPUs of standard-instance quota free for the m7i.8xlarge in `placeholders.txt.example`.
- If the run will be live at 2026-12-01 08:00Z, someone will update both Carbon price definitions in Langfuse to Google's December prices at that time, or the meter will read half the true spend. A launch before 2026-11-26 08:00Z ends before then.
- A [Slack app](https://api.slack.com/apps) has been created from `slack-app-manifest.yaml`, installed into a Slack workspace, and the `@crux-forecasting` bot has been invited into a dedicated run channel. The channel ID has been recorded. Any person in that channel can steer the run.

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

4. Set the dedicated inference key's OpenRouter limit to a **non-resetting $65,000**
   and record the key as `OPENROUTER_API_KEY` in `secrets.json`. Keep the harness's
   inference allowance at $30,000 in `scaffold.toml`.

5. Create a fresh, dedicated OpenRouter key with a **non-resetting $4,000 limit** for
   experimental model calls, distinct from the agent's `OPENROUTER_API_KEY`. Record it
   as `OPENROUTER_EXPERIMENT_API_KEY` in `secrets.json`. Retain the example's
   `OPENROUTER_EXPERIMENT_BUDGET_USD=2000`, `OPENROUTER_EXPERIMENT_KEY_CAP_USD=4000`
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
    ./devops/ec2-workspaces/make-new-workspace.sh forecasting-<unique-suffix> \
    --base-config scaffolds/sdk/run-harness/inductive-forecasting/placeholders.txt \
    --base-secrets scaffolds/sdk/run-harness/inductive-forecasting/secrets.json --dry-run
    ```
4. Repeat without `--dry-run` to provision.

Note: `provision.json` installs LaTeX/latexmk, `cm-super` fonts, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it.

## Prepare and start

1. SSH to the workspace as `ubuntu` using the run slug:
```bash
ssh forecasting-<unique-suffix>
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
configuration with a $30,000 inference allowance and a $1,500 RunPod limit, and the
experiment utility reports `allocation_usd=2000`, `provider_limit_usd=4000` and the
intended billing mode. Confirm the inference key's $65,000 cap in OpenRouter
separately. Experiment submissions pause at the $2,000 run allowance even while the
provider key has credit.

3. Install the network policy. Do this after the steps above: the installer moves
   the workspace and state to a separate `crux-agent` account, and the run then
   executes as that account. [network-proxy.md](../../../../devops/ec2-workspaces/network-proxy.md)
   describes what it installs and how to roll it back. From the repository root on
   your laptop, copy the installer, the proxy and the policy into the operator's
   home on the box, which the agent account cannot read:

```bash
ssh forecasting-<unique-suffix> 'install -d -m 700 ~/network'
scp devops/ec2-workspaces/install-network-proxy.sh devops/ec2-workspaces/network_proxy.py \
  devops/ec2-workspaces/network-policies/inductive-forecasting.toml forecasting-<unique-suffix>:network/
```

   Then, on the box, install it with direct non-web TCP allowed, so the agents can
   reach GPU pods over SSH and rsync:

```bash
sudo bash ~/network/install-network-proxy.sh --allow-direct-tcp ~/network/inductive-forecasting.toml
```

4. Check the network as the agent account. Pick any URL from the policy file's
   header for the blocked request, and search Bing for the paper's exact title
   (URL-encoded) to check that a results page showing it is blocked too.

```bash
proxy=http://127.0.0.1:18080
sudo -u crux-agent curl -sS -o /dev/null -w '%{http_code}\n' --proxy $proxy https://openrouter.ai/api/v1/models  # 200
sudo -u crux-agent curl -sS -o /dev/null -w '%{http_code}\n' --proxy $proxy '<URL from the policy file>'       # 403
sudo -u crux-agent curl -sS -o /dev/null -w '%{http_code}\n' --proxy $proxy \
  'https://www.bing.com/search?format=rss&q=<the paper title, URL-encoded>'                                   # 403
sudo -u crux-agent curl -sS --noproxy '*' --connect-timeout 5 https://example.com/ || echo "direct web refused"
sudo -u crux-agent timeout 5 bash -c '</dev/tcp/github.com/22' && echo "direct TCP ok"
sudo systemd-run --quiet --wait --pipe --collect --uid=crux-agent \
  -p EnvironmentFile=/etc/crux-run.env -p EnvironmentFile=/etc/crux-network-run.env \
  -p WorkingDirectory=/srv/crux-run/run-harness/workspace \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold.experiment_api status
```

   The last command reaches OpenRouter through the proxy with the experiment key
   and should report the same values as before. The proxy has not yet carried Codex
   workers or the Slack server on EC2; the first minutes of the run test them (step 5).

5. Start the run and observe the logs.

```bash
sudo systemctl start crux-sdk-run
journalctl -u crux-sdk-run -f # follow the logs; Ctrl+C if no errors within ~3 minutes
```

You should see the agent report its initial milestone in the Slack channel within about ~20 minutes. You can also check OpenRouter for inference spending and Langfuse for streaming traces before then.

If startup fails while npm fetches `slack-mcp-server` on a previously deployed box, follow the [staged-run proxy recovery instructions](../../../../devops/ec2-workspaces/network-proxy.md#observe-and-change-the-policy) and resume with the existing state directory.

During the first hour, confirm that the first Slack update arrives (the Slack server
works through the proxy), that Langfuse shows Codex worker generations (Codex
reaches OpenRouter through the proxy), and that
`sudo journalctl -u crux-network-proxy --since '1 hour ago'` shows no denials of
ordinary hosts. If workers fail with proxy or certificate errors, stop the run and
roll the network policy back as network-proxy.md describes.

## Steering

Steering messages can be sent to the principal investigator agent through the configured Slack channel. The agent will not react or respond to the steering messages, so use Langfuse to assess whether the agent acknowledged the message.

Watch the pace during the first day. Each Slack update reports how many of the 26 delegates are busy and whether each budget is on track. If inference spend stays well below about $250 an hour while delegates sit idle, steer the PI to fill them from its backlog; if it runs well above, steer it to cut back before the $27,000 reserve starts finalization early.

## If the PI reports withheld work

`workspace/AGENTS.md` tells every agent to stop reading anything that may be the
withheld paper and to report it; the PI escalates each report to the Slack channel
with the URL, where it appeared and how much was read. For each report:

- Check the URL against the source paper's known versions privately, not in the channel.
- If it is a location the policy misses, add a rule to
  `devops/ec2-workspaces/network-policies/inductive-forecasting.toml` and its tests,
  then stop the run between turns, copy the updated policy to `~/network/` on the
  box, rerun the installer with the same flags and start the run again. The
  installer refuses to change the policy of an active run.
- Record the time, the URL and the extent of the exposure in the run's notes, for
  the comparison with the source paper.

## Pausing and restarting

If you find an issue in the run that cannot be overcome with steering, consider pausing the run, making a fix and resuming later.

> [!WARNING]
> Paused downtime counts toward the five-day time limit.
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
