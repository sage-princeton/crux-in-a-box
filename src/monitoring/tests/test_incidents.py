import hashlib

import boto3
from moto import mock_aws

from incidents import IncidentLog, incident_records, render_html
from worker import State


def test_incident_archive_deduplicates_replays_counts_windows_and_preserves_recovery():
    with mock_aws():
        table = boto3.resource('dynamodb', region_name='us-east-1').create_table(
            TableName='history', BillingMode='PAY_PER_REQUEST', KeySchema=[{'AttributeName':'pk','KeyType':'HASH'}],
            AttributeDefinitions=[{'AttributeName':'pk','AttributeType':'S'}])
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='incident-history')
        s3.put_bucket_versioning(Bucket='incident-history', VersioningConfiguration={'Status':'Enabled'})
        log = IncidentLog(State(table), s3, 'incident-history')
        failed = {'summary':'Review unavailable','review_status':'failed','review_error':'Budget exhausted',
                  'findings':[], 'coverage_gaps':['Missing data']}
        log.record(failed, 'REVIEW#i-test#600', 'reviews/600')
        log.record(failed, 'REVIEW#i-test#600', 'reviews/600')
        log.record(failed, 'REVIEW#i-test#300', 'reviews/300')
        log.record(failed, 'REVIEW#i-test#900', 'reviews/900')
        row = log.rows()[0]
        assert row['occurrences'] == 3 and row['first_seen'] == 300 and row['last_seen'] == 900
        assert row['artifact_prefix'] == 'reviews/900'
        healthy = {'summary':'Completed', 'review_status':'completed', 'findings':[], 'coverage_gaps':[]}
        log.record(healthy, 'REVIEW#i-test#1500', 'reviews/recovery')
        log.record(failed, 'REVIEW#i-test#1200', 'reviews/late')
        assert log.rows()[0]['incident_status'] == 'resolved'
        assert log.rows()[0]['occurrences'] == 4
        summary = log.summaries()[0]
        assert summary['review_count'] == 5
        assert summary['completed_count'] == 1 and summary['failed_count'] == 4
        assert summary['incident_count'] == 1 and summary['last_updated'] == 1500
        log.record(failed, 'REVIEW#i-test#1200', 'reviews/late')
        assert log.summaries()[0]['review_count'] == 5
        log.sync_inventory([{'instance_id':'i-test','slug':'old-instance','state':'running'}])
        log.sync_inventory([], complete=True)
        assert log.summaries()[0]['state'] == 'no longer present'
        assert log.summaries()[0]['review_count'] == 5
        log.sync_inventory([{'instance_id':'i-worker','name':'crux-monitor-worker','state':'stopped'}])
        filtered = IncidentLog(log.state, s3, log.bucket, ['crux-monitor-worker'])
        assert [r['instance_id'] for r in filtered.summaries()] == ['i-test']
        artifact = filtered.publish()
        obj = s3.get_object(Bucket='incident-history', Key=artifact['key'], VersionId=artifact['version_id'])
        body = obj['Body'].read()
        assert hashlib.sha256(body).hexdigest() == artifact['sha256']
        assert obj['ContentType'] == 'text/html; charset=utf-8'
        assert body.count(b'<tr data-kind=') == 1


def test_html_escapes_incident_content():
    malicious = '<script>alert(1)</script>'
    row = {'kind':'finding','incident_status':'observed','title':malicious,'description':malicious,
           'instance_id':'i-test','severity':'low','confidence':'medium','occurrences':2,
           'first_seen':300,'last_seen':600,'artifact_prefix':'reviews/safe'}
    page = render_html([row], 'incident-history')
    assert malicious not in page and '&lt;script&gt;' in page
    assert 'https://s3.console.aws.amazon.com/' in page
    assert 'onclick=' not in page
    assert '<input id="search"' in page and 'data-kind="finding"' in page
    summaries = [{'instance_id':'i-test', 'slug':'old-instance', 'state':'stopped',
                  'incident_count':1, 'review_count':2, 'last_updated':600},
                 {'instance_id':'i-live', 'slug':'live-instance', 'state':'running',
                  'incident_count':0, 'review_count':0, 'last_updated':600}]
    page = render_html([row], 'incident-history', summaries)
    active, historical = page.split('<details id="stopped-instances">')
    assert 'live-instance' in active and 'old-instance' not in active
    assert 'Stopped instances and history (1)' in historical and 'old-instance' in historical
    assert 'data-kind="finding"' in historical


def test_finding_identity_groups_repeats_without_merging_different_evidence():
    finding = {'category':'Unexpected upload', 'evidence':'Uploaded artifact to example.test',
               'source_ids':['file:activity'], 'severity':'low', 'confidence':'medium'}
    def identity(value):
        return incident_records({'summary':'Reviewed', 'findings':[value], 'coverage_gaps':[]})[0][0]['identity']
    assert identity(finding) == identity({**finding, 'confidence':'high', 'category':'unexpected  upload'})
    assert identity(finding) != identity({**finding, 'evidence':'Uploaded credentials to example.test'})
