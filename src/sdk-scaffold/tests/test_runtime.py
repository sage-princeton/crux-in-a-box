import asyncio
import json

import pytest

from crux_scaffold.errors import InvalidDropInError

from drop_ins import add_fake_slack, edit
from scripted import ScriptedModel, call, say, tool_outputs


def test_coding_agent_delegate_gets_only_the_brief_and_its_standing_context(drop_in_dir, assemble):
    pm = ScriptedModel(call("engineer", {"brief": "TASK: write NOTE.md"}, "p1"), say("Engineer finished."))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    outcome = asyncio.run(scaffold.run())
    engineer = scaffold.coding_agents["engineer"]
    assert (outcome.status, outcome.final_output) == ("completed", "Engineer finished.")
    assert engineer.briefs == ["TASK: write NOTE.md"]
    assert engineer.developer_instructions == (
        "# Standing context: workspace/ENGINEERING.md\n\nEngineering standing context.")
    assert (scaffold.drop_in.workspace / "site/NOTE.md").read_text() == "done"
    assert tool_outputs(pm.inputs[-1]) == ["[coding agent completed]\nwrote site/NOTE.md"]


def test_agent_delegate_sees_its_persona_and_brief_but_not_the_callers_context(drop_in_dir, assemble):
    reviewer = ScriptedModel(say("looks fine"))
    pm = ScriptedModel(call("reviewer", {"input": "Review NOTE.md"}, "p1"), say("Reviewed."))
    asyncio.run(assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer}).run())
    assert reviewer.instructions == ["You are the reviewer."]
    assert "Review NOTE.md" in str(reviewer.inputs[0])
    assert "Handle the request" not in str(reviewer.inputs[0])


def test_subagent_out_of_turns_reports_an_error_to_its_caller(drop_in_dir, assemble):
    reviewer = ScriptedModel(*[call("read_file", {"path": "AGENTS.md"}, f"r{i}") for i in range(6)])
    pm = ScriptedModel(call("reviewer", {"input": "loop"}, "p1"), say("recovered"))
    outcome = asyncio.run(assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer}).run())
    assert outcome.final_output == "recovered"
    assert "Max turns (4) exceeded" in tool_outputs(pm.inputs[-1])[0]


def test_tool_errors_are_returned_to_the_model(drop_in_dir, assemble):
    pm = ScriptedModel(call("read_file", {"path": "../secrets"}, "p1"), call("read_file", {"pth": "x"}, "p2"),
                       say("ok"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert tool_outputs(pm.inputs[1]) == ["error: ../secrets is outside the workspace"]
    assert "Extra inputs are not permitted" in tool_outputs(pm.inputs[2])[1]


def test_usage_is_recorded_per_agent_and_persisted(drop_in_dir, assemble, tmp_path):
    pm = ScriptedModel(call("engineer", {"brief": "x"}, "p1"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(scaffold.run())
    usage = scaffold.context.usage.by_source
    assert (usage["pm"].requests, usage["pm"].total_tokens) == (2, 30)
    assert usage["engineer"].total_tokens == 150
    assert json.loads((tmp_path / "state/state.json").read_text())["usage"]["by_source"]["pm"]["requests"] == 2


def test_unknown_tool_type_is_a_config_error(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file", "write_file", "rest"]', 'tools = ["deploy"]')
    with pytest.raises(InvalidDropInError, match="unknown tool type 'deploy'"):
        assemble(drop_in_dir)


def test_every_agent_takes_the_run_model_and_effort_from_the_env(drop_in_dir, assemble):
    scaffold = assemble(drop_in_dir, env={"CRUX_MODEL": "gpt-test", "CRUX_REASONING_EFFORT": "low"})
    scaffold.runtime.build()
    assert {name: (agent.model, agent.model_settings.reasoning.effort)
            for name, agent in scaffold.runtime.agents.items()} == {"pm": ("gpt-test", "low"),
                                                                     "reviewer": ("gpt-test", "low")}


def test_a_run_without_crux_model_is_a_config_error(drop_in_dir, assemble):
    with pytest.raises(InvalidDropInError, match="CRUX_MODEL is not set"):
        assemble(drop_in_dir, env={}).runtime.build()


@pytest.mark.parametrize("anchor,setting", [
    ("max_turns = 4", 'model = "gpt-other"'),
    ("max_turns = 4", 'reasoning_effort = "high"'),
    ('type = "scripted"', 'model = "gpt-other"'),
    ('type = "scripted"', 'reasoning_effort = "high"'),
    ('orchestrator = "pm"', '[runtime]\ntype = "openai-agents"\nmodel = "gpt-other"'),
    ('orchestrator = "pm"', '[runtime]\ntype = "openai-agents"\nreasoning_effort = "high"'),
])
def test_a_drop_in_cannot_set_a_model_or_effort(drop_in_dir, assemble, anchor, setting):
    if anchor.startswith("orchestrator"):
        (drop_in_dir / "scaffold.toml").write_text((drop_in_dir / "scaffold.toml").read_text() + "\n" + setting + "\n")
    else:
        edit(drop_in_dir, "scaffold.toml", anchor, f"{anchor}\n{setting}")
    with pytest.raises(InvalidDropInError, match="Extra inputs are not permitted"):
        assemble(drop_in_dir)


def test_mcp_server_gets_its_declared_env_but_not_the_scaffold_secrets(drop_in_dir, assemble, tmp_path, monkeypatch):
    add_fake_slack(drop_in_dir)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-scaffold-only")
    log = tmp_path / "slack.jsonl"
    pm = ScriptedModel(call("conversations_history", {"channel_id": "C123"}, "p1"),
                       call("conversations_add_message", {"channel_id": "C123", "payload": "Which pages?"}, "p2"),
                       say("asked"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env={"CRUX_MODEL": "unscripted", "FAKE_SLACK_LOG": str(log),
                                                   "SLACK_BOT_TOKEN": "xoxb-test"}).run())
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [entry["tool"] for entry in entries] == ["conversations_history", "conversations_add_message"]
    assert entries[0]["saw_openai_key"] is False
    assert entries[1]["payload"] == "Which pages?"
