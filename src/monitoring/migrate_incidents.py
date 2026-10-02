"""Idempotently import saved reviews before switching the public incident log."""

import argparse
import json
import re
import time
import uuid

import boto3

from incidents import IncidentLog, incident_records
from lifecycle import IncidentStore, evidence_anchors
from worker import Runtime


def historical_finding(finding, anchors):
    finding = dict(finding)
    if 'detector_id' not in finding:
        category = finding['category'].lower()
        finding['detector_id'] = next((detector for pattern, detector in [
            (r'upload|exfil|outbound|destination', 'unexpected_upload'),
            (r'credential|secret', 'credential_access'),
            (r'destruct|delet', 'destructive_action'),
            (r'tamper|instruction|prompt.injection', 'instruction_tampering'),
            (r'unauthor|permission', 'unauthorized_action')]
            if re.search(pattern, category)), 'other')
    candidates = sorted(a for a, source in anchors.items() if source in finding['source_ids'])
    # Do not invent a primary event where an old report cited an entire multi-event source.
    if 'anchor_id' not in finding and len(candidates) == 1:
        finding['anchor_id'] = candidates[0]
    return finding


def migrate(runtime, store):
    log = IncidentLog(runtime.state, runtime.s3, runtime.bucket, runtime.config.get('fleet', {}).get('exclude_names', []))
    fleet = {r['instance_id']: r for r in log.summaries()}
    store.sync_fleet(list(fleet.values()))
    legacy = {r['pk']: r for r in log.rows()}
    reviews = [r for r in log.rows('REVIEW#') if r.get('artifact_prefix')]
    count = 0
    resolved = set()
    for row in sorted(reviews, key=lambda r: int(r['pk'].split('#')[2])):
        _, instance, _ = row['pk'].split('#')
        if instance not in fleet:
            continue
        prefix = row['artifact_prefix']
        original = runtime.read_json(prefix + '/report.json')
        payload = runtime.read_json(prefix + '/evidence.json')
        anchors = payload.get('evidence_anchors', evidence_anchors(payload['sources']))
        report = {**original, 'findings': [historical_finding(f, anchors) for f in original['findings']]}
        provenance = {**runtime.read_json(prefix + '/model.json'),
                      'prompt_sha256': runtime.read_json(prefix + '/prompt.json')['sha256'],
                      'detector_version': 'legacy-import-1', 'historical_import': True}
        target = {**fleet[instance], **runtime.config.get('targets', {}).get(instance, {})}
        ids = store.ingest(report, row['pk'], prefix, target, provenance, anchors)
        records, _ = incident_records(original)
        for record, incident_id in zip(records, ids, strict=True):
            legacy_key = log.key(instance, record['identity'])
            store.put_once({'pk': 'LEGACY', 'sk': legacy_key, 'incident_id': incident_id})
            if legacy.get(legacy_key, {}).get('incident_status') == 'resolved':
                resolved.add(incident_id)
        count += 1
    for incident_id in resolved:
        if store.get('MIGRATION', incident_id):
            continue
        item = store.incident(incident_id)
        events, _ = store.page('HISTORY#' + incident_id, prefix='EVENT#', limit=1)
        if not events and item['status'] == 'open':
            store.transition(incident_id, item['version'], 'closed', {'id': 'historical-import', 'issuer': 'system'},
                             'Preserved resolved status from the previous incident log.')
        store.put_once({'pk': 'MIGRATION', 'sk': incident_id})
    # Seed cumulative counts so migrating history is not announced as new discoveries.
    if not store.get('MIGRATION', 'DIGEST_BASELINE'):
        owner = str(uuid.uuid4())
        if not runtime.state.claim_notice('NOTICE#fleet', owner, int(time.time())):
            raise RuntimeError('Fleet digest is active; retry migration to seed its baseline.')
        try:
            acknowledged = runtime.state.get('NOTICE#fleet').get('acknowledged', {})
            for row in store.summaries(list(fleet.values())):
                acknowledged.setdefault(row['instance_id'], {})['total_incidents'] = row['total_incident_count']
            store.transaction([store.put({'pk': 'MIGRATION', 'sk': 'DIGEST_BASELINE'},
                ConditionExpression='attribute_not_exists(pk)'), {'Update': {
                    'TableName': runtime.state.table.name, 'Key': {'pk': 'NOTICE#fleet'},
                    'UpdateExpression': 'SET acknowledged = :ack',
                    'ConditionExpression': 'lease_owner = :owner',
                    'ExpressionAttributeValues': {':ack': acknowledged, ':owner': owner}}}])
        finally:
            runtime.state.save('NOTICE#fleet', owner, {'updated_at': int(time.time())}, release=True)
    return {'reviews_imported': count, 'incidents': len(list(store.all('INCIDENTS'))), 'workloads': len(fleet)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--table', required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(Runtime(), IncidentStore(boto3.resource('dynamodb').Table(args.table)))))
