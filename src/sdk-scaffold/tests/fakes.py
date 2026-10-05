"""Fakes for testing the loop and gates without an agent SDK: a runtime that replays turns and a gate that replays
declared results."""

from typing import Self

from pydantic import BaseModel

from crux_scaffold.components import Component, Options
from crux_scaffold.gates import GATES, Gate, GateContext, GateResult
from crux_scaffold.runtimes.base import AgentRuntime, TurnOutcome


class FakeRuntime(AgentRuntime):
    """Replays scripted turn outcomes and records the prompts the loop sent."""

    type_name = "fake"

    def __init__(self, outputs: list[TurnOutcome]) -> None:
        Component.__init__(self, "runtime", Options())
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def run(self, prompt: str) -> TurnOutcome:
        self.prompts.append(prompt)
        return self.outputs.pop(0)

    def describe(self) -> list[str]:
        return []

    @classmethod
    async def probe(cls, env, prompt: str, telemetry) -> str:
        return prompt


def done(text: str = "done") -> TurnOutcome:
    return TurnOutcome(final_output=text, completed=True)


class Scripted(BaseModel):
    passed: bool
    feedback: str = ""
    next_prompt: str | None = None


class ScriptedGateOptions(Options):
    results: list[Scripted]


@GATES.register
class ScriptedGate(Gate):
    """Returns its declared results in order, repeating the last one."""

    type_name = "scripted"
    Options = ScriptedGateOptions

    def __init__(self, name: str, options: ScriptedGateOptions) -> None:
        super().__init__(name, options)
        self.remaining = list(options.results)
        self.seen: list[GateContext] = []

    async def evaluate(self, ctx: GateContext) -> GateResult:
        self.seen.append(ctx)
        scripted = self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]
        return GateResult(gate=self.name, **scripted.model_dump())
