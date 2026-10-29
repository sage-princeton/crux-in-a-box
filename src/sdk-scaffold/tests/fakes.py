"""Fakes for testing the loop and gates without model calls: a runtime that replays turns and a gate that replays
declared results."""

from pydantic import BaseModel

from crux_scaffold.agent_runtimes.openai_agents_sdk import TurnOutcome, Verdict
from crux_scaffold.components import Options
from crux_scaffold.gates import GATES, Gate, GateContext, GateResult


class FakeRuntime:
    """Replays scripted turn outcomes and verdicts; records the prompts and judge requests."""

    def __init__(self, outputs: list[TurnOutcome], verdicts: list[Verdict] | None = None) -> None:
        self.outputs, self.verdicts = list(outputs), list(verdicts or [])
        self.prompts: list[str] = []
        self.judged: list[tuple[str, str, list[str], int]] = []

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self.prompts.append(prompt)
        return self.outputs.pop(0)

    async def judge(self, name: str, rubric: str, evidence: str, *, tools: list[str], max_turns: int | None,
                    review_workspace=None) -> Verdict:
        self.judged.append((rubric, evidence, tools, max_turns))
        return self.verdicts.pop(0)


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
