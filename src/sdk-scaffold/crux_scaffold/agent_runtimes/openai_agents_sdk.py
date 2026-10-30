"""
NB: this SDK works with any model that is "openai-compatible", e.g., it's not
specific to OpenAI models. This supports, for example, Google Vertex models
out of the box.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Literal, Self

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    set_trace_processors,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.items import ModelResponse
from agents.mcp import MCPServerStdio
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from agents.tracing import TracingProcessor
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry.trace import get_current_span
from pydantic import Field, HttpUrl, ValidationError, model_validator

from crux_scaffold.agent_runtimes.base import RUNTIMES, AgentRuntime, Assembly, TurnOutcome, Verdict
from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.components import CountLimit, Options, count_limit
from crux_scaffold.config import McpServerConfig, delegation_order
from crux_scaffold.context_strategies import is_standing_context
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.steering import SteeringOptions
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, Tool, ToolImage, validate_review_tools
from crux_scaffold.usage import UsageLedger
from crux_scaffold.workspace import ReviewWorkspace, RunContext

JUDGE = "judge"

_TURN: ContextVar[dict | None] = ContextVar("scaffold_turn", default=None)
_REQUEST: ContextVar[dict | None] = ContextVar("scaffold_request", default=None)


@contextmanager
def request_scope():
    turn_token = _TURN.set({"prompt": None, "calls": 0})
    request_token = _REQUEST.set(None)
    try:
        yield
    finally:
        _REQUEST.reset(request_token)
        _TURN.reset(turn_token)


class RequestTraceProcessor(TracingProcessor):
    """Enrich the existing generation before OpenInference closes its OTel span."""

    def on_span_end(self, span) -> None:
        request, data = _REQUEST.get(), span.span_data
        if request is not None and data.type in {"response", "generation"}:
            current = get_current_span()
            current.set_attribute("langfuse.observation.input", json.dumps(request))
            output = data.response if data.type == "response" else data.output
            if output is not None:
                encoded = output.model_dump_json() if data.type == "response" else json.dumps(output)
                current.set_attribute("langfuse.observation.output", encoded)

    def on_span_start(self, span) -> None:
        pass

    def on_trace_start(self, trace) -> None:
        pass

    def on_trace_end(self, trace) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


class ModelProviderOptions(Options):
    """Configure the SDK's provider; model and effort still come only from the run environment."""

    type: Literal["openai", "openai-compatible"]
    api: Literal["responses", "chat-completions"]
    base_url: HttpUrl | None = None
    api_key_env: str = Field("OPENAI_API_KEY", pattern=r"^[A-Z_][A-Z0-9_]*$")
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider(self) -> Self:
        if self.type == "openai-compatible" and self.base_url is None:
            raise ValueError("openai-compatible model_provider requires base_url")
        if self.type == "openai" and (
            self.base_url or self.extra_body or self.api != "responses" or self.api_key_env != "OPENAI_API_KEY"
        ):
            raise ValueError("custom provider settings require type=openai-compatible")
        if {
            "model",
            "models",
            "input",
            "messages",
            "instructions",
            "tools",
            "store",
            "stream",
            "previous_response_id",
            "conversation",
            "reasoning",
            "reasoning_effort",
            "include",
        } & self.extra_body.keys():
            raise ValueError("extra_body cannot override the run model, context, tools, reasoning or session settings")
        return self


class OpenAIAgentsOptions(Options):
    max_turns: CountLimit = 50
    model_provider: ModelProviderOptions = ModelProviderOptions(type="openai", api="responses")
    background_coding: bool = False
    steering: SteeringOptions | None = None


class BriefArguments(Arguments):
    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")


class CodingResultArguments(Arguments):
    wait: bool = Field(description="Whether to await completion; false returns immediately if still running.")


class UsageHooks(RunHooks):
    def __init__(self, ledger: UsageLedger, source: str | None = None) -> None:
        self.ledger = ledger
        self.source = source

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: ModelResponse) -> None:
        usage = response.usage
        self.ledger.add(
            self.source or agent.name,
            usage.input_tokens,
            usage.output_tokens,
            cached_input_tokens=usage.input_tokens_details.cached_tokens,
            reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens,
        )


@RUNTIMES.register
class OpenAIAgentsRuntime(AgentRuntime):
    """Agents are SDK Agents; delegates are tools, so each delegated call starts from a fresh context holding only
    its brief. The orchestrator's conversation lives in the context strategy's session."""

    type_name = "openai-agents"
    Options = OpenAIAgentsOptions

    def __init__(
        self, name: str, options: OpenAIAgentsOptions, *, assembly: Assembly, models: Mapping[str, Model] | None = None
    ) -> None:
        super().__init__(name, options, assembly=assembly)
        self.models = dict(models or {})
        self.hooks = UsageHooks(assembly.context.usage)
        self.run_config = RunConfig(call_model_input_filter=self._select_input)
        self.agents: dict[str, Agent] = {}
        self.servers: dict[str, MCPServerStdio] = {}
        self.client: AsyncOpenAI | None = None
        self.provider: OpenAIProvider | None = None
        self._coding_tasks: dict[str, asyncio.Task[CodingResult]] = {}
        self._stack = AsyncExitStack()
        self._steering_items: list[dict] = []

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.assembly.drop_in.config, self.assembly.context
        if self.options.model_provider.type == "openai-compatible":
            self.client = _validate_and_get_client(self.options.model_provider, ctx.env)
            self.provider = OpenAIProvider(
                openai_client=self.client, use_responses=self.options.model_provider.api == "responses"
            )
        if self.options.background_coding:
            declared = set(self.assembly.tools) | set(config.agents) | set(config.coding_agents)
            self._check_result_names(declared)
        used = {server for agent in config.agents.values() for server in agent.mcp_servers}
        if self.options.steering:
            used.add(self.options.steering.server)
        used = sorted(used)
        self.servers = {name: _mcp_server(name, config.mcp_servers[name], ctx.env) for name in used}
        for name in delegation_order(config.agents):
            spec = config.agents[name]
            tools = [self._function_tool(self.assembly.tools[tool]) for tool in spec.tools]
            tools += [self._delegate(delegate) for delegate in spec.delegates]
            if self.options.background_coding:
                tools += [
                    self._coding_result_tool(delegate)
                    for delegate in spec.delegates
                    if delegate in self.assembly.coding_agents
                ]
            self.agents[name] = Agent(
                name=name,
                instructions=self._instructions,
                tools=tools,
                mcp_servers=[self.servers[server] for server in spec.mcp_servers],
                model=self._model(name),
                model_settings=self._settings(),
            )

    async def __aenter__(self) -> Self:
        # TODO(AE-247): let the AgentRuntime base class own telemetry setup for every runtime.
        trace_agents_sdk(self.assembly.context.telemetry)
        self.build()
        steering = self.assembly.context.steering
        try:
            if self.client:
                await self._stack.enter_async_context(self.client)
            for server in self.servers.values():
                await self._stack.enter_async_context(server)
                if self.options.background_coding:
                    self._check_result_names({tool.name for tool in await server.list_tools()})
            if steering:
                steering.transport = self.servers[steering.options.server]
                available = {tool.name for tool in await steering.transport.list_tools()}
                required = {"conversations_replies" if steering.options.thread_ts else "conversations_history"}
                if any(tool.type_name == "escalate" for tool in self.assembly.tools.values()):
                    required.add("conversations_add_message")
                if missing := required - available:
                    raise InvalidDropInError(f"steering MCP server is missing tools: {', '.join(sorted(missing))}")
            self.session = self.assembly.context_strategy.session(
                self.assembly.drop_in.config.orchestrator, self.assembly.context
            )
        except BaseException as exc:
            await self._stack.aclose()
            if steering and isinstance(exc, Exception):
                status = (
                    "startup_failed: check the steering MCP server, credentials, scopes and required tools; "
                    "restart with the same state directory"
                )
                steering.state["status"] = status
                steering.save()
                steering.event("startup_failed", status=status)
                if not isinstance(exc, InvalidDropInError):
                    raise InvalidDropInError(status) from None
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            await self._cancel_coding()
        finally:
            await self._stack.aclose()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        with request_scope():
            return await self._run(prompt, workflow=workflow)

    async def _run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self._steering_items = []
        orchestrator = self.agents[self.assembly.drop_in.config.orchestrator]
        input = self._turn_input(prompt)
        try:
            try:
                result = await Runner.run(
                    orchestrator,
                    input,
                    context=self.assembly.context,
                    max_turns=count_limit(self.options.max_turns),
                    hooks=self.hooks,
                    session=self.session,
                    run_config=replace(self.run_config, workflow_name=workflow),
                )
            except MaxTurnsExceeded:
                outcome = TurnOutcome(final_output="", completed=False)
            else:
                outcome = TurnOutcome(final_output=str(result.final_output), completed=True)
            remaining = [f"{name}: {await self._coding_result(name, wait=True)}" for name in list(self._coding_tasks)]
            if remaining:
                report = "Scaffold coding completion report (uncollected delegate outputs):\n\n" + "\n\n".join(
                    remaining
                )
                await self.session.add_items([{"role": "user", "content": report}])
            outcome.final_output = "\n\n".join(part for part in [outcome.final_output, *remaining] if part)
            return outcome
        except BaseException:
            await self._cancel_coding()
            raise

    def _check_result_names(self, names: set[str]) -> None:
        config = self.assembly.drop_in.config
        generated = {
            f"{delegate}_result"
            for agent in config.agents.values()
            for delegate in agent.delegates
            if delegate in self.assembly.coding_agents
        }
        if collisions := sorted(generated & names):
            raise InvalidDropInError(f"Background coding result tool name collision: {', '.join(collisions)}")

    async def _cancel_coding(self) -> None:
        tasks = list(self._coding_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._coding_tasks.clear()

    async def _coding_result(self, name: str, *, wait: bool) -> str:
        task = self._coding_tasks.get(name)
        if task is None:
            return f"error: No uncollected coding task for '{name}'."
        if not wait and not task.done():
            return f"[coding agent running] {name}. Use rest before checking again, or call {name}_result(wait=true)."
        try:
            async with self.delegation_wait():
                return (await task).as_tool_output()
        except Exception as exc:  # noqa: BLE001 - Worker failures return to the PI; cancellation propagates.
            return f"error: Coding agent '{name}' failed: {exc}"
        finally:
            if self._coding_tasks.get(name) is task:
                del self._coding_tasks[name]

    def _coding_result_tool(self, name: str) -> FunctionTool:
        async def invoke(_: ToolContext[Any], raw: str) -> str:
            try:
                args = CodingResultArguments.model_validate_json(raw)
            except ValidationError as exc:
                return f"error: {exc}"
            return await self._coding_result(name, wait=args.wait)

        return FunctionTool(
            name=f"{name}_result",
            description=f"Check or collect the coding result from {name}.",
            on_invoke_tool=invoke,
            params_json_schema=ensure_strict_json_schema(CodingResultArguments.model_json_schema()),
        )

    async def judge(
        self,
        name: str,
        rubric: str,
        evidence: str,
        *,
        tools: list[str],
        max_turns: int | None,
        review_workspace: ReviewWorkspace | None = None,
    ) -> Verdict:
        ctx = self.assembly.context
        if review_workspace is not None:
            validate_review_tools([self.assembly.tools[tool] for tool in tools])
            ctx = replace(ctx, workspace=review_workspace)
        agent = Agent(
            name=JUDGE if review_workspace is not None else f"{JUDGE}:{name}",
            instructions=rubric,
            output_type=Verdict,
            model=self._model(JUDGE),
            model_settings=self._settings(),
            tools=[self._function_tool(self.assembly.tools[tool], ctx) for tool in tools],
        )
        try:
            with request_scope():
                result = await Runner.run(
                    agent,
                    evidence,
                    context=ctx,
                    max_turns=max_turns,
                    hooks=UsageHooks(ctx.usage, f"{JUDGE}:{name}"),
                    run_config=RunConfig(workflow_name=f"{JUDGE}:{name}", call_model_input_filter=self._select_cache_input),
                )
        except MaxTurnsExceeded:
            return Verdict(
                passed=False,
                next_prompt=None,
                feedback=f"The judge ran out of turns ({max_turns}) before reaching a verdict.",
            )
        return result.final_output

    def _turn_input(self, prompt: str) -> list[Any]:
        if turn := _TURN.get():
            turn["prompt"] = prompt
        return [{"role": "user", "content": prompt}]

    def describe(self) -> list[str]:
        self.build()
        config = self.assembly.drop_in.config
        provider = self.options.model_provider
        return [
            f"model provider {provider.type} ({provider.api})",
            *(
                f"{name}: tools [{', '.join(tool.name for tool in agent.tools)}] "
                f"mcp [{', '.join(config.agents[name].mcp_servers)}]"
                for name, agent in self.agents.items()
            ),
        ]

    @classmethod
    async def probe(
        cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry, *, options: OpenAIAgentsOptions | None = None
    ) -> str:
        trace_agents_sdk(telemetry)
        model = env.get("CRUX_MODEL")
        if not model:
            raise InvalidDropInError("CRUX_MODEL is not set")
        provider_options = (options or OpenAIAgentsOptions()).model_provider
        async with AsyncExitStack() as stack:
            if provider_options.type == "openai-compatible":
                client = await stack.enter_async_context(_validate_and_get_client(provider_options, env))
                model = OpenAIProvider(
                    openai_client=client, use_responses=provider_options.api == "responses"
                ).get_model(model)
            agent = Agent(
                name="probe",
                instructions="Reply with exactly the text you are asked for.",
                model=model,
                model_settings=_model_settings(provider_options, env),
            )
            return str((await Runner.run(agent, prompt, max_turns=1)).final_output)

    def _model(self, agent: str) -> str | Model:
        """The run's model; tests substitute scripted models per agent through `models`."""
        if agent in self.models:
            return self.models[agent]
        model = self.assembly.context.model
        return self.provider.get_model(model) if self.provider else model

    def _settings(self) -> ModelSettings:
        return _model_settings(self.options.model_provider, self.assembly.context.env)

    def _instructions(self, context: RunContextWrapper, agent: Agent) -> str:
        return self.assembly.instructions(agent.name)

    async def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        steering = self.assembly.context.steering
        annotations = []
        if steering and data.agent.name == self.assembly.drop_in.config.orchestrator:
            await steering.poll()
            pending = steering.pending()
            if pending:
                for message in pending:
                    envelope = steering.envelope(message)
                    try:
                        history = await self.session.get_items()
                        if envelope not in history:
                            await self.session.add_items([envelope])
                            history.append(envelope)
                        if envelope not in data.model_data.input:
                            self._steering_items.append(envelope)
                        steering.delivered(message["ts"])
                    except Exception:  # noqa: BLE001 - Session backends must fail closed without exposing raw errors.
                        status = (
                            "delivery_failed: check conversation/state storage; restart with the same state directory"
                        )
                        steering.state["status"] = status
                        steering.save()
                        steering.event("delivery_failed", message_id=message["ts"], status=status)
                        raise InvalidDropInError(status) from None
            if steering.state["status"] != "ok":
                annotations.append(f"Steering transport status: {steering.state['status']}")
        history = [item for item in data.model_data.input if not is_standing_context(item)]
        items = list(self.assembly.context_strategy.select(history))
        turn = _TURN.get()
        prompt = turn["prompt"] if turn else None
        prompt_index = next((index for index in range(len(items) - 1, -1, -1)
                             if items[index].get("role") == "user" and items[index].get("content") == prompt), None)
        new_task = turn is not None and turn["calls"] == 0 and prompt is not None
        if new_task and prompt_index is not None:
            items.pop(prompt_index)

        if steering and data.agent.name == self.assembly.drop_in.config.orchestrator:
            # Filter additions are not persisted by Runner. Keep newly stored messages in this run's input
            # until its next run reloads the durable session, even when a strategy trims older conversation.
            items.extend(item for item in self._steering_items if item not in items)
        research = self.assembly.research_context(data.agent.name)
        annotations.insert(0, f"<research_context>\n{research}\n</research_context>")
        status = await self.assembly.context.budget_status()
        annotations.append(f"<resource_status>\n{status}\n</resource_status>")
        result = self._cache_input(ModelInputData(input=items, instructions=data.model_data.instructions))
        if new_task:
            annotations.append(f"# Current task\n\n{prompt}")
        result.input.append({"role": "user", "content": "\n\n".join(annotations)})
        if turn is not None:
            turn["calls"] += 1
        self._trace_request(result)
        return result

    @staticmethod
    def _trace_request(data: ModelInputData) -> None:
        _REQUEST.set({"instructions": data.instructions, "messages": data.input})

    async def _select_cache_input(self, data: CallModelData[Any]) -> ModelInputData:
        result = self._cache_input(data.model_data)
        self._trace_request(result)
        return result

    def _cache_input(self, data: ModelInputData) -> ModelInputData:
        """OpenRouter translates these text boundaries to Claude's cache_control blocks.

        Mark stable instructions and retained conversation, never the replaced status tail.
        Normalize text consistently on every request and copy it so markers cannot enter sessions.
        """
        provider = self.options.model_provider
        if (provider.type != "openai-compatible" or provider.base_url.host != "openrouter.ai"
                or not self.assembly.context.model.removeprefix("~").startswith("anthropic/")):
            return data
        selected = list(data.input)
        if data.instructions:
            selected.insert(0, {"role": "developer", "content": data.instructions})
        candidates = []
        explicit = False
        for index, item in enumerate(selected):
            if not isinstance(item, dict):
                continue
            field = "output" if item.get("type") == "function_call_output" else "content"
            if field == "content" and item.get("role") not in {"user", "developer", "system"}:
                continue
            content = item.get(field)
            if isinstance(content, str):
                content = [{"type": "input_text", "text": content}]
            elif isinstance(content, list):
                content = [dict(part) if isinstance(part, dict) else part for part in content]
            else:
                continue
            explicit |= any(isinstance(part, dict) and "prompt_cache_breakpoint" in part for part in content)
            texts = [position for position, part in enumerate(content)
                     if isinstance(part, dict) and part.get("type") == "input_text" and part.get("text")]
            if not texts:
                continue
            selected[index] = {**item, field: content}
            candidate = (index, field, texts[-1])
            candidates.append(candidate)
        if candidates and not explicit:
            for index, field, position in {candidates[0], candidates[-1]}:
                selected[index][field][position]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return ModelInputData(input=selected, instructions=None)

    def _function_tool(self, tool: Tool, ctx: RunContext | None = None) -> FunctionTool:
        ctx = ctx or self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str):
            try:
                output = await tool.invoke(ctx, tool.Arguments.model_validate_json(raw or "{}"))
                if isinstance(output, list):
                    return [
                        ToolOutputImage(image_url="data:image/png;base64," + base64.b64encode(item.png).decode())
                        if isinstance(item, ToolImage)
                        else ToolOutputText(text=item)
                        for item in output
                    ]
                return output
            except (ToolError, ValidationError) as exc:
                return f"error: {exc}"

        return FunctionTool(
            name=tool.name,
            description=tool.description,
            on_invoke_tool=invoke,
            params_json_schema=ensure_strict_json_schema(tool.Arguments.model_json_schema()),
        )

    def _delegate(self, name: str) -> FunctionTool:
        config = self.assembly.drop_in.config
        if name not in config.agents:
            return self._coding_tool(self.assembly.coding_agents[name])
        spec = config.agents[name]
        tool = self.agents[name].as_tool(
            tool_name=name,
            tool_description=spec.description,
            max_turns=count_limit(spec.max_turns),
            hooks=self.hooks,
            run_config=self.run_config,
            input_builder=lambda data: self._turn_input(data["params"]["input"]),
        )
        invoke_tool = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], raw: str) -> str:
            with request_scope():
                async with self.delegation_slot():
                    return await invoke_tool(context, raw)

        tool.on_invoke_tool = invoke
        return tool

    def _coding_tool(self, agent: CodingAgent) -> FunctionTool:
        ctx = self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            brief = BriefArguments.model_validate_json(raw).brief

            async def execute() -> CodingResult:
                async with self.delegation_slot(background=self.options.background_coding):
                    return await agent.run(brief, ctx)

            if self.options.background_coding:
                if agent.name in self._coding_tasks:
                    return f"error: Collect {agent.name}_result before starting another brief for '{agent.name}'."
                self._coding_tasks[agent.name] = asyncio.create_task(execute())
                return (
                    f"[coding agent started] {agent.name}. Choose rest durations while it works; "
                    f"call {agent.name}_result(wait=false) to check or {agent.name}_result(wait=true) to await "
                    "and collect the result."
                )
            return (await execute()).as_tool_output()

        description = agent.description
        if self.options.background_coding:
            description += f" Starts in the background; collect its result using {agent.name}_result."
        return FunctionTool(
            name=agent.name,
            description=description,
            on_invoke_tool=invoke,
            params_json_schema=ensure_strict_json_schema(BriefArguments.model_json_schema()),
        )


def _mcp_server(name: str, config: McpServerConfig, env: Mapping[str, str]) -> MCPServerStdio:
    return MCPServerStdio(
        params={"command": config.command, "args": config.args, "env": config.resolved_env(name, env)},
        name=name,
        cache_tools_list=True,
        client_session_timeout_seconds=config.timeout_seconds,
        max_retry_attempts=0,
    )


def _validate_and_get_client(options: ModelProviderOptions, env: Mapping[str, str]) -> AsyncOpenAI:
    api_key = env.get(options.api_key_env)
    if not api_key:
        raise InvalidDropInError(f"{options.api_key_env} is not set")
    return AsyncOpenAI(api_key=api_key, base_url=str(options.base_url))


def _model_settings(options: ModelProviderOptions, env: Mapping[str, str]) -> ModelSettings:
    settings = ModelSettings(reasoning=Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None, summary="auto"))
    if options.type == "openai-compatible":
        # Compatible endpoints keep state locally; signed reasoning must survive the round trip.
        settings.preserve_raw_usage = True
        settings.extra_body = options.extra_body or None
        if options.api == "responses":
            settings.store = False
            settings.response_include = ["reasoning.encrypted_content"]
        else:
            settings.reasoning = Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None)
    return settings


def trace_agents_sdk(telemetry: Telemetry) -> None:
    set_tracing_disabled(not telemetry.sdk_tracing)
    if telemetry.sdk_tracing:
        instrument_agents_sdk()


def instrument_agents_sdk() -> None:
    """Export Agents SDK spans through OpenTelemetry (and so into Langfuse) instead of to the OpenAI platform.

    Langfuse reads a generation's input and output from `input.value` and `output.value`. The per-message copies
    (`llm.input_messages.*`) grow with the conversation, and past OpenTelemetry's 128-attribute limit they evict a
    span's oldest attributes: Langfuse's session, tags, model and usage."""
    instrumentor = OpenAIAgentsInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        set_trace_processors([RequestTraceProcessor()])
        instrumentor.instrument(exclusive_processor=False,
                                config=TraceConfig(hide_input_messages=True, hide_output_messages=True))
