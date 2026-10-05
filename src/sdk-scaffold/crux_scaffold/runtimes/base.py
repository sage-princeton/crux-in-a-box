"""Agent runtimes: the agent SDK that runs the declared agents (OpenAI Agents SDK today, Claude Agent SDK later).
A runtime wires the scaffold's SDK-neutral components (tools, coding agents, context strategy) into its SDK."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel

from crux_scaffold.coding_agents import CodingAgent
from crux_scaffold.components import Component, Registry
from crux_scaffold.context_strategies import ContextStrategy
from crux_scaffold.tools import Tool
from crux_scaffold.workspace import RunContext

if TYPE_CHECKING:
    from crux_scaffold.drop_in import DropInDirectory
    from crux_scaffold.telemetry import Telemetry


@dataclass
class Assembly:
    """What the scaffold built from the drop-in directory, for the runtime to wire into its SDK."""

    drop_in: DropInDirectory
    context: RunContext
    tools: Mapping[str, Tool]
    coding_agents: Mapping[str, CodingAgent]
    context_strategy: ContextStrategy


class TurnOutcome(BaseModel):
    final_output: str
    completed: bool


class AgentRuntime(Component):
    def __init__(self, name: str, options, *, assembly: Assembly) -> None:
        super().__init__(name, options)
        self.assembly = assembly

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Start MCP servers and open the orchestrator's session."""

    @abstractmethod
    async def __aexit__(self, *exc_info: object) -> None: ...

    @abstractmethod
    async def run(self, prompt: str) -> TurnOutcome:
        """Send one prompt to the orchestrator, continuing its session."""

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per agent: its tools, delegates and MCP servers."""

    @classmethod
    @abstractmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry) -> str:
        """One model call outside any drop-in, used by provisioning to prove the runtime works."""


RUNTIMES: Registry[AgentRuntime] = Registry("agent runtime")
