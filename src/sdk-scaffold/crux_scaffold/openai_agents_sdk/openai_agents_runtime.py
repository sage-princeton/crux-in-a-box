"""
NB: this SDK works with any model that is "openai-compatible", e.g., it's not
specific to OpenAI models. This supports, for example, Google Vertex models
out of the box.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, Self

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    RunContextWrapper,
    Runner,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.mcp import MCPServerStdio
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import ValidationError

from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.components import CountLimit, count_limit
from crux_scaffold.config import delegation_order
from crux_scaffold.context_strategies import ContextStrategy, is_standing_context
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.openai_agents_sdk._delegate_call import _DelegateCall
from crux_scaffold.openai_agents_sdk.brief_arguments import BriefArguments
from crux_scaffold.openai_agents_sdk.coding_result_arguments import CodingResultArguments
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.openai_agents_sdk.turn_outcome import TurnOutcome
from crux_scaffold.openai_agents_sdk.usage_hooks import UsageHooks
from crux_scaffold.openai_agents_sdk.verdict import Verdict
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Tool, ToolImage, validate_review_tools
from crux_scaffold.workspace import ReviewWorkspace, RunContext

if TYPE_CHECKING:
    from crux_scaffold.drop_in import DropInDirectory

JUDGE = "judge"


class OpenAIAgentsRuntime:
    """Run the scaffold's orchestrator, delegates and isolated judges through the OpenAI Agents SDK.

    Adapt scaffold tools, coding agents and MCP servers into SDK agents and tools. The context strategy stores
    the orchestrator's conversation; delegated calls start fresh with their brief and declared standing context.
    Expose provider settings directly as `model_provider` and derive `model_family` from the run's model name.
    Own delegation capacity, background coding tasks, usage hooks and client/server cleanup for the run.
    """

    type_name = "openai-agents"

    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        drop_in: DropInDirectory,
        context: RunContext,
        tools: Mapping[str, Tool],
        coding_agents: Mapping[str, CodingAgent],
        context_strategy: ContextStrategy,
        instructions: Callable[[str], str],
        max_turns: CountLimit = 50,
        background_coding: bool = False,
        models: Mapping[str, Model] | None = None,
    ) -> None:
        self.model_provider = model_provider
        self.max_turns = max_turns
        self.background_coding = background_coding
        self.drop_in = drop_in
        self.context = context
        self.tools = tools
        self.coding_agents = coding_agents
        self.context_strategy = context_strategy
        self.instructions = instructions
        self._delegate_limit = drop_in.config.delegation.max_concurrent
        self._active_delegates = 0
        self._delegate_changed = asyncio.Condition()
        self._delegate_call: ContextVar[_DelegateCall | None] = ContextVar("delegate_call", default=None)
        self.models = dict(models or {})
        self.hooks = UsageHooks(context.usage)
        self.run_config = RunConfig(call_model_input_filter=self._select_input)
        self.agents: dict[str, Agent] = {}
        self.servers: dict[str, MCPServerStdio] = {}
        self.client: AsyncOpenAI | None = None
        self.provider: OpenAIProvider | None = None
        self._coding_tasks: dict[str, asyncio.Task[CodingResult]] = {}
        self._stack = AsyncExitStack()
        self._steering_items: list[dict] = []

    @property
    def model_family(self) -> Literal["anthropic", "google", "openai"] | None:
        """Derive the model vendor from its prefix, ignoring a leading OpenRouter alias marker.

        Unprefixed names on the direct OpenAI provider belong to OpenAI. Other unrecognized names stay
        unclassified so family-specific request changes are never applied by guesswork.
        """
        model = self.context.model.removeprefix("~")
        if model.startswith("anthropic/"):
            return "anthropic"
        if model.startswith("google/"):
            return "google"
        if model.startswith("openai/") or ("/" not in model and self.model_provider.type == "openai"):
            return "openai"
        return None

    @asynccontextmanager
    async def delegation_slot(self, *, background: bool = False) -> AsyncGenerator[None, None]:
        """Bound delegated work across this runtime; suspend a parent while its nested calls run."""
        limit = self._delegate_limit
        if limit is None:
            yield
            return
        parent = None if background else self._delegate_call.get()
        changed = self._delegate_changed
        async with self._suspend_delegate(parent):
            async with changed:
                await changed.wait_for(lambda: self._active_delegates < limit)
                self._active_delegates += 1
                call = _DelegateCall(asyncio.current_task(), active=True)
            token = self._delegate_call.set(call)
            try:
                yield
            finally:
                self._delegate_call.reset(token)
                async with changed:
                    call.closed = True
                    if call.active:
                        call.active = False
                        self._active_delegates -= 1
                    changed.notify_all()

    @asynccontextmanager
    async def _suspend_delegate(self, parent: _DelegateCall | None) -> AsyncGenerator[None, None]:
        limit = self._delegate_limit
        if limit is None or parent is None:
            yield
            return
        changed = self._delegate_changed
        async with changed:
            parent.children += 1
            if parent.active:
                parent.active = False
                self._active_delegates -= 1
            changed.notify_all()
        try:
            yield
        finally:
            async with changed:
                parent.children -= 1
                changed.notify_all()
                await changed.wait_for(lambda: parent.closed or parent.task.cancelling() or parent.children
                                       or parent.active or self._active_delegates < limit)
                if not (parent.closed or parent.task.cancelling() or parent.children or parent.active):
                    parent.active = True
                    self._active_delegates += 1
                    changed.notify_all()

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.drop_in.config, self.context
        if self.model_provider.type == "openai-compatible":
            self.client = _validate_and_get_client(self.model_provider, ctx.env)
            self.provider = OpenAIProvider(
                openai_client=self.client, use_responses=self.model_provider.api == "responses"
            )
        if self.background_coding:
            declared = set(self.tools) | set(config.agents) | set(config.coding_agents)
            self._check_result_names(declared)
        used = {server for agent in config.agents.values() for server in agent.mcp_servers}
        if ctx.steering:
            used.add(ctx.steering.options.server)
        for name in sorted(used):
            server = config.mcp_servers[name]
            self.servers[name] = MCPServerStdio(
                params={"command": server.command, "args": server.args, "env": server.resolved_env(name, ctx.env)},
                name=name,
                cache_tools_list=True,
                client_session_timeout_seconds=server.timeout_seconds,
                max_retry_attempts=0,
            )
        for name in delegation_order(config.agents):
            spec = config.agents[name]
            tools = [self._function_tool(self.tools[tool]) for tool in spec.tools]
            tools += [self._delegate(delegate) for delegate in spec.delegates]
            if self.background_coding:
                tools += [
                    self._coding_result_tool(delegate)
                    for delegate in spec.delegates
                    if delegate in self.coding_agents
                ]
            self.agents[name] = Agent(
                name=name,
                instructions=self._instructions,
                tools=tools,
                mcp_servers=[self.servers[server] for server in spec.mcp_servers],
                model=self._model(name),
                model_settings=_model_settings(self.model_provider, self.context.env),
            )

    async def __aenter__(self) -> Self:
        trace_agents_sdk(self.context.telemetry)
        self.build()
        steering = self.context.steering
        try:
            if self.client:
                await self._stack.enter_async_context(self.client)
            for server in self.servers.values():
                await self._stack.enter_async_context(server)
                if self.background_coding:
                    self._check_result_names({tool.name for tool in await server.list_tools()})
            if steering:
                steering.transport = self.servers[steering.options.server]
                available = {tool.name for tool in await steering.transport.list_tools()}
                required = {"conversations_replies" if steering.options.thread_ts else "conversations_history"}
                if any(tool.type_name == "escalate" for tool in self.tools.values()):
                    required.add("conversations_add_message")
                if missing := required - available:
                    raise InvalidDropInError(f"steering MCP server is missing tools: {', '.join(sorted(missing))}")
            self.session = self.context_strategy.session(
                self.drop_in.config.orchestrator, self.context
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

    async def __aexit__(self, *_exc_info: object) -> None:
        try:
            await self._cancel_coding()
        finally:
            await self._stack.aclose()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self._steering_items = []
        orchestrator = self.agents[self.drop_in.config.orchestrator]
        history = await self.session.get_items()
        input = self._turn_input(orchestrator.name, prompt, history)
        try:
            try:
                result = await Runner.run(
                    orchestrator,
                    input,
                    context=self.context,
                    max_turns=count_limit(self.max_turns),
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
        config = self.drop_in.config
        generated = {
            f"{delegate}_result"
            for agent in config.agents.values()
            for delegate in agent.delegates
            if delegate in self.coding_agents
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
            async with self._suspend_delegate(self._delegate_call.get()):
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
        ctx = self.context
        if review_workspace is not None:
            validate_review_tools([self.tools[tool] for tool in tools])
            ctx = replace(ctx, workspace=review_workspace)
        agent = Agent(
            name=JUDGE if review_workspace is not None else f"{JUDGE}:{name}",
            instructions=rubric,
            output_type=Verdict,
            model=self._model(JUDGE),
            model_settings=_model_settings(self.model_provider, self.context.env),
            tools=[self._function_tool(self.tools[tool], ctx) for tool in tools],
        )
        try:
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

    def _turn_input(self, name: str, prompt: str, history: list[Any]) -> list[Any]:
        drop_in, ctx = self.drop_in, self.context
        load = partial(drop_in.standing_context, drop_in.config.agents[name], agent=name, telemetry=ctx.telemetry)
        prefix = self.context_strategy.inject_standing_context(history, load)
        return [*prefix, {"role": "user", "content": prompt}]

    def describe(self) -> list[str]:
        self.build()
        config = self.drop_in.config
        provider = self.model_provider
        return [
            f"model provider {provider.type} ({provider.api})",
            *(
                f"{name}: tools [{', '.join(tool.name for tool in agent.tools)}] "
                f"mcp [{', '.join(config.agents[name].mcp_servers)}]"
                for name, agent in self.agents.items()
            ),
        ]

    def _model(self, agent: str) -> str | Model:
        """The run's model; tests substitute scripted models per agent through `models`."""
        if agent in self.models:
            return self.models[agent]
        model = self.context.model
        return self.provider.get_model(model) if self.provider else model

    def _instructions(self, _context: RunContextWrapper, agent: Agent) -> str:
        return self.instructions(agent.name)

    async def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        steering = self.context.steering
        annotations = []
        if steering and data.agent.name == self.drop_in.config.orchestrator:
            await steering.poll()
            pending = steering.pending()
            if pending:
                for message in pending:
                    envelope = steering.envelope(message)
                    try:
                        history = await self.session.get_items()
                        if envelope not in history:
                            await self.session.add_items([envelope])
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
        items = list(self.context_strategy.select(list(data.model_data.input)))
        if steering and data.agent.name == self.drop_in.config.orchestrator:
            # Filter additions are not persisted by Runner. Keep newly stored messages in this run's input
            # until its next run reloads the durable session, even when a strategy trims older conversation.
            items.extend(item for item in self._steering_items if item not in items)
        status = await self.context.budget_status()
        annotations.append(f"<resource_status>\n{status}\n</resource_status>")
        items = self._cache_input(items)
        # Runner does not save filter additions. Keep volatile status after retained history,
        # using a user message so Gemini adapters do not fold it into cached system instructions.
        items.append({"role": "user", "content": "\n\n".join(annotations)})
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    async def _select_cache_input(self, data: CallModelData[Any]) -> ModelInputData:
        # Judges get only cache markers, without context selection, steering or resource metadata.
        return ModelInputData(input=self._cache_input(data.model_data.input),
                              instructions=data.model_data.instructions)

    def _cache_input(self, items: list[Any]) -> list[Any]:
        """OpenRouter translates these text boundaries to Claude's cache_control blocks.

        Mark the stable snapshot and a growing retained prefix, never the replaced status tail.
        Normalize text consistently on every request and copy it so markers cannot enter sessions.
        """
        provider = self.model_provider
        if (provider.type != "openai-compatible" or provider.base_url.host != "openrouter.ai"
                or self.model_family != "anthropic"):
            return items
        selected = list(items)
        candidates = []
        anchor = None
        explicit = False
        for index, item in enumerate(items):
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
            if is_standing_context(item):
                anchor = candidate
        if candidates and not explicit:
            for index, field, position in {anchor or candidates[0], candidates[-1]}:
                selected[index][field][position]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return selected

    def _function_tool(self, tool: Tool, ctx: RunContext | None = None) -> FunctionTool:
        ctx = ctx or self.context

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
        config = self.drop_in.config
        if name not in config.agents:
            return self._coding_tool(self.coding_agents[name])
        spec = config.agents[name]
        tool = self.agents[name].as_tool(
            tool_name=name,
            tool_description=spec.description,
            max_turns=count_limit(spec.max_turns),
            hooks=self.hooks,
            run_config=self.run_config,
            input_builder=lambda data: self._turn_input(name, data["params"]["input"], []),
        )
        invoke_tool = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], raw: str) -> str:
            async with self.delegation_slot():
                return await invoke_tool(context, raw)

        tool.on_invoke_tool = invoke
        return tool

    def _coding_tool(self, agent: CodingAgent) -> FunctionTool:
        ctx = self.context

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            brief = BriefArguments.model_validate_json(raw).brief

            async def execute() -> CodingResult:
                async with self.delegation_slot(background=self.background_coding):
                    return await agent.run(brief, ctx)

            if self.background_coding:
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
        if self.background_coding:
            description += f" Starts in the background; collect its result using {agent.name}_result."
        return FunctionTool(
            name=agent.name,
            description=description,
            on_invoke_tool=invoke,
            params_json_schema=ensure_strict_json_schema(BriefArguments.model_json_schema()),
        )


async def probe_model(
    env: Mapping[str, str], prompt: str, telemetry: Telemetry, *, model_provider: ModelProvider
) -> str:
    """Run one traced provisioning call with the configured model provider."""
    trace_agents_sdk(telemetry)
    model = env.get("CRUX_MODEL")
    if not model:
        raise InvalidDropInError("CRUX_MODEL is not set")
    async with AsyncExitStack() as stack:
        if model_provider.type == "openai-compatible":
            client = await stack.enter_async_context(_validate_and_get_client(model_provider, env))
            model = OpenAIProvider(
                openai_client=client, use_responses=model_provider.api == "responses"
            ).get_model(model)
        agent = Agent(
            name="probe",
            instructions="Reply with exactly the text you are asked for.",
            model=model,
            model_settings=_model_settings(model_provider, env),
        )
        return str((await Runner.run(agent, prompt, max_turns=1)).final_output)


def _validate_and_get_client(model_provider: ModelProvider, env: Mapping[str, str]) -> AsyncOpenAI:
    api_key = env.get(model_provider.api_key_env)
    if not api_key:
        raise InvalidDropInError(f"{model_provider.api_key_env} is not set")
    return AsyncOpenAI(api_key=api_key, base_url=str(model_provider.base_url))


def _model_settings(model_provider: ModelProvider, env: Mapping[str, str]) -> ModelSettings:
    summary = "auto" if model_provider.api == "responses" else None
    settings = ModelSettings(reasoning=Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None, summary=summary))
    if model_provider.type == "openai-compatible":
        # Compatible endpoints keep state locally; signed reasoning must survive the round trip.
        settings.preserve_raw_usage = True
        settings.extra_body = model_provider.extra_body or None
        if model_provider.api == "responses":
            settings.store = False
            settings.response_include = ["reasoning.encrypted_content"]
    return settings


def trace_agents_sdk(telemetry: Telemetry) -> None:
    """Export Agents SDK spans through OpenTelemetry (and so into Langfuse) instead of to the OpenAI platform.

    Langfuse reads a generation's input and output from `input.value` and `output.value`. The per-message copies
    (`llm.input_messages.*`) grow with the conversation, and past OpenTelemetry's 128-attribute limit they evict a
    span's oldest attributes: Langfuse's session, tags, model and usage."""
    set_tracing_disabled(not telemetry.sdk_tracing)
    if not telemetry.sdk_tracing:
        return
    instrumentor = OpenAIAgentsInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(config=TraceConfig(hide_input_messages=True, hide_output_messages=True))
