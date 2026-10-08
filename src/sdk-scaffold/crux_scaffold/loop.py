"""The outer loop: phases of prompts, each repeated until its gates pass, with state persisted after every
iteration so a restarted scaffold resumes where it stopped."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from pathlib import Path
from string import Template
from typing import Literal

from pydantic import BaseModel, Field

from crux_scaffold.agent_runtimes.base import AgentRuntime
from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.config import PhaseConfig
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.gates import Gate, GateContext, GateResult
from crux_scaffold.usage import UsageLedger
from crux_scaffold.workspace import RunContext


class IterationRecord(BaseModel):
    phase: str
    iteration: int
    completed: bool
    gates: list[GateResult]


class RunState(BaseModel):
    """Where the loop is and what it has done, saved after every iteration so a restarted scaffold resumes."""

    phase_index: int = 0
    iteration: int = 0
    next_prompt: str | None = None
    history: list[IterationRecord] = []
    usage: UsageLedger = UsageLedger()


class StateFile:
    """The run state, at `state.json` in the state directory, which it creates when it first saves."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "state.json"

    def load(self) -> RunState:
        return RunState.model_validate_json(self.path.read_text()) if self.path.is_file() else RunState()

    def save(self, state: RunState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        tmp.replace(self.path)


class LoopOutcome(BaseModel):
    """How the loop ended, the phase it ended in, and the orchestrator's last output."""

    status: Literal["completed", "iterations_exhausted", "budget_exhausted"]
    phase: str
    final_output: str


class Loop(Component):
    """The outer loop: decides what to prompt the orchestrator next and when the run is done, using the gates."""

    def __init__(self, name: str, options: Options, *, gates: Mapping[str, Gate]) -> None:
        super().__init__(name, options)
        self.gates = gates

    @abstractmethod
    async def run(self, runtime: AgentRuntime, drop_in: DropInDirectory, ctx: RunContext, state: RunState,
                  store: StateFile) -> LoopOutcome: ...

    @abstractmethod
    def describe(self) -> list[str]: ...


LOOPS: Registry[Loop] = Registry("loop")


class PhasedOptions(Options):
    phases: list[PhaseConfig] = Field(min_length=1)


@LOOPS.register
class PhasedLoop(Loop):
    """Runs phases in order, each repeated until its gates pass or it runs out of iterations."""

    type_name = "phased"
    Options = PhasedOptions

    def __init__(self, name: str, options: PhasedOptions, *, gates: Mapping[str, Gate]) -> None:
        super().__init__(name, options, gates=gates)
        names = [phase.name for phase in options.phases]
        if len(set(names)) != len(names):
            raise InvalidDropInError(f"loop phases must have unique names: {', '.join(names)}")
        for phase in options.phases:
            missing = [gate for gate in phase.gates if gate not in gates]
            if missing:
                raise InvalidDropInError(f"phase '{phase.name}' uses undefined gate(s): {', '.join(missing)}")

    async def run(self, runtime: AgentRuntime, drop_in: DropInDirectory, ctx: RunContext, state: RunState,
                  store: StateFile) -> LoopOutcome:
        phases = self.options.phases
        last_output = ""
        while state.phase_index < len(phases):
            phase = phases[state.phase_index]
            if ctx.budget.exhausted(ctx.usage):
                return LoopOutcome(status="budget_exhausted", phase=phase.name, final_output=last_output)
            prompt = state.next_prompt or drop_in.prompt(phase.prompt)
            state.iteration += 1
            with ctx.telemetry.trace(f"{phase.name} #{state.iteration}", {"phase": phase.name,
                                                                          "iteration": state.iteration}):
                turn = await runtime.run(prompt, workflow=phase.name)
                last_output = turn.final_output
                gate_ctx = GateContext(ctx, drop_in, runtime, phase.name, state.iteration, last_output)
                results = [await self.gates[gate].check(gate_ctx) for gate in phase.gates]
            if not turn.completed:
                results.insert(0, GateResult(gate="turn", passed=False,
                                             feedback="Your previous turn ran out of turns before it finished."))
            state.history.append(IterationRecord(phase=phase.name, iteration=state.iteration,
                                                 completed=turn.completed, gates=results))
            if all(result.passed for result in results):
                state.phase_index, state.iteration, state.next_prompt = state.phase_index + 1, 0, None
            elif state.iteration >= phase.max_iterations:
                store.save(state)
                return LoopOutcome(status="iterations_exhausted", phase=phase.name, final_output=last_output)
            else:
                state.next_prompt = self.continuation(drop_in, phase, state.iteration, results)
            store.save(state)
            if state.next_prompt and phase.interval_seconds:
                await ctx.sleep(phase.interval_seconds)
        return LoopOutcome(status="completed", phase=phases[-1].name, final_output=last_output)

    def continuation(self, drop_in: DropInDirectory, phase: PhaseConfig, iteration: int,
                     results: list[GateResult]) -> str:
        failed = [result for result in results if not result.passed]
        written = next((result.next_prompt for result in failed if result.next_prompt), None)
        if written:
            return written
        feedback = "\n\n".join(f"## Gate `{result.gate}` failed\n\n{result.feedback}" for result in failed)
        return Template(drop_in.read(phase.continue_prompt)).safe_substitute(phase=phase.name, iteration=iteration,
                                                                             feedback=feedback)

    def describe(self) -> list[str]:
        return [f"phase {phase.name}: gates [{', '.join(phase.gates)}] max_iterations {phase.max_iterations}"
                for phase in self.options.phases]
