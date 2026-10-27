"""Agent runtimes: the agent SDK that runs the declared agents (OpenAI Agents SDK today, Claude Agent SDK later).
A runtime wires the scaffold's SDK-neutral components (tools, coding agents, context strategy) into its SDK."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel

from crux_scaffold.coding_agents import CodingAgent
from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.context_strategies import ContextStrategy
from crux_scaffold.tools import Tool
from crux_scaffold.workspace import ReviewWorkspace, RunContext

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
    instructions: Callable[[str], str]


class TurnOutcome(BaseModel):
    """The orchestrator's answer to one prompt; `completed` is false when it ran out of turns."""

    final_output: str
    completed: bool


class Verdict(BaseModel):
    """A judge's structured answer."""

    passed: bool
    feedback: str
    next_prompt: str | None


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
    async def delegation_slot(self, *, background: bool = False) -> AsyncIterator[None]:
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
    async def delegation_wait(self) -> AsyncIterator[None]:
        """Suspend a delegated caller while it awaits an independently scheduled worker."""
        async with self._suspend_delegate(self._delegate_call.get()):
            yield

    @asynccontextmanager
    async def _suspend_delegate(self, parent: _DelegateCall | None) -> AsyncIterator[None]:
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

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Start MCP servers and open the orchestrator's session."""

    @abstractmethod
    async def __aexit__(self, *exc_info: object) -> None: ...

    @abstractmethod
    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        """Send one prompt to the orchestrator, continuing its session. `workflow` names the turn in traces."""

    @abstractmethod
    async def judge(self, name: str, rubric: str, evidence: str, *, tools: list[str], max_turns: int | None,
                    review_workspace: ReviewWorkspace | None = None) -> Verdict:
        """Ask an isolated judge agent for a verdict. It has the named scaffold tools and no session, so it sees
        only the rubric, the evidence and what it checks itself. With review_workspace, bind every tool to that
        snapshot and omit workflow metadata from model inputs. Count every call as judge:<name>.
        Out of turns is a failing verdict, not an error."""

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per agent: its tools, delegates and MCP servers."""

    @classmethod
    @abstractmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry, *,
                    options: Options | None = None) -> str:
        """One model call using optional validated runtime settings, without running the drop-in's loop."""


RUNTIMES: Registry[AgentRuntime] = Registry("agent runtime")
