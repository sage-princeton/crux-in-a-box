from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import replace
from functools import partial
from typing import Any, Self

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
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
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import Field, ValidationError

from crux_scaffold.agent_runtimes.base import RUNTIMES, AgentRuntime, Assembly, TurnOutcome, Verdict
from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.components import CountLimit, Options, count_limit
from crux_scaffold.config import McpServerConfig, delegation_order
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.steering import SteeringOptions
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, Tool, ToolImage, validate_review_tools
from crux_scaffold.usage import UsageLedger
from crux_scaffold.workspace import ReviewWorkspace, RunContext

JUDGE = "judge"


class OpenAIAgentsOptions(Options):
    max_turns: CountLimit = 50
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
        self.ledger.add(self.source or agent.name, response.usage.input_tokens, response.usage.output_tokens)


@RUNTIMES.register
class OpenAIAgentsRuntime(AgentRuntime):
    """Agents are SDK Agents; delegates are tools, so each delegated call starts from a fresh context holding only
    its brief. The orchestrator's conversation lives in the context strategy's session."""

    type_name = "openai-agents"
    Options = OpenAIAgentsOptions

    def __init__(self, name: str, options: OpenAIAgentsOptions, *, assembly: Assembly,
                 models: Mapping[str, Model] | None = None) -> None:
        super().__init__(name, options, assembly=assembly)
        self.models = dict(models or {})
        self.hooks = UsageHooks(assembly.context.usage)
        self.run_config = RunConfig(call_model_input_filter=self._select_input)
        self.agents: dict[str, Agent] = {}
        self.servers: dict[str, MCPServerStdio] = {}
        self._coding_tasks: dict[str, asyncio.Task[CodingResult]] = {}
        self._stack = AsyncExitStack()
        self._steering_items: list[dict] = []

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.assembly.drop_in.config, self.assembly.context
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
                tools += [self._coding_result_tool(delegate) for delegate in spec.delegates
                          if delegate in self.assembly.coding_agents]
            self.agents[name] = Agent(
                name=name, instructions=self._instructions, tools=tools,
                mcp_servers=[self.servers[server] for server in spec.mcp_servers],
                model=self._model(name), model_settings=self._settings())

    async def __aenter__(self) -> Self:
        # TODO(AE-247): let the AgentRuntime base class own telemetry setup for every runtime.
        trace_agents_sdk(self.assembly.context.telemetry)
        self.build()
        steering = self.assembly.context.steering
        try:
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
                self.assembly.drop_in.config.orchestrator, self.assembly.context)
        except BaseException as exc:
            await self._stack.aclose()
            if steering and isinstance(exc, Exception):
                status = ("startup_failed: check the steering MCP server, credentials, scopes and required tools; "
                          "restart with the same state directory")
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
        self._steering_items = []
        orchestrator = self.agents[self.assembly.drop_in.config.orchestrator]
        history = await self.session.get_items()
        input = self._turn_input(orchestrator.name, prompt, history)
        try:
            try:
                result = await Runner.run(orchestrator, input, context=self.assembly.context,
                                          max_turns=count_limit(self.options.max_turns), hooks=self.hooks,
                                          session=self.session,
                                          run_config=replace(self.run_config, workflow_name=workflow))
            except MaxTurnsExceeded:
                outcome = TurnOutcome(final_output="", completed=False)
            else:
                outcome = TurnOutcome(final_output=str(result.final_output), completed=True)
            remaining = [f"{name}: {await self._coding_result(name, wait=True)}"
                         for name in list(self._coding_tasks)]
            if remaining:
                report = ("Scaffold coding completion report (uncollected delegate outputs):\n\n"
                          + "\n\n".join(remaining))
                await self.session.add_items([{"role": "user", "content": report}])
            outcome.final_output = "\n\n".join(part for part in [outcome.final_output, *remaining] if part)
            return outcome
        except BaseException:
            await self._cancel_coding()
            raise

    def _check_result_names(self, names: set[str]) -> None:
        config = self.assembly.drop_in.config
        generated = {f"{delegate}_result" for agent in config.agents.values() for delegate in agent.delegates
                     if delegate in self.assembly.coding_agents}
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

        return FunctionTool(name=f"{name}_result", description=f"Check or collect the coding result from {name}.",
                            on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(CodingResultArguments.model_json_schema()))

    async def judge(self, name: str, rubric: str, evidence: str, *, tools: list[str], max_turns: int | None,
                    review_workspace: ReviewWorkspace | None = None) -> Verdict:
        ctx = self.assembly.context
        if review_workspace is not None:
            validate_review_tools([self.assembly.tools[tool] for tool in tools])
            ctx = replace(ctx, workspace=review_workspace)
        agent = Agent(name=JUDGE if review_workspace is not None else f"{JUDGE}:{name}",
                      instructions=rubric, output_type=Verdict, model=self._model(JUDGE),
                      model_settings=self._settings(),
                      tools=[self._function_tool(self.assembly.tools[tool], ctx) for tool in tools])
        try:
            result = await Runner.run(agent, evidence, context=ctx, max_turns=max_turns,
                                      hooks=UsageHooks(ctx.usage, f"{JUDGE}:{name}"),
                                      run_config=RunConfig(workflow_name=f"{JUDGE}:{name}"))
        except MaxTurnsExceeded:
            return Verdict(passed=False, next_prompt=None,
                           feedback=f"The judge ran out of turns ({max_turns}) before reaching a verdict.")
        return result.final_output

    def _turn_input(self, name: str, prompt: str, history: list[Any]) -> list[Any]:
        drop_in, ctx = self.assembly.drop_in, self.assembly.context
        load = partial(drop_in.standing_context, drop_in.config.agents[name], agent=name, telemetry=ctx.telemetry)
        prefix = self.assembly.context_strategy.inject_standing_context(history, load)
        return [*prefix, {"role": "user", "content": prompt}]

    def describe(self) -> list[str]:
        self.build()
        config = self.assembly.drop_in.config
        return [f"{name}: tools [{', '.join(tool.name for tool in agent.tools)}] "
                f"mcp [{', '.join(config.agents[name].mcp_servers)}]" for name, agent in self.agents.items()]

    @classmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry) -> str:
        trace_agents_sdk(telemetry)
        model = env.get("CRUX_MODEL")
        if not model:
            raise InvalidDropInError("CRUX_MODEL is not set")
        effort = env.get("CRUX_REASONING_EFFORT")
        settings = ModelSettings(reasoning=Reasoning(effort=effort, summary="auto"))
        agent = Agent(name="probe", instructions="Reply with exactly the text you are asked for.", model=model,
                      model_settings=settings)
        return str((await Runner.run(agent, prompt, max_turns=1)).final_output)

    def _model(self, agent: str) -> str | Model:
        """The run's model; tests substitute scripted models per agent through `models`."""
        return self.models.get(agent, self.assembly.context.model)

    def _settings(self) -> ModelSettings:
        effort = self.assembly.context.reasoning_effort
        return ModelSettings(reasoning=Reasoning(effort=effort, summary="auto"))

    def _instructions(self, context: RunContextWrapper, agent: Agent) -> str:
        return self.assembly.instructions(agent.name)

    async def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        steering = self.assembly.context.steering
        instructions = data.model_data.instructions
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
                        status = ("delivery_failed: check conversation/state storage; "
                                  "restart with the same state directory")
                        steering.state["status"] = status
                        steering.save()
                        steering.event("delivery_failed", message_id=message["ts"], status=status)
                        raise InvalidDropInError(status) from None
            if steering.state["status"] != "ok":
                instructions = f"{instructions or ''}\n\nSteering transport status: {steering.state['status']}"
        items = list(self.assembly.context_strategy.select(list(data.model_data.input)))
        if steering and data.agent.name == self.assembly.drop_in.config.orchestrator:
            # Filter additions are not persisted by Runner. Keep newly stored messages in this run's input
            # until its next run reloads the durable session, even when a strategy trims older conversation.
            items.extend(item for item in self._steering_items if item not in items)
        status = await self.assembly.context.budget_status()
        instructions = (instructions or "") + f"\n\n<resource_status>\n{status}\n</resource_status>"
        return ModelInputData(input=items, instructions=instructions)

    def _function_tool(self, tool: Tool, ctx: RunContext | None = None) -> FunctionTool:
        ctx = ctx or self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str):
            try:
                output = await tool.invoke(ctx, tool.Arguments.model_validate_json(raw or "{}"))
                if isinstance(output, list):
                    return [ToolOutputImage(image_url="data:image/png;base64," + base64.b64encode(item.png).decode())
                            if isinstance(item, ToolImage) else ToolOutputText(text=item) for item in output]
                return output
            except (ToolError, ValidationError) as exc:
                return f"error: {exc}"

        return FunctionTool(name=tool.name, description=tool.description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(tool.Arguments.model_json_schema()))

    def _delegate(self, name: str) -> FunctionTool:
        config = self.assembly.drop_in.config
        if name not in config.agents:
            return self._coding_tool(self.assembly.coding_agents[name])
        spec = config.agents[name]
        tool = self.agents[name].as_tool(
            tool_name=name, tool_description=spec.description, max_turns=count_limit(spec.max_turns), hooks=self.hooks,
            run_config=self.run_config,
            input_builder=lambda data: self._turn_input(name, data["params"]["input"], []))
        invoke_tool = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], raw: str) -> str:
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
                return (f"[coding agent started] {agent.name}. Choose rest durations while it works; "
                        f"call {agent.name}_result(wait=false) to check or {agent.name}_result(wait=true) to await "
                        "and collect the result.")
            return (await execute()).as_tool_output()

        description = agent.description
        if self.options.background_coding:
            description += f" Starts in the background; collect its result using {agent.name}_result."
        return FunctionTool(name=agent.name, description=description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(BriefArguments.model_json_schema()))


def _mcp_server(name: str, config: McpServerConfig, env: Mapping[str, str]) -> MCPServerStdio:
    return MCPServerStdio(params={"command": config.command, "args": config.args,
                                  "env": config.resolved_env(name, env)},
                          name=name, cache_tools_list=True, client_session_timeout_seconds=config.timeout_seconds,
                          max_retry_attempts=0)


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
