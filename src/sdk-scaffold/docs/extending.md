# Extending `crux_scaffold`

This guide is for contributors who are changing the scaffold. For terminology, the design overview and how to run a drop-in, see the [README](../README.md). The README's [Adapting the demo](../README.md#adapting-the-demo) section shows the built-ins swapped in by configuration alone.

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
| What part of a conversation reaches the model | [`ContextStrategy`](#context-strategies-contextstrategy) | `[context]` |
| Which coding-agent SDK implements a brief | [`CodingAgent`](#coding-agents-codingagent) | `[coding_agents]` |
| Which agent SDK runs the agents | [`AgentRuntime`](#agent-runtimes-agentruntime) | `[runtime]` |
| Where traces go | [`Telemetry`](#telemetry-telemetry) | Langfuse environment variables |
| Hard resource limits | [`Budget`](#budget-and-usage) | `[budget]` |

## The component pattern

`components.py` defines the pattern every extension point follows:

- **`Component`** has a class-level `type_name` and an `Options` model. It is constructed as `cls(name, options, **dependencies)`. `name` is the declared name, such as `site_tests`. `type_name` is the implementation, such as `command`.
- **`Options`** is a frozen pydantic model with `extra="forbid"`, so a misspelled key in `scaffold.toml` fails at load time. A component with no configuration keeps the empty base `Options`.
- **`Registry`** maps type names to classes of one kind. `@REGISTRY.register` adds a class. Re-registering the same class is allowed, because a drop-in extension can be imported twice. Registering a different class under a taken name is a `InvalidDropInError`. `create(name, table)` looks up `table["type"]`, which defaults to the declared name, and validates the rest of the table against the class's `Options`.

Built-in components register themselves when their module is imported. `scaffold.py` imports every built-in module. A drop-in lists its own modules under `extensions`. `DropInDirectory.import_extensions` imports each one as `crux_drop_in.<stem>`, and its classes register themselves with the same registries.

**`scaffold.py` is the composition root.** It is the only place components are constructed and wired together. A new dependency between components is passed in there, as a constructor keyword or through `Assembly`; components do not build one another.

**Agent SDKs stay at the edges.** `config.py`, `drop_in.py`, `loop.py`, `gates.py`, `tools.py`, `usage.py` and `workspace.py` never import an agent SDK. The OpenAI Agents SDK is confined to `agent_runtimes/openai_agents.py` and `context_strategies.py`, and Codex to `coding_agents.py`. A new SDK gets its own module.

**Errors have two kinds:**

- **`InvalidDropInError`**: the drop-in cannot run as declared. Raise it while assembling, before any model call. The CLI exits 2 and prints the message.
- **`ToolError`**: a mistake the model should correct, such as a bad path. The runtime returns it to the model as `error: …` and the run continues.

### `RunContext`

Tools, gates and coding agents receive the run's `RunContext` (`workspace.py`):

| Field | Use |
|---|---|
| `workspace` | `read_file`, `write_file` and `list_files` are confined to the workspace and raise `ToolError` outside it. `run_shell(command, timeout)` starts in the workspace but is not sandboxed. Its output is capped and prefixed with `exit_code=`. |
| `state_dir` | The run's own state, from `--state-dir`. It sits outside the workspace and persists across restarts. Each component creates what it writes there. |
| `model`, `reasoning_effort` | The run's model and effort, for every agent and coding agent. `CRUX_MODEL` and `CRUX_REASONING_EFFORT` are the only place they are set; `model` raises `InvalidDropInError` when `CRUX_MODEL` is unset. |
| `env` | The scaffold's environment. Read configuration from here, not from `os.environ`, so tests can inject it. |
| `usage`, `budget` | The token ledger and its limits. See [Budget and usage](#budget-and-usage). |
| `telemetry` | See [Telemetry](#telemetry-telemetry). |
| `sleep` | Every wait goes through this, so tests run without real time passing. |

## Tools (`Tool`)

`tools.py`, registry `TOOLS`. Built-ins: `read_file`, `write_file`, `list_files`, `run_shell`, `command`, `rest`, `budget_status`. `run_shell` runs any command the model asks for. `command` runs one fixed command that the drop-in declares, and takes no arguments.

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
- **`invoke`** returns text for the model. Raise `ToolError` for anything the model should fix. Any other exception is a bug in the tool, not feedback for the model.
- **`Options`** is the drop-in's configuration of the tool, from `[tools.<name>]`, such as `rest`'s bounds. Policy that limits the agent belongs here, not in `Arguments`.
- **One instance per declared name** is shared by every agent that lists it. State on the instance, such as `rest`'s time used, is shared too.
- **Declare** it in an agent's `tools` list. A `[tools.<name>]` table is needed only for options or for a `type` other than the name. For example, `[tools.site_tests]` with `type = "command"`.

## MCP servers (steering and external systems)

`[mcp_servers.<name>]` in `scaffold.toml`, with `McpServerConfig` in `config.py`. These are not components. Any stdio MCP server becomes a set of tools for the agents whose `mcp_servers` list names it. A steering channel, such as the demo's Slack integration, is an MCP server, so adding a channel normally needs no scaffold code.

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
- **A judgment gate asks the runtime.** `llm_judge` calls `ctx.runtime.judge(...)`, an isolated model with no tools and no session. That model sees only the rubric and the evidence the gate assembles. A new judgment gate also calls `judge`, rather than an SDK, so gates stay SDK-neutral.
- **A gate's definition comes from the drop-in,** never from files the agents can edit. That is what lets a gate judge the agents' work.
- **Declare** it under `[gates.<name>]` and list it in a phase's `gates`.

## Loops (`Loop`)

`loop.py`, registry `LOOPS`. Built-in: `phased`.

First try adding phases, gates, continue prompts and `interval_seconds` to `phased` (`PhaseConfig` in `config.py`). A new loop is for a different control flow, not a different sequence of prompts.

A drop-in always declares its `[loop]`; there is no default. Every phase names its `continue_prompt`, a `string.Template` with `$phase`, `$iteration` and `$feedback`.

- It is constructed with the declared `gates` as a dependency. `run(runtime, drop_in, ctx, state, store) -> LoopOutcome` drives `runtime.run(prompt, workflow=…)` until it is done.
- **Resumable:**
  - keep progress in `RunState`;
  - call `store.save(state)` after every iteration, so a restarted scaffold resumes where it stopped;
  - an iteration interrupted by a stop reruns.
- **Bounded:**
  - check `ctx.budget.exhausted(ctx.usage)` before each iteration;
  - return `budget_exhausted` or `iterations_exhausted` instead of running on. The CLI exits 3 for both.
- **Traced:** wrap each iteration in `ctx.telemetry.trace(...)`, one trace per iteration.
- **Inspectable:** `describe()` returns the lines that `check` prints.

## Context strategies (`ContextStrategy`)

`context_strategies.py`, registry `CONTEXT_STRATEGIES`. Built-ins:

- `persistent` keeps and sends everything.
- `trim_recent` overrides `select`. It sends only recent items, starting at a user message so that a tool call is never separated from its result.
- `openai_compaction` overrides `session`. It wraps the default session in the Agents SDK's compaction session. Its compaction calls are not yet added to `ctx.usage`.

- **`session(session_id, state_dir)`** returns the orchestrator's durable conversation store. The default is SQLite in `state_dir`, which survives restarts.
- **`select(items)`** returns the items sent on the next model call. The runtime applies it to every model call, delegated agents included. The stored history is unchanged.
- **Standing context is never trimmed.** Personas and standing context are the agents' instructions, not conversation items, so `select` never sees them.
- **Strategies use the runtime's types.** Items and `Session` are OpenAI Agents SDK types today, so a strategy targets the `openai-agents` runtime. A second runtime decides whether to adapt them or to take strategies of its own.

## Coding agents (`CodingAgent`)

`coding_agents.py`, registry `CODING_AGENTS`. Built-in: `codex`.

- **`Options`** subclasses `CodingAgentOptions`, whose base has `description` and `context`. The model and effort are not options: use `ctx.model` and `ctx.reasoning_effort`. `codex` also gives Codex a 1M-token context window (`CONTEXT_WINDOW_TOKENS`).
- **The constructor** must accept `developer_instructions`, the drop-in's standing context for the coding agent. Give any other dependency a default, such as `codex`'s `client_factory`, so tests can replace it.
- **`execute(brief, ctx) -> CodingResult`** runs one brief to completion in the workspace. Each brief starts fresh: the brief is the whole task. Don't override `run`. It wraps `execute` in a telemetry `generation` and adds the usage to the ledger.
- **`CodingResult`**:
  - Report `usage` as OpenAI counts it: cache reads and writes are included in `input_tokens`, and reasoning in `output_tokens`. Telemetry splits these into exclusive buckets.
  - Set `completed=False` when the turn failed. Put the error in `final_response` so the calling agent sees it.
- **Long turns stream progress.** Send each finished step as `ctx.telemetry.record(...)` while the turn runs, as `codex` does with each item.
- **Sandboxing is the coding agent's own,** declared in its options, such as `codex`'s `sandbox`.
- **Declare** it under `[coding_agents.<name>]` and list it in an agent's `delegates`. The runtime exposes it as a tool that takes one `brief` argument.

## Agent runtimes (`AgentRuntime`)

`agent_runtimes/base.py`, registry `RUNTIMES`. Built-in: `openai-agents`, in `agent_runtimes/openai_agents.py`.

A runtime is constructed with an `Assembly`: the drop-in, the `RunContext`, the built tools and coding agents, and the context strategy. `runtime_overrides` add further keyword arguments, such as the test models that `openai-agents` takes. A new runtime module must be imported by `scaffold.py` so it registers. Its SDK stays inside that module.

**Methods:**

| Method | Contract |
|---|---|
| `__aenter__`, `__aexit__` | Start and stop the MCP servers. Open the orchestrator's session from `context_strategy.session(...)`. |
| `run(prompt, *, workflow)` | Send one prompt to the orchestrator, continuing its session. Return `TurnOutcome(completed=False)` when the agent runs out of turns; don't raise. `workflow` names the turn in traces. |
| `judge(name, rubric, evidence)` | Ask an isolated judge, with no tools and no session, for a `Verdict` (`passed`, `feedback`, `next_prompt`). Count its usage like an agent's. The judge uses `ctx.model`, like every agent. |
| `describe()` | One line per agent, printed by `check`. |
| `probe(env, prompt, telemetry)` (classmethod) | One traced model call outside any drop-in, which provisioning uses to prove the runtime works. |

**The behavior the rest of the scaffold relies on:**

- **Agents.**
  - Build one agent per `[agents.<name>]`, with `drop_in.instructions(name)` as its instructions.
  - Every agent uses `ctx.model` and `ctx.reasoning_effort`. A drop-in cannot set a model, so runs differ in model only by `CRUX_MODEL`.
- **Tools.**
  - Expose each `Tool` with its `Arguments` schema.
  - Validate the model's arguments and call `invoke`.
  - Return a `ToolError` or validation error to the model as text.
- **Delegates.**
  - A delegated agent or coding agent is a tool whose call starts from a fresh context holding only the caller's brief.
  - A coding agent is called through `CodingAgent.run`.
- **MCP servers.** Start each server an agent lists, with the environment from `McpServerConfig.resolved_env`.
- **Context.** Apply `context_strategy.select` to every model call.
- **Usage.** Add every model call's tokens to `ctx.usage` under the agent's name, so the budget holds.
- **Tracing.** When `telemetry.sdk_tracing` is true, export the SDK's spans through OpenTelemetry so they land in the current trace. Keep each span under OpenTelemetry's 128-attribute limit (see the README's Tracing section).

## Telemetry (`Telemetry`)

`telemetry.py`. Implementations: `LangfuseTelemetry` and `NullTelemetry`. Telemetry is not a registry: `telemetry_from_env` chooses the backend from the environment. A new backend implements the ABC and is chosen there.

- **`trace(name, metadata)`** opens a new trace in the run's session. Observations made inside it nest under it.
- **`record(name, kind, input, output)`** sends a finished observation immediately.
- **`generation(name, model, input)`** wraps a long model call. Set `output` and `usage` on the outcome it yields before it ends.
- **`flush()`** is called when the run ends, whether it completed, failed or was stopped.
- **`sdk_tracing`** tells the runtime whether to export its SDK's spans.

Langfuse v4 stores each observation once, when it ends, so keep observations short and send progress as it happens. An observation still open when the scaffold is stopped is sent with level `WARNING`. Usage goes to Langfuse as exclusive buckets (`usage_details`) so that each token is priced once.

## Budget and usage

`usage.py`. `Budget` (`[budget]`) holds hard limits, which the loop checks between iterations. `UsageLedger` tallies tokens per agent and coding agent, and it is persisted with the run state, so a restart keeps the count. The `budget_status` tool shows the agents where they stand. Any new component that calls a model adds its usage to `ctx.usage`. Otherwise the budget does not see it.

## Testing

Tests never call a model or the network. Use the existing doubles before writing new ones:

| Double | Where | Stands in for |
|---|---|---|
| `ScriptedModel`, `call`, `say`, `StoppedModel` | `tests/scripted.py` | A model, passed to `openai-agents` through `runtime_overrides={"models": …}` |
| `ScriptedCodingAgent` (`type = "scripted"`) | `tests/scripted.py` | A coding agent that writes declared files |
| `FakeRuntime`, `ScriptedGate` (`type = "scripted"`) | `tests/fakes.py` | A runtime and gates, for loop tests |
| `fake_slack_mcp.py` | `tests/` | A stdio MCP server |
| `BASE_FILES`, `write_tree`, `edit` | `tests/drop_ins.py` | A minimal drop-in directory |

A new component needs tests for:

- its declared options, including an invalid one;
- its behavior through the interface;
- for a tool or gate, one run through `main(["run", ...])` or the runtime with a scripted model.

`tests/test_docs.py` fails when a built-in type or extension point is missing from this guide.
