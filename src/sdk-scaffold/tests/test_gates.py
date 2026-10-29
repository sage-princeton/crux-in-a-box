import asyncio
from pathlib import Path

from crux_scaffold.agent_runtimes.base import Verdict
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GATES, GateContext
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace

from fakes import FakeRuntime


class RecordingTelemetry(NullTelemetry):
    def __init__(self):
        self.records = []

    def record(self, name, kind, input, output):
        self.records.append((name, kind, input, output["passed"]))


def gate_context(drop_in_dir: Path, telemetry=None, runtime=None) -> GateContext:
    drop_in = DropInDirectory.load(drop_in_dir)
    ctx = RunContext(Workspace(drop_in.workspace), drop_in_dir / ".state", {}, UsageLedger(), Budget(),
                     telemetry or NullTelemetry())
    return GateContext(ctx, drop_in, runtime or FakeRuntime([]), "build", 2, "I added the field.")


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


def test_llm_judge_sees_the_rubric_output_and_inspected_files_only(drop_in_dir):
    (drop_in_dir / "rubric.md").write_text("Pass if the spec is met.")
    (drop_in_dir / "workspace/REQUEST.md").write_text("- [ ] show location")
    runtime = FakeRuntime([], [Verdict(passed=False, feedback="location missing", next_prompt="Add location.")])
    gate = GATES.create("request_met", {"type": "llm_judge", "rubric": "rubric.md",
                                        "inspect": ["REQUEST.md", "missing.md"]})
    result = asyncio.run(gate.check(gate_context(drop_in_dir, runtime=runtime)))
    rubric, evidence, tools, max_turns = runtime.judged[0]
    assert (tools, max_turns) == (["read_file", "list_files"], 20)
    assert rubric == "Pass if the spec is met."
    assert "iteration 2: the agent's final output\n\nI added the field." in evidence
    assert "# Workspace file `REQUEST.md`\n\n- [ ] show location" in evidence
    assert "# Workspace file `missing.md`\n\n(missing)" in evidence
    assert (result.passed, result.feedback, result.next_prompt) == (False, "location missing", "Add location.")


def test_llm_judge_gets_the_tools_and_turns_the_drop_in_declares(drop_in_dir):
    (drop_in_dir / "rubric.md").write_text("Run the tests before passing.")
    runtime = FakeRuntime([], [Verdict(passed=True, feedback="tests pass", next_prompt=None)])
    gate = GATES.create("request_met", {"type": "llm_judge", "rubric": "rubric.md",
                                        "tools": ["read_file", "site_tests"], "max_turns": 8})
    assert gate.tools == ["read_file", "site_tests"]
    asyncio.run(gate.check(gate_context(drop_in_dir, runtime=runtime)))
    assert runtime.judged[0][2:] == (["read_file", "site_tests"], 8)


def test_a_command_gate_uses_no_tools():
    assert GATES.create("tests", {"type": "command", "command": "true"}).tools == []
