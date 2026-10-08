"""The composition root: the only place components are built from a drop-in directory and wired together."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import crux_scaffold.agent_runtimes.openai_agents  # noqa: F401  registers the built-in runtime
from crux_scaffold.agent_runtimes.base import RUNTIMES, Assembly
from crux_scaffold.coding_agents import CODING_AGENTS, CodingAgent
from crux_scaffold.context_strategies import CONTEXT_STRATEGIES
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GATES
from crux_scaffold.loop import LOOPS, LoopOutcome, StateFile
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import TOOLS, Tool
from crux_scaffold.workspace import RunContext, Sleep, Workspace


class Scaffold:
    """Builds every component a drop-in declares and runs its loop."""

    def __init__(self, drop_in: DropInDirectory, env: Mapping[str, str], *, state_dir: Path, telemetry: Telemetry,
                 sleep: Sleep = asyncio.sleep, runtime_overrides: Mapping[str, Any] | None = None) -> None:
        config = drop_in.config
        self.drop_in = drop_in
        self.store = StateFile(state_dir)
        self.state = self.store.load()
        self.context = RunContext(Workspace(drop_in.workspace), state_dir, env, self.state.usage, config.budget,
                                  telemetry, sleep)
        self.strategy = CONTEXT_STRATEGIES.create("context", config.context)
        self.loop = LOOPS.create("loop", config.loop, gates={name: GATES.create(name, table)
                                                             for name, table in config.gates.items()})
        self.coding_agents = self._coding_agents()
        assembly = Assembly(drop_in, self.context, self._tools(), self.coding_agents, self.strategy)
        self.runtime = RUNTIMES.create("runtime", config.runtime, assembly=assembly, **(runtime_overrides or {}))

    def _tools(self) -> dict[str, Tool]:
        config = self.drop_in.config
        names = set(config.tools) | {tool for agent in config.agents.values() for tool in agent.tools}
        return {name: TOOLS.create(name, config.tools.get(name, {})) for name in sorted(names)}

    def _coding_agents(self) -> dict[str, CodingAgent]:
        agents = {}
        for name, table in self.drop_in.config.coding_agents.items():
            cls, options = CODING_AGENTS.resolve(name, table)
            agents[name] = cls(name, options, developer_instructions=self.drop_in.standing_context(options.context))
        return agents

    async def run(self) -> LoopOutcome:
        try:
            async with self.runtime:
                return await self.loop.run(self.runtime, self.drop_in, self.context, self.state, self.store)
        finally:
            self.context.telemetry.flush()

    def describe(self) -> list[str]:
        config = self.drop_in.config
        return [f"drop-in {self.drop_in.root}; runtime {self.runtime.type_name}; orchestrator {config.orchestrator}",
                f"context {self.strategy.type_name}; budget {config.budget.max_total_tokens or 'none'} tokens",
                *self.runtime.describe(),
                *(f"{name}: coding agent {agent.type_name}" for name, agent in self.coding_agents.items()),
                *self.loop.describe(),
                *(f"gate {name}: {gate.type_name}" for name, gate in self.loop.gates.items())]
