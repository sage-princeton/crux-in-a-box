"""Agent runtimes: the agent SDK that runs the declared agents (OpenAI Agents SDK today, Claude Agent SDK later).
A runtime wires the scaffold's SDK-neutral components (tools, coding agents, context strategy) into its SDK."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
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
    """The orchestrator's answer to one prompt; `completed` is false when it ran out of turns."""

    final_output: str
    completed: bool


@dataclass
class _DelegateCall:
    task: asyncio.Task
    active: bool = False
    children: int = 0
    closed: bool = False


class AgentRuntime(Component):
    """The agent SDK that runs the declared agents. It builds one SDK agent per `[agents.<name>]` from the
    `Assembly` and runs the orchestrator's turns. See docs/extending.md for the contract."""

    def __init__(self, name: str, options, *, assembly: Assembly) -> None:
        super().__init__(name, options)
        self.assembly = assembly
        self._delegate_limit = assembly.drop_in.config.delegation.max_concurrent
        self._active_delegates = 0
        self._delegate_changed = asyncio.Condition()
        self._delegate_call: ContextVar[_DelegateCall | None] = ContextVar("delegate_call", default=None)

    @asynccontextmanager
    async def delegation_slot(self) -> AsyncIterator[None]:
        """Bound delegated work across this runtime; suspend a parent while its nested calls run."""
        limit = self._delegate_limit
        if limit is None:
            yield
            return
        parent = self._delegate_call.get()
        changed = self._delegate_changed
        async with changed:
            if parent is not None:
                parent.children += 1
                if parent.active:
                    parent.active = False
                    self._active_delegates -= 1
                changed.notify_all()
        try:
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
        finally:
            if parent is not None:
                async with changed:
                    parent.children -= 1
                    changed.notify_all()
                    await changed.wait_for(lambda: parent.closed or parent.task.cancelling() or parent.children
                                           or parent.active or self._active_delegates < limit)
                    if not (parent.closed or parent.task.cancelling() or parent.children or parent.active):
                        parent.active = True
                        self._active_delegates += 1
                        changed.notify_all()

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Start MCP servers and open the orchestrator's session."""

    @abstractmethod
    async def __aexit__(self, *exc_info: object) -> None: ...

    @abstractmethod
    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        """Send one prompt to the orchestrator, continuing its session. `workflow` names the turn in traces."""

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per agent: its tools, delegates and MCP servers."""

    @classmethod
    @abstractmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry) -> str:
        """One model call outside any drop-in, used by provisioning to prove the runtime works."""


RUNTIMES: Registry[AgentRuntime] = Registry("agent runtime")
