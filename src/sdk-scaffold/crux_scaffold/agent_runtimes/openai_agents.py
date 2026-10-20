from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack
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
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.items import ModelResponse
from agents.mcp import MCPServerStdio
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData
from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import Field, HttpUrl, ValidationError, model_validator

from crux_scaffold.agent_runtimes.base import RUNTIMES, AgentRuntime, Assembly, TurnOutcome
from crux_scaffold.coding_agents import CodingAgent
from crux_scaffold.components import Options
from crux_scaffold.config import McpServerConfig, delegation_order
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, Tool
from crux_scaffold.usage import UsageLedger


class ModelProviderOptions(Options):
    """Configure the SDK's provider; model and effort still come only from the run environment."""

    type: Literal["openai", "openai-compatible"] = "openai"
    api: Literal["responses", "chat-completions"] = "responses"
    base_url: HttpUrl | None = None
    api_key_env: str = Field("OPENAI_API_KEY", pattern=r"^[A-Z_][A-Z0-9_]*$")
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider(self) -> Self:
        if self.type == "openai-compatible" and self.base_url is None:
            raise ValueError("openai-compatible model_provider requires base_url")
        if self.type == "openai" and (self.base_url or self.extra_body or self.api != "responses"
                                      or self.api_key_env != "OPENAI_API_KEY"):
            raise ValueError("custom provider settings require type=openai-compatible")
        if {"model", "models", "input", "messages", "instructions", "tools", "store", "stream",
            "previous_response_id", "conversation", "reasoning", "reasoning_effort", "include"} & self.extra_body.keys():
            raise ValueError("extra_body cannot override the run model, context, tools, reasoning or session settings")
        return self


class OpenAIAgentsOptions(Options):
    max_turns: int = Field(50, ge=1)
    model_provider: ModelProviderOptions = ModelProviderOptions()


class BriefArguments(Arguments):
    brief: str = Field(description="Everything the coding agent needs: the task, acceptance criteria and scope.")


class UsageHooks(RunHooks):
    def __init__(self, ledger: UsageLedger) -> None:
        self.ledger = ledger

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: ModelResponse) -> None:
        usage = response.usage
        self.ledger.add(agent.name, usage.input_tokens, usage.output_tokens,
                        cached_input_tokens=usage.input_tokens_details.cached_tokens,
                        reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens)


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
        self.client: AsyncOpenAI | None = None
        self.provider: OpenAIProvider | None = None
        self._stack = AsyncExitStack()

    def build(self) -> None:
        if self.agents:
            return
        config, ctx = self.assembly.drop_in.config, self.assembly.context
        if self.options.model_provider.type == "openai-compatible":
            self.client = _compatible_client(self.options.model_provider, ctx.env)
            self.provider = OpenAIProvider(openai_client=self.client,
                                           use_responses=self.options.model_provider.api == "responses")
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
        try:
            if self.client:
                await self._stack.enter_async_context(self.client)
            for server in self.servers.values():
                await self._stack.enter_async_context(server)
            self.session = self.assembly.context_strategy.session(
                self.assembly.drop_in.config.orchestrator, self.assembly.context.state_dir)
        except BaseException:
            await self._stack.aclose()
            raise
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
        provider = self.options.model_provider
        return [f"model provider {provider.type} ({provider.api})",
                *(f"{name}: tools [{', '.join(tool.name for tool in agent.tools)}] "
                  f"mcp [{', '.join(config.agents[name].mcp_servers)}]" for name, agent in self.agents.items())]

    @classmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry, *,
                    options: OpenAIAgentsOptions | None = None) -> str:
        trace_agents_sdk(telemetry)
        model = env.get("CRUX_MODEL")
        if not model:
            raise InvalidDropInError("CRUX_MODEL is not set")
        provider_options = (options or OpenAIAgentsOptions()).model_provider
        async with AsyncExitStack() as stack:
            if provider_options.type == "openai-compatible":
                client = await stack.enter_async_context(_compatible_client(provider_options, env))
                model = OpenAIProvider(openai_client=client, use_responses=provider_options.api == "responses").get_model(
                    model)
            agent = Agent(name="probe", instructions="Reply with exactly the text you are asked for.", model=model,
                          model_settings=_model_settings(provider_options, env))
            return str((await Runner.run(agent, prompt, max_turns=1)).final_output)

    def _model(self, agent: str) -> str | Model:
        """The run's model; tests substitute scripted models per agent through `models`."""
        if agent in self.models:
            return self.models[agent]
        model = self.assembly.context.model
        return self.provider.get_model(model) if self.provider else model

    def _settings(self) -> ModelSettings:
        return _model_settings(self.options.model_provider, self.assembly.context.env)

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


def _compatible_client(options: ModelProviderOptions, env: Mapping[str, str]) -> AsyncOpenAI:
    key = env.get(options.api_key_env)
    if not key:
        raise InvalidDropInError(f"{options.api_key_env} is not set")
    return AsyncOpenAI(api_key=key, base_url=str(options.base_url))


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
        instrumentor.instrument(config=TraceConfig(hide_input_messages=True, hide_output_messages=True))
