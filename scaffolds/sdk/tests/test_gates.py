import asyncio
from pathlib import Path

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.gates import GATES, GateContext
from crux_scaffold.openai_agents_sdk.openai_agents_runtime import Verdict
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
    rubric, evidence, tools, max_turns, model = runtime.judged[0]
    assert model is None
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
    assert runtime.judged[0][2:] == (["read_file", "site_tests"], 8, None)


def test_a_command_gate_uses_no_tools():
    assert GATES.create("tests", {"type": "command", "command": "true"}).tools == []


PANEL = ["google/gemini-4-argon", "anthropic/claude-opus-5.5", "openai/gpt-6-astra"]


def panel_gate(drop_in_dir, **options):
    (drop_in_dir / "rubric.md").write_text("Review the work.")
    return GATES.create("paper", {"type": "llm_judge", "rubric": "rubric.md", "models": PANEL, **options})


def test_a_judge_panel_passes_on_a_majority_and_returns_every_review_anonymously(drop_in_dir):
    runtime = FakeRuntime([], [Verdict(passed=True, feedback="Clear and solid.", next_prompt="ignored"),
                               Verdict(passed=False, feedback="Tables are unreadable.", next_prompt=None),
                               Verdict(passed=True, feedback="Convincing evidence.", next_prompt=None)])
    result = asyncio.run(panel_gate(drop_in_dir).check(gate_context(drop_in_dir, runtime=runtime)))
    assert [judged[4] for judged in runtime.judged] == PANEL
    assert all(judged[:4] == runtime.judged[0][:4] for judged in runtime.judged)  # same rubric, evidence, tools
    assert (result.passed, result.next_prompt) == (True, None)
    assert result.feedback.startswith("Panel review: 2 of 3 reviewers recommend acceptance; 2 are needed.")
    for number, text, stance in ((1, "Clear and solid.", "recommends acceptance"),
                                 (2, "Tables are unreadable.", "does not recommend acceptance"),
                                 (3, "Convincing evidence.", "recommends acceptance")):
        assert f"## Reviewer {number} ({stance})\n\n{text}" in result.feedback
    assert not any(name in result.feedback.lower() for name in ("argon", "claude", "opus", "astra", "gpt"))


def test_a_panel_without_a_majority_fails_and_counts_a_reviewer_that_errored_against_it(drop_in_dir):
    runtime = FakeRuntime([], [Verdict(passed=True, feedback="Accept.", next_prompt=None),
                               RuntimeError("provider unavailable"),
                               Verdict(passed=False, feedback="Reject.", next_prompt=None)])
    result = asyncio.run(panel_gate(drop_in_dir).check(gate_context(drop_in_dir, runtime=runtime)))
    assert not result.passed
    assert result.feedback.startswith("Panel review: 1 of 3 reviewers recommend acceptance; 2 are needed.")
    assert "## Reviewer 2 (did not complete: RuntimeError)" in result.feedback


def test_a_panel_raises_when_no_reviewer_completes(drop_in_dir):
    runtime = FakeRuntime([], [RuntimeError("down"), RuntimeError("down"), RuntimeError("down")])
    with pytest.raises(RuntimeError, match="down"):
        asyncio.run(panel_gate(drop_in_dir).check(gate_context(drop_in_dir, runtime=runtime)))


@pytest.mark.parametrize("models", [[""], ["a", "a"]])
def test_panel_models_must_be_distinct_names(models):
    with pytest.raises(InvalidDropInError, match="models"):
        GATES.create("paper", {"type": "llm_judge", "rubric": "rubric.md", "models": models})
