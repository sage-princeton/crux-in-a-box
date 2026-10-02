"""Durable incident identities, observations, and conditional operator transitions."""

import json
import time
import uuid

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from review import digest


DETECTORS = {
    'unexpected_upload': ('Unexpected upload destination', 'The reviewer flagged a transfer outside the approved task.'),
    'credential_access': ('Unexpected credential access', 'The reviewer flagged access to credentials outside the approved task.'),
    'destructive_action': ('Potentially destructive action', 'The reviewer flagged an action that could damage data or services.'),
    'unauthorized_action': ('Action outside task permissions', 'The reviewer flagged an action outside the supplied permissions.'),
    'instruction_tampering': ('Possible instruction tampering', 'The reviewer flagged an attempt to change instructions or monitoring.'),
    'other': ('Activity requires review', 'The reviewer flagged activity for operator inspection.'),
    'monitoring:unavailable': ('AI reviews unavailable', 'Monitoring could not produce a safety verdict.'),
    'monitoring:idle': ('No recent evidence', 'No recent evidence was available for review.'),
    'monitoring:coverage': ('Incomplete monitoring coverage', 'Some expected evidence was unavailable.'),
}
PUBLIC_FIELDS = ('id', 'workload_label', 'title', 'summary', 'severity', 'confidence', 'status',
                 'first_seen', 'last_seen', 'updated_at', 'review_count', 'observation_count', 'kind')


class Conflict(Exception):
    """The record changed since the operator read it."""


def conditional(error):
    if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
        return True
    return (error.response['Error']['Code'] == 'TransactionCanceledException'
            and any(r.get('Code') == 'ConditionalCheckFailed'
                    for r in error.response.get('CancellationReasons', [])))


def public_incident(item):
    return {key: item[key] for key in PUBLIC_FIELDS if key in item}


def sftp_anchor_positions(source):
    """Map stable event anchors to 1-based JSON record / text line positions."""
    # Exports are JSON/JSONL records or complete text lines. Appending
    # another event must not change any existing event's identity.
    data = source['data']
    try:
        records = json.loads(data)
    except (ValueError, TypeError):
        records = data.splitlines()
        if source.get('truncated') and records and not data.endswith('\n'):
            records = records[:-1]
    if not isinstance(records, list):
        records = [records]
    for position, record in enumerate(records, 1):
        if isinstance(record, str):
            if not record.strip():
                continue
            try:
                record = json.loads(record)
            except ValueError:
                pass
        identity = record
        if isinstance(record, dict):
            identity = next(({key: record[key]} for key in ('event_id', 'eventId', 'id') if record.get(key)), record)
        yield source['id'] + '@' + digest(identity), position


def evidence_anchors(sources):
    """Event identity uses source records, never reviewer prose or review time."""
    result = {}
    for source in sources:
        sid = source['id']
        if source.get('kind') == 'cloudwatch':
            for event in source['data']:
                result['event:' + event['eventId']] = sid
        elif source.get('kind') == 'sftp':
            result.update({anchor: sid for anchor, _ in sftp_anchor_positions(source)})
        else:
            result[sid] = sid
    return result


class IncidentStore:
    def __init__(self, table):
        self.table = table

    def get(self, pk, sk):
        return self.table.get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item')

    def incident(self, incident_id):
        return self.get('INCIDENTS', incident_id)

    def page(self, pk, *, prefix=None, status=None, cursor=None, limit=50):
        condition = Key('pk').eq(pk)
        if prefix:
            condition &= Key('sk').begins_with(prefix)
        args = {'KeyConditionExpression': condition, 'ConsistentRead': True, 'Limit': limit}
        if status:
            args['FilterExpression'] = Attr('status').eq(status)
        if cursor:
            args['ExclusiveStartKey'] = {'pk': pk, 'sk': cursor}
        page = self.table.query(**args)
        return page['Items'], page.get('LastEvaluatedKey', {}).get('sk')

    def all(self, pk):
        cursor = None
        while True:
            rows, cursor = self.page(pk, cursor=cursor, limit=200)
            yield from rows
            if not cursor:
                break

    def instance_page(self, instance, status=None, cursor=None, limit=50):
        """Fill a filtered page from the sparse incident index, not review history."""
        rows, start = [], cursor
        while len(rows) < limit:
            args = {'IndexName': 'instance-incidents',
                    'KeyConditionExpression': Key('instance_id').eq(instance), 'Limit': limit - len(rows)}
            if status:
                args['FilterExpression'] = Attr('status').eq(status)
            if start:
                args['ExclusiveStartKey'] = {'pk': 'INCIDENTS', 'sk': start, 'id': start, 'instance_id': instance}
            page = self.table.query(**args)
            rows.extend(page['Items'])
            start = page.get('LastEvaluatedKey', {}).get('id')
            if not start:
                break
        return rows, start

    def sync_fleet(self, inventory, complete=False):
        now = int(time.time())
        if complete:
            present = {row['instance_id'] for row in inventory}
            inventory = inventory + [{**row, 'state': 'no longer present'} for row in self.all('FLEET')
                                     if row['instance_id'] not in present]
        for row in inventory:
            fields = {k: row[k] for k in ('instance_id', 'slug', 'name', 'state') if k in row}
            fields.setdefault('slug', row['instance_id'])
            fields.update(observed_at=now)
            names = {'#f' + str(n): key for n, key in enumerate(fields)}
            values = {':f' + str(n): value for n, value in enumerate(fields.values())}
            values.update({':count': row.get('review_count', 0), ':last': row.get('last_review', now)})
            update = 'SET ' + ', '.join(key + '=:f' + key[2:] for key in names)
            update += ', review_count=if_not_exists(review_count,:count), last_updated=if_not_exists(last_updated,:last)'
            for field in ('last_review', 'review_status', 'health_fingerprint'):
                if field in row:
                    values[':' + field] = row[field]
                    update += f', {field}=if_not_exists({field},:{field})'
            # Remove the large legacy deduplication set even on existing rows.
            self.table.update_item(Key={'pk': 'FLEET', 'sk': row['instance_id']},
                UpdateExpression=update + ' REMOVE review_windows',
                ExpressionAttributeNames=names, ExpressionAttributeValues=values)

    def record_review(self, review, report, target):
        """Count a completed ingestion once, including reports with no findings."""
        instance, end = review['instance_id'], review['window_end']
        try:
            self.transaction([{'Update': {'TableName': self.table.name,
                'Key': {'pk': review['pk'], 'sk': 'STATE'},
                'UpdateExpression': 'SET counted = :yes', 'ConditionExpression': 'attribute_not_exists(counted)',
                'ExpressionAttributeValues': {':yes': True}}}, {'Update': {
                'TableName': self.table.name, 'Key': {'pk': 'FLEET', 'sk': instance},
                'UpdateExpression': 'SET instance_id=:iid, slug=if_not_exists(slug,:slug), #state=if_not_exists(#state,:state) ADD review_count :one',
                'ExpressionAttributeNames': {'#state': 'state'},
                'ExpressionAttributeValues': {':iid': instance, ':slug': target.get('slug', instance),
                    ':state': target.get('instance_state', 'unknown'), ':one': 1}}}])
        except ClientError as error:
            if not conditional(error):
                raise
        try:
            self.table.update_item(Key={'pk': 'FLEET', 'sk': instance},
                UpdateExpression='SET last_review=:end, last_updated=:end, review_status=:status, health_fingerprint=:health REMOVE review_windows',
                ConditionExpression='attribute_not_exists(last_review) OR last_review <= :end',
                ExpressionAttributeValues={':end': end, ':status': review['review_status'], ':health': digest({
                    'status': review['review_status'], 'reason': report.get('review_error', '') if review['review_status'] == 'failed' else '',
                    'limited_coverage': bool(report['coverage_gaps'])})})
        except ClientError as error:
            if not conditional(error):
                raise

    def put_once(self, item):
        try:
            self.table.put_item(Item=item, ConditionExpression='attribute_not_exists(pk)')
            return True
        except ClientError as error:
            if not conditional(error):
                raise
            return False

    def transaction(self, items):
        self.table.meta.client.transact_write_items(TransactItems=items)

    def put(self, item, **conditions):
        return {'Put': {'TableName': self.table.name, 'Item': item, **conditions}}

    def versioned(self, item, old):
        if old is None:
            return self.put(item, ConditionExpression='attribute_not_exists(pk)')
        return self.put(item, ConditionExpression='#version = :version',
                        ExpressionAttributeNames={'#version': 'version'},
                        ExpressionAttributeValues={':version': old['version']})

    def ingest(self, report, review_id, prefix, target, provenance, anchors):
        provenance = {key: provenance[key] for key in (
            'requested_model', 'reported_model', 'prompt_sha256', 'detector_version', 'deployment', 'historical_import'
        ) if key in provenance}
        _, instance, window = review_id.split('#')
        end = int(window)
        workload = target.get('workload_id', instance)
        status = report.get('review_status', 'failed' if report['summary'] == 'Review unavailable' else 'completed')
        review = {'pk': 'REVIEW#' + review_id, 'sk': 'STATE', 'review_id': review_id,
                  'instance_id': instance, 'workload_id': workload, 'window_end': end,
                  'window_start': end - 1800, 'artifact_prefix': prefix, 'provenance': provenance,
                  'review_status': status}
        self.put_once(review)
        findings = []
        for finding in report['findings']:
            detector = finding.get('detector_id', 'other')
            if detector not in DETECTORS or detector.startswith('monitoring:'):
                raise ValueError('Unknown finding detector')
            anchor = finding.get('anchor_id')
            if anchor is None:
                # Historical reports did not record a primary event; preserve this uncertainty.
                anchor = 'legacy:' + digest(sorted(set(finding['source_ids'])))
            elif anchor not in anchors or anchors[anchor] not in finding['source_ids']:
                raise ValueError('Finding anchor is not in its cited evidence')
            findings.append((detector, anchor, finding))
        health = ('unavailable' if status == 'failed' else 'idle' if status == 'idle'
                  else 'coverage' if report['coverage_gaps'] else None)
        if health:
            findings.append(('monitoring:' + health, 'workload', {
                'severity': 'high' if status == 'failed' else 'info', 'confidence': 'high',
                'evidence': report.get('review_error', report['summary']),
                'source_ids': [], 'coverage_gaps': report['coverage_gaps']}))
        incident_ids = []
        for detector, anchor, finding in findings:
            identity = [1, workload, detector, anchor]
            incident_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'crux-incident:' + digest(identity)))
            incident_ids.append(incident_id)
            observation_id = digest([review_id, incident_id])
            observation = {'pk': 'HISTORY#' + incident_id, 'sk': 'OBS#' + observation_id,
                           'review_id': review_id, 'at': end, 'finding': finding,
                           'artifact_prefix': prefix, 'provenance': provenance}
            for attempt in range(6):
                if self.get(observation['pk'], observation['sk']):
                    break
                old = self.incident(incident_id)
                title, summary = DETECTORS[detector]
                item = dict(old or {'pk': 'INCIDENTS', 'sk': incident_id, 'id': incident_id,
                    'status': 'open', 'first_seen': end, 'last_seen': end, 'version': 0,
                    'observation_count': 0, 'review_count': 0, 'instance_id': instance,
                    'workload_id': workload, 'workload_label': target.get('slug', instance),
                    'kind': 'monitoring' if detector.startswith('monitoring:') else 'finding',
                    'detector_id': detector, 'correlation_version': 1, 'anchor_id': anchor,
                    'title': title, 'summary': summary})
                item.update(version=int(item['version']) + 1,
                            observation_count=int(item['observation_count']) + 1,
                            review_count=int(item['review_count']) + 1,
                            first_seen=min(end, int(item['first_seen'])),
                            updated_at=int(time.time()))
                if end >= item['last_seen']:
                    item.update(last_seen=end, severity=finding['severity'], confidence=finding['confidence'])
                try:
                    self.transaction([self.versioned(item, old), self.put(observation,
                        ConditionExpression='attribute_not_exists(pk)')])
                    break
                except ClientError as error:
                    if not conditional(error) or attempt == 5:
                        raise
        if not provenance.get('historical_import'):
            self.record_review(review, report, target)
        return incident_ids

    def transition(self, incident_id, expected_version, status, actor, note=''):
        expected_version = int(expected_version)
        if status not in ('open', 'closed') or not actor or len(note) > 4000:
            raise ValueError('Invalid incident transition')
        old = self.incident(incident_id)
        if old is None:
            raise KeyError(incident_id)
        if old['version'] != expected_version or old['status'] == status:
            raise Conflict('Incident changed; reload before trying again.')
        now = int(time.time())
        item = {**old, 'status': status, 'version': expected_version + 1, 'updated_at': now}
        event = {'pk': 'HISTORY#' + incident_id, 'sk': f'EVENT#{expected_version + 1:012d}',
                 'at': now, 'from_status': old['status'], 'status': status,
                 'actor': actor, 'note': note, 'version': expected_version + 1}
        try:
            self.transaction([self.versioned(item, old), self.put(event,
                ConditionExpression='attribute_not_exists(pk)')])
        except ClientError as error:
            if conditional(error):
                raise Conflict('Incident changed; reload before trying again.') from error
            raise
        return item

    def summaries(self, fleet):
        incidents = list(self.all('INCIDENTS'))
        for row in fleet:
            related = [i for i in incidents if i['instance_id'] == row['instance_id']]
            row.update(incident_count=sum(i['status'] == 'open' for i in related),
                       total_incident_count=len(related),
                       last_updated=max([int(row.get('last_updated', row.get('last_review', 0)))] + [int(i['updated_at']) for i in related]))
        return fleet
