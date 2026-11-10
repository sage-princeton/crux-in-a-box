# Carbon local research demo

A 45-minute, $75 CPU-only compatibility run through OpenRouter. The PI, three SDK
assistants and eight available Codex workers use `google/gemini-4-carbon` at high
effort. Both blind panels use Carbon, Claude Opus 5.5 and GPT-6 Astra, preserving
TabPFN's reviewer diversity. Paid review and compaction calls share the same budget.
Slack is a ciab-devenv fixture. No RunPod access or external compute is required.

`prepare.py` copies TabPFN's actual loop/gates, review rubric, paper template and
worker instructions from this same checkout, then applies this directory's explicit
inputs. This avoids maintaining a second gate implementation. Research/finalize,
PDF-digest review requests, cached blind panels, reserve transition, final evidence
checks and reproduction remain unchanged. The research question is a small CPU
study, so this tests harness compatibility rather than six-hour research quality.

Carbon has a unified 1,048,576-token window and the Gemini 4 default image allowance
is 1,120 tokens. Keep the existing 650,000-token PI compaction trigger. Native Codex
workers currently use the scaffold's 1,000,000-token setting independently.
Sources: [model](https://ai.google.dev/gemini-api/docs/models/gemini-4-carbon),
[media](https://ai.google.dev/gemini-api/docs/media-resolution),
[migration](https://ai.google.dev/gemini-api/docs/whats-new-gemini-4-carbon),
[OpenRouter](https://openrouter.ai/google/gemini-4-carbon).
High effort is intentional for comparison with the research runs; Carbon's default
is medium and high may take longer. Image allowance estimates context occupancy;
it does not set provider media resolution. Google documents implicit caching while
OpenRouter's launch metadata disagrees; inspect actual usage before claiming savings.

From this selected worktree, materialize a fresh directory outside Git:

```sh
python3 scaffolds/sdk/run-harness/google-carbon-demo/prepare.py --output /private/tmp/carbon-demo-inputs
```

Use ciab-devenv `bootstrap --source <this-worktree> --provider openrouter --drop-in
<prepared-directory> --manifest <prepared-directory>/provision.json` with inherited
`CRUX_MODEL=google/gemini-4-carbon` and `CRUX_REASONING_EFFORT=high`. Load shared
Langfuse credentials from SSM `/crux/system/env` in memory and verify project CRUX
before provisioning probes. Configure the cost service to query that same project.
Remove the fixture RUNPOD_API_KEY from the run service environment before launch.
Run `doctor`, `check`, then `run --timeout 2700`. Provisioning may make paid probes;
include those separately in spend reconciliation. The 900-second reserve gives
finalization 15 minutes. Langfuse cost is delayed, so the $75 limit is cooperative
and can overshoot while calls are in flight. Use a provider key cap if strict
billing enforcement is required. Preserve evidence, run `down` and verify this
instance's machine and containers are stopped. Validation findings belong in the PR.
