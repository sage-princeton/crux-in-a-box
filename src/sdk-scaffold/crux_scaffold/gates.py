"""Phase gates: checks the loop runs between iterations to decide whether a phase is done and, if not, what to
prompt next. A gate may also write the next prompt itself."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.workspace import RunContext

if TYPE_CHECKING:
    from crux_scaffold.drop_in import DropInDirectory

MAX_FEEDBACK_CHARS = 4_000


class GateResult(BaseModel):
    """A gate's verdict. `feedback` goes into the continue prompt when the gate fails, and `next_prompt`, when
    set, replaces the continue prompt."""

    gate: str
    passed: bool
    feedback: str
    next_prompt: str | None = None


@dataclass
class GateContext:
    """What a gate sees: the run, the drop-in, where the loop is, and the orchestrator's last output."""

    run: RunContext
    drop_in: DropInDirectory
    phase: str
    iteration: int
    last_output: str


class Gate(Component):
    """A check the loop runs after each iteration to decide whether the phase is done. Its definition comes from
    the drop-in, never from files the agents can edit, so the agents cannot author their own verdict."""

    async def check(self, ctx: GateContext) -> GateResult:
        result = await self.evaluate(ctx)
        # "evaluator" is a Langfuse observation type, not part of a formal or versioned spec yet (AE-247).
        ctx.run.telemetry.record(f"gate:{self.name}", "evaluator",
                                 {"phase": ctx.phase, "iteration": ctx.iteration}, result.model_dump())
        return result

    @abstractmethod
    async def evaluate(self, ctx: GateContext) -> GateResult: ...


GATES: Registry[Gate] = Registry("gate")


class CommandGateOptions(Options):
    command: str
    timeout_seconds: float = 600


@GATES.register
class CommandGate(Gate):
    """Passes when the command exits 0 in the workspace; its output is the feedback."""

    type_name = "command"
    Options = CommandGateOptions

    async def evaluate(self, ctx: GateContext) -> GateResult:
        output = ctx.run.workspace.run_shell(self.options.command, self.options.timeout_seconds)
        return GateResult(gate=self.name, passed=output.startswith("exit_code=0\n"),
                          feedback=f"$ {self.options.command}\n{output[-MAX_FEEDBACK_CHARS:]}")
