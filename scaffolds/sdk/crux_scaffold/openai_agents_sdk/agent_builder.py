from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from agents import Agent, FunctionTool, RunConfig, RunContextWrapper

from crux_scaffold.components import count_limit
from crux_scaffold.config import AgentConfig, delegation_order
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.agent_tools import AgentTools
from crux_scaffold.openai_agents_sdk.sdk_resources import SdkResources
from crux_scaffold.openai_agents_sdk.usage_hooks import UsageHooks
from crux_scaffold.tools import Tool
from crux_scaffold.workspace import RunContext


class AgentBuilder:
    """Assemble declared SDK agents in dependency order with their tools and models."""

    def __init__(self, resources: SdkResources, tools: AgentTools, instructions: Callable[[str], str]) -> None:
        self.resources = resources
        self._tools = tools
        self._instructions_for = instructions
        self.agents: dict[str, Agent] = {}
        self.hooks = UsageHooks(resources.context.usage)

    @property
    def tools(self) -> Mapping[str, Tool]:
        return self._tools.tools

    def build(self, run_config: RunConfig, turn_input: Callable[[str, str, list[Any]], list[Any]]) -> None:
        if self.agents:
            return
        self._run_config, self._turn_input = run_config, turn_input
        self.resources.prepare()
        self._build_agents()

    def function_tool(self, name: str, context: RunContext) -> FunctionTool:
        return self._tools.function(self.tools[name], context)

    def check_result_names(self, names: set[str]) -> None:
        delegates = (name for spec in self.resources.drop_in.config.agents.values() for name in spec.delegates)
        if collisions := sorted(self._tools.result_names(delegates) & names):
            raise InvalidDropInError(f"Background coding result tool name collision: {', '.join(collisions)}")

    def describe(self) -> list[str]:
        provider = self.resources.model_provider
        return [f"model provider {provider.type} ({provider.api})", *map(self._describe_agent, self.agents)]

    def _build_agents(self) -> None:
        config = self.resources.drop_in.config
        self.check_result_names(set(self.tools) | set(config.agents) | set(config.coding_agents))
        for name in delegation_order(config.agents):
            self.agents[name] = self._agent(name)

    def _agent(self, name: str) -> Agent:
        return Agent(name=name,
                     instructions=self._instructions,
                     tools=self._agent_tools(name),
                     **self._agent_settings(name))

    def _agent_settings(self, name: str) -> dict:
        return {
            "mcp_servers": [self.resources.servers[server] for server in self._spec(name).mcp_servers],
            "model": self.resources.connection.model(name),
            "model_settings": self.resources.connection.settings(),
        }

    def _agent_tools(self, name: str) -> list[FunctionTool]:
        spec = self._spec(name)
        tools = [self.function_tool(tool, self.resources.context) for tool in spec.tools]
        tools.extend(self._delegate(delegate) for delegate in spec.delegates)
        return tools + self._tools.results(spec.delegates)

    def _delegate(self, name: str) -> FunctionTool:
        if name not in self.resources.drop_in.config.agents:
            return self._tools.coding(name)
        return self._tools.limit(self._agent_delegate(name))

    def _agent_delegate(self, name: str) -> FunctionTool:
        return self.agents[name].as_tool(
            tool_name=name, input_builder=partial(self._delegate_input, name), **self._delegate_settings(name))

    def _delegate_settings(self, name: str) -> dict:
        return {"tool_description": self._spec(name).description,
                "max_turns": count_limit(self._spec(name).max_turns),
                "hooks": self.hooks,
                "run_config": self._run_config}

    def _delegate_input(self, name: str, data: dict) -> list[Any]:
        return self._turn_input(name, data["params"]["input"], [])

    def _instructions(self, _context: RunContextWrapper, agent: Agent) -> str:
        return self._instructions_for(agent.name)

    def _spec(self, name: str) -> AgentConfig:
        return self.resources.drop_in.config.agents[name]

    def _describe_agent(self, name: str) -> str:
        tools = ", ".join(tool.name for tool in self.agents[name].tools)
        servers = ", ".join(self._spec(name).mcp_servers)
        return f"{name}: tools [{tools}] mcp [{servers}]"
