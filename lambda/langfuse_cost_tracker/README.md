# Langfuse cost tracker

One Lambda serves any run exported to a Langfuse project, regardless of model
provider or scaffold. It follows the provider cost trackers' Python zip + HTTP
API Gateway `POST /cost` deployment pattern. No model-provider keys are needed.

The run selector is `environment = run_slug.lower()`. Use a unique lowercase
run slug and the same environment in every exporter; the SDK lowercases it,
while custom Codex configurations preserve the supplied case. Session IDs are
unsuitable: a run can have many coding-agent sessions. Keep the start timestamp
stable across restarts.

## Deploy

Requires AWS CLI v2, Python 3, and an authenticated AWS profile with Lambda,
API Gateway, and IAM permissions. Load these variables from your secret store
outside the repository:

- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`: the Langfuse project's keys.
- `LANGFUSE_BASE_URL`: defaults to `https://us.cloud.langfuse.com`; use the same
  host as the exporters. HTTPS is required.
- `COST_TRACKER_TOKEN`: a strong shared bearer token for callers. Possession
  grants read access to cost totals for every run in this Langfuse project.

```bash
./deploy.sh --profile crux-2-acct-admin-access
```

`--region` defaults to `AWS_REGION` or `us-east-1`. `--function-name` defaults
to `langfuse-cost-tracker`; use a different name for another Langfuse project.
The script creates or updates the Lambda and API and prints the invoke URL.
It uses private temporary files for credentials and removes them on exit.
If authentication fails, reauthenticate with `aws configure sso --use-device-code`.

## HTTP contract

Send `Authorization: Bearer <COST_TRACKER_TOKEN>` and `Content-Type: application/json`.
Credentials are never part of the JSON request.

```json
{"run_slug": "my-run", "from_timestamp": "2026-10-01T12:00:00Z"}
```

Alternatively, send `start_date: "2026-10-01"` for midnight UTC. Supply exactly
one start field. The query ends at invocation time. The response contains:

```json
{
  "source": "langfuse", "run_slug": "my-run", "environment": "my-run",
  "from_timestamp": "2026-10-01T12:00:00Z", "to_timestamp": "2026-10-02T12:00:00Z",
  "retrieved_at": "2026-10-02T12:00:01Z", "has_data": true, "observations": 100,
  "total_spend": 12.34, "input_tokens": 100000, "output_tokens": 20000, "total_tokens": 120000
}
```

`total_spend` is USD reported by Langfuse, not a provider invoice. The v2
[Metrics API](https://langfuse.com/docs/metrics/features/metrics-api) aggregates
observations server-side, without downloading prompts or paginating traces.
Totals include coding-agent generations and already reflect Langfuse's token
buckets: do not add cached/reasoning tokens or local ledger totals again.

The query timestamps describe the requested window and retrieval, not an
ingestion completeness watermark. Open generations, delayed exports, missing
instrumentation, and unpriced models can understate totals. Empty results return
zero totals with `has_data: false`; callers must show that data is missing.
Nonempty results do not prove every generation is priced or exported.
Upstream failures and malformed responses return 502, never a fabricated zero.
Missing/invalid authorization returns 401; invalid requests return 400.

The legacy OpenClaw provisioning path still uses the existing provider cost
trackers and their provider-key spend baseline. This service is for runs with
Langfuse exports; the SDK scaffold consumes it for budget awareness (AE-248).

## Checks

```bash
python3 -m unittest discover -s lambda/langfuse_cost_tracker/tests
shellcheck lambda/langfuse_cost_tracker/deploy.sh
```

Tests exercise handler requests, Langfuse query construction, authentication,
error handling, and deployment with a fake AWS CLI. They do not call cloud services.
