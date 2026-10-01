import asyncio
from pathlib import Path

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GATES, GateContext
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


class RecordingTelemetry(NullTelemetry):
    def __init__(self):
        self.records = []

    def record(self, name, kind, input, output):
        self.records.append((name, kind, input, output["passed"]))


def gate_context(drop_in_dir: Path, telemetry=None) -> GateContext:
    drop_in = DropInDirectory.load(drop_in_dir)
    ctx = RunContext(Workspace(drop_in.workspace), drop_in_dir / ".state", {}, UsageLedger(), Budget(),
                     telemetry or NullTelemetry())
    return GateContext(ctx, drop_in, "build", 2, "I added the field.")


def test_command_gate_passes_on_exit_0_and_feeds_back_its_output(drop_in_dir):
    passing = GATES.create("tests", {"type": "command", "command": "echo ok"})
    failing = GATES.create("tests", {"type": "command", "command": "echo 'FAILED (failures=1)'; exit 1"})
    ctx = gate_context(drop_in_dir)
    assert asyncio.run(passing.check(ctx)).passed
    result = asyncio.run(failing.check(ctx))
    assert (result.passed, result.next_prompt) == (False, None)
    assert result.feedback == "$ echo 'FAILED (failures=1)'; exit 1\nexit_code=1\nFAILED (failures=1)\n"


def test_command_gate_runs_in_the_workspace(drop_in_dir):
    (drop_in_dir / "workspace/REQUEST.md").write_text("spec")
    gate = GATES.create("spec", {"type": "command", "command": "test -s REQUEST.md"})
    assert asyncio.run(gate.check(gate_context(drop_in_dir))).passed


def test_every_gate_result_is_recorded_as_an_evaluation(drop_in_dir):
    telemetry = RecordingTelemetry()
    gate = GATES.create("tests", {"type": "command", "command": "true"})
    asyncio.run(gate.check(gate_context(drop_in_dir, telemetry=telemetry)))
    assert telemetry.records == [("gate:tests", "evaluator", {"phase": "build", "iteration": 2}, True)]
