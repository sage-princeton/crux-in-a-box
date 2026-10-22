# Extending `crux_scaffold`

This guide is for contributors who are changing the scaffold. For terminology, the design overview and how to run a drop-in, see the [README](../README.md).

Every pluggable part of the scaffold is a **component**: an implementation of one interface, registered under a type name and configured by a table in `scaffold.toml`. Most new behavior is a new component, or no code at all. This guide describes each extension point: its contract, what it receives, and how to test it.

## Where a change goes

Prefer the first option that works:

1. **Declare it.** Use an existing type in `scaffold.toml`. A fixed check is a `command` tool or a `command` gate. A new steering channel or external system is an MCP server. Another step in the run is another phase.
2. **Implement an existing interface in the drop-in.** Behavior specific to one CRUX belongs in that drop-in's extension module, such as the demo's `site_preview` tool.
3. **Add a built-in implementation of an existing interface.** Do this when the behavior is general enough for other CRUXes.
4. **Widen an existing interface.** Add a method, a field on a context object or a result field only when an implementation cannot do its job without it. Update every implementation and test fake in the same change.
5. **Add a new interface.** This is the last resort. Follow the component pattern below, wire it in the composition root and give it a section in this guide.

| To change… | Extension point | Without code |
|---|---|---|
| What agents can do | [`Tool`](#tools-tool) | `command` tool; MCP servers |
| How the operator and agents reach each other, or another external system | [MCP servers](#mcp-servers-steering-and-external-systems) | `[mcp_servers]` |
| When a phase is done, and what to prompt next | [`Gate`](#gates-gate) | `command` gate; `continue_prompt` |
| How phases and iterations are sequenced | [`Loop`](#loops-loop) | `[[loop.phases]]` |
| What part of a conversation reaches the model and when standing context is injected | [`ContextStrategy`](#context-strategies-contextstrategy) | `[context]`; each agent's `context`, `workspace_context`, `context_max_chars` |
| Which coding-agent SDK implements a brief | [`CodingAgent`](#coding-agents-codingagent) | `[coding_agents]` |
| Which agent SDK runs the agents | [`AgentRuntime`](#agent-runtimes-agentruntime) | `[runtime]` |
| Where traces go | [`Telemetry`](#telemetry-telemetry) | Langfuse environment variables |
| Hard resource limits | [`Budget`](#budget-and-usage), [`AgentRuntime`](#agent-runtimes-agentruntime) | `[budget]`, `[delegation]` |
| External consumption and credits | [`ResourceProvider`](#resource-providers-resourceprovider) | `[resources.<name>]` |

## The component pattern

`components.py` defines the pattern every extension point follows:

- **`Component`** has a class-level `type_name` and an `Options` model. It is constructed as `cls(name, options, **dependencies)`. `name` is the declared name, such as `site_tests`. `type_name` is the implementation, such as `command`.
- **`Options`** is a frozen pydantic model with `extra="forbid"`, so a misspelled key in `scaffold.toml` fails at load time. A component with no configuration keeps the empty base `Options`.
- **`Registry`** maps type names to classes of one kind. `@REGISTRY.register` adds a class. Re-registering the same class is allowed, because a drop-in extension can be imported twice. Registering a different class under a taken name is a `InvalidDropInError`. `create(name, table)` looks up `table["type"]`, which defaults to the declared name, and validates the rest of the table against the class's `Options`.

Built-in components register themselves when their module is imported. `scaffold.py` imports every built-in module. A drop-in lists its own modules under `extensions`. `DropInDirectory.import_extensions` imports each one as `crux_drop_in.<stem>`, and its classes register themselves with the same registries.

**`scaffold.py` is the composition root.** It is the only place components are constructed and wired together. A new dependency between components is passed in there, as a constructor keyword or through `Assembly`; components do not build one another.

**Agent SDKs stay at the edges.** `config.py`, `drop_in.py`, `loop.py`, `gates.py`, `tools.py`, `usage.py` and `workspace.py` never import an agent SDK. The OpenAI Agents SDK is confined to `agent_runtimes/openai_agents_sdk.py` and `context_strategies.py`, and Codex to `coding_agents.py`. A new SDK gets its own module.

**Errors have two kinds:**

- **`InvalidDropInError`**: the drop-in cannot run as declared. Raise it while assembling, before any model call. The CLI exits 2 and prints the message.
- **`ToolError`**: a mistake the model should correct, such as a bad path. The runtime returns it to the model as `error: …` and the run continues.

### `RunContext`

Tools, gates and coding agents receive the run's `RunContext` (`workspace.py`):

| Field | Use |
|---|---|
| `workspace` | `read_file`, `read_bytes`, `write_file` and `list_files` are confined to the workspace and raise `ToolError` outside it. `run_shell(command, timeout)` starts in the workspace but is not sandboxed. Its output is capped and prefixed with `exit_code=`. |
| `state_dir` | The run's own state, from `--state-dir`. It sits outside the workspace and persists across restarts. Each component creates what it writes there. |
| `model`, `reasoning_effort` | The run's model and effort, for every agent and coding agent. `CRUX_MODEL` and `CRUX_REASONING_EFFORT` are the only place they are set; `model` raises `InvalidDropInError` when `CRUX_MODEL` is unset. |
| `env` | The scaffold's environment. Read configuration from here, not from `os.environ`, so tests can inject it. |
| `usage`, `budget` | The token ledger, persisted start time, and budget limits. See [Budget and usage](#budget-and-usage). |
| `resources` | Optional `ResourceMonitor` assembled from declared providers. `await budget_status()` refreshes and formats all usage; `budget_exhausted()` checks known limits. |
| `now` | UTC clock callable, replaceable in tests. |
| `telemetry` | See [Telemetry](#telemetry-telemetry). |
| `sleep` | Low-level sleep, replaceable in tests. |
| `wait(seconds)` | Returns seconds slept; polls steering and wakes early for pending messages. Use for cooperative waits. |
| `steering` | Optional durable `Steering` inbox/outbox assembled by `scaffold.py`; runtime binds its MCP connection. |

`workspace.run_shell_async(command, timeout)` is the cancellable counterpart to
`run_shell`. It yields during process execution and kills/reaps the shell process
group on timeout or cancellation. Both return the same bounded text format;
`ReviewWorkspace` rejects both. Explicitly detached child sessions are outside
that process group and need run-owned cleanup.

## Tools (`Tool`)

`tools.py`, registry `TOOLS`. Built-ins: `read_file`, `write_file`, `list_files`, `read_pdf`, `run_shell`, `command`, `rest`, `budget_status`, `escalate`.

`run_shell(command)` lets the agent supply a Bash command. Its `timeout_seconds` option defaults to 600.
It delegates to `ctx.workspace.run_shell_async`, starting in the workspace root without an additional sandbox.
The result contains the exit code and combined stdout/stderr, retaining the last 20,000 output characters.
On timeout, the workspace kills the process group and returns `exit_code=timeout after <seconds>s`.
The existing `command` tool instead runs a fixed command declared by the drop-in.

`read_pdf(path, page)` reads one 1-based PDF page from `ctx.workspace.read_bytes`. It returns the page count,
extracted text (capped at 20,000 characters), and a PNG rendering (at most 1,600 pixels on its longest side).
Figures reach the model as image content. It reads no PDF metadata or embedded attachments and runs no artifact
code. Invalid PDFs and page numbers return `ToolError` with no source paths. PDFium and Pillow are pinned
dependencies; there is no shell command or external converter.

```python
class PreviewArguments(Arguments):
    page: str = Field(description="'index' for the events listing, or an event slug.")


@TOOLS.register
class SitePreview(Tool):
    type_name = "site_preview"
    description = "Build the site and return one page's HTML."
    Arguments = PreviewArguments

    async def invoke(self, ctx: RunContext, args: PreviewArguments) -> str: ...
```

- **`description`** is what the model reads. Make it a class attribute, or a property when an option sets it, as `command` does.
- **`Arguments`** is the model-facing schema. The runtime sends it in strict mode, so every field is required and should have a `description`. A tool with no arguments keeps the empty base `Arguments`.
- **`invoke`** returns `ToolOutput`: text, or a list of text and SDK-neutral `ToolImage(png: bytes)` objects. The runtime translates images into native model image content. Raise `ToolError` for anything the model should fix; other exceptions are tool bugs.
- **`Options`** is the drop-in's configuration of the tool, from `[tools.<name>]`, such as `rest`'s bounds. Policy that limits the agent belongs here, not in `Arguments`.
- **One instance per declared name** is shared by every agent that lists it. State on the instance, such as `rest`'s time used, is shared too.
- **Declare** it in an agent's `tools` list. A `[tools.<name>]` table is needed only for options or for a `type` other than the name. For example, `[tools.site_tests]` with `type = "command"`.

### Rest bounds and allowance

`rest` takes agent-selected `seconds` (an integer) and `reason`. Its `RestOptions` are `min_seconds` (default 10), `max_seconds` (300) and `total_seconds` (3600). Each call grants `min(max(seconds, min_seconds), max_seconds, remaining)`, then awaits `ctx.sleep(granted)`. Declare sensible bounds with `min_seconds <= max_seconds`; increasing `max_seconds` permits waits longer than the default five minutes. The remainder can shorten the final wait below the minimum. Exhausted allowance returns feedback immediately without sleeping.

The return value reports the granted duration and remaining allowance; the tool-call input preserves the requested duration. Waiting itself requires no model calls. Rest does not wake on coding completion; configured operator steering can wake it early. The PI chooses its estimate and can change it between calls; see the [configuration and calling example](../README.md#rest-while-coding-delegates-work).

Allowance is mutable state on the shared tool instance, not persisted run state. Reassembling the scaffold resets it. Usage is added after sleeping, so simultaneous calls can overcommit the remaining allowance; use sequential calls when the cumulative allowance must hold. `total_seconds` limits this tool's explicit sleeps, not coding execution time or the native completion wait of a delegate.

## MCP servers (steering and external systems)

`[mcp_servers.<name>]` in `scaffold.toml`, with `McpServerConfig` in `config.py`. These are not components. Any stdio MCP server becomes a set of tools for the agents whose `mcp_servers` list names it. A channel such as the demo's Slack integration normally needs no scaffold code. Automatic delivery additionally uses `[runtime.steering]`; exposing read tools alone does not ingest messages.

- `command`, `args` and `timeout_seconds` start the server.
- The server receives only its declared `env`, plus a minimal `PATH` and `HOME`. A `${VAR}` reference is filled from the scaffold's environment. A missing variable is a `InvalidDropInError`, and the value is never echoed.
- Restrict a server with its own settings, as the demo does with `SLACK_MCP_ADD_MESSAGE_TOOL`, rather than trusting the persona to stay within limits.

## Gates (`Gate`)

`gates.py`, registry `GATES`. Built-ins: `command`, `llm_judge`.

- **`evaluate(ctx: GateContext) -> GateResult`** decides whether the phase may end. Don't override `check`: it calls `evaluate` and records the result as an `evaluator` observation.
- **`GateContext`** carries:
  - `run`, the `RunContext`;
  - `drop_in`;
  - `runtime`, for gates that ask a model;
  - `phase` and `iteration`;
  - `last_output`, the orchestrator's final output for the iteration.
- **`GateResult`** has:
  - `passed`;
  - `feedback`, which is inserted into the continue prompt when the gate fails;
  - an optional `next_prompt`, which replaces the continue prompt.

  Cap long feedback, as `command` does with `MAX_FEEDBACK_CHARS`.
- **A judgment gate asks the runtime.** `llm_judge` calls `ctx.runtime.judge(...)`, an isolated agent with no session. It sees the rubric and the evidence the gate assembles, and checks the work with the scaffold tools the gate names. A new judgment gate also calls `judge`, rather than an SDK, so gates stay SDK-neutral.
- **`tools`** lists the scaffold tools a gate's checks call, by declared name. The scaffold builds them along with the agents' tools. The default is none; `llm_judge` returns its `tools` option (`read_file` and `list_files` unless declared).
- **`validate_tools(tools)`** runs in the composition root after tools are built and before the runtime is
  constructed. The default does nothing; `llm_judge` rejects unsafe tools in blind mode at assembly time.
- **A gate's definition comes from the drop-in,** never from files the agents can edit. That is what lets a gate judge the agents' work.
- **Declare** it under `[gates.<name>]` and list it in a phase's `gates`.

### Blind `llm_judge` evidence policy

Ordinary judging is unchanged: `rubric`, `inspect`, `tools` and `max_turns` work as before. `blind` defaults to
false. With `blind = true`, `artifacts` must be a nonempty alias-to-workspace-path mapping and `inspect` must be
empty. Artifact sources must be relative file paths without `..`; aliases must match
`[A-Za-z0-9][A-Za-z0-9_.-]*`. `artifacts` is rejected outside blind mode. Unknown options remain errors.

Each evaluation creates a new `ReviewWorkspace.snapshot` from those exact files, using descriptor-relative opens
with no symlink following at the workspace root or in any source component. Only regular files up to 32 MiB each
are accepted. Missing or
inaccessible submissions fail the gate with an alias-only error, without a model call. The snapshot holds bytes in
memory, so later source edits cannot change that review's evidence. No original path, timestamp or directory tree
is exposed. `read_file` and `read_bytes` accept exact aliases; `list_files(".")` returns only aliases. Other reads,
directory listings, path resolution, writes and shell commands fail. Errors do not echo physical paths.

The gate sends only the fixed rubric and sorted alias list to `runtime.judge`, passing the snapshot as
`review_workspace`. Only exact built-in `read_file`, `list_files` and `read_pdf` implementations are allowed;
subclasses and custom commands cannot bypass the boundary. Those tools can have declared names, which must also
be neutral. A fresh reviewer agent uses no session, delegate, MCP server, standing context or context strategy.
It has the neutral agent name `judge`; the operator-facing workflow and ledger still identify `judge:<gate>`.
Each model call counts, including calls before turn exhaustion, and `Gate.check` records the reviewer-authored
structured result as an evaluator observation. Previous verdicts never return to the next reviewer.

The drop-in supplies the rubric, artifact selection and anonymized contents, including any acceptance threshold.
Do not permit author conversations, plans/logs, previous reviews, expected scores or revision cues. This policy
restricts evidence access; it does not remove identifying text or labels inside an explicitly permitted artifact.
Read-only verification compares permitted text/data and PDF text/figures; blind mode does not execute submitted
code or provide an unsandboxed command tool. Exercise the real runtime with the configured formats and rubric
before a research run; automated tests must stay offline.

## Loops (`Loop`)

`loop.py`, registry `LOOPS`. Built-in: `phased`.

First try adding phases, gates, continue prompts and `interval_seconds` to `phased` (`PhaseConfig` in `config.py`). A new loop is for a different control flow, not a different sequence of prompts.

A drop-in always declares its `[loop]`; there is no default. Every phase names its `continue_prompt`, a `string.Template` with `$phase`, `$iteration` and `$feedback`.

`PhaseConfig.max_iterations` accepts a positive integer (default 1) or the string
`"unlimited"`. The latter skips iteration exhaustion while retaining budget checks.
`AgentConfig.max_turns` (default 30), `OpenAIAgentsOptions.max_turns` (default 50),
and `JudgeOptions.max_turns` (default 20) accept the same `CountLimit` type.
`count_limit` maps `"unlimited"` to `None` for the SDK's native unbounded runner.
Existing numeric defaults are preserved. These settings do not disable budget
checks or delegation policies. A resource-driven drop-in must additionally monitor
in-flight work if it disables turn limits; `phased` only checks between iterations.

- It is constructed with the declared `gates` as a dependency. `run(runtime, drop_in, ctx, state, store) -> LoopOutcome` drives `runtime.run(prompt, workflow=…)` until it is done.
- **Resumable:**
  - keep progress in `RunState`;
  - call `store.save(state)` after every iteration, so a restarted scaffold resumes where it stopped;
  - an iteration interrupted by a stop reruns.
- **Bounded:**
  - `await ctx.refresh_resources()` then check `ctx.budget_exhausted()` before each iteration;
  - return `budget_exhausted` or `iterations_exhausted` instead of running on. The CLI exits 3 for both.
- **Traced:** wrap each iteration in `ctx.telemetry.trace(...)`, one trace per iteration.
- **Inspectable:** `describe()` returns the lines that `check` prints.

## Context strategies (`ContextStrategy`)

`context_strategies.py`, registry `CONTEXT_STRATEGIES`. Built-ins: `persistent`, `trim_recent`, `openai_compaction`.

- **`session(session_id, ctx: RunContext)`** returns the orchestrator's durable conversation store. The default is SQLite in `ctx.state_dir`, which survives restarts.
- **`select(items)`** returns the items sent on the next model call. The runtime applies it to orchestrator and delegate model calls; isolated judges use their own evidence. The stored history is unchanged.
- **`inject_standing_context(history, load)`** is abstract: every strategy must implement it. Before an outer runtime turn, it receives the stored conversation and a zero-argument loader for this agent's current standing context. Return a list of messages to prepend to the new turn's input. The runtime saves them with that turn through the SDK session, so the decision survives restarts and uses no process-local flags. Call `load` only when injecting. Fresh agent delegates use an empty history for every invocation.
- **`persistent`** injects a developer snapshot once, then sends the whole history. The `is_standing_context` helper recognizes snapshots by their developer role and header.
- **`trim_recent`** has `max_items` (default 200, positive). It trims at user-message boundaries, preserving complete tool-call sequences even when they exceed the limit. It refreshes context when the previous snapshot falls outside the recent history and keeps the latest snapshot in every model input throughout a turn.
- **`openai_compaction`** has `trigger_items` (default 200, positive). It wraps the durable store in `OpenAIResponsesCompactionSession` and uses the run's model. Its metered client records usage as `compaction:<session_id>` in the ledger and telemetry. It injects first, and on the next outer turn when history has a compaction item newer than its latest snapshot (or no snapshot). Unsupported models fail before a model call.
- **Standing-context sources** are declared per agent or coding agent using `StandingContextOptions`: `context` is a list of required drop-in-relative files; `workspace_context` is a list of workspace-relative files that may not exist yet; `context_max_chars` caps the whole snapshot at 32,000 characters by default (minimum 200). Paths are confined to their root at validation and at injection. Existing sources are checked for unresolved placeholders at launch. `DropInDirectory.standing_context(spec, *, telemetry, agent)` loads a bounded snapshot, marks absent dynamic files, and records a `standing_context:<agent>` span with paths, character count and truncation status. `instructions(agent)` supplies the persona separately, on every call.
- **Strategies use the runtime's types.** Items and `Session` are OpenAI Agents SDK types today, so a strategy targets the `openai-agents` runtime. A second runtime decides whether to adapt them or to take strategies of its own.

## Coding agents (`CodingAgent`)

`coding_agents.py`, registry `CODING_AGENTS`. Built-in: `codex`.

- **`Options`** subclasses `CodingAgentOptions`, whose base has `description`, `context`, `workspace_context` and `context_max_chars`. The model and effort are not options: use `ctx.model` and `ctx.reasoning_effort`. `codex` also gives Codex a 1M-token context window (`CONTEXT_WINDOW_TOKENS`).
- **The constructor** must accept `developer_instructions: str | Callable[[], str]`. The composition root supplies a loader so the agent gets current files when each fresh coding brief starts. The base's `developer_instructions` property resolves it; read it once at the beginning of `execute`. Give any other dependency a default, such as `codex`'s `client_factory`, so tests can replace it.
- **`execute(brief, ctx) -> CodingResult`** runs one brief to completion in the workspace. Each brief starts fresh: the brief is the whole task. Don't override `run`. It wraps `execute` in a telemetry `generation` and adds the usage to the ledger.
- **`CodingResult`**:
  - Report `usage` as OpenAI counts it: cache reads and writes are included in `input_tokens`, and reasoning in `output_tokens`. Telemetry splits these into exclusive buckets.
  - Set `completed=False` when the turn failed. Put the error in `final_response` so the calling agent sees it.
- **Long turns stream progress.** Send each finished step as `ctx.telemetry.record(...)` while the turn runs, as `codex` does with each item.
- **Sandboxing is the coding agent's own,** declared in its options, such as `codex`'s `sandbox`.
- **Declare** it under `[coding_agents.<name>]` and list it in an agent's `delegates`. The runtime exposes it as a tool that takes one `brief` argument.
- **Background execution** is an option of the `openai-agents` runtime. `CodingAgent.run` and `execute` still run one brief to completion; the runtime owns the task and exposes result retrieval to callers.

- **Delegation limits apply at the runtime boundary.** One coding-worker brief occupies one slot and adds one depth edge. Native subagents inside its coding harness do not consume additional scaffold slots or depth. A coding-agent implementation does not acquire a second slot in `execute` or `run`.

## Agent runtimes (`AgentRuntime`)

`agent_runtimes/base.py`, registry `RUNTIMES`. Built-in: `openai-agents`, in `agent_runtimes/openai_agents_sdk.py`.

A runtime is constructed with an `Assembly`: the drop-in, the `RunContext`, the built tools and coding agents, and the context strategy. `runtime_overrides` add further keyword arguments, such as the test models that `openai-agents` takes. A new runtime module must be imported by `scaffold.py` so it registers. Its SDK stays inside that module.

`Assembly.instructions(agent_name) -> str` supplies the complete instructions for an agent. The composition root joins the generated setup, optional drop-in guidance and persona for the orchestrator; other agents receive only their own persona. Personas are read at assembly time. Standing context is validated at launch and injected separately as strategy-controlled developer messages. Runtimes must call `instructions` on every model call, including delegated calls, rather than cache its result: the setup reads live loop state and must reflect phase changes and resumed iterations. The OpenAI Agents runtime adapts it to an SDK instructions callback.

The generated setup uses assembled tool and coding-agent descriptions and only capabilities listed on the orchestrator. For the built-in `phased` loop it includes the current phase and iteration, phase limits and intervals, and referenced gates (including `command` criteria). Other loops supply their summary through `Loop.describe()` without assuming phased control flow. Rest bounds come from each available `rest` tool, and the token limit comes from `RunContext.budget`. MCP servers are identified by declared name; their commands, arguments and environment are never included.

`[preamble]` in `scaffold.toml` has two optional keys: `enabled` (default `true`) controls generation, and `append` (default empty string) supplies inline text after the setup and before the persona. Appended text remains enabled even if generation is disabled, and follows the normal unresolved-placeholder validation. These settings affect only the orchestrator; disabling generation also removes the configured steering guidance. The setup renders live phase state and remaining rest allowance, assembled tools and delegate capabilities, delegation limits, budget/resource policies, context strategy and configured steering semantics. Optional limits use explicit unlimited wording; phase limits default to 1 and may explicitly be unlimited. It excludes provider endpoints/credentials and MCP launch commands/arguments/environment values. Current resource measurements continue to be added separately by the runtime input filter, including with generation disabled. This is scaffold instruction composition, not a new component registry.

**Methods:**

| Method | Contract |
|---|---|
| `__aenter__`, `__aexit__` | Start and stop the MCP servers. Open the orchestrator's session from `context_strategy.session(...)`. |
| `run(prompt, *, workflow)` | Send one prompt to the orchestrator, continuing its session. Return `TurnOutcome(completed=False)` when the agent runs out of turns; don't raise. `workflow` names the turn in traces. |
| `judge(name, rubric, evidence, *, tools, max_turns, review_workspace=None)` | Run a fresh judge agent, with the named tools and no session, for a `Verdict` (`passed`, `feedback`, `next_prompt`). `max_turns` is a positive integer or `None` for no turn limit. When a `ReviewWorkspace` is supplied, bind every tool to that snapshot and use a neutral agent name; never fall back to the shared workspace or add workflow metadata to model inputs. Running out of turns returns a failing `Verdict`; don't raise. Count every call under `judge:<name>`. The judge uses `ctx.model`, like every agent. |
| `describe()` | Assembly details, including provider and one line per agent, printed by `check`. |
| `delegation_slot(*, background=False)` (base implementation) | An async context manager around each delegated SDK-agent or coding-agent invocation. Queues at the shared concurrency cap and suspends a nested caller's slot until its children finish. Do not wrap the root orchestrator. |
| `delegation_wait()` (base implementation) | Suspend a delegated caller while awaiting independently scheduled background work; reacquire its capacity before resuming. Does not acquire a new slot. |
| `probe(env, prompt, telemetry, *, options=None)` (classmethod) | One traced model call without running a loop. Optional validated runtime options let `probe --drop-in` use the run's model provider; provisioning can omit them for the default provider. |

**The behavior the rest of the scaffold relies on:**

- **Agents.**
  - Build one agent per `[agents.<name>]`, with instructions supplied by `Assembly.instructions(name)` on each model call.
  - Every agent uses `ctx.model` and `ctx.reasoning_effort`. A drop-in cannot set a model, so runs differ in model only by `CRUX_MODEL`.
- **Tools.**
  - Expose each `Tool` with its `Arguments` schema.
  - Validate the model's arguments and call `invoke`.
  - Return a `ToolError` or validation error to the model as text.
- **Delegates.**
  - A delegated agent or coding agent is a tool whose call starts from a fresh context holding only the caller's brief.
  - A coding agent is called through `CodingAgent.run`.
  - Wrap the entire delegated invocation in `async with self.delegation_slot()`, including SDK execution, coding-worker execution and error handling. The base runtime owns the run's shared capacity and propagates nested call identity through task-local context. Blocking callers must await their children inside the scope. Independently scheduled background workers acquire `delegation_slot(background=True)` inside their task, covering the complete worker execution without suspending their continuing caller. Wrap result waits in `delegation_wait()` so a one-slot parent can await its worker. Own, drain and cancel background tasks through the runtime lifecycle; do not suppress cancellation.
- **MCP servers.** Start each server an agent lists, with the environment from `McpServerConfig.resolved_env`.
- **Context.** Apply `context_strategy.select` to orchestrator and delegate model calls, then append `await ctx.budget_status()` to ephemeral instructions. The status must survive trimming and must not accumulate in stored history; delegated SDK agents receive it too. Isolated judges skip this filter so run metadata and resource status cannot enter their evidence.
- **Standing context.** Before each orchestrator turn, pass its stored history and context loader to `inject_standing_context`, then prepend the returned messages to its prompt as SDK input. Agent delegates do the same with an empty history for each call. Preserve the persona in instructions separately.
- **Usage.** Add every model call's tokens to `ctx.usage` under the agent's name, so the budget holds.
- **Reasoning summaries.** The built-in runtime and Codex coding-agent turns set the reasoning-summary option to `auto`, selecting the most detailed summarizer available for the model. Returned summaries are present in the SDK response spans or `codex:reasoning` observations; these are summaries, not private chain-of-thought, and availability depends on the model/provider.
- **Tracing.** When `telemetry.sdk_tracing` is true, export the SDK's spans through OpenTelemetry so they land in the current trace. Keep each span under OpenTelemetry's 128-attribute limit (see the README's Tracing section).

### Model providers in `openai-agents`

The existing runtime's `model_provider` options configure the SDK's `OpenAIProvider`; this is not another component registry. Keeping the provider inside the runtime preserves the SDK boundary and reuses the existing loop, tools, MCP, delegates and context strategy.

| `[runtime.model_provider]` key | Contract |
|---|---|
| `type` | `openai` (default), or `openai-compatible`. OpenAI keeps the existing Responses behavior. |
| `api` | `responses` (default) or `chat-completions`; custom settings require `openai-compatible`. |
| `base_url` | Required URL for a compatible provider. |
| `api_key_env` | Credential variable name, default `OPENAI_API_KEY`. Resolve from `RunContext.env`, never embed the value in TOML. |
| `extra_body` | Provider-specific request fields, such as OpenRouter's `provider.only` and `allow_fallbacks`. Cannot replace the run model, input, tools, reasoning, or session settings. Never put credentials here. |

All agents and judges share one configured provider/client, which closes when the runtime exits, including failed MCP startup. `CRUX_MODEL` and `CRUX_REASONING_EFFORT` remain the sole model and effort settings. The compatible Responses path disables server storage, requests encrypted reasoning for local replay, and preserves raw usage. The Chat Completions adapter sends reasoning effort without Responses-only summary/include options. Coding-agent SDK configuration remains the coding agent's responsibility.

The [Google Argon smoke test drop-in](../examples/google-argon-smoke-test/scaffold.toml) configures OpenRouter with a Vertex-only route. The operator must separately configure Vertex BYOK and disable shared-capacity fallback on that account, then verify the actual response's billing/provider evidence.

### Optional background coding

`[runtime] background_coding = true` enables background coding delegates in `openai-agents`; the default is `false`, retaining the synchronous delegation contract. It does not change ordinary SDK-agent delegates.

- Calling a coding delegate, such as `engineer(brief)`, starts `CodingAgent.run` in an asynchronous task and returns its running status. The worker continues while the caller uses other tools, including `rest`.
- Every agent that can delegate to `engineer` also receives the generated `engineer_result` tool. Its required boolean `wait` selects an immediate status/result check (`false`) or an await of the result (`true`). A native wait needs no repeated model calls and does not use `rest` allowance.
- There is one outstanding task per declared coding-agent name across all callers. A second start is rejected until the first result is collected, including when the worker has already finished. The result is shared across callers and collection releases the slot for another brief.
- Generated result-tool names must not conflict with other tools or delegates. Declared-name conflicts are rejected while assembling; MCP tool-name conflicts are checked when connecting the servers. Both checks happen before any model call.
- Before `runtime.run` returns normally or because `max_turns` was reached, it awaits every outstanding coding task, appends uncollected outputs to `TurnOutcome.final_output`, and records them in the existing PI session with the prefix `Scaffold coding completion report`, preserving completed results for the next iteration or restart. Thus phase gates and the loop's state/usage save run after the work finishes. Finishing the PI's response does not detach a worker; persisting completed results does not resume pending tasks.
- On exceptions or cancellation, the runtime cancels and awaits outstanding tasks. Background tasks are ephemeral: there is no durable job registry, restart attachment, or recovery contract.
- Coding usage and progress keep the existing `CodingAgent.run` telemetry/ledger path. Waiting imposes no cumulative coding wall-time limit; the loop still checks its token budget between iterations.

The one-task-per-name rule applies alongside the [delegation policy](#delegation-policy-delegation). Background workers share its run-wide capacity for their full execution, including when a caller leaves results uncollected. Completed, uncollected results occupy no execution capacity. Native subagents inside a coding harness are excluded. With `[runtime.steering]`, `rest` can wake for pending messages; native result waits still await the coding task without interrupting it. See the [runtime delivery contract](#runtime-delivery-contract).

### Delegation policy (`[delegation]`)

`DelegationConfig` in `config.py` extends the existing configuration and runtime contracts; it is not another registered component. Both options default to `None` (unlimited) and reject unknown keys, booleans, strings and floats:

| Option | Contract |
|---|---|
| `max_concurrent` | Positive integer limiting active delegated invocations across every caller in one scaffold runtime. The orchestrator is excluded. Calls queue in memory when full; queued calls occupy no slots. A delegated SDK parent is suspended while nested delegates run, then reacquires capacity before returning to its own work. Parallel children share this suspension; no child can resume the parent while another child is still pending. Slots are released on success, error, cancellation and timeout. |
| `max_depth` | Nonnegative integer bounding delegate edges: root depth 0, a direct delegate depth 1, its delegate depth 2. Coding workers are leaves; their native subagents are excluded. Zero forbids all declared delegation. |

Validation reuses `delegation_order` after reference and cycle checks. It computes the longest path through every declared agent, including disconnected agents and coding-worker leaves, and raises `InvalidDropInError` with an offending path before runtime construction or any model call. For the declared acyclic graph, this covers every permitted runtime path; the runtime must expose only declared delegates. An extension introducing paths outside that graph must enforce the numeric depth limit for those paths itself.

Capacity is shared for the lifetime of the runtime, across callers and loop iterations, and is not persisted across restarts or coordinated across separate scaffold processes. This policy limits scaffold delegation, not ordinary tools or the internal fan-out of a coding harness. Stalled-work diagnosis, durable jobs and recovery are separate capabilities.

## Telemetry (`Telemetry`)

`telemetry.py`. Implementations: `LangfuseTelemetry` and `NullTelemetry`. Telemetry is not a registry: `telemetry_from_env` chooses the backend from the environment. A new backend implements the ABC and is chosen there.

- **`trace(name, metadata)`** opens a new trace in the run's session. Observations made inside it nest under it.
- **`record(name, kind, input, output)`** sends a finished observation immediately.
- **`generation(name, model, input)`** wraps a long model call. Set `output` and `usage` on the outcome it yields before it ends.
- **`flush()`** is called when the run ends, whether it completed, failed or was stopped.
- **`sdk_tracing`** tells the runtime whether to export its SDK's spans.

Langfuse v4 stores each observation once, when it ends, so keep observations short and send progress as it happens. An observation still open when the scaffold is stopped is sent with level `WARNING`. Usage goes to Langfuse as exclusive buckets (`usage_details`) so that each token is priced once.

## Budget and usage

`usage.py`. `Budget` (`[budget]`) declares finite nonnegative `max_total_tokens`, `max_cost_usd` (model USD), and `max_wall_seconds`, all optional. `model_resource` defaults to `models` and identifies the provider with `cost_usd`/`USD` and `total_tokens`/`tokens` metrics. A configured dollar budget requires that resource to be declared. The token limit uses `max(local, reported)`, avoiding overlap. External spending is reported independently; use per-provider limits for it.

`UsageLedger` persists tokens per agent/coding agent and the timezone-aware `started_at`. The composition root saves the start before entering the runtime and saves again on exit, including interruption. The phased loop rolls back an unfinished iteration's counter, so restarting reruns it without resetting time or tokens. New model-calling components must add tokens to `ctx.usage`.

`UsageLedger.add(source, input_tokens, output_tokens, *, cached_input_tokens=0, reasoning_output_tokens=0)` accepts the SDK's inclusive input/output totals and their optional detail counts. Cache reads remain part of input and reasoning remains part of output; neither detail is added a second time to `total_tokens`.

`RunContext.budget_status(force=False)` refreshes the providers and formats local/remote usage, limits and remaining amounts. The runtime uses it before every model call; `budget_status` uses `force=True` to bypass cache intervals. `budget_exhausted()` checks the last known measurements. The loop enforces between iterations; delayed, missing and stale external data are visible, and outages alone do not stop a run. No provider totals are added together automatically.

## Resource providers (`ResourceProvider`)

`resources.py`, registry `RESOURCE_PROVIDERS`. Built-ins: `langfuse`; `aws-compute` and `runpod` in `external_resources.py`.

Providers are separate from tools because they run deterministically without a model choosing to call them. They are separate from telemetry because they read external services, including non-model resources. They follow the same `Component`, `Options`, `Registry` pattern and are constructed only by `scaffold.py` from `[resources.<name>]`.

- **`Options`** subclasses `ResourceOptions`: `refresh_seconds` (0), `timeout_seconds` (10), `limits` (empty map). Unknown options fail assembly. Limits compare consumption to a maximum in the metric's unit; remaining-credit measurements are informative, not consumption limits.
- **`validate(env)`** rejects missing/invalid settings with `InvalidDropInError` during assembly. It must not query external services. Read settings from the supplied environment or `ctx.env`, never `os.environ`.
- **`async read(ctx) -> ResourceSnapshot`** fetches measurements scoped to this run. Return `as_of` (aware timestamp), a `metrics` mapping of names to `ResourceMetric(value, unit)`, and a concise `note` describing scope/lag. Values must be finite and nonnegative. Missing measurements stay absent; do not substitute zero. Metrics optionally carry an aware `as_of` (otherwise the snapshot timestamp applies) and `stale` (default false). The monitor sets these when retaining omitted measurements. Apply network timeouts and do not return raw provider payloads, credentials or prompts.
- **`ResourceMonitor`** refreshes providers concurrently with bounded timeouts, serializes overlapping refreshes, honors caching, and retains last successful measurements on failure. `snapshot(name)`, `status(name)`, `describe(now)` and `exhausted()` expose the results. A successful partial response publishes its fresh values, timestamp and note, retaining omitted metrics with `stale=true` and their original measurement timestamps. Repeated partial updates and caching do not advance those timestamps. Reports show stale metrics as current usage/remaining unknown, alongside last known values and their ages. Retained values remain available to budget and reserve checks. Provider `fresh`/`cached` status describes the read; individual retained metrics remain stale. Failed or entirely empty responses retain the previous snapshot as stale. Reports show observation age, cache/error state, units and configured limits; exceptions are sanitized.
- **`ResourceReadError`** carries a safe operator-facing recovery message. Build it only from fixed text or validated configuration, never a raw upstream exception, response body or credential. Other exceptions receive a generic unavailable message.
- **`langfuse`** uses `url_env` (`COST_TRACKER_URL`) and `token_env` (`COST_TRACKER_TOKEN`) plus `RUN_SLUG`. It posts the persisted start to AE-225's endpoint, validates the returned run/time window, and reports `cost_usd`, `input_tokens`, `output_tokens`, `total_tokens`. No observations means unknown measurements, with an explicit note.
- **`aws-compute`** options: `profile_env` (`AWS_PROFILE`), `region` (`us-east-1`), `tag_key` (`Name`), `tag_value_env` (`RUN_SLUG`), `refresh_seconds` (3600), `timeout_seconds` (30). Requires the AWS CLI at assembly, then authenticates with STS before other calls. It filters Cost Explorer by account, region, EC2 compute service and exact tag, follows pagination, and groups by tag and usage type to verify scope. Metrics: `cost_usd`/`USD`, `usage_<AWS usage type>` in its original unit, `running_instances`/`instances`. Daily billing can lag more than 24 hours; an unactivated/missing cost-allocation tag yields unknown spend, not zero.
- **`runpod`** options: `api_key_env` (`RUNPOD_API_KEY`), `run_slug_env` (`RUN_SLUG`), `refresh_seconds` (60), `timeout_seconds` (30). Pages through API v2 pods, including cluster members, and adopts exact pod-environment matches `CRUX_RUN_SLUG=<run slug>`. No IDs are required in the drop-in. Inventory is stored atomically in `ctx.state_dir/resources/runpod-<provider-name hash>.json`, bound to slug/start time, before billing; it retains IDs after termination, restart or a failed billing refresh. A changed ownership marker, invalid inventory or scope mismatch fails the refresh. All remembered IDs receive hour-aligned billing queries; live counts/rate use the complete pod list. Metrics: `cost_usd`, `gpu_cost_usd`, `cpu_cost_usd`, `disk_cost_usd` (USD), `running_pods` (pods), `running_gpus` (GPUs), `current_hourly_usd` (USD/hour). No known pods after a complete discovery means zero; any known pod without billing rows makes aggregate cost unknown. Limits: 100 discovery pages, 1,000 tracked IDs per provider, 1 MiB per response/inventory, one overall read deadline. Use a unique slug and dedicated pods, and force `budget_status` after creating a short-lived pod before terminating it: pods never observed alive cannot be attributed. Credentials, environment maps and raw pod details never enter snapshots or inventory.

To add refine.ink credits or another API, register a `ResourceProvider` in the drop-in's extension module and declare its type under `[resources.<name>]`. For example, report a `credits_used` metric with unit `credits`, then configure `limits = { credits_used = 100 }`. Its response automatically appears in every status block and participates in limit checks without changing the runtime or budget formatter. Tests should supply a fake provider, clock and HTTP/subprocess responses; never query a real account.

## Testing

Tests never call a model or the network. Use the existing doubles before writing new ones:

| Double | Where | Stands in for |
|---|---|---|
| `ScriptedModel`, `call`, `say`, `StoppedModel` | `tests/scripted.py` | A model, passed to `openai-agents` through `runtime_overrides={"models": …}` |
| `ScriptedCodingAgent` (`type = "scripted"`) | `tests/scripted.py` | A coding agent that writes declared files |
| `FakeRuntime`, `ScriptedGate` (`type = "scripted"`) | `tests/fakes.py` | A runtime and gates, for loop tests |
| Scripted HTTP transport | `tests/test_openrouter.py` | The real SDK's compatible provider adapter, including signed reasoning replay, tools, usage, and traces |
| `fake_slack_mcp.py` | `tests/` | A stdio MCP server |
| `BASE_FILES`, `write_tree`, `edit` | `tests/drop_ins.py` | A minimal drop-in directory |

A new component needs tests for:

- its declared options, including an invalid one;
- its behavior through the interface;
- for a tool or gate, one run through `main(["run", ...])` or the runtime with a scripted model.

`tests/test_docs.py` fails when a built-in type or extension point is missing from this guide.

## Durable Slack transport state

`steering.py` provides the SDK-neutral `Steering` inbox/outbox used by the runtime's
MCP integration. It receives an existing MCP connection and telemetry; it does not
start a server or build components. This extends the existing runtime/MCP and tool
contracts rather than adding a transport registry.

`SteeringOptions` declares a server, channel, optional fixed thread, nonempty
`operator_ids` allowlist and explicit `after_ts` (Slack timestamp; `0` includes all
available history). `poll_interval_seconds` bounds waiting between polls;
`max_pages` bounds one scan. It targets the CSV contract of
[slack-mcp-server v1.3.0](https://github.com/korotovsky/slack-mcp-server/blob/v1.3.0/pkg/handler/conversations.go).
Channel history is newest first; thread replies are oldest first, so thread scans
must finish every page. Channel mode accepts top-level messages; thread mode
accepts replies in its configured thread. Other authors and bot posts are ignored.
Edits/deletions of old messages are not new instructions: post a new correction.

`steering.json` in the state directory atomically stores the source/policy, scanned
timestamp, ordered inbox, pending/delivered status and escalation outbox. A complete
scan commits its cursor together with messages; a failed, malformed or capped scan
leaves it unchanged. Pagination cursors are transient; each retry scans from the
head so cursor expiration cannot skip messages. Reusing state with another source
or allowlist fails. Keep this state with the conversation store when resuming.

`Escalation` requires a stable `request_id`, `failure`, `diagnosis`,
`requested_action` and `meanwhile`. `Steering.escalate` persists before posting and
returns without awaiting an operator reply. Identical IDs return the saved result;
changing an existing request raises `ToolError`. A timeout or restart during a post
has an **uncertain** outcome and is never resent automatically. Check the channel
before creating a new ID. The runtime disables MCP write retries because
the upstream posting tool has no idempotency key.

`Steering.event` records message IDs/authors, channel/thread, request IDs and delivery
outcomes, never message bodies, MCP environment values, responses or exception
strings. `steering.json` contains the actual instructions and escalation text; treat
it as private run state. Tests use an MCP call double and exercise paginated scans,
malformed responses, duplicate/restart recovery and uncertain posts without a network.


### Runtime delivery contract

`OpenAIAgentsOptions.steering` is an optional strict `SteeringOptions` table under
`[runtime.steering]` (see the README for all keys). `scaffold.py` validates its
server reference and constructs `RunContext.steering`. `openai-agents` starts that
MCP server even when no agent exposes its tools, verifies the required read/post
tools and binds the connection. Startup failures close opened servers and report
sanitized, actionable state before any model call. No additional environment
variables or transport credentials are introduced.

The recipient is the configured orchestrator, whose instructions include a
generated precedence/delivery contract. The asynchronous model-input filter polls
before each orchestrator call, writes each deterministic `operator_steering` user
envelope to the durable session, then commits the inbox's delivery marker. On
restart it checks for an existing envelope before writing again. Delivered means
persisted to conversation; model success or compliance cannot be guaranteed.
Filters do not themselves persist input in the SDK, so the runtime also carries
new envelopes through the current Runner call after context selection. Subsequent
Runner calls reload them from the conversation. Older instructions remain ordinary
history and can be trimmed by a context strategy. Delegates receive only their
brief, never the operator inbox automatically; new runtimes must implement this
contract if they add a steering option. Custom strategies must provide a durable
session for restart guarantees.

`rest` charges only the sleep completed before early wake. Phase intervals also
use `RunContext.wait`; both check pending state before starting another transport
scan. Polls during waits are separated by `poll_interval_seconds`; actual latency
also includes scan time. A running model, command, delegate or coding-agent call
runs to its boundary. Pending steering never cancels background work. New message
polling stops when the run finishes. This extends cooperative waiting (AE-260)
without introducing a background-job implementation.

The built-in `escalate` tool uses `Escalation` as its argument schema and requires
`runtime.steering` during assembly. It posts without awaiting a reply. Read/status
failures are visible in `check`, run state, telemetry and orchestrator instructions;
escalation outcomes return to the caller. Conversation-write failures stop before
calling the model and retain pending delivery for restart. Steering telemetry uses
ordinary `span` records, with names `steering:received`, `steering:delivered`,
`steering:delivery_failed`, `steering:read_failed`, `steering:startup_failed`,
`steering:escalation` and `steering:escalation_outcome`.
