"""The composition root: the only place components are built from a drop-in directory and wired together."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import crux_scaffold.agent_runtimes.openai_agents  # noqa: F401  registers the built-in runtime
from crux_scaffold.agent_runtimes.base import RUNTIMES, Assembly
from crux_scaffold.coding_agents import CODING_AGENTS, CodingAgent
from crux_scaffold.context_strategies import CONTEXT_STRATEGIES, Persistent
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GATES, CommandGate
from crux_scaffold.loop import LOOPS, LoopOutcome, PhasedLoop, StateFile
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import TOOLS, Rest, Tool
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
        self._agent_instructions = {name: drop_in.instructions(name) for name in config.agents}
        self.assembly = Assembly(drop_in, self.context, self._tools(), self.coding_agents, self.strategy,
                                 self._instructions)
        self.runtime = RUNTIMES.create("runtime", config.runtime, assembly=self.assembly, **(runtime_overrides or {}))

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

    def _instructions(self, name: str) -> str:
        config = self.drop_in.config
        if name != config.orchestrator:
            return self._agent_instructions[name]
        return "\n\n---\n\n".join(filter(None, [self._preamble() if config.preamble.enabled else "",
                                               config.preamble.append.strip(), self._agent_instructions[name]]))

    def _preamble(self) -> str:
        assembly = self.assembly
        config = assembly.drop_in.config
        spec = config.agents[config.orchestrator]
        runtime_type = config.runtime.get("type", "runtime")
        lines = ["# Scaffold setup", "",
                 f"You are `{config.orchestrator}`, the orchestrator in the `{runtime_type}` agent runtime.",
                 "The scaffold sends you one prompt per loop iteration. Your persona and standing context follow "
                 "this generated setup.", "", "## Loop and gates", "", *self._loop_instructions(), "",
                 "## Delegates", ""]
        for name in spec.delegates:
            if name in assembly.coding_agents:
                agent = assembly.coding_agents[name]
                lines.append(f"- `{name}` (coding agent, `{agent.type_name}`): {agent.description}")
            else:
                lines.append(f"- `{name}` (agent): {config.agents[name].description}")
        if spec.delegates:
            lines += ["", "Each delegate call starts fresh with its own standing instructions and your brief; "
                      "it does not receive your conversation. Include the task, acceptance criteria and scope. "
                      "Coding agents bring their own coding tools, sandbox and context management."]
        else:
            lines.append("No delegates are available to you.")
        lines += ["", "## Tools and MCP servers", ""]
        lines += [f"- `{name}`: {assembly.tools[name].description}" for name in spec.tools]
        if not spec.tools:
            lines.append("No scaffold tools are available to you.")
        lines += [f"- `{name}` (stdio MCP server): use its discovered MCP tools and their descriptions."
                  for name in spec.mcp_servers]
        if spec.mcp_servers:
            lines += ["", "If a connected server provides an operator channel (such as Slack), use its MCP tools "
                      "to read steering, ask questions and report progress. The outer loop does not automatically "
                      "poll or inject operator messages. Follow the channel and thread guidance in your "
                      "standing context."]
        else:
            lines.append("No MCP servers are available to you.")
        limit = assembly.context.budget.max_total_tokens
        lines += ["", "## Budget and resting", "", f"Token limit: {'none' if limit is None else f'{limit:,}'}. "
                  "Usage is shared across agents and coding agents. The loop checks the budget before each iteration; "
                  "it does not interrupt an in-flight iteration."]
        for name in spec.tools:
            tool = assembly.tools[name]
            if isinstance(tool, Rest):
                policy = tool.options
                lines += [f"`{name}` bounds each requested wait to {policy.min_seconds:g}–{policy.max_seconds:g}s "
                          f"and {policy.total_seconds:g}s total, shared by all callers of this tool. "
                          "Use it when waiting for external input, then check again. When its budget is exhausted, "
                          "continue with what you have and record assumptions. Rest counters reset on process restart."]
        lines += ["", "## Restart and context", "",
                  "The loop saves progress and token usage after each finished iteration. Restarting with the same "
                  "state directory resumes saved progress; an interrupted iteration reruns. Check existing work "
                  "before repeating actions, since its side effects may already exist.",
                  f"Context strategy: `{assembly.context_strategy.type_name}`. "
                  "Persona and standing context are instructions, outside conversation selection."]
        if isinstance(assembly.context_strategy, Persistent):
            lines.append("The orchestrator's conversation is stored in SQLite in the state directory and survives "
                         "restarts. The persistent strategy sends the whole stored conversation on each call.")
        return "\n".join(lines)

    def _loop_instructions(self) -> list[str]:
        loop = self.loop
        lines = [f"Loop: `{loop.type_name}`."]
        if not isinstance(loop, PhasedLoop):
            return [*lines, *loop.describe()]
        phases = loop.options.phases
        if self.state.phase_index < len(phases):
            phase = phases[self.state.phase_index]
            lines += [f"Current phase: `{phase.name}`; iteration {max(1, self.state.iteration)} "
                      f"of at most {phase.max_iterations}."]
        else:
            lines.append("All phases are complete.")
        lines += ["Phases run in order. After each iteration the loop evaluates the phase's gates. "
                  "A completed turn with all gates passing advances the phase; with no gates, a completed turn "
                  "advances immediately. Failed gates or a turn that runs out of turns produce a continuation "
                  "prompt, unless the phase's iteration limit stops the run.", ""]
        lines += [f"- `{phase.name}`: gates [{', '.join(phase.gates) or 'none'}]; "
                  f"max iterations {phase.max_iterations}; retry interval {phase.interval_seconds:g}s."
                  for phase in phases]
        for name in dict.fromkeys(name for phase in phases for name in phase.gates):
            gate = loop.gates[name]
            detail = f": exit code 0 from `{gate.options.command}`" if isinstance(gate, CommandGate) else ""
            lines.append(f"- Gate `{name}` ({gate.type_name}){detail}.")
        lines += ["", "Gate definitions come from the drop-in. Gate feedback is supplied in the next prompt; "
                  "a failing gate can replace the continuation prompt."]
        return lines

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
