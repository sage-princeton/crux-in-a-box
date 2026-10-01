import asyncio
import json

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import ConfigError
from crux_scaffold.gates import GATES
from crux_scaffold.loop import LOOPS, RunState, StateFile
from crux_scaffold.runtimes.base import TurnOutcome
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget
from crux_scaffold.workspace import RunContext, Workspace

from fakes import FakeRuntime, done

TWO_PHASES = {"type": "phased", "phases": [
    {"name": "clarify", "prompt": "PROMPT.md", "gates": ["spec"], "max_iterations": 2},
    {"name": "build", "prompt": "prompts/build.md", "gates": ["review"], "max_iterations": 3,
     "continue_prompt": "prompts/continue.md", "interval_seconds": 7},
]}
PASS = [{"passed": True}]


@pytest.fixture
def loop_env(drop_in_dir, tmp_path):
    (drop_in_dir / "prompts").mkdir()
    (drop_in_dir / "prompts/build.md").write_text("Build it.")
    (drop_in_dir / "prompts/continue.md").write_text("Iteration $iteration of $phase failed:\n$feedback")
    drop_in = DropInDirectory.load(drop_in_dir)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    def make(review=PASS, budget=None, state_dir=tmp_path / "state"):
        store = StateFile(state_dir / "state.json")
        state = store.load()
        ctx = RunContext(Workspace(drop_in.workspace), state_dir, {}, state.usage, budget or Budget(),
                         NullTelemetry(), sleep)
        gates = {"spec": GATES.create("spec", {"type": "command", "command": "test -s REQUEST.md"}),
                 "review": GATES.create("review", {"type": "scripted", "results": review})}
        return LOOPS.create("loop", TWO_PHASES, gates=gates), ctx, state, store

    return drop_in, make, slept


def test_phases_advance_when_their_gates_pass(loop_env):
    drop_in, make, _ = loop_env
    (drop_in.workspace / "REQUEST.md").write_text("spec")
    loop, ctx, state, store = make()
    runtime = FakeRuntime([done("spec written"), done("built")])
    outcome = asyncio.run(loop.run(runtime, drop_in, ctx, state, store))
    assert (outcome.status, outcome.phase, outcome.final_output) == ("completed", "build", "built")
    assert runtime.prompts == ["Handle the request in channel C123.", "Build it."]


def test_failed_gates_feed_back_through_the_continue_prompt_until_iterations_run_out(loop_env):
    drop_in, make, slept = loop_env
    loop, ctx, state, store = make()
    runtime = FakeRuntime([done("no spec"), done("still none")])
    outcome = asyncio.run(loop.run(runtime, drop_in, ctx, state, store))
    assert (outcome.status, outcome.phase) == ("iterations_exhausted", "clarify")
    assert runtime.prompts[1].startswith("Phase `clarify` is not done after iteration 1.")
    assert "## Gate `spec` failed\n\n$ test -s REQUEST.md\nexit_code=1" in runtime.prompts[1]
    assert slept == []


def test_a_gates_next_prompt_replaces_the_continue_prompt_and_the_phase_interval_applies(loop_env):
    drop_in, make, slept = loop_env
    (drop_in.workspace / "REQUEST.md").write_text("spec")
    review = [{"passed": False, "feedback": "no tests", "next_prompt": "Add tests."},
              {"passed": False, "feedback": "still no tests"}, {"passed": True}]
    loop, ctx, state, store = make(review)
    runtime = FakeRuntime([done(), done("v1"), done("v2"), done("v3")])
    assert asyncio.run(loop.run(runtime, drop_in, ctx, state, store)).status == "completed"
    assert runtime.prompts[2] == "Add tests."
    assert runtime.prompts[3] == "Iteration 2 of build failed:\n## Gate `review` failed\n\nstill no tests"
    assert slept == [7, 7]


def test_gates_see_the_phase_iteration_and_final_output(loop_env):
    drop_in, make, _ = loop_env
    (drop_in.workspace / "REQUEST.md").write_text("spec")
    loop, ctx, state, store = make()
    asyncio.run(loop.run(FakeRuntime([done(), done("built it")]), drop_in, ctx, state, store))
    seen = loop.gates["review"].seen[0]
    assert (seen.phase, seen.iteration, seen.last_output) == ("build", 1, "built it")


def test_a_turn_that_runs_out_of_turns_fails_the_iteration(loop_env):
    drop_in, make, _ = loop_env
    (drop_in.workspace / "REQUEST.md").write_text("spec")
    loop, ctx, state, store = make()
    runtime = FakeRuntime([TurnOutcome(final_output="", completed=False), done(), done()])
    asyncio.run(loop.run(runtime, drop_in, ctx, state, store))
    assert "Gate `turn` failed\n\nYour previous turn ran out of turns" in runtime.prompts[1]


def test_state_is_saved_after_every_iteration_and_a_restart_resumes(loop_env, tmp_path):
    drop_in, make, _ = loop_env
    loop, ctx, state, store = make()
    asyncio.run(loop.run(FakeRuntime([done("a"), done("b")]), drop_in, ctx, state, store))
    saved = json.loads((tmp_path / "state/state.json").read_text())
    assert (saved["phase_index"], saved["iteration"], len(saved["history"])) == (0, 2, 2)
    (drop_in.workspace / "REQUEST.md").write_text("spec")
    StateFile(tmp_path / "state/state.json").save(RunState(phase_index=1))
    loop, ctx, state, store = make()
    runtime = FakeRuntime([done("built")])
    assert asyncio.run(loop.run(runtime, drop_in, ctx, state, store)).status == "completed"
    assert runtime.prompts == ["Build it."]


def test_the_budget_is_a_hard_stop_between_iterations(loop_env):
    drop_in, make, _ = loop_env
    loop, ctx, state, store = make(budget=Budget(max_total_tokens=100))
    ctx.usage.add("pm", 90, 20)
    runtime = FakeRuntime([])
    outcome = asyncio.run(loop.run(runtime, drop_in, ctx, state, store))
    assert (outcome.status, runtime.prompts) == ("budget_exhausted", [])


def test_undefined_gates_and_duplicate_phases_are_config_errors():
    with pytest.raises(ConfigError, match="phase 'a' uses undefined gate\\(s\\): tests"):
        LOOPS.create("loop", {"type": "phased", "phases": [{"name": "a", "prompt": "P.md", "gates": ["tests"]}]},
                     gates={})
    with pytest.raises(ConfigError, match="unique names"):
        LOOPS.create("loop", {"type": "phased", "phases": [{"name": "a", "prompt": "P.md"},
                                                         {"name": "a", "prompt": "P.md"}]}, gates={})
