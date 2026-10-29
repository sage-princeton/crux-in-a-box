from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import partial
from typing import Any

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from pydantic import BaseModel, ValidationError

from crux_scaffold.coding_agents import CodingAgent
from crux_scaffold.errors import ToolError
from crux_scaffold.openai_agents_sdk.brief_arguments import BriefArguments
from crux_scaffold.openai_agents_sdk.coding_result_arguments import CodingResultArguments
from crux_scaffold.openai_agents_sdk.coding_tasks import CodingTasks
from crux_scaffold.openai_agents_sdk.delegation_limiter import DelegationLimiter
from crux_scaffold.tools import Tool, ToolImage, ToolOutput
from crux_scaffold.workspace import RunContext


class AgentTools:
    """Build SDK tool definitions for scaffold tools and bounded delegates."""

    def __init__(self, tools: Mapping[str, Tool], coding_agents: Mapping[str, CodingAgent],
                 coding_tasks: CodingTasks, delegation: DelegationLimiter) -> None:
        self.tools = tools
        self._coding_agents = coding_agents
        self._coding_tasks = coding_tasks
        self._delegation = delegation

    @property
    def background(self) -> bool:
        return self._coding_tasks.background

    def function(self, tool: Tool, context: RunContext) -> FunctionTool:
        return FunctionTool(name=tool.name,
                            description=tool.description,
                            on_invoke_tool=partial(self._invoke_function, tool, context),
                            params_json_schema=_schema(tool.Arguments))

    def coding(self, name: str) -> FunctionTool:
        return self._coding_tool(self._coding_agents[name])

    def results(self, delegates: Iterable[str]) -> list[FunctionTool]:
        return [self._result_tool(name) for name in delegates if name in self._coding_agents] if self.background else []

    def result_names(self, delegates: Iterable[str]) -> set[str]:
        return {f"{name}_result" for name in delegates if name in self._coding_agents} if self.background else set()

    def limit(self, tool: FunctionTool) -> FunctionTool:
        tool.on_invoke_tool = partial(self._invoke_delegate, tool.on_invoke_tool)
        return tool

    async def _invoke_function(self, tool: Tool, context: RunContext, _: ToolContext[Any], raw: str
                               ) -> str | list[ToolOutputImage | ToolOutputText]:
        try:
            return _sdk_output(await tool.invoke(context, tool.Arguments.model_validate_json(raw or "{}")))
        except (ToolError, ValidationError) as exc:
            return f"error: {exc}"

    def _coding_tool(self, agent: CodingAgent) -> FunctionTool:
        return FunctionTool(name=agent.name,
                            description=self._coding_description(agent),
                            on_invoke_tool=partial(self._invoke_coding, agent),
                            params_json_schema=_schema(BriefArguments))

    def _coding_description(self, agent: CodingAgent) -> str:
        suffix = f" Starts in the background; collect its result using {agent.name}_result." if self.background else ""
        return agent.description + suffix

    async def _invoke_coding(self, agent: CodingAgent, _: ToolContext[Any], raw: str) -> str:
        return await self._coding_tasks.run(agent, BriefArguments.model_validate_json(raw).brief)

    def _result_tool(self, name: str) -> FunctionTool:
        return FunctionTool(name=f"{name}_result",
                            description=f"Check or collect the coding result from {name}.",
                            on_invoke_tool=partial(self._invoke_result, name),
                            params_json_schema=_schema(CodingResultArguments))

    async def _invoke_result(self, name: str, _: ToolContext[Any], raw: str) -> str:
        try:
            args = CodingResultArguments.model_validate_json(raw)
        except ValidationError as exc:
            return f"error: {exc}"
        return await self._coding_tasks.collect(name, wait=args.wait)

    async def _invoke_delegate(self, invoke: Callable[..., Awaitable[str]], context: ToolContext[Any], raw: str) -> str:
        async with self._delegation.slot():
            return await invoke(context, raw)


def _schema(arguments: type[BaseModel]) -> dict:
    return ensure_strict_json_schema(arguments.model_json_schema())


def _sdk_output(output: ToolOutput) -> str | list[ToolOutputImage | ToolOutputText]:
    return [_sdk_part(item) for item in output] if isinstance(output, list) else output


def _sdk_part(item: str | ToolImage) -> ToolOutputImage | ToolOutputText:
    if isinstance(item, ToolImage):
        return ToolOutputImage(image_url="data:image/png;base64," + base64.b64encode(item.png).decode())
    return ToolOutputText(text=item)
