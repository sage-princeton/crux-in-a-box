from types import SimpleNamespace

import boto3

from incidents import IncidentLog
from migrate_incidents import migrate
from test_lifecycle import store
from worker import State


def test_historical_import_is_idempotent_private_and_preserves_manual_closure(store):
    old_table = boto3.resource('dynamodb', region_name='us-east-1').create_table(
        TableName='legacy', BillingMode='PAY_PER_REQUEST', KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}],
        AttributeDefinitions=[{'AttributeName': 'pk', 'AttributeType': 'S'}])
    state = State(old_table)
    log = IncidentLog(state, None, 'evidence')
    log.sync_inventory([{'instance_id': 'i-test', 'slug': 'test-workload', 'state': 'running'}])
    report = {'summary': 'Reviewed', 'review_status': 'completed', 'coverage_gaps': [],
              'findings': [{'category': 'Unexpected upload', 'evidence': 'Private raw transcript',
                            'source_ids': ['observation:123'], 'severity': 'low', 'confidence': 'medium'}]}
    saved = {}
    for end in (300, 600):
        prefix = f'reviews/{end}'
        key = f'REVIEW#i-test#{end}'
        old_table.put_item(Item={'pk': key, 'artifact_prefix': prefix})
        log.record(report, key, prefix)
        saved.update({prefix + '/report.json': report,
                      prefix + '/evidence.json': {'sources': [{'id': 'observation:123', 'kind': 'langfuse', 'data': {}}]},
                      prefix + '/model.json': {'reported_model': 'fixture-model', 'usage': {'cost': 0.001}},
                      prefix + '/prompt.json': {'sha256': 'fixture-prompt'}})
    runtime = SimpleNamespace(state=state, s3=None, bucket='evidence', config={}, read_json=saved.__getitem__)
    assert migrate(runtime, store)['incidents'] == 1
    item = list(store.all('INCIDENTS'))[0]
    assert item['review_count'] == 2
    assert state.get('NOTICE#fleet')['acknowledged']['i-test']['total_incidents'] == 1
    store.transition(item['id'], item['version'], 'closed', {'id': 'operator'})
    assert migrate(runtime, store)['incidents'] == 1
    assert store.incident(item['id'])['status'] == 'closed'
    assert store.incident(item['id'])['review_count'] == 2
