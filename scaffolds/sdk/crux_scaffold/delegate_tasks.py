"""Async delegate lifecycle, independent of the SDK that executes a conversation."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from crux_scaffold.delegate_mailboxes import DelegateMailboxes
from crux_scaffold.telemetry import Telemetry

# TODO(AE-330): Document all classes and methods with reStructuredText, including raised exceptions.


class DelegateTasks:
    """Own running tasks; the mailbox store owns durable identity, results and messages."""

    def __init__(self, mailboxes: DelegateMailboxes, execute: Callable[[dict, str], Awaitable[str]],
                 telemetry: Telemetry) -> None:
        self.mailboxes, self.execute, self.telemetry = mailboxes, execute, telemetry
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, name: str, brief: str) -> dict:
        """Start a new brief without blocking its caller."""
        task = self.mailboxes.start(name, brief)
        self._launch(task, brief)
        return self._view(task)

    @staticmethod
    def _view(task: dict) -> dict:
        """Expose work state without leaking conversation-storage or accounting internals into tool responses."""
        keys = ('id', 'name', 'status', 'started_at', 'updated_at', 'collected', 'result')
        return {key: task[key] for key in keys}

    def _launch(self, task: dict, prompt: str) -> None:
        async def run():
            try:
                next_prompt = prompt
                while True:
                    result = await self.execute(task, next_prompt)
                    if not self.mailboxes.pending('delegate', task['id']):
                        break
                    # Mail arriving during the last model call still belongs to this conversation.
                    next_prompt = 'Continue using the PI messages that arrived as your last turn ended.'
            except asyncio.CancelledError:
                self.mailboxes.update(task['id'], status='interrupted', result='Interrupted; partial work may exist.')
                raise
            except Exception as exc:  # noqa: BLE001 - Delegate failure is a result the PI can act on.
                self.mailboxes.update(task['id'], status='failed', result=str(exc))
            else:
                self.mailboxes.update(task['id'], status='completed', result=result)
            finally:
                self.flush_events()
        self.mailboxes.update(task['id'], status='running', collected=False, result=None)
        self.tasks[task['id']] = asyncio.create_task(run())
        self.flush_events()

    def _current(self, task_id: str) -> dict:
        task = self.mailboxes.get(task_id)
        if self.mailboxes.latest(task['name'])['id'] != task_id:
            raise ValueError('This task has been superseded by a new brief')
        return task

    async def stop(self, task_id: str) -> dict:
        """Wait for delegate cancellation/cleanup before reporting interruption."""
        self._current(task_id)
        task = self.tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its coroutine started cannot run its finally block.
            if self.mailboxes.get(task_id)['status'] == 'running':
                self.mailboxes.update(task_id, status='interrupted', result='Interrupted before execution.')
        self.flush_events()
        return self._view(self.mailboxes.get(task_id))

    async def send(self, task_id: str, message: str, *, reply_to: str | None, interrupt: bool) -> dict:
        """Steer an active conversation, or explicitly resume that same conversation if it ended."""
        task = self._current(task_id)
        receipt = self.mailboxes.send(task_id, message, reply_to=reply_to)
        if interrupt:
            await self.stop(task_id)
        active = self.tasks.get(task_id)
        if not active or active.done():
            self._launch(task, 'Continue the assigned brief using the new PI messages.')
        self.flush_events()
        return receipt

    def request_progress(self, task_id: str) -> dict:
        """Solicit a fresh report from active work; ended work returns its final state without restarting it."""
        task = self._current(task_id)
        if task['status'] != 'running':
            return self._view(task)
        receipt = self.mailboxes.send(task_id, 'Report your latest progress using report_progress: work completed, '
                                      'evidence/artifacts, blockers, and next step. Then continue the task.',
                                      kind='request_progress')
        self.flush_events()
        return receipt

    async def result(self, name: str, *, wait: bool, timeout: float | None) -> dict:
        """Collect a finished result; a question releases a PI-selected wait so the PI can answer."""
        task = self.mailboxes.latest(name)
        if task is None:
            raise ValueError(f'No task for {name}')
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        while wait and task['status'] == 'running':
            if any(t['id'] == task['id'] and t['questions'] for t in self.mailboxes.summary()):
                break
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(.1)
            task = self.mailboxes.get(task['id'])
        if task['status'] != 'running':
            task = self.mailboxes.update(task['id'], collected=True)
        self.flush_events()
        return self._view(task)

    async def inbox(self, wait_seconds: float = 0) -> dict:
        """Wait only when the PI requests it. No upward message otherwise schedules PI work."""
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while not self.mailboxes.pending('pi') and asyncio.get_running_loop().time() < deadline:
            if not any(not task.done() for task in self.tasks.values()):
                break
            await asyncio.sleep(.1)
        self.flush_events()
        return {'tasks': self.status(), 'messages': self.mailboxes.pending('pi')}

    def status(self) -> list[dict]:
        """Small durable context: identity, lifecycle, progress and unanswered questions, not entire briefs."""
        keys = ('id', 'name', 'status', 'started_at', 'updated_at', 'collected', 'progress', 'questions')
        return [{key: task[key] for key in keys} for task in self.mailboxes.summary()]

    async def close(self) -> None:
        """Stop all active delegates before shutting down their transports."""
        await asyncio.gather(*(self.stop(key) for key, task in self.tasks.items() if not task.done()))
        self.flush_events()

    def flush_events(self) -> None:
        """Export durable events written by either the runtime or a worker's MCP subprocess."""
        events = self.mailboxes.events()
        for event in events:
            self.telemetry.record(f"delegate:{event['kind']}", 'span', None, event)
        self.mailboxes.traced([event['id'] for event in events])
