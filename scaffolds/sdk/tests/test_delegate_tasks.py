"""Inbox waits observe all delegates without owning or cancelling their work."""
import asyncio

import pytest

from crux_scaffold.delegate_mailboxes import DelegateMailboxes
from crux_scaffold.delegate_tasks import DelegateTasks
from crux_scaffold.telemetry import NullTelemetry


@pytest.mark.parametrize('event', ['progress', 'question', 'completed', 'failed', 'interrupted'])
def test_inbox_wakes_for_any_delegate_while_another_remains_running(tmp_path, event):
    async def run():
        release = asyncio.Event()

        async def execute(task, prompt):
            if task['name'] == 'slow':
                await asyncio.Future()
            await release.wait()
            if event == 'failed':
                raise RuntimeError('experiment failed')
            return 'experiment complete'

        boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
        tasks = DelegateTasks(boxes, execute, NullTelemetry())
        slow = tasks.start('slow', 'Long experiment')
        other = tasks.start('other', 'Independent work')
        try:
            waiting = asyncio.create_task(tasks.inbox(wait_seconds=10))
            await asyncio.sleep(.02)
            assert not waiting.done()
            if event in {'progress', 'question'}:
                # A separate connection represents mail from a Codex MCP subprocess.
                mailbox = DelegateMailboxes(boxes.path).delegate(other['id'])
                message = (mailbox.ask_pi('Which control?') if event == 'question'
                           else mailbox.report_progress('Found a blocker'))
            elif event == 'interrupted':
                await tasks.stop(other['id'])
            else:
                release.set()
            inbox = await asyncio.wait_for(waiting, 1)
            assert not tasks.tasks[slow['id']].done()
            assert not boxes.get(slow['id'])['collected']
            if event in {'progress', 'question'}:
                assert inbox['messages'] == [message]
                assert inbox['results'] == []
            else:
                assert len(inbox['results']) == 1
                result = inbox['results'][0]
                assert result['id'] == other['id'] and result['status'] == event and result['collected']
                assert result['result']
                assert 'brief' not in result and 'usage' not in result
                assert boxes.get(other['id'])['result'] == result['result']
                # A result that was already collected must not release every later wait.
                again = asyncio.create_task(tasks.inbox(wait_seconds=10))
                await asyncio.sleep(.02)
                assert not again.done()
                again.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await again
        finally:
            await tasks.close()
    asyncio.run(run())


def test_inbox_collects_all_ready_results_and_resumed_tasks_can_finish_again(tmp_path):
    async def run():
        async def execute(task, prompt):
            messages = tasks.mailboxes.pending('delegate', task['id'])
            tasks.mailboxes.delivered([m['id'] for m in messages])
            return prompt

        tasks = DelegateTasks(DelegateMailboxes(tmp_path / 'delegates.sqlite'), execute, NullTelemetry())
        first = tasks.start('first', 'First result')
        second = tasks.start('second', 'Second result')
        await asyncio.gather(*tasks.tasks.values())
        inbox = await tasks.inbox()
        assert {r['result'] for r in inbox['results']} == {'First result', 'Second result'}
        assert all(t['collected'] for t in inbox['tasks'])
        assert (await tasks.inbox())['results'] == []
        await tasks.send(first['id'], 'Revise', reply_to=None, interrupt=False)
        await tasks.tasks[first['id']]
        resumed = (await tasks.inbox())['results']
        assert len(resumed) == 1 and resumed[0]['id'] == first['id']
        assert tasks.mailboxes.get(second['id'])['result'] == 'Second result'
    asyncio.run(run())


def test_inbox_timeout_and_cancellation_leave_delegates_running(tmp_path):
    async def run():
        async def execute(task, prompt):
            await asyncio.Future()

        tasks = DelegateTasks(DelegateMailboxes(tmp_path / 'delegates.sqlite'), execute, NullTelemetry())
        task = tasks.start('worker', 'Continue independently')
        try:
            for wait_seconds in [0, .02]:
                inbox = await asyncio.wait_for(tasks.inbox(wait_seconds), 1)
                assert inbox['messages'] == inbox['results'] == []
                assert inbox['tasks'][0]['status'] == 'running'
            waiting = asyncio.create_task(tasks.inbox(10))
            await asyncio.sleep(.02)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not tasks.tasks[task['id']].done()
            assert not tasks.mailboxes.get(task['id'])['collected']
        finally:
            await tasks.close()
    asyncio.run(run())


def test_idle_and_restart_recovery_return_without_waiting(tmp_path):
    async def run():
        async def execute(task, prompt):
            raise AssertionError('Recovery must not start a delegate')

        boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
        tasks = DelegateTasks(boxes, execute, NullTelemetry())
        assert await asyncio.wait_for(tasks.inbox(10), .5) == {'tasks': [], 'messages': [], 'results': []}
        task = boxes.start('worker', 'Original brief')
        boxes.recover()
        result = (await asyncio.wait_for(tasks.inbox(10), .5))['results'][0]
        assert result['id'] == task['id'] and result['status'] == 'interrupted'
        assert result['collected']
    asyncio.run(run())
