# Contrastive RL pilot

Six hours, $100 agent inference (including delegates/reviews), $30 RunPod.
AWS infrastructure is separate. This bundle uses the supplied question/context
and CRTR ZIP. No additional source paper or repository URL is required.

## Runtime and scientific scope

Use the SDK from the same main checkout as this bundle. `research_extensions.py`
loads its existing TabPFN example's loop and review gates; their registered names
and `state/tabpfn/` paths are retained to reuse the tested implementation. The
source installation must include `examples/tabpfn/tabpfn_extensions.py`, as the
production SDK provisioner does. No shared runtime is changed.

The default provisioning model is `anthropic/claude-sonnet-5.5`, high effort,
through ordinary OpenRouter billing, with no BYOK, preset or provider restrictions.
It was present in the OpenRouter catalog during preparation; actual SDK/Codex
inference still needs the production probes. Change model/effort in the private
provisioning config if needed. The runtime uses Responses and trim_recent context.

The existing research delivery/review flow is retained: paper/PDF, code, evidence,
reproduction, HTML results and completion report. The plain article skeleton uses
installed LaTeX packages, with no venue-style download. A rejected/negative result
can be packaged honestly at the reserve. Scientific acceptance is separate from
whether the environment proved useful.

Finalization starts at five hours, $90 inference or $27 RunPod. The loop monitors
reported resources every 15 seconds and cancels unfinished calls at six hours.
Billing delays can cause overshoot. RunPod cleanup targets only pods whose
CRUX_RUN_SLUG exactly matches RUN_SLUG. Continuously retrieve results before exit;
pod-local storage is deleted. Inspect CLEANUP_STATUS.json and the provider console.

## Inputs to finish

- AWS profile/session and existing SSH key (`crux-acp` by default).
- Existing cost tracker URL/token and fleet Langfuse access in SSM
  `/crux/system/env`, verified against the CRUX project before live inference.
- Supplied OpenRouter and RunPod keys are already stored in the ignored
  `src/ec2-workspaces/run-secrets-contrastive-rl.json` with mode 600.

Keep that private JSON limited to OPENROUTER_API_KEY, RUNPOD_API_KEY,
COST_TRACKER_URL and COST_TRACKER_TOKEN. Fleet Langfuse keys come
from SSM. Never include operator AWS credentials in the agent's secrets bundle.
Leave the OpenRouter key's existing provider limit unchanged. The agent is
instructed to spend at most $100 total across the PI, delegates and reviews;
the TOML budget is also $100, with finalization at $90. Preflight reports the
provider limit as information and does not require changing it. Progress and
diagnosed blockers go to PLAN.md, LOG.md and COMPLETION_REPORT.md. Monitor the
systemd journal and Langfuse while the unattended run works.

## Prepare and provision

From the repo root, run local readiness at any time; it prints credential names
and status, never values. Add `--check-services` for read-only OpenRouter,
and RunPod API checks. This allocates no resources and calls no model:

```bash
python3 contrastive-reinforcement-learning/pilot/preflight.py \
  --secrets src/ec2-workspaces/run-secrets-contrastive-rl.json --check-services
```

Before preparation, missing staged sources are expected.
The read-only RunPod check verifies API access, not funding, GPU availability or
permission to create/delete pods. The service check does not verify tracing.

Copy `placeholders.txt.example` to the ignored
`src/ec2-workspaces/placeholders-contrastive-rl-base.txt`; set the intended AWS
profile and verify the key pair/local private key. Use a unique run slug. The
drop-in and SDK must be committed before provisioning because the provisioner
stages HEAD. Do not commit the private config/secrets or CRTR archive.

```bash
src/ec2-workspaces/make-new-workspace.sh contrastive-rl-pilot-1 \
  --base-config src/ec2-workspaces/placeholders-contrastive-rl-base.txt \
  --base-secrets src/ec2-workspaces/run-secrets-contrastive-rl.json --dry-run
```

Launch from the repo root through the pilot script:

```bash
python3 contrastive-reinforcement-learning/pilot/launch.py contrastive-rl-pilot-1 \
  --archive contrastive-reinforcement-learning/CRTR-main.zip
```

The script validates and prepares the source locally before creating resources,
then calls `make-new-workspace.sh` with the private pilot config/secrets. It packs
the prepared bundle and scanner into one scripted staging stream, builds the
paper, checks the real service configuration and starts crux-sdk-run. There are
no manual archive/scanner copy steps. For a box already successfully provisioned
by the fleet scripts, add `--provisioned`:

```bash
python3 contrastive-reinforcement-learning/pilot/launch.py contrastive-rl-20261029 \
  --archive contrastive-reinforcement-learning/CRTR-main.zip --provisioned
```

The launcher refuses to replace an active or previously started run. It records
the supplied key's already-used inference in PRELAUNCH.json and PLAN.md, and
deducts it from the runtime's nominal $100 allowance. It leaves provider limits
unchanged. The six-hour clock starts at research launch. Follow the service
journal and the run's Langfuse environment for progress.

## Records and shutdown

State/sessions, usage, steering and canonical review records live under
`/srv/crux-run/state`; research artifacts live under the bundle's workspace.
Restart the same service/state to resume; downtime counts. Preserve both together
on the box. Use the run slug to filter Langfuse traces and costs. Never pull raw
session SQLite or telemetry into this repository. The OpenClaw export-run.sh is
not an SDK exporter. This bundle's `export_trace.py` reads SDK session messages
without copying the database and fetches Langfuse observations filtered by run
environment and start time. It rejects cross-run observations, scrubs the service
credentials and credential-shaped text in memory, then scans the output with both
literal and shape checks. Only a successful scan installs the export directory.
An empty Langfuse result fails export; allow ingestion to finish and retry with a
fresh output directory. The temporary blacklist stays on the box and is deleted.

The launcher stages `scan-secrets.py` in the run bundle. After stopping research, run the exporter with the
actual service environment (including Langfuse credentials):

```bash
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python \
  /srv/crux-run/run-harness/export_trace.py \
  --scanner /srv/crux-run/run-harness/scan-secrets.py --output /srv/crux-run/export-1
```

Transfer only `export-1/` into ignored `runs-export/<run-slug>/`, scan it again
locally, and inspect the record before sharing. Retrieve research artifacts
separately after secret scanning. Raw sessions and service configuration stay on
the box. Fixture tests cover session/delegate scrubbing, exact non-vendor secrets,
rejection of cross-run data and withholding output on a failed scan.

After evidence retrieval, verify GPU cleanup, stop the service and tear down the
EC2 instance and retained resources using the workspace teardown flow. Analyze
scrubbed evidence under ignored runs-export/: time/spend to first baseline and
comparison, setup failures, retries, interventions and supported scientific claims.
Verify a sample against live tracing before relying on it for the full run.
