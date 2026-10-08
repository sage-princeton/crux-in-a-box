from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import replace
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
from agents.tool_context import ToolContext
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import Field, ValidationError

from crux_scaffold.agent_runtimes.base import RUNTIMES, AgentRuntime, Assembly, TurnOutcome
from crux_scaffold.coding_agents import CodingAgent
from crux_scaffold.components import Options
from crux_scaffold.config import McpServerConfig, delegation_order
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, Tool
from crux_scaffold.usage import UsageLedger


class OpenAIAgentsOptions(Options):
    max_turns: int = Field(50, ge=1)


class BriefArguments(Arguments):
    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")


class UsageHooks(RunHooks):
    def __init__(self, ledger: UsageLedger) -> None:
        self.ledger = ledger

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: ModelResponse) -> None:
        self.ledger.add(agent.name, response.usage.input_tokens, response.usage.output_tokens)


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
        self._stack = AsyncExitStack()

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.assembly.drop_in.config, self.assembly.context
        used = sorted({server for agent in config.agents.values() for server in agent.mcp_servers})
        self.servers = {name: _mcp_server(name, config.mcp_servers[name], ctx.env) for name in used}
        for name in delegation_order(config.agents):
            spec = config.agents[name]
            tools = [self._function_tool(self.assembly.tools[tool]) for tool in spec.tools]
            tools += [self._delegate(delegate) for delegate in spec.delegates]
            self.agents[name] = Agent(
                name=name, instructions=self.assembly.drop_in.instructions(name), tools=tools,
                mcp_servers=[self.servers[server] for server in spec.mcp_servers],
                model=self._model(name), model_settings=self._settings())

    async def __aenter__(self) -> Self:
        # TODO(AE-247): let the AgentRuntime base class own telemetry setup for every runtime.
        trace_agents_sdk(self.assembly.context.telemetry)
        self.build()
        for server in self.servers.values():
            await self._stack.enter_async_context(server)
        self.session = self.assembly.context_strategy.session(
            self.assembly.drop_in.config.orchestrator, self.assembly.context.state_dir)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        orchestrator = self.agents[self.assembly.drop_in.config.orchestrator]
        try:
            result = await Runner.run(orchestrator, prompt, context=self.assembly.context,
                                      max_turns=self.options.max_turns, hooks=self.hooks, session=self.session,
                                      run_config=replace(self.run_config, workflow_name=workflow))
        except MaxTurnsExceeded:
            return TurnOutcome(final_output="", completed=False)
        return TurnOutcome(final_output=str(result.final_output), completed=True)

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
        settings = ModelSettings(reasoning=Reasoning(effort=effort)) if effort else ModelSettings()
        agent = Agent(name="probe", instructions="Reply with exactly the text you are asked for.", model=model,
                      model_settings=settings)
        return str((await Runner.run(agent, prompt, max_turns=1)).final_output)

    def _model(self, agent: str) -> str | Model:
        """The run's model; tests substitute scripted models per agent through `models`."""
        return self.models[agent] if agent in self.models else self.assembly.context.model

    def _settings(self) -> ModelSettings:
        effort = self.assembly.context.reasoning_effort
        return ModelSettings(reasoning=Reasoning(effort=effort)) if effort else ModelSettings()

    def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        items = self.assembly.context_strategy.select(list(data.model_data.input))
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    def _function_tool(self, tool: Tool) -> FunctionTool:
        ctx = self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            try:
                return await tool.invoke(ctx, tool.Arguments.model_validate_json(raw or "{}"))
            except (ToolError, ValidationError) as exc:
                return f"error: {exc}"

        return FunctionTool(name=tool.name, description=tool.description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(tool.Arguments.model_json_schema()))

    def _delegate(self, name: str) -> FunctionTool:
        config = self.assembly.drop_in.config
        if name in config.agents:
            return self.agents[name].as_tool(tool_name=name, tool_description=config.agents[name].description,
                                             max_turns=config.agents[name].max_turns, hooks=self.hooks,
                                             run_config=self.run_config)
        return self._coding_tool(self.assembly.coding_agents[name])

    def _coding_tool(self, agent: CodingAgent) -> FunctionTool:
        ctx = self.assembly.context

        async def invoke(_: ToolContext[Any], raw: str) -> str:
            brief = BriefArguments.model_validate_json(raw).brief
            return (await agent.run(brief, ctx)).as_tool_output()

        return FunctionTool(name=agent.name, description=agent.description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(BriefArguments.model_json_schema()))


def _mcp_server(name: str, config: McpServerConfig, env: Mapping[str, str]) -> MCPServerStdio:
    return MCPServerStdio(params={"command": config.command, "args": config.args,
                                  "env": config.resolved_env(name, env)},
                          name=name, cache_tools_list=True, client_session_timeout_seconds=config.timeout_seconds)


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
