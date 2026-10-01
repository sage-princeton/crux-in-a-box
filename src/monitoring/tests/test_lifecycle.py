import time

import boto3
import pytest
from moto import mock_aws

from lifecycle import Conflict, IncidentStore, evidence_anchors, public_incident


@pytest.fixture
def store():
    with mock_aws():
        table = boto3.resource('dynamodb', region_name='us-east-1').create_table(
            TableName='incidents', BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in ('pk', 'sk')])
        yield IncidentStore(table)


def report(text='Private evidence', anchor='observation:123'):
    return {'summary': 'Review complete', 'review_status': 'completed', 'coverage_gaps': [],
            'findings': [{'detector_id': 'unexpected_upload', 'anchor_id': anchor,
                          'category': 'Private title', 'evidence': text, 'source_ids': [anchor],
                          'severity': 'low', 'confidence': 'medium'}]}


def ingest(store, window=300, text='Private evidence', anchor='observation:123'):
    store.ingest(report(text, anchor), f'REVIEW#i-test#{window}', 'reviews/test',
                 {'slug': 'test-workload'}, {'prompt_sha256': 'prompt-1', 'reported_model': 'model-1'}, {anchor: anchor})
    return list(store.all('INCIDENTS'))[0]


def test_close_reopen_repeated_review_preserves_id_state_and_history(store):
    item = ingest(store)
    incident_id = item['id']
    actor = {'id': 'operator@example.test', 'issuer': 'test-idp'}
    closed = store.transition(incident_id, item['version'], 'closed', actor, 'Private resolution')
    ingest(store, 600, 'Completely rephrased explanation')
    repeated = store.incident(incident_id)
    assert repeated['status'] == 'closed' and repeated['review_count'] == 2
    assert len(list(store.all('INCIDENTS'))) == 1
    with pytest.raises(Conflict):
        store.transition(incident_id, closed['version'], 'open', actor)
    reopened = store.transition(incident_id, repeated['version'], 'open', actor)
    assert reopened['status'] == 'open' and reopened['id'] == incident_id
    events, _ = store.page('HISTORY#' + incident_id, prefix='EVENT#')
    assert [e['status'] for e in events] == ['closed', 'open']
    assert events[0]['note'] == 'Private resolution' and events[0]['actor'] == actor
    # Retrying the same review is not another observation, even if the reviewer wording changes.
    ingest(store, 600, 'Retry text')
    assert store.incident(incident_id)['review_count'] == 2
    observations, _ = store.page('HISTORY#' + incident_id, prefix='OBS#')
    assert len(observations) == 2
    assert all(o['provenance']['prompt_sha256'] == 'prompt-1' for o in observations)
    assert store.get('REVIEW#REVIEW#i-test#600', 'STATE')['window_start'] == -1200
    assert 'Private' not in str(public_incident(reopened))


def test_separate_source_event_creates_new_incident_and_reopening_does_not(store):
    item = ingest(store)
    ingest(store, 600, anchor='observation:456')
    fleet = [{'instance_id': 'i-test', 'last_updated': 600}]
    assert store.summaries(fleet)[0]['total_incident_count'] == 2
    store.transition(item['id'], item['version'], 'closed', {'id': 'operator'})
    assert store.summaries(fleet)[0]['incident_count'] == 1
    item = store.incident(item['id'])
    store.transition(item['id'], item['version'], 'open', {'id': 'operator'})
    assert store.summaries(fleet)[0]['total_incident_count'] == 2


def test_anchors_are_derived_from_source_events_not_window_or_prose():
    sources = [{'id': 'observation:1', 'kind': 'langfuse', 'data': {'text': 'anything'}},
               {'id': 'file:/exports/activity', 'kind': 'sftp', 'mtime': 100, 'data': 'upload'},
               {'id': 'logs:group', 'kind': 'cloudwatch', 'data': [{'eventId': 'event-1', 'message': 'upload'}]}]
    before = evidence_anchors(sources)
    sources[1]['mtime'] = 200
    assert evidence_anchors(sources) == before
    assert before['event:event-1'] == 'logs:group'
    sources[1]['data'] = 'different event'
    assert evidence_anchors(sources) != before


def test_expired_sessions_and_private_rows_never_enter_incident_listing(store):
    ingest(store)
    store.put_once({'pk': 'SESSION#private', 'sk': 'STATE', 'expires_at': int(time.time()) - 1})
    rows, _ = store.page('INCIDENTS')
    assert len(rows) == 1 and rows[0]['pk'] == 'INCIDENTS'
