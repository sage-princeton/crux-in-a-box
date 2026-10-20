# `sdk-scaffold/` — a CRUX scaffold built on agent SDKs

`crux_scaffold` runs a CRUX from a **drop-in directory**. Everything about the run is declared in the drop-in's `scaffold.toml`: which agents exist and what each may call, how the outer loop decides a phase is done, how context is managed, and the token budget. The scaffold assembles that declaration from registered components. It runs agents on the OpenAI Agents SDK with OpenAI or compatible model providers, and supports delegated coding through Codex ([AE-230](https://linear.app/agent-evals/issue/AE-230/prototype-custom-scaffold-using-agent-sdks), [AE-268](https://linear.app/agent-evals/issue/AE-268)).

## Terminology

These terms follow `ciab-design-docs` (*CRUX Scaffold Philosophy* and *CRUX Scaffold Design*).

| Term | Meaning |
|---|---|
| **CRUX** | An experiment that evaluates AI agents on a real-world task. |
| **Scaffold** | The infrastructure around the LLMs: tools, prompts, loops. Claude Code, Codex, OpenClaw and this package are all scaffolds. The team docs also say *harness*; this package avoids that word. |
| **Drop-in directory** | A CRUX's input to a scaffold: `scaffold.toml`, prompts, personas, standing context, extensions and the workspace. On a run box it is staged at `/srv/crux-run/run-harness`. |
| **Workspace** | The directory the agents work in, inside the drop-in directory. |
| **Agent runtime** | The agent SDK that runs the declared agents: OpenAI Agents SDK now, Claude Agent SDK later. |
| **Model provider** | The endpoint and API adapter the runtime uses to query models. Declared in `scaffold.toml`; the model and effort still come from the run environment. |
| **Agent**, **orchestrator** | An LLM agent declared with a persona, standing context and tools. The orchestrator is the agent that receives the loop's prompts. |
| **Coding agent** | A non-interactive coding-agent SDK session (Codex now, Claude Code later) that agents delegate implementation to. It runs the full coding harness as a subprocess, with its own tools, sandbox and context management, so it is far more capable at changing code than an agent. Declare an agent for planning, coordination and talking to people, and a coding agent for code changes. |
| **Delegate** | An agent or coding agent that another agent calls as a tool. It starts from a fresh context holding only the caller's brief. |
| **Persona**, **standing context** | Who an agent is (`personas/*.md`); what it must know for the whole run (the files in its `context`). |
| **Toolkit**, **tool** | The functions agents can call: built-ins plus a drop-in's extensions. |
| **Loop**, **phase**, **iteration** | The outer loop runs phases in order. Each phase repeats iterations, one prompt to the orchestrator each, until its gates pass. |
| **Gate** | A check the loop runs between iterations (the team's *gating criteria*): a deterministic verifier, or a judgment by an isolated model the agents cannot author. A failing gate produces the next prompt. |
| **Context strategy** | How an agent's conversation is stored, and what part of it reaches the model on each call. |
| **Budget** | Hard token limits the loop enforces between iterations. |
| **Steering channel** | How the operator and the agents reach each other. The demo uses Slack through MCP. |
| **Operator** | The human running the CRUX. |
| **Run box** | The EC2 instance a run executes on; `src/ec2-workspaces/` calls it a *workspace*. |

## Design

Each scaffold responsibility in the team's design has one interface. Built-in implementations register themselves under a type name; a drop-in adds its own through `extensions`. This first version ships one implementation of each interface, the ones the demo needs.

| Responsibility | Interface (registry) | Built-in types |
|---|---|---|
| Agent orchestration, model configuration | `AgentRuntime` (`RUNTIMES`) | `openai-agents`; OpenAI and compatible Responses/Chat Completions providers |
| Agent loop: phases and gating criteria | `Loop` (`LOOPS`), `Gate` (`GATES`) | `phased`; `command` |
| Context management | `ContextStrategy` (`CONTEXT_STRATEGIES`) | `persistent` |
| Toolkit | `Tool` (`TOOLS`) | `read_file`, `write_file`, `list_files`, `command`, `rest`, `budget_status` |
| Coding subagents | `CodingAgent` (`CODING_AGENTS`) | `codex` |
| Communication (steering) | MCP servers in `[mcp_servers]` | any stdio MCP server; the demo uses Slack |
| Observability | `Telemetry` | Langfuse, or off |
| Resource management | `Budget`, `budget_status` | token limits |

`scaffold.py` is the composition root: it is the only place components are built and wired together. `config.py`, `drop_in.py`, `loop.py`, `gates.py` and `tools.py` never import an agent SDK. The OpenAI Agents SDK is confined to `agent_runtimes/openai_agents.py` and `context_strategies.py`, and Codex to `coding_agents.py`.

The web-cms-product-change demo in this design, as a C4 container view:

```mermaid
flowchart LR
    operator["<b>Operator</b><br/>[Person]<br/>Posts the request and answers questions"]
    slack["<b>Slack</b><br/>[External system]<br/>Request channel and thread"]
    openai["<b>OpenAI API</b><br/>[External system]<br/>Agent and Codex models"]
    langfuse["<b>Langfuse</b><br/>[External system]<br/>Traces, token usage, cost"]

    subgraph box["Run box"]
        loop["<b>Phased loop</b><br/>[Container: crux_scaffold]<br/>clarify, then implement;<br/>command gates; state.json"]
        runtime["<b>Agent runtime</b><br/>[Container: OpenAI Agents SDK]<br/>product_manager agent, toolkit,<br/>persistent session"]
        codex["<b>Coding agent</b><br/>[Container: Codex SDK]<br/>engineer: one Codex thread per brief"]
        slackmcp["<b>Slack MCP server</b><br/>[Container: slack-mcp-server]<br/>Reads the channel; posts to it only"]
        dropin[("<b>Drop-in directory</b><br/>[Files]<br/>scaffold.toml, prompts,<br/>personas, workspace/site")]
    end

    operator -- "Requests, answers" --> slack
    loop -- "One prompt per iteration" --> runtime
    loop -- "command gates" --> dropin
    runtime -- "MCP tools" --> slackmcp
    slackmcp -- "Bot token" --> slack
    runtime -- "engineer tool: brief" --> codex
    codex -- "Edits site/, sandboxed" --> dropin
    runtime -- "Model calls" --> openai
    codex -- "Model calls" --> openai
    runtime -. "OpenTelemetry spans" .-> langfuse

    classDef person fill:#08427b,color:#fff,stroke:#052e56
    classDef container fill:#1168bd,color:#fff,stroke:#0b4884
    classDef external fill:#999,color:#fff,stroke:#6b6b6b
    class operator person
    class loop,runtime,codex,slackmcp,dropin container
    class slack,openai,langfuse external
    style box fill:none,stroke:#444,stroke-dasharray:5 5
```

## A drop-in directory

```
scaffold.toml            the declaration (see examples/web-cms-product-change/scaffold.toml)
PROMPT.md                first phase's prompt, sent verbatim
prompts/*.md             later phase prompts, and each phase's continue prompt
personas/*.md            one per agent
scaffold_extensions.py   optional: the drop-in's own components
workspace/               where agents work; AGENTS.md and other standing context live here
OPERATOR_GUIDE.md        how to resolve placeholders and launch; operator notes live only here
```

The run stops before any model call if an operator-facing file or standing-context file still contains a `{{KEY}}` or `{{KEY|default}}` placeholder. The error lists each one as `file:line`.

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

## Commands

```bash
python -m crux_scaffold check --drop-in DIR --state-dir STATE   # validate and print the assembly; no model calls
python -m crux_scaffold run --drop-in DIR --state-dir STATE     # run the loop; a rerun with STATE resumes it
python -m crux_scaffold probe [--coding-agent codex]   # one traced model call (plus one Codex turn)
python -m crux_scaffold probe --drop-in DIR           # probe the drop-in's configured model provider
```

`probe --coding-agent codex` retries temporary-directory cleanup for up to 3.1 seconds when Codex's background plugin checkout is still writing files after its session closes. Other cleanup errors, or a directory that remains busy, still fail the probe.

`check` and `probe` are the first two of the team's testing protocols: nothing in the environment is broken, and the agent can do anything at all. Exit codes:

- 0: the loop completed.
- 1: a probe failed.
- 2: a configuration error.
- 3: the loop stopped early because its iterations or budget ran out.
- 128 + the signal number: the run was stopped by SIGTERM or SIGINT. The unfinished iteration reruns on restart.

## Tracing

Langfuse v4 never updates an observation once it has stored it, so each observation is sent exactly once, when it ends. The scaffold keeps what it sends short-lived so that a long run stays visible while it progresses. These rules follow the AE-240 findings for the Codex boxes:

- **One trace per loop iteration.** Traces are named like `clarify #1` and grouped by the run's session (`RUN_SLUG`). A trace's root arrives when its iteration ends; its children arrive as they finish.
- **Coding-agent turns stream.** A Codex turn is one `generation` observation. Each Codex item, such as a command, file change or message, is sent as a child the moment it completes. The generation itself arrives at the end of the turn with the output and token usage. Usage is split into Langfuse's exclusive buckets (uncached input, cache reads, cache writes, output, reasoning), so each token is priced once, at its own rate.
- **Reasoning summaries are requested and traced.** Native OpenAI Agents calls set the Responses API reasoning-summary setting to `auto`, which selects the most detailed summarizer available for the model; their SDK response spans carry returned summary text. Delegated Codex calls use `model_reasoning_summary = "auto"` for the same selection and send each returned reasoning item as a `codex:reasoning` span. These are model-generated summaries, not private chain-of-thought; a model or provider may omit them.
- **Generations stay small.** Langfuse reads an Agents SDK generation's input and output from `input.value` and `output.value`, so the SDK's per-message attributes (`llm.input_messages.*`) are not recorded. They grow with the conversation, and past OpenTelemetry's 128-attribute limit they would evict the span's session, tags, model and usage.
- **A stopped scaffold flushes.** SIGTERM or SIGINT ends every open observation with level `WARNING` and a `stopped` status message before the process exits. A SIGKILL, such as from the OOM killer, still loses the open iteration's root and anything not yet flushed. Langfuse's flush interval is 5 seconds.

## Environment

| Variable | Use |
|---|---|
| `OPENAI_API_KEY` | Agent and Codex model calls |
| The provider's `api_key_env`, e.g. `OPENROUTER_API_KEY` | Compatible model calls; the drop-in names the variable, never its secret value |
| `CRUX_MODEL`, `CRUX_REASONING_EFFORT` | The model and effort for every agent and coding agent. They are the only place these are set; a drop-in cannot override them. A run without `CRUX_MODEL` stops with a configuration error. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Tracing. When unset, tracing is off; `probe` requires it. |
| `RUN_SLUG`, `CRUX_WORKSPACE_ID` | The trace environment (the slug, lowercased), session, tags and metadata. These match the Codex and Claude boxes. |
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

**GCP billing:** OpenRouter must have the intended Google Vertex BYOK integration configured, with shared-capacity fallback disabled for Argon. The request above pins Vertex and disables provider fallback; it cannot change the account's BYOK settings. Check `is_byok` in the returned usage and the generation's provider before treating a run as GCP-billed. An OpenRouter key alone does not establish that billing path. See [OpenRouter BYOK](https://openrouter.ai/docs/guides/overview/auth/byok).

The Responses path uses `store=false` and the complete local SDK session, including encrypted reasoning, across tool calls and restarts. Returned summaries reach Langfuse through the existing SDK instrumentation; cache and reasoning counts also reach the persisted usage ledger. Chat Completions uses the SDK's corresponding adapter; reasoning replay and tool compatibility depend on the selected endpoint. The runtime remains `openai-agents` in trace metadata because that is the scaffold executing the model calls. Langfuse's catalog cost estimate is separate from the provider's actual BYOK billing receipt.

`examples/argon-smoke` exercises a file write, a fresh-context reviewer, a file read, and a command gate. It has no coding-agent dependency. Codex is a separate coding harness with its own provider setup; changing this runtime's provider does not configure Codex or add an Antigravity adapter.

With the model-provider key and Langfuse variables exported, run from `src/sdk-scaffold`:

```bash
export CRUX_MODEL=google/gemini-4-argon CRUX_REASONING_EFFORT=low
ARGON_SMOKE_DIR=$(mktemp -d)
cp -R examples/argon-smoke "$ARGON_SMOKE_DIR/drop-in"
export RUN_SLUG="argon-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
python -m crux_scaffold check --drop-in "$ARGON_SMOKE_DIR/drop-in" --state-dir "$ARGON_SMOKE_DIR/state"
python -m crux_scaffold probe --drop-in "$ARGON_SMOKE_DIR/drop-in"
python -m crux_scaffold run --drop-in "$ARGON_SMOKE_DIR/drop-in" --state-dir "$ARGON_SMOKE_DIR/state"
```

In Langfuse, select the lowercased `RUN_SLUG` environment or the original `RUN_SLUG` session. Expect `crux-probe` and `smoke #1` traces, with Argon generations, researcher/reviewer agents, file tools, and the `note_written` gate. The local `state/state.json` retains the token ledger. Shared Langfuse credentials are normally provisioned from the JSON SSM parameter `/crux/system/env`; the SDK CLI itself reads environment variables and does not fetch secrets or load local env files.

## Run the demo in Docker

`docker/demo.sh` builds an image holding the scaffold, the demo drop-in and the Slack MCP server, then runs it. The container stages the drop-in once in the volume `crux-sdk-scaffold-demo` at `/work/web-cms-product-change`, so a rerun resumes the run there.

1. **Slack.** Create the app from [`examples/web-cms-product-change/slack-app-manifest.yaml`](examples/web-cms-product-change/slack-app-manifest.yaml), then install it, invite the bot to a channel and copy the channel ID. See the demo's [OPERATOR_GUIDE.md](examples/web-cms-product-change/OPERATOR_GUIDE.md), section 1.
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
   cd src/sdk-scaffold
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

Set `AGENT_PLATFORM=openai-agents` and `DROP_IN_PATH=<drop-in directory>` in the workspace base config; see [`../ec2-workspaces/README.md`](../ec2-workspaces/README.md). Provisioning does four things, and creates no AgentRQ workspace:

1. It stages this directory, root-owned, at `/opt/crux-sdk-scaffold`, and the drop-in at `/srv/crux-run/run-harness`.
2. It writes the environment above to `/etc/crux-run.env`.
3. It installs system Bubblewrap and Ubuntu's AppArmor profile for it, then checks sandboxed shell execution and file write/readback before running `probe --coding-agent codex`. `workspace-write` remains the default; global user-namespace restrictions are unchanged.
4. It installs `crux-sdk-run.service`, but does not start it.

The automated EC2 flow currently supports direct OpenAI only: its preflight rejects `MODEL_PROVIDER=openrouter`, and its provisioning probe does not load the drop-in's provider settings and always probes Codex too. Use the CLI workflow above for Argon. Provisioning needs those assumptions removed before it can launch a compatible-provider run; this runtime change does not configure the separate Codex harness.

## Development

```bash
cd src/sdk-scaffold
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt -r ../../requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/python -m pytest -q tests
```

Contribution guidelines are in [AGENTS.md](AGENTS.md). Tests never call a model or the network. They use scripted models, a scripted coding agent, a fake runtime, a fake stdio Slack MCP server and an in-memory span exporter.
