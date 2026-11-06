"""Durable routing and task identity, independent of any agent SDK."""
import pytest

from crux_scaffold.delegate_mailboxes import DelegateMailboxes


def test_mail_survives_restart_and_reply_is_correlated(tmp_path):
    path = tmp_path / 'delegates.sqlite'
    boxes = DelegateMailboxes(path)
    task = boxes.start('researcher', 'Find evidence')
    delegate = boxes.delegate(task['id'])
    question = delegate.ask_pi('Which baseline?')
    progress = delegate.report_progress('Read three papers; blocked on baseline')
    reopened = DelegateMailboxes(path)
    assert [m['id'] for m in reopened.pending('pi')] == [question['id'], progress['id']]
    reopened.delivered([question['id'], progress['id']])
    assert reopened.pending('pi') == []
    assert reopened.summary()[0]['questions'][0]['id'] == question['id']
    reply = reopened.send(task['id'], 'Use v3', reply_to=question['id'])
    assert reply['reply_to'] == question['id']
    assert reopened.summary()[0]['questions'] == []
    assert delegate.pending() == [reply]
    assert reopened.summary()[0]['progress']['text'].startswith('Read three')


def test_task_ids_prevent_cross_task_replies_and_overlap(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    first = boxes.start('worker', 'One')
    with pytest.raises(ValueError, match='outstanding'):
        boxes.start('worker', 'Two')
    question = boxes.delegate(first['id']).ask_pi('Proceed?')
    boxes.update(first['id'], status='completed', result='done', collected=True)
    second = boxes.start('worker', 'Two')
    assert first['id'] != second['id']
    with pytest.raises(ValueError, match='question'):
        boxes.send(second['id'], 'yes', reply_to=question['id'])
    with pytest.raises(ValueError, match='Unknown'):
        boxes.send('missing', 'hello')


def test_restart_marks_active_work_interrupted_without_erasing_history(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    task = boxes.start('worker', 'One')
    boxes.update(task['id'], conversation_id='native-thread', usage={'input_tokens': 100})
    boxes.send(task['id'], 'Please inspect the tests')
    boxes.recover()
    recovered = boxes.get(task['id'])
    assert recovered['status'] == 'interrupted'
    assert recovered['conversation_id'] == 'native-thread'
    assert recovered['usage']['input_tokens'] == 100
    assert boxes.delegate(task['id']).pending()[0]['text'] == 'Please inspect the tests'
    assert not hasattr(boxes.delegate(task['id']), 'interrupt')
    assert not hasattr(boxes.delegate(task['id']), 'send')


def test_events_have_stable_ids_and_delivery_is_idempotent(tmp_path):
    boxes = DelegateMailboxes(tmp_path / 'delegates.sqlite')
    task = boxes.start('worker', 'One')
    msg = boxes.delegate(task['id']).report_progress('Writing tests')
    boxes.delivered([msg['id']])
    boxes.delivered([msg['id']])
    events = boxes.events()
    assert len({event['id'] for event in events}) == len(events)
    assert [e['kind'] for e in events].count('delivered') == 1
    boxes.traced([e['id'] for e in events])
    assert DelegateMailboxes(boxes.path).events() == []
