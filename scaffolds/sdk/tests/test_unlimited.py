import asyncio

import pytest

from crux_scaffold.agent_runtimes.base import Verdict
from crux_scaffold.agent_runtimes.openai_agents_sdk import OpenAIAgentsOptions
from crux_scaffold.config import AgentConfig, PhaseConfig
from crux_scaffold.gates import JudgeOptions

from drop_ins import edit
from scripted import ScriptedModel, call, say


@pytest.mark.parametrize("options,required,field", [
    (OpenAIAgentsOptions, {}, "max_turns"),
    (AgentConfig, {"persona": "pi.md"}, "max_turns"),
    (JudgeOptions, {"rubric": "review.md"}, "max_turns"),
    (PhaseConfig, {"name": "work", "prompt": "p.md", "continue_prompt": "c.md"}, "max_iterations"),
])
def test_unlimited_is_explicit_and_invalid_counts_still_fail(options, required, field):
    assert getattr(options(**required, **{field: "unlimited"}), field) == "unlimited"
    for invalid in (0, -1, True, 1.5, "forever"):
        with pytest.raises(ValueError):
            options(**required, **{field: invalid})


def test_unlimited_orchestrator_and_phases_run_past_defaults(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'continue_prompt = "prompts/continue.md"',
         'continue_prompt = "prompts/continue.md"\nmax_iterations = "unlimited"\ngates = ["ready"]')
    with (drop_in_dir / "scaffold.toml").open("a") as stream:
        stream.write('''
[runtime]
type = "openai-agents"
max_turns = "unlimited"
[gates.ready]
type = "command"
command = "test -f ready"
[tools.list_files]
''')
    turns = [call("read_file", {"path": "AGENTS.md"}, f"l{i}") for i in range(55)]
    model = ScriptedModel(say("Continue"), *turns,
                          call("write_file", {"path": "ready", "content": "yes"}, "done"), say("Finished"))
    scaffold = assemble(drop_in_dir, {"pm": model})
    assert asyncio.run(scaffold.run()).status == "completed"
    assert len(scaffold.state.history) == 2
    assert scaffold.context.usage.by_source["pm"].requests == 58


def test_unlimited_judge_uses_sdk_no_limit(drop_in_dir, assemble):
    with (drop_in_dir / "scaffold.toml").open("a") as stream:
        stream.write('\n[tools.list_files]\n')
    model = ScriptedModel(*[call("list_files", {"path": "."}, f"l{i}") for i in range(25)],
                          say(Verdict(passed=True, feedback="Checked", next_prompt=None).model_dump_json()))
    scaffold = assemble(drop_in_dir, {"judge": model})
    verdict = asyncio.run(scaffold.runtime.judge("review", "Check", "Evidence", tools=["list_files"],
                                                 max_turns=None))
    assert verdict.passed
    assert scaffold.context.usage.by_source["judge:review"].requests == 26
