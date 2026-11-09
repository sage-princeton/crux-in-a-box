"""Durable delegate conversations, with PI control and a deliberately smaller delegate endpoint.

This is runtime state, not a tool registry or a transport. SQLite lets the runtime and a Codex MCP subprocess
share mail without sharing an event loop. No message starts or interrupts a PI turn.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# TODO(AE-321): Document all classes and methods with reStructuredText, including raised exceptions.


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DelegateMailboxes:
    """Own task identity, routing, reply correlation and durable delivery/trace receipts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, name TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS messages
                    (id TEXT PRIMARY KEY, task_id TEXT, recipient TEXT, data TEXT, delivered TEXT);
                CREATE TABLE IF NOT EXISTS events
                    (id TEXT PRIMARY KEY, kind TEXT, data TEXT, traced INTEGER DEFAULT 0);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # TODO(AE-322): Define structured enums for permissible message and event kinds.
    def _event(self, db, kind: str, data: dict) -> None:
        db.execute('INSERT INTO events (id, kind, data) VALUES (?, ?, ?)',
                   (uuid4().hex, kind, json.dumps({**data, 'at': _now()})))

    def start(self, name: str, brief: str) -> dict:
        """Reserve one outstanding conversation per delegate; a new brief always gets a new identity."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM tasks WHERE name=? ORDER BY rowid DESC LIMIT 1', (name,)).fetchone()
            if existing and not json.loads(existing['data'])['collected']:
                raise ValueError(f"Delegate '{name}' has an outstanding task; collect its result or resume it.")
            task = {'id': uuid4().hex, 'name': name, 'brief': brief, 'status': 'running', 'started_at': _now(),
                        'updated_at': _now(), 'collected': False, 'result': None, 'conversation_id': None, 'usage': {}}
            db.execute('INSERT INTO tasks VALUES (?, ?, ?)', (task['id'], name, json.dumps(task)))
            self._event(db, 'started', task)
            return task

    def get(self, task_id: str) -> dict:
        """Read a task, including its persisted conversation and accounted usage."""
        with self._connect() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError(f'Unknown delegate task {task_id}')
        return json.loads(row['data'])

    def latest(self, name: str) -> dict | None:
        """Return the most recent brief for a named delegate."""
        with self._connect() as db:
            row = db.execute('SELECT data FROM tasks WHERE name=? ORDER BY rowid DESC LIMIT 1', (name,)).fetchone()
        return json.loads(row['data']) if row else None

    # TODO(AE-325): Separate lifecycle updates from conversation bookkeeping methods.
    def update(self, task_id: str, **changes) -> dict:
        """Update lifecycle or conversation bookkeeping atomically; emit lifecycle changes for tracing."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                raise ValueError(f'Unknown delegate task {task_id}')
            task = {**json.loads(row['data']), **changes, 'updated_at': _now()}
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task), task_id))
            if 'status' in changes:
                self._event(db, task['status'], task)
            return task

    def recover(self) -> None:
        """A restarted runtime owns no old asyncio tasks. Keep their mail and explicit resumable identity."""
        for task in self.summary():
            if task['status'] == 'running':
                self.update(task['id'], status='interrupted', result='Runtime restarted; explicitly resume or collect.')

    def send(self, task_id: str, text: str, *, reply_to: str | None = None, kind: str = 'steer') -> dict:
        """PI-to-delegate mail; replies must identify a question from this same conversation."""
        return self._send(task_id, 'delegate', kind, text, reply_to)

    def _send(self, task_id: str, recipient: str, kind: str, text: str, reply_to: str | None = None) -> dict:
        task = self.get(task_id)
        if not text.strip():
            raise ValueError('Message must not be empty')
        with self._connect() as db:
            if reply_to is not None:
                row = db.execute('SELECT data FROM messages WHERE id=? AND task_id=?', (reply_to, task_id)).fetchone()
                if row is None or json.loads(row['data'])['kind'] != 'question':
                    raise ValueError('reply_to must identify a question from this task')
            # TODO(AE-324): Use a message schema with constructors and get/set helpers.
            message = {'id': uuid4().hex, 'task_id': task_id, 'delegate': task['name'], 'recipient': recipient,
                           'kind': kind, 'text': text, 'reply_to': reply_to, 'created_at': _now()}
            db.execute('INSERT INTO messages VALUES (?, ?, ?, ?, NULL)',
                       (message['id'], task_id, recipient, json.dumps(message)))
            self._event(db, 'sent', message)
        return message

    def pending(self, recipient: str, task_id: str | None = None) -> list[dict]:
        """Read queued messages without acknowledging them."""
        with self._connect() as db:
            rows = db.execute('SELECT data FROM messages WHERE recipient=? AND delivered IS NULL '
                              'AND (? IS NULL OR task_id=?) ORDER BY rowid', (recipient, task_id, task_id)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def delivered(self, message_ids: list[str]) -> None:
        """Record handoff to a model input or native steer API, not proof that a model acted on it."""
        with self._connect() as db:
            for message_id in message_ids:
                row = db.execute('SELECT data FROM messages WHERE id=? AND delivered IS NULL', (message_id,)).fetchone()
                if row:
                    db.execute('UPDATE messages SET delivered=? WHERE id=?', (_now(), message_id))
                    self._event(db, 'delivered', json.loads(row['data']))

    def summary(self) -> list[dict]:
        """Latest task per delegate, latest progress and unresolved questions survive history compaction."""
        with self._connect() as db:
            tasks = [json.loads(row['data']) for row in db.execute(
                'SELECT data FROM tasks WHERE rowid IN (SELECT MAX(rowid) FROM tasks GROUP BY name)')]
            for task in tasks:
                messages = [json.loads(row['data']) for row in db.execute(
                    'SELECT data FROM messages WHERE task_id=? ORDER BY rowid', (task['id'],))]
                replied = {m['reply_to'] for m in messages if m['reply_to']}
                task['questions'] = [m for m in messages if m['kind'] == 'question' and m['id'] not in replied]
                task['progress'] = next((m for m in reversed(messages) if m['kind'] == 'progress'), None)
        return tasks

    def events(self) -> list[dict]:
        """Unexported lifecycle/mail events, including ones written by a separate MCP process."""
        with self._connect() as db:
            return [{'id': row['id'], 'kind': row['kind'], 'data': json.loads(row['data'])} for row in
                    db.execute('SELECT * FROM events WHERE traced=0 ORDER BY rowid')]

    def traced(self, event_ids: list[str]) -> None:
        """Acknowledge telemetry export; event IDs allow consumers to deduplicate retries."""
        with self._connect() as db:
            db.executemany('UPDATE events SET traced=1 WHERE id=?', [(key,) for key in event_ids])

    def delegate(self, task_id: str) -> DelegateMailbox:
        """Bind a delegate endpoint to its task; it cannot choose recipients or control the PI."""
        self.get(task_id)
        return DelegateMailbox(self, task_id)


class DelegateMailbox:
    """Delegate-side capabilities only: questions, progress and incoming PI messages."""

    # TODO(AE-323): Accept the orchestrator name instead of hard-coding PI routing.
    def __init__(self, store: DelegateMailboxes, task_id: str) -> None:
        self.store, self.task_id = store, task_id

    def ask_pi(self, question: str) -> dict:
        """Queue a question for an inbox wait or normal PI boundary, without interrupting active work."""
        return self.store._send(self.task_id, 'pi', 'question', question)

    def report_progress(self, progress: str) -> dict:
        """Report work, artifacts and blockers; this does not claim task completion."""
        return self.store._send(self.task_id, 'pi', 'progress', progress)

    def pending(self) -> list[dict]:
        """Read incoming PI instructions for this conversation only."""
        return self.store.pending('delegate', self.task_id)
