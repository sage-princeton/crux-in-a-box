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
        config = {'fleet':{'exclude_names':['crux-control'], 'langfuse_by_name':True},
                  'targets':{control:{'authorization':'explicit override'}}}
        targets, inventory = inventory_targets(ec2, config)
        assert control not in targets and renamed not in targets
        assert targets[web]['langfuse'] == {'environment':'crux-web-pilot'}
        assert 'langfuse' not in targets[first] and 'langfuse' not in targets[second]
        assert targets[first]['slug'] != targets[second]['slug']
        assert targets[worker]['service_worker'] and 'langfuse' not in targets[worker]
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
                secrets=lambda:{'MONITORING_SLACK_WEBHOOK_URL':'https://hooks.slack.com/services/test/fixture/only'})
            with pytest.raises(httpx.HTTPStatusError):
                deliver_summary(runtime, [row])
            assert 'acknowledged' not in runtime.state.get('NOTICE#fleet')
            assert deliver_summary(runtime, [row]) == 'sent'
            assert attempts[1]['text'] == 'crux-web-pilot: 2 incidents based on 3 reviews, 2 new incidents (last updated: 2026-09-28 20:15 ET)'
            assert attempts[1]['blocks'][0]['text']['type'] == 'plain_text'
            assert deliver_summary(runtime, [{**row, 'review_count':4, 'last_updated':1790641200}]) == 'suppressed'
            changed = {**row, 'incident_count':3, 'review_count':5}
            assert deliver_summary(runtime, [changed]) == 'sent'
            assert '3 incidents based on 5 reviews, 1 new incident (' in attempts[-1]['text']
            assert deliver_summary(runtime, [changed]) == 'suppressed'
