"""
NB: this SDK works with any model that is "openai-compatible", e.g., it's not
specific to OpenAI models. This supports, for example, Google Vertex models
out of the box.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Annotated, Any, Literal, Self

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.items import ModelResponse
from agents.mcp import MCPServerStdio
from agents.models.interface import Model
from agents.retry import ModelRetryBackoffSettings, ModelRetrySettings, RetryPolicyContext, retry_policies
from agents.run_config import CallModelData, ModelInputData
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import Field, HttpUrl, ValidationError, model_validator

from crux_scaffold.agent_runtimes.base import RUNTIMES, AgentRuntime, Assembly, TurnOutcome, Verdict
from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.components import CountLimit, Options, count_limit
from crux_scaffold.config import McpServerConfig, delegation_order
from crux_scaffold.context_strategies import is_standing_context
from crux_scaffold.delegate_mailboxes import DelegateMailboxes
from crux_scaffold.delegate_tasks import DelegateTasks
from crux_scaffold.errors import InvalidDropInError, RequestSizeExceededError, ToolError
from crux_scaffold.steering import SteeringOptions
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, Tool, ToolImage, validate_review_tools
from crux_scaffold.usage import UsageLedger
from crux_scaffold.workspace import ReviewWorkspace, RunContext

JUDGE = "judge"


class ModelProviderOptions(Options):
    """Configure the SDK's provider; model and effort still come only from the run environment."""

    type: Literal["openai", "openai-compatible"]
    api: Literal["responses", "chat-completions"]
    base_url: HttpUrl | None = None
    api_key_env: str = Field("OPENAI_API_KEY", pattern=r"^[A-Z_][A-Z0-9_]*$")
    extra_body: dict[str, Any] = Field(default_factory=dict)
    max_request_bytes: int | None = Field(None, gt=0, strict=True)

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
    # TODO(AE-326): Make interactive delegates the only mode and remove the opt-in flag.
    interactive_delegates: bool = False
    coding_result_wait_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] | Literal["unlimited"] = 1800
    steering: SteeringOptions | None = None


class BriefArguments(Arguments):
    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")


class CodingResultArguments(Arguments):
    wait: bool = Field(description="Whether to await completion; false returns immediately if still running.")


class TaskArguments(Arguments):
    task_id: str


class SendArguments(TaskArguments):
    message: str
    reply_to: str | None = None
    interrupt: bool = False


class MessageArguments(Arguments):
    message: str


class InboxArguments(Arguments):
    wait_seconds: float = Field(0, ge=0, allow_inf_nan=False)


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
            cache_write_input_tokens=usage.input_tokens_details.cache_write_tokens,
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
        # TODO(AE-318): consolidate task handles, timestamps and cancellation state into coding-task objects.
        self._coding_tasks: dict[str, asyncio.Task[CodingResult]] = {}
        self._coding_started: dict[str, datetime] = {}
        self._coding_cancelled: dict[str, tuple[datetime, datetime]] = {}  # started, cancelled; not yet reported
        self._stack = AsyncExitStack()
        self._steering_items: list[dict] = []
        self.delegates: DelegateTasks | None = None
        self._delegate_context: ContextVar[tuple[str, list[dict]] | None] = ContextVar('delegate_context', default=None)
        if options.interactive_delegates:
            self.delegates = DelegateTasks(DelegateMailboxes(assembly.context.state_dir / 'delegates.sqlite'),
                                           self._run_delegate, assembly.context.telemetry)

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.assembly.drop_in.config, self.assembly.context
        if (self.options.model_provider.type == "openai-compatible"
                or self.options.model_provider.max_request_bytes is not None):
            self.client = _validate_and_get_client(self.options.model_provider, ctx.env)
            self.provider = OpenAIProvider(
                openai_client=self.client, use_responses=self.options.model_provider.api == "responses"
            )
        if self.delegates and any(spec.delegates for name, spec in config.agents.items() if name != config.orchestrator):
            raise InvalidDropInError('interactive_delegates supports direct PI delegates only')
        if self.options.background_coding or self.delegates:
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
            if self.options.background_coding and not self.delegates:
                tools += [
                    self._coding_result_tool(delegate)
                    for delegate in spec.delegates
                    if delegate in self.assembly.coding_agents
                ]
            if self.delegates:
                tools += self._communication_tools(name)
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
        if self.delegates:
            self.delegates.mailboxes.recover()
        try:
            if self.client:
                await self._stack.enter_async_context(self.client)
            for server in self.servers.values():
                await self._stack.enter_async_context(server)
                if self.options.background_coding or self.delegates:
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
            if self.delegates:
                await self.delegates.close()
            await self._cancel_coding()
        finally:
            await self._stack.aclose()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self._steering_items = []
        orchestrator = self.agents[self.assembly.drop_in.config.orchestrator]
        try:
            try:
                result = await Runner.run(
                    orchestrator,
                    prompt,
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
            if self.delegates:
                outstanding = [task for task in self.delegates.status() if not task['collected']]
                if outstanding:
                    outcome = TurnOutcome(completed=False, final_output=(
                        'Delegate work is outstanding. Read delegate_inbox, answer questions, and collect results '
                        'before finishing. Interrupt work explicitly if it should stop.\n' + json.dumps(outstanding)))
            return outcome
        except BaseException:
            if self.delegates:
                await self.delegates.close()
            await self._cancel_coding()
            raise

    def _check_result_names(self, names: set[str]) -> None:
        config = self.assembly.drop_in.config
        if self.delegates:
            generated = {'send_message', 'interrupt_delegate', 'request_progress', 'delegate_inbox',
                         'ask_pi', 'report_progress'}
        else:
            generated = {
                f"{delegate}_result"
                for agent in config.agents.values()
                for delegate in agent.delegates
                if delegate in self.assembly.coding_agents
            }
        if collisions := sorted(generated & names):
            raise InvalidDropInError(f"Background coding result tool name collision: {', '.join(collisions)}")

    async def _cancel_coding(self) -> None:
        """Cancel running briefs and remember them for their result tools; finished results stay collectable."""
        tasks, now = list(self._coding_tasks.values()), self.assembly.context.now()
        for name, task in list(self._coding_tasks.items()):
            if not task.done():
                task.cancel()
                del self._coding_tasks[name]
                self._coding_cancelled[name] = (self._coding_started[name], now)
        await asyncio.gather(*tasks, return_exceptions=True)

    def coding_status(self) -> str:
        now, entries = self.assembly.context.now(), []
        for name, task in self._coding_tasks.items():
            entries.append(f"`{name}` finished, result not collected" if task.done()
                           else f"`{name}` {self._running(name, now)}")
        entries += [f"`{name}` cancelled at {_stamp(stopped)} (started {_stamp(started)}), not yet reported"
                    for name, (started, stopped) in self._coding_cancelled.items()]
        return f"Coding briefs: {'; '.join(entries)}." if entries else ""

    # TODO(AE-318): rename this formatter to _format_running_coding_task.
    def _running(self, name: str, now: datetime) -> str:
        started = self._coding_started[name]
        last = max(started, self.assembly.coding_agents[name].last_activity or started)
        return (f"running since {_stamp(started)} ({(now - started).total_seconds():.0f}s; "
                f"last activity {(now - last).total_seconds():.0f}s ago)")

    async def _coding_result(self, name: str, *, wait: bool, timeout: float | None = None) -> str:
        # TODO(AE-318): move lifecycle decisions and status formatting into the coding-task abstraction.
        task = self._coding_tasks.get(name)
        if task is not None and wait and not task.done():
            async with self.delegation_wait():
                # Never `await task`: cancelling this call would then cancel the brief too.
                await asyncio.wait([task], timeout=timeout)
        if task is None or task.cancelled():
            if name not in self._coding_cancelled:
                return f"error: No uncollected coding task for '{name}'."
            started, stopped = self._coding_cancelled.pop(name)
            return (f"[coding agent cancelled] {name}: the scaffold cancelled the brief started {_stamp(started)} "
                    f"at {_stamp(stopped)} and stopped the worker. Partial changes from it may be in the workspace.")
        if not task.done():
            waited = f" Waited {timeout:g}s; the brief continues." if wait and timeout is not None else ""
            return (f"[coding agent running] {name}: {self._running(name, self.assembly.context.now())}.{waited} "
                    f"Use rest before checking again, or call {name}_result(wait=true).")
        if self._coding_tasks.get(name) is task:
            del self._coding_tasks[name]
        try:
            return task.result().as_tool_output()
        except Exception as exc:  # noqa: BLE001 - Worker failures return to the PI.
            return f"error: Coding agent '{name}' failed: {exc}"

    def _coding_result_tool(self, name: str) -> FunctionTool:
        limit = self.options.coding_result_wait_seconds

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            try:
                args = CodingResultArguments.model_validate_json(raw)
            except ValidationError as exc:
                return f"error: {exc}"
            return await self._coding_result(name, wait=args.wait, timeout=None if limit == "unlimited" else limit)

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
            if provider_options.type == "openai-compatible" or provider_options.max_request_bytes is not None:
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
        is_orchestrator = data.agent.name == self.assembly.drop_in.config.orchestrator
        steering = self.assembly.context.steering
        annotations = []
        delegate_context = self._delegate_context.get()
        session_id = data.agent.name if is_orchestrator else None
        pinned_mail = []
        # TODO(AE-328): Extract mailbox consumption into a separate class method.
        if self.delegates:
            boxes = self.delegates.mailboxes
            if is_orchestrator:
                messages = boxes.pending('pi')
                annotations.append('<delegate_status>\n' + json.dumps(self.delegates.status()) + '\n</delegate_status>')
                if messages:
                    annotations.append('<delegate_messages>\n' + json.dumps(messages) + '\n</delegate_messages>')
                    await self.session.add_items([{'role': 'user', 'content': annotations[-1]}])
                    boxes.delivered([m['id'] for m in messages])
            elif delegate_context and boxes.get(delegate_context[0])['name'] == data.agent.name:
                session_id, pinned_mail = delegate_context
                messages = boxes.pending('delegate', session_id)
                if messages:
                    envelope = {'role': 'user', 'content': '<pi_messages>\n' + json.dumps(messages) + '\n</pi_messages>'}
                    session = self.assembly.context_strategy.session(session_id, self.assembly.context)
                    await session.add_items([envelope])
                    pinned_mail.append(envelope)
                    boxes.delivered([m['id'] for m in messages])
            self.delegates.flush_events()
        if steering and is_orchestrator:
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
        prompt_item = history[-1] if history and history[-1].get("role") == "user" else None
        if pinned_mail:
            session = self.assembly.context_strategy.session(session_id, self.assembly.context)
            for index, item in enumerate(await session.get_items()):
                if item in pinned_mail and item not in history:
                    history.insert(index, item)
        if is_orchestrator and self._steering_items:
            stored = [item for item in await self.session.get_items() if not is_standing_context(item)]
            for index, item in enumerate(stored):
                if item in self._steering_items and item not in history:
                    history.insert(index, item)
        strategy = self.assembly.context_strategy
        ctx = self.assembly.context
        research = self.assembly.research_context(data.agent.name)
        status = await ctx.budget_status()
        request_context = {
            "instructions": data.model_data.instructions, "research": research, "resources": status,
            "tools": [{"type": "function", "name": tool.name, "description": tool.description,
                       "parameters": tool.params_json_schema, "strict": tool.strict_json_schema}
                      for tool in await data.agent.get_all_tools(RunContextWrapper(data.context))
                      if isinstance(tool, FunctionTool)],
        }

        prepare_request = partial(
            self._prepare_request, request_context=request_context, prompt_item=prompt_item,
            annotations=annotations, pinned_steering=(self._steering_items if steering and is_orchestrator else []) + pinned_mail,
        )
        items = list(await strategy.select_for_call(
            list(history), ctx=ctx,
            session_id=session_id,
            request_context=request_context, agent=data.agent, model_provider=self.run_config.model_provider,
            prepare_input=self._cache_input, research_context=lambda: self.assembly.research_context(data.agent.name),
            prepare_request=prepare_request,
        ))
        positions = {id(item): index for index, item in enumerate(history)}
        retained = [positions.get(id(item)) for item in items]
        self.assembly.context.telemetry.record(
            "context:select", "span",
            {"agent": data.agent.name, "strategy": strategy.type_name,
             "max_items": getattr(strategy.options, "max_items", None), "items_before": len(history)},
            {"items_selected": len(items), "items_dropped": len(history) - len(set(retained) - {None}),
             "retained_positions": retained},
        )
        return prepare_request(items)

    def _prepare_request(
        self, selected: list[Any], *, request_context: dict[str, Any], prompt_item: dict | None,
        annotations: list[str], pinned_steering: list[dict],
    ) -> ModelInputData:
        """Render the same request for token checks and ordinary inference.

        :param selected: History selected by the context strategy.
        :param request_context: Fixed instructions and the latest research and resource context.
        :param prompt_item: Current user task to move to the trailing message.
        :param annotations: Steering transport notices for this call.
        :param pinned_steering: Delivered messages to replay until Runner reloads the session.
        :returns: Cache-formatted history followed by the current context and task.
        """
        items = list(selected)
        if prompt_item is not None:
            for index in range(len(items) - 1, -1, -1):
                if items[index] == prompt_item:
                    items.pop(index)
                    break
        items.extend(item for item in pinned_steering if item not in items)
        tail = [f"<research_context>\n{request_context['research']}\n</research_context>", *annotations,
                f"<resource_status>\n{request_context['resources']}\n</resource_status>"]
        if prompt_item is not None:
            tail.append(f"# Current task\n\n{prompt_item['content']}")
        result = self._cache_input(ModelInputData(input=items, instructions=request_context["instructions"]))
        result.input.append({"role": "user", "content": "\n\n".join(tail)})
        return result

    async def _select_cache_input(self, data: CallModelData[Any]) -> ModelInputData:
        return self._cache_input(data.model_data)

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
        if self.delegates:
            return self._interactive_tool(name)
        if name not in config.agents:
            return self._coding_tool(self.assembly.coding_agents[name])
        spec = config.agents[name]
        max_turns = sys.maxsize if spec.max_turns == "unlimited" else spec.max_turns
        tool = self.agents[name].as_tool(
            tool_name=name,
            tool_description=spec.description,
            # Agent.as_tool treats None as the SDK's default 10 turns.
            max_turns=max_turns,
            hooks=self.hooks,
            run_config=self.run_config,
        )
        invoke_tool = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], raw: str) -> str:
            async with self.delegation_slot():
                return await invoke_tool(context, raw)

        tool.on_invoke_tool = invoke
        return tool

    def _json_tool(self, name: str, description: str, arguments: type[Arguments], callback) -> FunctionTool:
        async def invoke(_: ToolContext[Any], raw: str) -> str:
            try:
                return json.dumps(await callback(arguments.model_validate_json(raw)))
            except (ValueError, ValidationError) as exc:
                return json.dumps({'error': str(exc)})
        return FunctionTool(name=name, description=description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(arguments.model_json_schema()))

    def _interactive_tool(self, name: str) -> FunctionTool:
        async def start(args):
            return self.delegates.start(name, args.brief)
        config = self.assembly.drop_in.config
        description = (config.agents[name].description if name in config.agents else self.assembly.coding_agents[name].description)
        return self._json_tool(name, description +
                               ' Starts in the background; collect results with delegate_inbox.', BriefArguments, start)

    # TODO(AE-329): Move communication tool construction into a dedicated class.
    def _communication_tools(self, name: str) -> list[FunctionTool]:
        config = self.assembly.drop_in.config
        if name != config.orchestrator:
            async def report(args, *, question):
                active = self._delegate_context.get()
                if not active:
                    raise ValueError('No active delegate conversation')
                mailbox = self.delegates.mailboxes.delegate(active[0])
                receipt = mailbox.ask_pi(args.message) if question else mailbox.report_progress(args.message)
                self.delegates.flush_events()
                return receipt
            return [self._json_tool('ask_pi', 'Ask the PI a question without interrupting it. Continue independent work.',
                                    MessageArguments, partial(report, question=True)),
                    self._json_tool('report_progress', 'Report completed work, evidence, blockers and next step to the PI.',
                                    MessageArguments, partial(report, question=False))]

        async def send(args):
            return await self.delegates.send(**args.model_dump())

        async def stop(args):
            return await self.delegates.stop(args.task_id)

        async def progress(args):
            return self.delegates.request_progress(args.task_id)

        async def inbox(args):
            limit = self.options.coding_result_wait_seconds
            seconds = args.wait_seconds if limit == 'unlimited' else min(args.wait_seconds, limit)
            return await self.delegates.inbox(seconds)

        tools = [self._json_tool('send_message', 'Steer or answer a delegate. Resumes ended conversations; interrupt=true '
                                'stops active work before resuming. Use task IDs from dispatch/inbox.', SendArguments, send),
                 self._json_tool('interrupt_delegate', 'Stop a delegate and await cleanup. Does not resume it.',
                                 TaskArguments, stop),
                 self._json_tool('request_progress', 'Ask an active delegate for a fresh progress report; returns a receipt, '
                                 'not the report. Read delegate_inbox for the response.', TaskArguments, progress),
                 self._json_tool('delegate_inbox', 'Inspect all delegate status and messages and collect every ready result. '
                                 'wait_seconds=0 checks immediately; a positive wait wakes on any new message or task '
                                 'completion, failure or interruption, or returns when idle or timed out. Waiting never '
                                 'stops delegate work.',
                                 InboxArguments, inbox)]
        return tools

    async def _run_delegate(self, task: dict, prompt: str) -> str:
        ctx = self.assembly.context
        name = task['name']
        if name in self.assembly.coding_agents:
            if prompt != task['brief'] and not self.delegates.mailboxes.get(task['id'])['conversation_id']:
                prompt = task['brief'] + '\n\n' + prompt
            async with self.delegation_slot(background=True):
                result = await self.assembly.coding_agents[name].run(
                    prompt, ctx, mailbox=self.delegates.mailboxes.delegate(task['id']))
                if not result.completed:
                    raise RuntimeError(result.as_tool_output())
                return result.as_tool_output()
        session = self.assembly.context_strategy.session(task['id'], ctx)
        if prompt != task['brief'] and not await session.get_items():
            prompt = task['brief'] + '\n\n' + prompt
        # Persist the incoming turn before inference so injected mail retains its true chronological position.
        await session.add_items([{'role': 'user', 'content': prompt}])
        token = self._delegate_context.set((task['id'], []))
        try:
            async with self.delegation_slot(background=True):
                result = await Runner.run(self.agents[name], [], context=ctx, session=session, hooks=self.hooks,
                                          max_turns=count_limit(self.assembly.drop_in.config.agents[name].max_turns),
                                          run_config=replace(self.run_config, workflow_name=f"delegate:{name}:{task['id']}"))
                return str(result.final_output)
        finally:
            self._delegate_context.reset(token)

    def _coding_tool(self, agent: CodingAgent) -> FunctionTool:
        ctx = self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            brief = BriefArguments.model_validate_json(raw).brief

            async def execute() -> CodingResult:
                async with self.delegation_slot(background=self.options.background_coding):
                    return await agent.run(brief, ctx)

            if self.options.background_coding:
                if task := self._coding_tasks.get(agent.name):
                    if task.done():
                        return f"error: Collect {agent.name}_result before starting another brief for '{agent.name}'."
                    return (f"error: '{agent.name}' is still {self._running(agent.name, ctx.now())}. Check it with "
                            f"{agent.name}_result(wait=false) or collect it with {agent.name}_result(wait=true) "
                            "before starting another brief.")
                self._coding_started[agent.name] = ctx.now()
                self._coding_tasks[agent.name] = asyncio.create_task(execute())
                cancelled = self._coding_cancelled.pop(agent.name, None)
                return (
                    f"[coding agent started] {agent.name}. Choose rest durations while it works; "
                    f"call {agent.name}_result(wait=false) to check or {agent.name}_result(wait=true) to await "
                    "and collect the result."
                    + (f" Its previous brief was cancelled at {_stamp(cancelled[1])}; partial changes from it may be "
                       "in the workspace." if cancelled else "")
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


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


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
    client_options = {}
    if options.max_request_bytes is not None:
        # TODO(AE-316): Look up this limit by provider/model instead of configuring it per drop-in.
        # https://linear.app/agent-evals/issue/AE-316/move-model-specific-configurations-into-configuration-maps-queried-at
        async def check_request_size(request) -> None:
            size = len(request.content)
            if size > options.max_request_bytes:
                raise RequestSizeExceededError(size, options.max_request_bytes)

        client_options['http_client'] = DefaultAsyncHttpxClient(event_hooks={'request': [check_request_size]})
    return AsyncOpenAI(api_key=api_key, base_url=str(options.base_url) if options.base_url else None, **client_options)


def _provider_ended_response(context: RetryPolicyContext) -> bool:
    """A response the provider cut short for its own reasons, such as a content-filter cutoff or a failed status. The
    SDK reports these with one message prefix. Running out of output tokens would only repeat, so it isn't retried."""
    message = str(context.error)
    return (isinstance(context.error, ModelBehaviorError)
            and message.startswith("Responses stream ended with terminal event")
            and "max_output_tokens" not in message)


# A provider outage, overload or filter cutoff should cost a wait, not the run: an exception here cancels the
# iteration's running coding agents.
TRANSIENT_FAILURES = retry_policies.any(
    retry_policies.provider_suggested(),
    retry_policies.retry_after(),
    retry_policies.network_error(),
    retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
    _provider_ended_response,
)
# Retries per model call, backing off from 2s to 60s: about four minutes before a failure ends the iteration.
MODEL_RETRIES = 8


def _model_settings(options: ModelProviderOptions, env: Mapping[str, str]) -> ModelSettings:
    settings = ModelSettings(
        reasoning=Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None, summary="auto"),
        retry=ModelRetrySettings(
            max_retries=MODEL_RETRIES, policy=TRANSIENT_FAILURES,
            backoff=ModelRetryBackoffSettings(initial_delay=2, max_delay=60, multiplier=2, jitter=True),
        ),
    )
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
        instrumentor.instrument(config=TraceConfig(hide_input_messages=True, hide_output_messages=True))
