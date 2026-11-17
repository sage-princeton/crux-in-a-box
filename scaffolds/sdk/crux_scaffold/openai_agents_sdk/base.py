"""Agent runtimes: the agent SDK that runs the declared agents (OpenAI Agents SDK today, Claude Agent SDK later).
A runtime wires the scaffold's SDK-neutral components (tools, coding agents, context strategy) into its SDK."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Self

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.openai_agents_sdk.assembly import Assembly
from crux_scaffold.openai_agents_sdk.delegation_limiter import DelegationLimiter
from crux_scaffold.openai_agents_sdk.turn_outcome import TurnOutcome
from crux_scaffold.openai_agents_sdk.verdict import Verdict
from crux_scaffold.workspace import ReviewWorkspace

if TYPE_CHECKING:
    from crux_scaffold.telemetry import Telemetry


class AgentRuntime(Component):
    """The agent SDK that runs the declared agents. It builds one SDK agent per `[agents.<name>]` from the
    `Assembly` and runs the orchestrator's turns. See docs/extending.md for the contract."""

    def __init__(self, name: str, options, *, assembly: Assembly) -> None:
        super().__init__(name, options)
        self.assembly = assembly
        self._delegation = DelegationLimiter(assembly.drop_in.config.delegation.max_concurrent)

    @asynccontextmanager
    async def delegation_slot(self, *, background: bool = False) -> AsyncIterator[None]:
        """Hold shared delegation capacity for a caller."""
        async with self._delegation.slot(background=background):
            yield

    @asynccontextmanager
    async def delegation_wait(self) -> AsyncIterator[None]:
        """Suspend a delegated caller while it awaits independently scheduled work."""
        async with self._delegation.suspend_current():
            yield

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
                    review_workspace: ReviewWorkspace | None = None, model: str | None = None) -> Verdict:
        """Ask an isolated judge agent for a verdict. It has the named scaffold tools and no session, so it sees
        only the rubric, the evidence and what it checks itself. With review_workspace, bind every tool to that
        snapshot and omit workflow metadata from model inputs. With model, use that model through the run's
        provider instead of the run's model (a judge panel). Count every call as judge:<name>.
        Out of turns is a failing verdict, not an error."""

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per agent: its tools, delegates and MCP servers."""

    def coding_status(self) -> str:
        """One line on background coding briefs that are running, finished but uncollected, or cancelled and not yet
        reported, for the orchestrator's research context; empty when there are none."""
        return ""

    @classmethod
    @abstractmethod
    async def probe(cls, env: Mapping[str, str], prompt: str, telemetry: Telemetry, *,
                    options: Options | None = None) -> str:
        """One model call using optional validated runtime settings, without running the drop-in's loop."""


RUNTIMES: Registry[AgentRuntime] = Registry("agent runtime")
