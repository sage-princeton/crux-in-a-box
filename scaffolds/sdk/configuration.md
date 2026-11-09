# SDK configuration reference

## A drop-in directory

The [contrastive RL example](run-harness/contrastive-rl/OPERATOR_GUIDE.md) uses Argon
and the standard workspace provisioner with private `placeholders.txt` and
`secrets.json` operator files, six hours, $100 inference and $30 RunPod. Its pinned
CRTR source, example datasets and checkpoints are checked in with the workspace;
no source archive or preparation step is required.

```
scaffold.toml            the declaration (see run-harness/web-cms-product-change/scaffold.toml)
PROMPT.md                first phase's prompt, sent verbatim
prompts/*.md             later phase prompts, each phase's continue prompt, and judge rubrics
personas/*.md            one per agent
scaffold_extensions.py   optional: the drop-in's own components
workspace/               where agents work; AGENTS.md and other standing context live here
OPERATOR_GUIDE.md        how to resolve placeholders and launch; operator notes live only here
```

The [TabPFN research example](run-harness/tabpfn/OPERATOR_GUIDE.md) combines a PI,
research assistants, background Codex workers, blind PDF reviews, compaction,
Slack steering and resource-driven finalization. Its only resource caps are six
hours, $200 model spend and $100 RunPod; it declares no token or delegation limits.
Its local loop monitors reported resource caps during work and cancels unfinished
turns at the deadline. Billing delays still allow dollar overshoot.

The run stops before any model call if an operator-facing file or standing-context file still contains a `{{KEY}}` or `{{KEY|default}}` placeholder. The error lists each one as `file:line`.

`max_turns = "unlimited"` disables the turn limit in `[runtime]`, an `[agents]`
entry or an `llm_judge` gate. `max_iterations = "unlimited"` disables a phase's
iteration limit. Existing numeric defaults remain unchanged. The built-in phased
loop checks budgets between iterations; unlimited turns alone do not enforce
spending or deadlines within an iteration.

A model call that fails for a transient reason (a 5xx or 429 response, a
network error or a provider's content-filter cutoff) is retried inside the
turn, up to 8 times, backing off from 2 to 60 seconds. A failure that outlasts
the retries ends the iteration and cancels its running coding agents.

Codex workers retry provider failures at two levels:

```toml
[coding_agents.worker]
type = "codex"
# TODO(AE-319): move idle-timeout, turn-retry and backoff settings into scaffold defaults.
# stream_idle_timeout_ms = 300000       # default; keep it above the slowest first token
turn_retries = 2                        # further turns after a transient failure (default)
turn_retry_backoff_seconds = [60, 300]  # wait before each; the last repeats (default)
```

The SDK hardcodes `request_max_retries = 10` and `stream_max_retries = 10`
for both direct OpenAI and compatible Codex providers; these are not drop-in
options. `stream_idle_timeout_ms` optionally overrides the compatible provider
(`CRUX_CODEX_BASE_URL`) idle timeout; unset, it keeps Codex's 300 s default.
When a turn still fails on a transient provider error (a rate limit, overload, 5xx, connection or stream failure, as Codex
reports it), the scaffold waits and continues the brief on the same thread with
a short factual message. Auth, context-window and usage-limit errors are not
retried. A brief that still fails reports the error kind, its turn attempts and
the last error, so the caller can tell an outage from a code failure. Successful
pilot requests took up to 246 s to their first token, so do not lower
`stream_idle_timeout_ms` below Codex's 300 s default.

Codex workers and probes explicitly use `web_search = "disabled"` for both direct
OpenAI and compatible providers. This prevents hosted search from being combined
with function tools on endpoints that reject that combination, including Google
AI Studio through OpenRouter. Codex's shell and other function tools remain available.

Each agent and coding agent declares its existing context sources in `scaffold.toml`:

```toml
[agents.product_manager]
persona = "personas/product_manager.md"
context = ["workspace/AGENTS.md"]        # stable instructions, drop-in-relative
workspace_context = ["REQUEST.md"]       # concise current state, workspace-relative
context_max_chars = 32000                # static file text, loaded at assembly
workspace_context_max_chars = 8000       # dynamic file text per call
```

Static files must exist at launch. Dynamic files may be created later; missing state is labeled unknown. Every load checks path confinement, including symlinks. Both limits include headings and truncation notices (minimum 200). Dynamic files share their budget, identify their source and modification time, and explicitly mark truncation. File recency does not certify the freshness of its claims. Keep plans/results/review dispositions concise and reference full artifacts, logs and review files; conversation compaction does not replace those durable research records.

A coding agent's harness brings its own system prompt, written for an interactive session with a person. `system_prompt = "prompts/worker.md"` under `[coding_agents.<name>]` replaces it with a drop-in file, read once at assembly. Codex requires one and receives it as its base instructions. The scaffold also turns off Codex's own loading of the workspace's `AGENTS.md`, so a Codex worker reads that file only when its `context` lists it.

The existing SDK input filter implements this request structure:

```text
instructions: fixed scaffold setup + persona + declared static files
messages:     selected conversation history
              current tool calls/results, if continuing the same turn
              newly delivered operator steering, if any
              user: fresh research/workflow/resource context + current task (first call only)
```

The first call combines the fresh prefix with the next task. Subsequent tool calls keep that task and complete tool exchanges in history, appending fresh context without repeating the task. Only raw tasks, actual exchanges and steering enter SQLite. All context strategies use this same assembly path; local compaction summarizes conversation history without storing the temporary research/resource tail. On restart, known legacy standing-context developer snapshots (including typed text blocks) are excluded from model/compaction input; genuine historical messages remain. This does not fix replay of an interrupted loop's launch prompt (AE-295).

Agent delegates receive their own sources and brief, without the caller's conversation. Coding agents receive stable developer instructions plus a fresh context-prefixed brief at invocation; their harness owns later calls. Isolated judges retain their rubric and declared evidence without research/workflow/resource context.

Claude models through OpenRouter receive the static instructions once as a request-only developer message instead of the top-level `instructions` string, allowing an explicit cache breakpoint on that content. A second breakpoint covers the latest retained text block, before the dynamic tail. The instruction breakpoint is present even on the first call or when trimming leaves no history. Explicit extension markers take precedence; markers never enter sessions or compaction. Other providers retain their existing adapter behavior.

Cache hits require an unchanged prefix, the model's minimum token count and an unexpired entry (five minutes by default). Changing instructions, tools or selected history can require a new write. Top-level automatic `cache_control` alone would cache the replaced status tail and miss on the next call; use the runtime's retained-prefix breakpoints instead. See [Anthropic's prefix-checking rules](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#how-automatic-prefix-checking-works) and [OpenRouter's caching adapters](https://openrouter.ai/docs/guides/best-practices/prompt-caching#anthropic-claude). Isolated judges receive cache markers without run status or steering metadata; one-shot provisioning probes keep their existing behavior.

## Conversation retention

`[context]` selects `persistent` (all history), `trim_recent` (a recent window),
or `compaction` (a PI handoff summary plus recent complete exchanges).
`trim_recent` uses `max_items` (default 200) to select a recent window without a
model call. It preserves complete exchanges, so this is not a hard request-size cap.
`compaction` summarizes older history only when the estimated token threshold is
reached, regardless of item count. Remove `max_items` or the older `trigger_items`
from compaction configurations; both keys are rejected.

All retain the original SQLite history. Local compaction uses ordinary inference
with the same model/provider/effort as the PI, including OpenRouter Argon; it does
not require a provider compaction endpoint.

```toml
[context]
type = "compaction"
context_window_tokens = 128000
# trigger_tokens = 90000  # optional earlier token trigger
keep_recent_items = 20
image_tokens = 4096      # estimated allowance per image; tune for the selected model
```

Before every PI call, including tool continuations, compaction checks an
approximate token count of the selected conversation, fixed
instructions, function/MCP tool schemas and fresh context. The token trigger
is 90% of `context_window_tokens`, or the lower explicit `trigger_tokens`. The
128,000-token default is a configurable conservative window, not model metadata;
set it for the selected model. Text estimates use serialized UTF-8 bytes divided
by four. Typed images use `image_tokens` each instead of counting base64, URL or
file-reference bytes as text. This configurable allowance is not a provider image
tokenizer: actual image costs depend on model, dimensions and detail settings.
Provider serialization and tokenization can differ from these estimates.
`keep_recent_items` is a soft target that preserves whole tool exchanges; fewer
items are retained when needed to fit. Before saving a checkpoint, compaction
rechecks the complete rendered request, including the actual summary, refreshed
research/resources and replayed steering. It summarizes farther if necessary.
If no complete cut can bring the estimate strictly below the token trigger, the
call fails without replacing the checkpoint or sending oversized ordinary
inference. Fixed instructions, tool schemas and the current task cannot be
removed to make room. The summary and its covered-history position survive restart in a separate checkpoint;
keep it with `sessions.sqlite`. Changed history invalidates the checkpoint.
Compaction calls are billed and traced as `compaction:<agent>`. Durable research
files remain the source of current plans/results and are refreshed after selection.

## Rest while coding delegates work

Coding delegates normally return only when their work finishes. This already waits without repeated orchestrator model calls, but the orchestrator cannot check progress or revise its wait during the call. Enable `background_coding` to let the orchestrator start a coding delegate, choose a rest duration, and check the result afterward.

Merge this fragment into a drop-in that already declares its loop, prompts and workspace, and supply `personas/pi.md`:

```toml
orchestrator = "pi"

[runtime]
type = "openai-agents"
background_coding = true

[agents.pi]
persona = "personas/pi.md"
tools = ["rest"]
delegates = ["engineer"]

[coding_agents.engineer]
type = "codex"
description = "Implement the coding task in the brief and report the changes and verification."
sandbox = "workspace-write"

[tools.rest]
min_seconds = 10
max_seconds = 900
total_seconds = 3600
```

The PI's persona can instruct it to estimate task duration from the brief and progress, then choose each wait. For example:

1. Start a short task with `engineer(brief="...")`, then request `rest(seconds=30, reason="Allow time for the small change and its tests")`.
2. Call `engineer_result(wait=false)`. If it is still running, revise the estimate and request `rest(seconds=90, reason="Allow more time for verification")`, then check again.
3. After collecting that result, start a larger task and choose `rest(seconds=600, reason="Allow time for the broader implementation and test suite")` before checking its result.

The worker continues during each rest. The PI chooses the estimates; the scaffold does not calculate them. A rest requires no repeated PI model calls, and does not wake early when the worker finishes. Configured automatic operator steering can wake rest early. The generated result tool returns immediately with status or a completed result when `wait=false`; `wait=true` waits for completion without polling or further PI model calls, for at most `[runtime] coding_result_wait_seconds` (default 1800; `"unlimited"` removes the bound). When the bound elapses, the tool returns a running status with the brief's start time, age and seconds since its last activity, and the brief keeps running. Use that native wait when the explicit rest allowance is exhausted. It does not consume rest allowance or impose a coding-task wall-time limit, and ending or cancelling a wait never cancels the brief.

With the settings above, a request for 1 second grants 10 seconds, and a request for 1200 grants 900. Each grant is also capped by the remaining allowance, even when fewer than 10 seconds remain. At zero allowance, `rest` returns an exhaustion message without sleeping. Tool inputs show the request; the response reports the granted duration and remaining allowance. The defaults are 10 seconds minimum, 300 maximum and 3600 cumulative; increasing `max_seconds` enables longer waits.

Rest allowance belongs to the declared tool instance, shared by every agent using that name, and resets when the scaffold is assembled again. Use sequential rest calls as in this example: concurrent calls to the same instance can overcommit its remaining allowance because usage is recorded after each sleep completes.

Each declared coding delegate has one outstanding task shared by its callers. Collect its result before starting another brief. Tasks live within the current runtime call: before returning to the loop's gates and state save, the runtime awaits outstanding work, appends any uncollected results to the turn output, and records them in the PI's existing session with the prefix `Scaffold coding completion report`, preserving completed results for the next iteration or restart. Errors or cancellation cancel and await running tasks; finished results stay collectable. A cancelled Codex brief asks the app-server to interrupt its turn and then closes it, and both the bounded interrupt request and the close complete even if cancellation repeats. The runtime remembers each cancelled brief: its result tool then reports when the brief started, when it was cancelled, and that partial changes may be in the workspace. Worker usage is booked incrementally in the ledger and priced child generations as token reports arrive; cancellation or failure retains those increments, and the brief's final summary adds no duplicate usage. While briefs are running, finished but uncollected, or cancelled and unreported, one line in the PI's research context lists them on every model call. Pending tasks are not restored after restart; ordinary SDK delegates retain their blocking behavior. The [delegation limits](#delegation-limits) also cover background workers. Configure [operator steering](#reliable-operator-steering-and-escalations) separately.

## Delegation limits

Declare optional limits in `scaffold.toml`:

```toml
[delegation]
max_concurrent = 4
max_depth = 2
```

`max_concurrent` is a positive integer shared across all callers in the run. Each delegated SDK-agent call or coding-worker brief uses one slot. The root orchestrator uses none. Requests wait in memory when capacity is full; queued work does not count as active. A delegated parent releases its slot while nested delegates run and reacquires capacity before continuing, so nesting works even with one slot. Success, failure, cancellation and timeout release capacity. Background coding workers hold a slot from the start of their work until completion, rather than only during the start request. Their callers keep their own slots while continuing work and release them while awaiting a result; checking a result without waiting uses no additional slot.

The orchestrator has depth 0; each delegate edge adds 1. For example, `orchestrator -> researcher -> coder` has depth 2. `max_depth` is a nonnegative integer; 0 forbids delegation. Before any model call, configuration validation checks the longest path in the entire declared delegate graph, including unused agents and coding-worker leaves. It reports an offending path when the limit is exceeded. Cycles remain invalid.

Each coding worker counts once at the scaffold boundary. Its native subagent fan-out consumes no additional scaffold slots or depth. Omit either option to leave that dimension unlimited; existing drop-ins retain their behavior. Queues are local to the running scaffold process and are not durable jobs.

## Extending

Implement the interface, register the class, and list the module in `extensions`. The class's `Options` model is its declarative configuration. For example, the demo's tool:

```python
@TOOLS.register
class SitePreview(Tool):
    type_name = "site_preview"
    description = "Build the site and return one page's HTML."
    Arguments = PreviewArguments

    async def invoke(self, ctx: RunContext, args: PreviewArguments) -> str: ...
```

```toml
extensions = ["scaffold_extensions.py"]

[agents.product_manager]
tools = ["read_file", "site_preview"]
```

Gates, context strategies, coding agents, loops and runtimes extend the same way. [docs/extending.md](docs/extending.md) describes each extension point's contract, and when to extend one rather than add a new abstraction.

## Adapting the demo

A CRUX with different needs changes declarations, not code. The same drop-in could, for example:

- **Let an agent choose shell commands.** Add `run_shell` to its tools and optionally set a timeout (default 600 seconds). Commands run through Bash from the workspace root and return the exit code and combined output; a command killed at the timeout returns the output it printed so far. The tool's description tells the agent the timeout. For longer jobs the agent passes `background = true`: the command starts detached, outlives the call and the timeout, and returns its PID and a log file under `.jobs/` in the workspace.
  ```toml
  [agents.product_manager]
  tools = ["read_file", "run_shell"]

  [tools.run_shell]
  timeout_seconds = 300
  ```
- **Judge the outcome, not just the tests.** Add an isolated `llm_judge` gate to the `implement` phase. The judge is an agent with no access to the orchestrator's conversation. It starts from its rubric, the orchestrator's final output and the workspace files listed in `inspect`, then verifies the work with its `tools`: `read_file` and `list_files` by default, plus any declared tool, such as a `command` tool that runs the tests. Running out of `max_turns` fails the gate. Have the rubric ask the judge to cite what it checked. It can write the next prompt itself.
  ```toml
  [[loop.phases]]
  name = "implement"
  gates = ["site_tests_pass", "request_met"]

  [gates.request_met]
  type = "llm_judge"
  rubric = "prompts/judge_request.md"
  inspect = ["REQUEST.md"]
  tools = ["read_file", "list_files", "site_tests"]
  max_turns = 20
  ```
- **Review a submission blind.** Opt into evidence isolation with `blind = true`. Map neutral filenames to the
  exact submission files in the workspace. Each review snapshots those files afresh and exposes only the aliases;
  it receives the fixed rubric and artifact list, with no orchestrator output, phase, iteration or prior reviews.
  The reviewer can read UTF-8 evidence and extract/render PDF pages, including figures, through restricted tools.
  Its verdict, feedback, next prompt, telemetry and usage accounting work as in ordinary judging.
  ```toml
  [gates.paper_review]
  type = "llm_judge"
  rubric = "prompts/research_review.md"
  blind = true
  artifacts = { "submission.pdf" = "results/paper.pdf", "data.csv" = "results/data.csv" }
  tools = ["read_file", "list_files", "read_pdf"]
  max_turns = 20
  ```
  Do not set `inspect` in blind mode. Only the built-in `read_file`, `list_files` and `read_pdf` implementations
  are permitted: shell commands, writes, custom tools and access to other workspace files are rejected. Missing,
  symlinked, non-regular or over-32-MiB artifacts fail the gate before calling a reviewer. Aliases are simple
  filenames, without directories. The drop-in owns anonymization of the rubric, aliases and artifact contents:
  leave out author identities, plans/logs, prior reviews, expected scores and revision cues. PDF tools expose page
  text and pixels, without document properties, attachments, source paths or filesystem metadata. No artifact code
  is executed. Run a small live review with your configured runtime and submission formats before a research run;
  automated tests use scripted models and make no network calls.

## Reliable operator steering and escalations

Steering is opt-in for the `openai-agents` runtime. It delivers operator instructions
to the configured **orchestrator**, including when the model has no Slack read tools.
Reuse a declared Slack MCP server and configure the run's source and authorized users:

```toml
[runtime]
type = "openai-agents"

[runtime.steering]
server = "slack"                    # a declared [mcp_servers.slack]
channel_id = "C123"
operator_ids = ["U123"]            # Slack user IDs allowed to issue instructions
# Explicit run boundary; 0 includes all available history.
after_ts = "1792036800.000000"
# thread_ts = "1792036800.000000"  # optional fixed thread; omit for top-level channel posts
poll_interval_seconds = 30          # poll spacing during rest and phase intervals
max_pages = 100                     # per scan; incomplete scans report failure without advancing
```

Before **each orchestrator model call**, the scaffold scans for new messages, sorts
them by Slack timestamp and adds identified operator instructions to its durable
conversation. Newer instructions take precedence over conflicting workflow prompts,
agent plans and older operator instructions, within system/developer and configured
resource/tool constraints. An allowlist is required; other users and agent/bot posts
are ignored. In channel mode, replies must be new top-level posts; in fixed-thread
mode, reply in that thread. Post a new message to correct an earlier one; edits are
not redelivered. Delegates retain their isolated brief; the orchestrator passes on
relevant steering when it next delegates.

`rest` and phase intervals poll at the configured interval and wake when messages
are pending. Detection latency during waits is at most that interval **plus the
MCP scan time**, while transport is healthy. During a model call or tool/delegate
call, delivery waits for its completion and the next model-call boundary; arbitrary
calls are never interrupted. Work that has already finished is not restarted for
late messages. There is no independent progress/incident monitoring here (AE-211).

Add `escalate` to an agent's `tools` to request operator action without waiting:
provide `request_id`, `failure`, `diagnosis`, `requested_action` and `meanwhile`.
The tool posts to the configured source and returns immediately so independent work
can continue. Operator replies follow the same automatic steering path. Reuse the
same ID for the same request: the saved outcome is returned without another post.
An uncertain/rejected post needs channel verification before a new ID is used.

Resume with the same state directory: `steering.json` contains source/cursor, ordered
pending messages and escalation outcomes; `sessions.sqlite` contains the delivered
instructions. Preserve both together. The delivery marker means **stored in the
conversation**, not proof that the model obeyed it. A failed model call keeps that
instruction available for retry. An interrupted conversation write is reconciled
against the saved envelope, preventing duplicate instructions on restart. As with
all session history, older steering is subject to the chosen context strategy.

`check` prints the saved transport status. Read failures also appear in the model's
instructions; malformed/capped scans keep the old cursor for a complete retry.
Session-write failures stop delivery with recovery instructions. Missing servers,
invalid policies and `escalate` without steering fail validation (exit 2); missing
MCP tools or startup failures fail before the first model call (also exit 2).
Only declared MCP environment variables reach the transport; steering telemetry
records IDs/authors and outcomes, excluding message bodies and transport exceptions.
Run state and conversations contain message text and must be treated as private.

## Commands

```bash
python -m crux_scaffold check --drop-in DIR --state-dir STATE   # validate and print the assembly; no model calls
python -m crux_scaffold run --drop-in DIR --state-dir STATE     # run the loop; a rerun with STATE resumes it
python -m crux_scaffold probe [--coding-agent codex]   # one traced model call (plus one Codex turn)
python -m crux_scaffold probe --drop-in DIR           # probe the drop-in's configured model provider
```

`probe --drop-in DIR` selects the runtime and its options from `scaffold.toml`. Without `--drop-in`, the probe uses the built-in OpenAI Agents runtime and direct OpenAI provider.

`probe --coding-agent codex` retries temporary-directory cleanup for up to 3.1 seconds when Codex's background plugin checkout is still writing files after its session closes. Other cleanup errors, or a directory that remains busy, still fail the probe.

`check` and `probe` are the first two of the team's testing protocols: nothing in the environment is broken, and the agent can do anything at all. Exit codes:

- 0: the loop completed.
- 1: a probe failed.
- 2: a configuration error.
- 3: the loop stopped early because its iterations or budget ran out.
- 128 + the signal number: the run was stopped by SIGTERM or SIGINT. The unfinished iteration reruns on restart.

## Budgets and resource context

Every orchestrator and delegated SDK model call receives a fresh resource-status
block after context selection. It includes tokens, elapsed time, model spend,
remaining configured budgets, and each declared external resource. The block is
ephemeral: it does not accumulate in the conversation history. Every SDK role,
including coding workers, reports usage as model calls finish. Codex cumulative
usage updates contribute only new increments to Langfuse and the local ledger,
without waiting for the brief to end. Its internal model calls are owned by Codex.
The `budget_status` tool forces an on-demand refresh through the same providers.

```toml
[budget]
max_total_tokens = 1000000
max_cost_usd = 100.0
max_wall_seconds = 86400
model_resource = "models"

[resources.models]
type = "langfuse"
```

Deploy the [Langfuse cost tracker](../../monitoring/costs/langfuse/README.md)
and supply `COST_TRACKER_URL`, `COST_TRACKER_TOKEN`, and a unique `RUN_SLUG`.
The model-dollar budget requires a declared `model_resource`; the `langfuse`
provider reads its cost and token totals from AE-225's API. The token budget
uses the larger of the local and reported totals, never their sum. Other resource
costs are displayed independently and can have their own limits.

Providers are registered components under `[resources.<name>]`. Each has
`refresh_seconds` (default 0), `timeout_seconds` (default 10), and optional
`limits = { metric_name = value }`. Limits apply to consumption measurements in
the provider's stated units. A future credits or API-spend source can implement
`ResourceProvider` in a drop-in extension; see [the contract](docs/extending.md#resource-providers-resourceprovider).

The phased loop checks token, time, model-cost and resource limits between
iterations, using available measurements. An in-flight turn can overshoot a
limit. Providers refresh independently and may use cached data; the status
shows timestamps, cache state, missing measurements and retained stale values.
Partial updates publish fresh measurements and retain missing values with their
original timestamps. Retained values still participate in limit checks, but their
current usage and remaining allowance are shown as unknown.
An outage does not silently become zero usage or halt the run on its own.
Reported spend can lag or omit unexported/unpriced generations, so these are
pacing controls rather than guarantees against an invoice exceeding a limit.

The first run timestamp and local tokens are saved in `state.json`, including on
an orderly interruption. Restarts keep elapsed time, including downtime. Use a
new state directory for a new run. When resuming state created before timestamps
were recorded, the first upgraded launch establishes the start; migrate
`usage.started_at` to the actual UTC start if earlier spend/time should be included.
Each launch also records `process_started_at`, and a launch that resumes a saved
`usage.started_at` increments `restarts`; the phased loop's workflow state shows both
on every model call, so agents need not infer restarts. Older state files load with
no process start and 0 restarts.
Without configured providers, local tokens and elapsed time still appear and
external usage is explicitly unavailable. `check` validates configuration without
querying providers or starting the clock.

### AWS compute and RunPod

Add either or both providers to the same declaration:

```toml
[resources.aws]
type = "aws-compute"
region = "us-east-1"
tag_key = "Name"
tag_value_env = "RUN_SLUG"
profile_env = "AWS_PROFILE"
refresh_seconds = 3600
limits = { cost_usd = 200.0 }

[resources.gpu]
type = "runpod"
refresh_seconds = 60
limits = { cost_usd = 200.0 }
```

`aws-compute` requires the AWS CLI and an explicit profile from `AWS_PROFILE`
(or `profile_env`). Every command passes that profile; a failed identity check
stops that refresh and reports the SSO reauthentication command. The profile
needs `sts:GetCallerIdentity`, `ce:GetCostAndUsage`, and `ec2:DescribeInstances`.
It reads regional EC2 compute spending scoped to that account and the exact run
tag, plus running instance count. Other AWS services, storage and public IP
charges are outside this collector's scope. Usage quantities retain their
individual AWS usage types and units.

The existing provisioner tags instances `Name=RUN_SLUG`. Activate the selected
tag for cost allocation before the run, and use a value dedicated to this run.
AWS billing is daily, can lag more than a day, and Cost Explorer charges per
request; the default refresh is hourly. Empty billing is shown as unknown,
even when a live instance count is available. See [AWS Cost Explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-what-is.html).

`runpod` requires `RUNPOD_API_KEY` (or `api_key_env`) and a unique `RUN_SLUG`
(or `run_slug_env`). No pod IDs are needed before launch. When creating a pod,
set its environment variable `CRUX_RUN_SLUG` to the run slug. The status block
supplies the exact value to use. The collector pages through the account's pods
on each refresh and adopts only exact marker matches, including cluster members;
names, creation time and other runs' pods do not establish ownership.

Discovered IDs are saved atomically under `state_dir/resources/runpod-*.json`
before billing is queried. They remain tracked after termination and process
restart, so delayed charges are still counted when the pod disappears from the
live list. Preserve the whole state directory when resuming. Inventory is bound
to the provider name, run slug and persisted start time; corruption or a scope
mismatch fails the refresh instead of forgetting prior spending. Never reuse a
pod or run slug across runs, or change a tracked pod's ownership marker.

Discovery polls at `refresh_seconds`. After creating a short-lived pod, call
`budget_status` before terminating it to force discovery; a pod created and
terminated entirely between refreshes cannot be attributed from billing alone.
Pods already terminated before first discovery are outside coverage. With no
discovered pods a successful complete list reports zero; once pods are known,
missing billing is unknown, not zero. Pending billing does not hide fresh pod
counts or hourly rates. Discovery/billing request failures retain the last
successful snapshot as stale.

The API v2 collector reports total/GPU/CPU/disk spend, running pods/GPUs, and
the current hourly rate. Billing uses hour-aligned windows; AWS uses day-aligned
windows, so both can include time before the scaffold started. Neither
extrapolates an invoice from a current rate. Account-wide balance changes are
not used to attribute shared-account spend to a run.
RunPod's billing API does not report GPU-hours; the provider labels that
limitation. Serverless and separate network-volume spending are outside this
collector. See [RunPod billing](https://docs.runpod.io/api-reference-v2/billing/get-pod-billing-history)
and [pod discovery](https://docs.runpod.io/api-reference-v2/pods/list-pods).

Each provider has a 30-second overall read deadline. Failed or entirely empty
refreshes retain previous measurements as stale. Partial refreshes keep each
missing measurement's last known value and timestamp separately from the fresh
measurements, so known costs cannot disappear or appear newly measured. Keep credentials outside the repository and pass
only the required credentials/profile into the scaffold process or container.

## Environment

| Variable | Use |
|---|---|
| `OPENAI_API_KEY` | Direct OpenAI agent, judge and Codex model calls |
| The provider's `api_key_env`, e.g. `OPENROUTER_API_KEY` | Compatible agent and judge model calls; the drop-in names the variable, never its secret value |
| `CRUX_MODEL`, `CRUX_REASONING_EFFORT` | The model and effort for every agent and coding agent. They are the only place these are set; a drop-in cannot override them. A run without `CRUX_MODEL` stops with a configuration error. |
| `CRUX_CODEX_BASE_URL`, `CRUX_CODEX_API_KEY_ENV` | Optional compatible Responses endpoint and credential-variable name for Codex workers and probes. EC2 provisioning derives these from the drop-in; CLI runs set them explicitly. The credential itself stays in the named environment variable. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Tracing. When unset, tracing is off; `probe` requires it. |
| `RUN_SLUG`, `CRUX_WORKSPACE_ID` | The trace environment (the slug, lowercased), session, tags and metadata. These match the Codex and Claude boxes. |
| `COST_TRACKER_URL`, `COST_TRACKER_TOKEN` | HTTPS endpoint and bearer credential for a declared `langfuse` resource provider; `RUN_SLUG` is also required. |
| `AWS_PROFILE` | Required explicit profile for `aws-compute`; the profile and AWS CLI must be available in the scaffold's environment. |
| `RUNPOD_API_KEY` | List access to pods (including their ownership environment marker) and read access to pod billing for `runpod`. |
| Variables that `[mcp_servers]` reference | For example `SLACK_BOT_TOKEN`. An MCP server receives only its declared `env` and a minimal `PATH`/`HOME` environment. |

## Model providers and GCP-hosted Argon

OpenAI remains the default. To query another compatible endpoint, add a provider to the existing runtime:

```toml
[runtime]
type = "openai-agents"

[runtime.model_provider]
type = "openai-compatible"
api = "responses"  # or "chat-completions" for endpoints without Responses support
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[runtime.model_provider.extra_body.provider]
only = ["google-vertex"]
allow_fallbacks = false
```

Set `CRUX_MODEL=google/gemini-4-argon` and `CRUX_REASONING_EFFORT=low`, `medium`, or `high`, and supply `OPENROUTER_API_KEY` in the process environment. Configuration selects the provider and transport; the environment selects the model and effort for all declared agents. Other compatible endpoints can use their own base URL and credential variable, without an OpenRouter dependency. The implementation uses the Agents SDK's `OpenAIProvider`; a third-party model adapter is unnecessary for these endpoints.

Optionally set `max_request_bytes` in `[runtime.model_provider]` to a positive integer for either provider type. Omit it to disable the check. The complete serialized HTTP body is measured before sending; requests at the limit are allowed. This includes UTF-8 text, images, tool schemas/results, encrypted reasoning and `extra_body`, independently of the token context window. Both research pilots set `max_request_bytes = 20000000` for their current Google AI Studio route; this is not a universal provider limit. Proxies can transform the body downstream, so a conservative limit may be needed for the selected route.

An oversized SDK request fails locally with actual/allowed byte counts and is not retried. A background delegate's failure reaches the PI through its existing result tool, allowing a new, smaller brief. A failure in the PI itself, judge or compactor still propagates; same-agent recovery and automatic compaction/rewrite are not implemented. The check covers SDK agents and probes, but does not cover the separate native Codex client. Limits will move to provider/model configuration maps under [AE-316](https://linear.app/agent-evals/issue/AE-316/move-model-specific-configurations-into-configuration-maps-queried-at).

**GCP billing:** OpenRouter must have the intended Google Vertex BYOK integration configured, with shared-capacity fallback disabled for Argon. The request above pins Vertex and disables provider fallback; it cannot change the account's BYOK settings. Check `is_byok` in the returned usage and the generation's provider before treating a run as GCP-billed. An OpenRouter key alone does not establish that billing path. See [OpenRouter BYOK](https://openrouter.ai/docs/guides/overview/auth/byok).

The Responses path uses `store=false` and the complete local SDK session, including encrypted reasoning, across tool calls and restarts. Returned summaries reach Langfuse through the existing SDK instrumentation; cache and reasoning counts also reach the persisted usage ledger. Chat Completions uses the SDK's corresponding adapter; reasoning replay and tool compatibility depend on the selected endpoint. The runtime remains `openai-agents` in trace metadata because that is the scaffold executing the model calls. Langfuse's catalog cost estimate is separate from the provider's actual BYOK billing receipt.

`run-harness/google-argon-smoke-test` exercises a file write, a fresh-context reviewer, a file read, and a command gate. It has no coding-agent dependency. Codex is a separate coding harness: for compatible Responses endpoints set `CRUX_CODEX_BASE_URL` and `CRUX_CODEX_API_KEY_ENV` too, or let EC2 provisioning derive them. Codex keeps the run's model and effort and uses command-based provider authentication without an OpenAI login. It cannot send the runtime's `extra_body`. For OpenRouter routing rules with Codex workers, use a model such as `google/gemini-4-argon@preset/vertex-only` and configure that preset with the same Vertex-only, no-fallback routing. The runtime's provider block does not add an Antigravity adapter.

For live tests, load the shared Langfuse settings from AWS SSM `/crux/system/env` in `us-east-1` and verify that the keys resolve to the **CRUX** project before making a model call. Keep fetched credentials in process memory. With the model-provider key and these Langfuse variables exported, run from `scaffolds/sdk`:

```bash
export CRUX_MODEL=google/gemini-4-argon CRUX_REASONING_EFFORT=low
GOOGLE_ARGON_SMOKE_TEST_DIR=$(mktemp -d)
cp -R run-harness/google-argon-smoke-test "$GOOGLE_ARGON_SMOKE_TEST_DIR/drop-in"
export RUN_SLUG="google-argon-smoke-test-$(date -u +%Y%m%dT%H%M%SZ)"
python -m crux_scaffold check --drop-in "$GOOGLE_ARGON_SMOKE_TEST_DIR/drop-in" --state-dir "$GOOGLE_ARGON_SMOKE_TEST_DIR/state"
python -m crux_scaffold probe --drop-in "$GOOGLE_ARGON_SMOKE_TEST_DIR/drop-in"
python -m crux_scaffold run --drop-in "$GOOGLE_ARGON_SMOKE_TEST_DIR/drop-in" --state-dir "$GOOGLE_ARGON_SMOKE_TEST_DIR/state"
```

In Langfuse's **CRUX** project, select the lowercased `RUN_SLUG` environment or the original `RUN_SLUG` session. Expect `crux-probe` and `smoke #1` traces, with Argon generations, researcher/reviewer agents, file tools, and the `note_written` gate. The local `state/state.json` retains the token ledger. The SDK CLI itself reads environment variables and does not fetch secrets or load local env files.

Live checks on 2026-10-22 exercised file write, isolated reviewer delegation, file read, the command gate, persisted token accounting, and trace retrieval from the shared CRUX project using the Responses API:

| Model | Provider route | Result and trace |
|---|---|---|
| `google/gemini-3.8-flash` | Google Vertex BYOK | [Passed](https://us.cloud.langfuse.com/project/cmtt04tkw03sgad0d26k5qm9d/traces/644d5daf2ae06cff198f8c1e668c1d6f) |
| `google/gemini-4-argon` | Google Vertex BYOK | [Passed](https://us.cloud.langfuse.com/project/cmtt04tkw03sgad0d26k5qm9d/traces/dfe33d289f874d77b7a9b8db2b85f15a) |
| `qwen/qwen3-coder` | DeepInfra through OpenRouter | [Passed](https://us.cloud.langfuse.com/project/cmtt04tkw03sgad0d26k5qm9d/traces/d85f408bf2f2f4607f7e920e5a345905) |
| `openai/gpt-6-astra` | OpenAI through OpenRouter | [Passed](https://us.cloud.langfuse.com/project/cmtt04tkw03sgad0d26k5qm9d/traces/eafbfa00f2dc920981f0bb51a0a088b0) |
| `anthropic/claude-sonnet-5.5` | Anthropic through OpenRouter | [Passed](https://us.cloud.langfuse.com/project/cmtt04tkw03sgad0d26k5qm9d/traces/233c4a37211e560bcdcb6ca878b08005) |

Qwen's Vertex attempt returned a 404 indicating that the GCP project could not access the publisher model in `us-south1`. Its successful retry changed the temporary drop-in's provider restriction to `only = ["deepinfra"]`, retaining `allow_fallbacks = false`; those calls used OpenRouter billing (`is_byok = false`). The checked-in Argon example keeps its Vertex-only route.

Astra and Sonnet used temporary provider restrictions of `only = ["openai"]` and `only = ["anthropic"]`, respectively, with `allow_fallbacks = false` and `CRUX_REASONING_EFFORT=low`. Both completed the probe and smoke workflow, and all six generation receipts per model confirmed the expected provider and model. These calls used OpenRouter billing (`is_byok = false`).

## Run the demo in Docker

`docker/demo.sh` builds an image holding the scaffold, the demo drop-in and the Slack MCP server, then runs it. The container stages the drop-in once in the volume `crux-sdk-scaffold-demo` at `/work/web-cms-product-change`, so a rerun resumes the run there.

1. **Slack.** Create the app from [`run-harness/web-cms-product-change/slack-app-manifest.yaml`](run-harness/web-cms-product-change/slack-app-manifest.yaml), then install it, invite the bot to a channel and copy the channel ID. See the demo's [OPERATOR_GUIDE.md](run-harness/web-cms-product-change/OPERATOR_GUIDE.md), section 1.
2. **Secrets.** Export them in the shell that runs `demo.sh`. `read -rs` keeps them off the screen and out of shell history:

   ```bash
   for name in OPENAI_API_KEY SLACK_BOT_TOKEN LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
     printf '%s: ' "$name"; read -rs "$name"; export "$name"; echo
   done
   export LANGFUSE_BASE_URL=https://us.cloud.langfuse.com CRUX_MODEL=<model> CRUX_REASONING_EFFORT=medium
   export RUN_SLUG=local-<you> SLACK_CHANNEL_ID=C…
   ```

   The Langfuse keys are the CRUX project's, the same ones stored in SSM `/crux/system/env`. `demo.sh` passes each variable to the container by name (`--env NAME`), so no value appears on a command line.
3. **Run.**

   ```bash
   cd scaffolds/sdk
   docker/demo.sh probe   # one traced model call and one Codex turn
   docker/demo.sh check   # stage the drop-in and print its assembly; no model calls
   docker/demo.sh run
   ```

   Before `run`, post the request in the channel as yourself, as a top-level message: *"Can events show where they're happening?"* Then answer the agent's question in the thread. The demo's OPERATOR_GUIDE.md, sections 3 and 4, describes the run and what success looks like.
4. **Inspect, stop, reset.**
   - `docker/demo.sh bash` opens a shell in the volume. `REQUEST.md`, `LOG.md` and `site/` are in `/work/web-cms-product-change/workspace`, and the loop state is in `/work/web-cms-product-change/.state`.
   - Ctrl-C or `docker stop` ends the run cleanly (exit 130 or 143). `docker/demo.sh run` resumes it.
   - `docker/demo.sh reset` deletes the volume so the next run starts fresh.

Inside the container, Codex runs with `sandbox = "full-access"` because its Linux sandbox (bubblewrap) cannot create namespaces in an unprivileged container. The container is the boundary instead: it runs as a non-root user, and the volume at `/work` is its only persistent storage. A run box keeps `workspace-write`.

## On a run box

Set `AGENT_PLATFORM=openai-agents` and `DROP_IN_PATH=<drop-in directory>` in the workspace base config; see [`../../devops/ec2-workspaces/README.md`](../../devops/ec2-workspaces/README.md). Provisioning does four things, and creates no AgentRQ workspace:

1. It stages this directory, root-owned, at `/opt/crux-sdk-scaffold`, and the drop-in at `/srv/crux-run/run-harness`.
2. It writes the environment above to `/etc/crux-run.env`.
3. It installs system Bubblewrap and Ubuntu's AppArmor profile for it, then checks sandboxed shell execution and file write/readback before running `probe --drop-in /srv/crux-run/run-harness`. It adds `--coding-agent codex` only when the drop-in declares Codex workers. `workspace-write` remains the default; global user-namespace restrictions are unchanged.
4. It installs `crux-sdk-run.service`, but does not start it.

The provisioning probe reads runtime/provider settings before operator preparation,
ignoring steering and unresolved prompt placeholders. `check` and `run` still
require a fully prepared drop-in.

The automated EC2 flow reads the committed drop-in's provider configuration before selecting and validating its credential. Compatible HTTPS endpoints work with Responses or Chat Completions; declared Codex workers require Responses. `MODEL_PROVIDER=openrouter` is accepted when the drop-in points to OpenRouter, and leaving the legacy setting at `direct` still honors the drop-in. Unselected provider credentials are excluded from the run. `compaction` works with the selected provider through ordinary inference, including OpenRouter; no compaction endpoint is required. OpenRouter runs with Codex workers and `extra_body` require a `model@preset/NAME` model; the preset must duplicate those routing settings. Preflight checks the suffix, not the account-side preset or BYOK configuration. Commit drop-in and SDK edits before provisioning: both are staged from `HEAD`.

## Development

```bash
cd scaffolds/sdk
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt -r ../../requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/python -m pytest -q tests
```

Contribution guidelines are in [AGENTS.md](AGENTS.md). Tests never call a model or the network. They use scripted models, a scripted coding agent, a fake runtime, a fake stdio Slack MCP server and an in-memory span exporter.

### Interactive delegates

Set `runtime.interactive_delegates = true` to run research assistants and Codex workers asynchronously with persistent
conversations. This mode supports direct PI delegates (no nested SDK delegation). This is opt-in; existing blocking SDK delegates and `background_coding` behavior remain the
default. Each dispatch returns a task ID. The PI gets `send_message`, `interrupt_delegate`, `request_progress`,
`delegate_inbox` and `<delegate>_result`; delegates get only `ask_pi` and `report_progress`.
Each declared delegate name has one outstanding conversation. Collect its result before assigning a new
brief; declare separate delegates for concurrent conversations. Existing global concurrency limits still apply.

`send_message` queues steering or a reply (`reply_to` is the question's message ID). An ended task resumes the
same conversation; `interrupt=true` stops active work before resuming it. `interrupt_delegate` stops without
resuming. `request_progress` solicits a report from active work and immediately returns a receipt; it does not
claim the delegate has answered. `delegate_inbox(wait_seconds=0..60)` optionally waits for mail. Result waits
use `coding_result_wait_seconds` and return early for unanswered questions. Neither kind of upward message
interrupts a PI model/tool call. The PI must collect outstanding results before gates run.

State and mail live in `state/delegates.sqlite`, SDK conversations in `state/sessions.sqlite`. After restart,
unfinished tasks are interrupted and require explicit PI resumption. Latest progress and unanswered questions
are included independently of conversation history selection. Message/lifecycle events are exported as
`delegate:*` telemetry spans with stable event and task IDs. Delivery means input handoff, not model compliance.

Interactive mode takes precedence over `background_coding`. Codex upward mail uses a local stdio MCP server
with a task-bound identity and only two tools; downward mail uses native `turn/steer`. Interruption uses native
`turn/interrupt`, drains the turn and closes the app-server. Background jobs/pods launched by a worker are not
implicitly terminated. Follow-up work resumes the stored native thread. Its cumulative token checkpoint
prevents charging earlier turns again. Keep `state/codex/` alongside the mailbox and usage ledger on restart.
Each interactive task uses its own persistent `state/codex/<task_id>/` home, so concurrent workers do not
initialize the same native SQLite databases. Resuming a task reuses that home and its native thread.
The two local mailbox tools are explicitly approved even under `deny_all`; other worker permissions are
unchanged. Missing mailbox tools fail the task before worker inference.
