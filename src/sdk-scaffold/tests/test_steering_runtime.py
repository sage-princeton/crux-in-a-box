import asyncio
import csv
import io
import json

import pytest
from agents.run_config import CallModelData, ModelInputData
from test_steering import Recorder

from crux_scaffold.context_strategies import is_standing_context
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.tools import RestArguments

from drop_ins import add_fake_slack, edit
from scripted import ScriptedModel, call, say, tool_outputs


def inbox(path, *messages):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["MsgID", "UserID", "Text", "ThreadTs", "Cursor"])
    for ts, text in messages:
        writer.writerow([ts, "U_OP", text, "", ""])
    path.write_text(output.getvalue())


def configured(root, tmp_path, *, extra=""):
    add_fake_slack(root)
    # The runtime must start the steering server even when the model has no Slack tools.
    edit(root, "scaffold.toml", '\nmcp_servers = ["slack"]', '')
    edit(root, "scaffold.toml", 'tools = ["read_file", "write_file", "rest"]',
         'tools = ["read_file", "write_file", "rest", "escalate"]')
    edit(root, "scaffold.toml", 'env = { FAKE_SLACK_LOG =',
         'env = { FAKE_SLACK_INBOX = "${FAKE_SLACK_INBOX}", FAKE_SLACK_LOG =')
    with (root / "scaffold.toml").open("a") as config:
        config.write('''
[runtime]
type = "openai-agents"

[runtime.steering]
server = "slack"
channel_id = "C123"
operator_ids = ["U_OP"]
after_ts = "1.000000"
poll_interval_seconds = 2
''' + extra)
    source = tmp_path / "inbox.csv"
    inbox(source)
    return {"CRUX_MODEL": "unscripted", "FAKE_SLACK_LOG": str(tmp_path / "slack.jsonl"),
            "SLACK_BOT_TOKEN": "fake", "FAKE_SLACK_INBOX": str(source)}


def messages(items):
    return [json.loads(item["content"]) for item in items if isinstance(item, dict)
            and item.get("role") == "user" and str(item.get("content", "")).startswith('{"kind": "operator_steering"')]


@pytest.mark.parametrize("context", ["persistent", "trim_recent"])
def test_no_model_polling_is_needed_and_delivery_survives_restart(drop_in_dir, assemble, tmp_path, context):
    env = configured(drop_in_dir, tmp_path)
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         f'orchestrator = "pm"\n\n[context]\ntype = "{context}"')
    inbox(tmp_path / "inbox.csv", ("2.000000", "Use the restored dataset"))
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "read"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    asyncio.run(scaffold.run())
    for items in pm.inputs:
        assert [m["text"] for m in messages(items)] == ["Use the restored dataset"]
    assert "precedence" in pm.instructions[0]
    assert all(sum(is_standing_context(item) for item in items) == 1 for items in pm.inputs)
    assert all(str(items).count("<resource_status>") == 1 for items in pm.inputs)
    assert "total tokens: 0" in pm.inputs[0][-1]["content"]
    assert "total tokens: 15" in pm.inputs[1][-1]["content"]
    assert "U_OP" == messages(pm.inputs[0])[0]["source"]
    assert scaffold.context.steering.pending() == []

    async def resumed():
        pm2 = ScriptedModel(say("resumed"))
        second = assemble(drop_in_dir, {"pm": pm2}, env=env)
        async with second.runtime:
            await second.runtime.run("Continue", workflow="resume")
            history = await second.runtime.session.get_items()
        assert len(messages(history)) == 1
        assert "<resource_status>" not in str(history)
        assert len(messages(pm2.inputs[0])) == 1
        assert sum(is_standing_context(item) for item in pm2.inputs[0]) == 1
    asyncio.run(resumed())


def test_rest_wakes_for_steering_and_charges_only_waited_time(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    pm = ScriptedModel(call("rest", {"seconds": 30, "reason": "operator reply"}, "rest"), say("awake"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        inbox(tmp_path / "inbox.csv", ("2.000000", "Proceed now"))
    scaffold.context.sleep = sleep
    asyncio.run(scaffold.run())
    assert sleeps == [2]
    assert scaffold.runtime.assembly.tools["rest"].used_seconds == 2
    assert "steering" in tool_outputs(pm.inputs[1])[0]
    assert [m["text"] for m in messages(pm.inputs[1])] == ["Proceed now"]


def test_pending_steering_wakes_rest_without_sleep(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Proceed"))
    scaffold = assemble(drop_in_dir, env=env)
    async def check():
        async with scaffold.runtime:
            result = await scaffold.runtime.assembly.tools["rest"].invoke(
                scaffold.context, RestArguments(seconds=30, reason="reply"))
            assert "Rested 0s" in result
    asyncio.run(check())


def test_escalation_reply_arrives_while_agent_continues_work(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    pm = ScriptedModel(call("escalate", {"request_id": "data-down", "failure": "Dataset offline",
                                         "diagnosis": "Connection refused", "requested_action": "Restore it",
                                         "meanwhile": "Write offline tests"}, "escalate"),
                       call("write_file", {"path": "offline.md", "content": "Independent work"}, "write"),
                       say("response received"))
    telemetry = Recorder()
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env, telemetry=telemetry)
    original = scaffold.context.workspace.write_file
    def write(path, content):
        result = original(path, content)
        inbox(tmp_path / "inbox.csv", ("2.000000", "Dataset restored; continue"))
        return result
    scaffold.context.workspace.write_file = write
    asyncio.run(scaffold.run())
    assert "posted" in tool_outputs(pm.inputs[1])[0]
    assert [m["text"] for m in messages(pm.inputs[2])] == ["Dataset restored; continue"]
    assert (drop_in_dir / "workspace/offline.md").read_text() == "Independent work"
    posts = [json.loads(line) for line in (tmp_path / "slack.jsonl").read_text().splitlines()
             if '"tool": "conversations_add_message"' in line]
    assert len(posts) == 1 and "Work continuing meanwhile" in posts[0]["payload"]
    assert {"steering:escalation", "steering:escalation_outcome", "steering:received", "steering:delivered"} <= {
        event[0] for event in telemetry.events}
    assert "Dataset offline" not in json.dumps(telemetry.events)
    assert "Dataset restored" not in json.dumps(telemetry.events)


def test_conversation_write_before_crash_is_reconciled_without_duplicate(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Only once"))
    first = assemble(drop_in_dir, env=env)
    async def interrupted():
        async with first.runtime:
            steering = first.context.steering
            await steering.poll()
            await first.runtime.session.add_items([steering.envelope(steering.pending()[0])])
            # Simulates a crash before the inbox's delivery marker was committed.
    asyncio.run(interrupted())
    pm = ScriptedModel(say("recovered"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=env).run())
    assert len(messages(pm.inputs[0])) == 1


def test_transport_failure_is_visible_to_the_model(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    (tmp_path / "inbox.csv").write_text("malformed response")
    pm = ScriptedModel(say("working offline"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=env).run())
    assert "read_failed" in pm.inputs[0][-1]["content"]
    assert "check MCP credentials" in pm.inputs[0][-1]["content"]
    assert "Steering transport status" not in pm.instructions[0]


def test_transport_status_changes_only_the_trailing_context(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    scaffold = assemble(drop_in_dir, env=env)
    scaffold.runtime.build()
    steering = scaffold.context.steering
    statuses = iter(["read_failed: check MCP credentials", "ok"])

    async def poll():
        steering.state["status"] = next(statuses)

    steering.poll = poll
    history = [{"role": "user", "content": "question"}]
    data = CallModelData(ModelInputData(input=history, instructions="fixed"),
                         scaffold.runtime.agents["pm"], scaffold.context)

    async def check():
        failed = await scaffold.runtime._select_input(data)
        recovered = await scaffold.runtime._select_input(data)
        assert failed.instructions == recovered.instructions == "fixed"
        assert failed.input[:-1] == recovered.input[:-1] == history
        assert failed.input[-1]["role"] == recovered.input[-1]["role"] == "user"
        assert "read_failed" in failed.input[-1]["content"]
        assert "Steering transport status" not in recovered.input[-1]["content"]
        assert "<resource_status>" in recovered.input[-1]["content"]

    asyncio.run(check())


@pytest.mark.parametrize("change", ["undefined_server", "missing_policy", "bad_policy"])
def test_invalid_steering_fails_before_any_model_call(drop_in_dir, assemble, tmp_path, change):
    env = configured(drop_in_dir, tmp_path)
    if change == "undefined_server":
        edit(drop_in_dir, "scaffold.toml", 'server = "slack"', 'server = "unknown"')
    elif change == "missing_policy":
        edit(drop_in_dir, "scaffold.toml", '[runtime.steering]', '[unused]')
    else:
        edit(drop_in_dir, "scaffold.toml", 'poll_interval_seconds = 2', 'poll_interval_seconds = 0')
    with pytest.raises(InvalidDropInError):
        assemble(drop_in_dir, env=env)


def test_messages_arriving_during_a_coding_call_wait_for_its_return(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    pm = ScriptedModel(call("engineer", {"brief": "independent task"}, "code"), say("adjusted plan"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    engineer = scaffold.coding_agents["engineer"]
    execute = engineer.execute
    finished = []

    async def long_call(brief, ctx):
        inbox(tmp_path / "inbox.csv", ("2.000000", "Change the next task"))
        await asyncio.sleep(0)
        assert scaffold.context.steering.pending() == []
        result = await execute(brief, ctx)
        finished.append(True)
        return result
    engineer.execute = long_call
    asyncio.run(scaffold.run())
    assert finished == [True]
    assert [m["text"] for m in messages(pm.inputs[1])] == ["Change the next task"]


def test_model_failure_keeps_a_single_durable_instruction_for_retry(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Retry safely"))
    with pytest.raises(AssertionError, match="ran out"):
        asyncio.run(assemble(drop_in_dir, {"pm": ScriptedModel()}, env=env).run())
    pm = ScriptedModel(say("retried"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=env).run())
    assert len(messages(pm.inputs[0])) == 1


def test_session_failure_leaves_delivery_pending_and_reports_recovery(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Keep this pending"))
    pm = ScriptedModel(say("must not be called"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)

    async def check():
        async with scaffold.runtime:
            original = scaffold.runtime.session.add_items
            async def failed_write(items):
                if messages(items):
                    raise OSError("private transport information")
                await original(items)
            scaffold.runtime.session.add_items = failed_write
            with pytest.raises(InvalidDropInError, match="delivery_failed"):
                await scaffold.runtime.run("continue", workflow="check")
        assert scaffold.context.steering.pending()[0]["text"] == "Keep this pending"
        assert "private transport" not in scaffold.context.steering.path.read_text()
    asyncio.run(check())
    assert not pm.inputs


def test_steering_does_not_enter_an_isolated_delegates_input(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Operator context for PM"))
    pm = ScriptedModel(call("reviewer", {"input": "Review only this brief"}, "review"), say("reviewed"))
    reviewer = ScriptedModel(say("looks fine"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer}, env=env).run())
    assert not messages(reviewer.inputs[0])
    assert "Operator context for PM" not in str(reviewer.inputs[0])


def test_escalate_without_steering_is_a_configuration_error(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file", "write_file", "rest"]', 'tools = ["escalate"]')
    with pytest.raises(InvalidDropInError, match="escalate requires runtime.steering"):
        assemble(drop_in_dir)


@pytest.mark.parametrize("background", [False, True])
def test_missing_transport_tool_fails_before_model_and_records_actionable_status(
        drop_in_dir, assemble, tmp_path, background):
    env = configured(drop_in_dir, tmp_path)
    edit(drop_in_dir, "scaffold.toml", 'type = "openai-agents"',
         f'type = "openai-agents"\nbackground_coding = {str(background).lower()}')
    pm = ScriptedModel(say("must not be called"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    scaffold.runtime.build()
    server = scaffold.runtime.servers["slack"]
    original = server.list_tools
    async def missing_history(*args, **kwargs):
        return [tool for tool in await original(*args, **kwargs) if tool.name != "conversations_history"]
    server.list_tools = missing_history
    with pytest.raises(InvalidDropInError, match="missing tools: conversations_history"):
        asyncio.run(scaffold.run())
    assert not pm.inputs
    assert "startup_failed" in scaffold.context.steering.path.read_text()
    assert server.session is None


def test_phase_interval_wakes_for_steering(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    edit(drop_in_dir, "scaffold.toml", 'continue_prompt = "prompts/continue.md"',
         'continue_prompt = "prompts/continue.md"\ngates = ["ready"]\nmax_iterations = 2\ninterval_seconds = 30')
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('\n[gates.ready]\ntype = "command"\ncommand = "test -f ready"\n')
    pm = ScriptedModel(say("waiting"), say("resumed"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        inbox(tmp_path / "inbox.csv", ("2.000000", "Ready; continue"))
        (drop_in_dir / "workspace/ready").touch()
    scaffold.context.sleep = sleep
    assert asyncio.run(scaffold.run()).status == "completed"
    assert sleeps == [2]
    assert [m["text"] for m in messages(pm.inputs[1])] == ["Ready; continue"]


def test_new_steering_survives_context_selection_during_a_turn(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    inbox(tmp_path / "inbox.csv", ("2.000000", "Preserve new steering"))
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "read"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    scaffold.strategy.select = lambda items: items[-2:]
    asyncio.run(scaffold.run())
    assert all(len(messages(items)) == 1 for items in pm.inputs)


def test_fixed_thread_steering_uses_replies_without_exposing_slack_tools(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path, extra='thread_ts = "1.000000"\n')
    inbox(tmp_path / "inbox.csv", ("2.000000", "Thread response"))
    pm = ScriptedModel(say("received"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=env).run())
    assert messages(pm.inputs[0])[0]["thread"] == "1.000000"
    calls = [json.loads(line) for line in (tmp_path / "slack.jsonl").read_text().splitlines()]
    assert calls[0]["tool"] == "conversations_replies"
    assert calls[0]["thread_ts"] == "1.000000"


def test_steering_arrives_while_a_background_coding_task_keeps_working(drop_in_dir, assemble, tmp_path):
    env = configured(drop_in_dir, tmp_path)
    edit(drop_in_dir, "scaffold.toml", 'type = "openai-agents"',
         'type = "openai-agents"\nbackground_coding = true')
    pm = ScriptedModel(call("engineer", {"brief": "independent task"}, "code"),
                       call("write_file", {"path": "ack.txt", "content": "Operator reply received"}, "ack"),
                       call("engineer_result", {"wait": True}, "result"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=env)
    engineer = scaffold.coding_agents["engineer"]
    execute, write = engineer.execute, scaffold.context.workspace.write_file
    release, finished = asyncio.Event(), []

    async def long_call(brief, ctx):
        inbox(tmp_path / "inbox.csv", ("2.000000", "Continue the independent task"))
        await release.wait()
        result = await execute(brief, ctx)
        finished.append(True)
        return result

    def acknowledge(path, content):
        if path == "ack.txt":
            assert not finished
            release.set()
        return write(path, content)

    engineer.execute = long_call
    scaffold.context.workspace.write_file = acknowledge
    asyncio.run(scaffold.run())
    assert [m["text"] for m in messages(pm.inputs[1])] == ["Continue the independent task"]
    assert finished == [True]
    assert not scaffold.runtime._coding_tasks
