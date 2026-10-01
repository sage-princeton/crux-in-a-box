import json
from types import SimpleNamespace

import boto3
import httpx
import pytest
from moto import mock_aws

from fleet import deliver_summary, inventory_targets
from review import CoverageError, collect_langfuse
from worker import State


def test_fleet_excludes_controller_even_with_override_and_disambiguates_names():
    with mock_aws():
        ec2 = boto3.client('ec2', region_name='us-east-1')
        def instance(name, extra=()):
            return ec2.run_instances(ImageId='ami-12345678', MinCount=1, MaxCount=1,
                TagSpecifications=[{'ResourceType':'instance','Tags':[{'Key':'Name','Value':name}, *extra]}])['Instances'][0]['InstanceId']
        control = instance('crux-control')
        renamed = instance('renamed-control', [{'Key':'CruxRole','Value':'control'}])
        web = instance('crux-web-pilot')
        first = instance('duplicate')
        second = instance('duplicate')
        worker = instance('crux-monitor-worker', [{'Key':'AWSBatchServiceTag','Value':'batch'}])
        stopped = instance('stopped-workload')
        ec2.stop_instances(InstanceIds=[stopped])
        config = {'fleet':{'exclude_names':['crux-control','crux-monitor-worker'], 'langfuse_by_name':True},
                  'targets':{control:{'authorization':'explicit override'}}}
        targets, inventory = inventory_targets(ec2, config)
        assert control not in targets and renamed not in targets
        assert targets[stopped]['instance_state'] == 'stopped'
        assert targets[web]['langfuse'] == {'environment':'crux-web-pilot'}
        assert 'langfuse' not in targets[first] and 'langfuse' not in targets[second]
        assert targets[first]['slug'] != targets[second]['slug']
        assert worker not in targets
        assert len(inventory) == 4


def test_environment_filter_is_bounded_and_rejects_other_instances():
    secrets = {'MONITORING_LANGFUSE_BASE_URL':'https://langfuse.example',
               'MONITORING_LANGFUSE_PUBLIC_KEY':'public', 'MONITORING_LANGFUSE_SECRET_KEY':'private'}
    def handler(request):
        assert request.url.params.get_list('environment') == ['crux-web-pilot']
        assert request.url.params['fromStartTime'] and request.url.params['toStartTime']
        return httpx.Response(200, json={'data':[{'id':'1','environment':'crux-control'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CoverageError, match='unexpected instance'):
            collect_langfuse(client, {'environment':'crux-web-pilot'}, secrets, 0, 300)


def test_fleet_summary_acknowledges_new_incidents_only_after_success():
    with mock_aws():
        table = boto3.resource('dynamodb', region_name='us-east-1').create_table(
            TableName='fleet', BillingMode='PAY_PER_REQUEST', KeySchema=[{'AttributeName':'pk','KeyType':'HASH'}],
            AttributeDefinitions=[{'AttributeName':'pk','AttributeType':'S'}])
        row = {'instance_id':'i-web', 'slug':'crux-web-pilot', 'incident_count':2,
               'review_count':3, 'last_updated':1790640900, 'state':'running', 'health_fingerprint':'idle'}
        attempts = []
        def handler(request):
            attempts.append(json.loads(request.content))
            return httpx.Response(503 if len(attempts) == 1 else 200, text='busy' if len(attempts) == 1 else 'ok')
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            runtime = SimpleNamespace(state=State(table), http=client,
                public_incident_log_url="https://incident-history.s3.us-east-1.amazonaws.com/reviews/incidents/index.html",
                secrets=lambda:{'MONITORING_SLACK_WEBHOOK_URL':'https://hooks.slack.com/services/test/fixture/only'})
            with pytest.raises(httpx.HTTPStatusError):
                deliver_summary(runtime, [row])
            assert 'acknowledged' not in runtime.state.get('NOTICE#fleet')
            assert deliver_summary(runtime, [row, {**row, 'instance_id':'i-stopped', 'slug':'stopped-workload', 'state':'stopped'}]) == 'sent'
            assert attempts[1]['text'].splitlines()[0] == '• crux-web-pilot: 2 incidents based on 3 reviews, *2 new incidents :warning:* (last updated: 2026-09-28 20:15 ET)'
            assert attempts[1]['text'].endswith('|Open the public incident log>')
            assert 'stopped-workload' not in attempts[1]['text']
            assert attempts[1]['blocks'][0]['text']['type'] == 'mrkdwn'
            assert deliver_summary(runtime, [{**row, 'review_count':4, 'last_updated':1790641200}]) == 'suppressed'
            changed = {**row, 'incident_count':3, 'review_count':5}
            assert deliver_summary(runtime, [changed]) == 'sent'
            assert '3 incidents based on 5 reviews, *1 new incident :warning:* (' in attempts[-1]['text']
            assert deliver_summary(runtime, [changed]) == 'suppressed'


def test_oversized_langfuse_io_falls_back_to_bounded_metadata_with_explicit_gap():
    from review import MAX_EVIDENCE_BYTES
    fields = []
    def handler(request):
        fields.append(request.url.params['fields'])
        assert request.url.params['environment'] == 'ae239-test'
        data = {'id':'1','environment':'ae239-test','model':'gpt-example'}
        if 'io' in request.url.params['fields'].split(','):
            data['input'] = 'x' * MAX_EVIDENCE_BYTES
        return httpx.Response(200, json={'data':[data]})
    secrets = {'MONITORING_LANGFUSE_BASE_URL':'https://langfuse.example',
               'MONITORING_LANGFUSE_PUBLIC_KEY':'public', 'MONITORING_LANGFUSE_SECRET_KEY':'private'}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sources = collect_langfuse(client, {'environment':'ae239-test'}, secrets, 0, 300)
    assert len(fields) == 2
    assert 'io' not in fields[-1].split(',')
    assert sources[0]['truncated'] and 'content-level review is incomplete' in sources[0]['coverage_gap']
    assert 'input' not in sources[0]['data']
