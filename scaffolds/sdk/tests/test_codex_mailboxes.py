"""Native Codex steering, conversation resumption and the restricted worker MCP transport."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_coding_agents import ANSWER, CODEX, FakeCodex, completed, item, usage

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.delegate_mailboxes import DelegateMailboxes
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


class ResumableCodex(FakeCodex):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.thread.id = 'native-thread'
        self.resumes = []
        self._client = SimpleNamespace(request=self.mailbox_status)

    async def mailbox_status(self, *args, **kwargs):
        return SimpleNamespace(data=[SimpleNamespace(name='crux_delegate', tools_error=None,
                                                      tools={'ask_pi': {}, 'report_progress': {}})])

    async def thread_resume(self, thread_id, **kwargs):
        self.resumes.append(thread_id)
        return self.thread


def context(tmp_path):
    return RunContext(Workspace(tmp_path), tmp_path / 'state',
                      {'CRUX_MODEL': 'test', 'CRUX_REASONING_EFFORT': 'high'}, UsageLedger(), Budget(), NullTelemetry())


def test_resume_uses_same_native_thread_without_double_counting(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'state/delegates.sqlite')
    task = boxes.start('engineer', 'Implement')
    first = ResumableCodex([usage(1200, 300, reasoning=40), item(ANSWER), completed()])
    second = ResumableCodex([usage(1400, 350, reasoning=50), item(ANSWER), completed()])
    clients = iter([first, second])
    agent = CODING_AGENTS.create('engineer', CODEX, client_factory=lambda ctx: next(clients))
    ctx = context(tmp_path)

    async def run():
        await agent.run('Implement', ctx, mailbox=boxes.delegate(task['id']))
        boxes.send(task['id'], 'Add a control')
        await agent.run('Continue', ctx, mailbox=boxes.delegate(task['id']))
    asyncio.run(run())
    assert len(first.starts) == 1 and not second.starts
    assert second.resumes == ['native-thread']
    assert 'Add a control' in second.thread.turns[0][0]
    assert second.thread.turns[0][1]['effort'] == 'high'
    assert ctx.usage.total_tokens == 1750
    assert ctx.usage.by_source['engineer'].reasoning_output_tokens == 50
    assert boxes.get(task['id'])['usage']['input_tokens'] == 1400
    assert not boxes.delegate(task['id']).pending()


def test_active_turn_receives_native_steer(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'state/delegates.sqlite')
    task = boxes.start('engineer', 'Implement')
    ctx = context(tmp_path)

    async def run():
        running, steered = asyncio.Event(), asyncio.Event()
        messages = []

        class Turn:
            async def stream(self):
                running.set()
                await steered.wait()
                yield SimpleNamespace(payload=completed())

            async def steer(self, message):
                messages.append(message)
                steered.set()

        fake = ResumableCodex([])
        async def turn(*args, **kwargs):
            return Turn()
        fake.thread.turn = turn
        agent = CODING_AGENTS.create('engineer', CODEX, client_factory=lambda ctx: fake)
        work = asyncio.create_task(agent.run('Implement', ctx, mailbox=boxes.delegate(task['id'])))
        await running.wait()
        boxes.send(task['id'], 'Report progress now', kind='request_progress')
        await asyncio.wait_for(work, 2)
        assert 'Report progress now' in messages[0]
        assert not boxes.delegate(task['id']).pending()
    asyncio.run(run())


def test_worker_mcp_exposes_only_bound_questions_and_progress(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    task = boxes.start('engineer', 'Implement')

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'crux_scaffold.delegate_mcp', str(boxes.path), task['id']],
                                       env={'PYTHONPATH': str(Path(__file__).parents[1])})
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            assert {t.name for t in (await session.list_tools()).tools} == {'ask_pi', 'report_progress'}
            answer = await session.call_tool('ask_pi', {'message': 'Which baseline?'})
            assert not answer.is_error
            question = json.loads(answer.content[0].text)
            assert question['task_id'] == task['id']
            answer = await session.call_tool('report_progress', {'message': 'Tests ready'})
            assert not answer.is_error
        assert len(boxes.pending('pi')) == 2
    asyncio.run(run())


def test_native_mailbox_subprocess_receives_package_path(tmp_path, monkeypatch):
    from crux_scaffold.coding_agents import codex_client
    seen = []
    monkeypatch.setattr('crux_scaffold.coding_agents.AsyncCodex', lambda config: seen.append(config))
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    task = boxes.start('worker', 'Work')
    codex_client(context(tmp_path), mailbox=boxes.delegate(task['id']))
    settings = dict(value.split('=', 1) for value in seen[0].config_overrides)
    assert json.loads(settings['mcp_servers.crux_delegate.env.PYTHONPATH']) == str(Path(__file__).parents[1])
    approvals = {key: json.loads(value) for key, value in settings.items() if key.endswith('.approval_mode')}
    assert approvals == {f'mcp_servers.crux_delegate.tools.{tool}.approval_mode': 'approve'
                         for tool in ('ask_pi', 'report_progress')}


def test_parallel_worker_homes_are_distinct_and_stable_on_resume(tmp_path, monkeypatch):
    from crux_scaffold.coding_agents import codex_client
    seen = []
    monkeypatch.setattr('crux_scaffold.coding_agents.AsyncCodex', lambda config: seen.append(config))
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    first, second = boxes.start('worker_a', 'Work'), boxes.start('worker_b', 'Work')
    ctx = context(tmp_path)
    for task in (first, second, first):
        codex_client(ctx, mailbox=DelegateMailboxes(boxes.path).delegate(task['id']))
    homes = [config.env['CODEX_HOME'] for config in seen]
    assert homes[0] == homes[2] == str(ctx.state_dir / 'codex' / first['id'])
    assert homes[1] == str(ctx.state_dir / 'codex' / second['id'])
    assert homes[0] != homes[1]


def test_broken_mailbox_fails_before_any_worker_inference(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    task = boxes.start('worker', 'Work')
    fake = ResumableCodex([])
    async def unavailable(*args, **kwargs):
        return SimpleNamespace(data=[])
    fake._client.request = unavailable
    agent = CODING_AGENTS.create('worker', CODEX, client_factory=lambda ctx: fake)
    import pytest
    with pytest.raises(RuntimeError, match='mailbox tools unavailable'):
        asyncio.run(agent.run('Work', context(tmp_path), mailbox=boxes.delegate(task['id'])))
    assert fake.thread.turns == []
    assert boxes.get(task['id'])['conversation_id'] is None
