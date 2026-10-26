import json
import os
from dataclasses import replace

import boto3
import pytest
from crux_devops.aws import AWS
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Resource, Run, Scaffold
from moto import mock_aws


@pytest.fixture
def aws(monkeypatch):
    for key in list(os.environ):
        if key.startswith('AWS_ENDPOINT_URL') or key == 'AWS_PROFILE':
            monkeypatch.delenv(key)
    monkeypatch.setenv('AWS_IGNORE_CONFIGURED_ENDPOINT_URLS', 'true')
    with mock_aws():
        session = boto3.Session(region_name='us-east-1')
        scopes = []
        def factory(**kwargs):
            scopes.append(kwargs)
            return session
        backend = AWS(factory)
        backend.requested_scopes = scopes
        backend.test_session = session
        yield backend


def run(resources):
    return Run('test', resources[0].id, Scaffold('sdk', 'openai-agents', 'inputs', 'test'), tuple(resources))


def scope(**kwargs):
    return dict(profile='explicit-test', region='us-east-1', account='123456789012', **kwargs)


def box(aws):
    ec2 = aws.test_session.client('ec2')
    image = ec2.register_image(Name='test-ubuntu')['ImageId']
    vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')['Vpc']['VpcId']
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock='10.0.1.0/24')['Subnet']['SubnetId']
    group = ec2.create_security_group(GroupName='test', Description='test', VpcId=vpc)['GroupId']
    ec2.create_key_pair(KeyName='test')
    aws.test_session.client('iam').create_instance_profile(InstanceProfileName='test')
    return Resource('box', 'aws.ec2', scope(image_id=image, instance_type='t3.small', subnet_id=subnet,
                                          security_group_ids=[group], key_name='test', instance_profile='test'))


def test_ec2_idempotency_owner_tags_and_private_receipt(aws, tmp_path):
    resource = box(aws)
    lifecycle = Lifecycle(run([resource]), tmp_path / 'state.json', aws.registry())
    first = lifecycle.apply()
    instance_id = first['resources']['box']['outputs']['instance_id']
    assert lifecycle.apply()['resources']['box']['outputs']['instance_id'] == instance_id
    assert aws.requested_scopes == [{'profile_name': 'explicit-test', 'region_name': 'us-east-1'}]
    ec2 = aws.test_session.client('ec2')
    instance = ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]
    assert instance['MetadataOptions']['HttpTokens'] == 'required'
    assert {t['Key']: t['Value'] for t in instance['Tags']}['CruxOwner'] == first['owner']
    lifecycle.destroy()
    assert ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]['State']['Name'] == 'terminated'


def test_changed_remote_owner_blocks_teardown(aws, tmp_path):
    lifecycle = Lifecycle(run([box(aws)]), tmp_path / 'state.json', aws.registry())
    state = lifecycle.apply()
    instance_id = state['resources']['box']['outputs']['instance_id']
    aws.test_session.client('ec2').create_tags(Resources=[instance_id], Tags=[{'Key': 'CruxOwner', 'Value': 'successor'}])
    with pytest.raises(ValueError, match='ownership changed'):
        lifecycle.destroy()


def test_eip_creation_recovers_without_saved_receipt(aws, tmp_path):
    resource = Resource('address', 'aws.elastic_ip', scope())
    lifecycle = Lifecycle(run([resource]), tmp_path / 'state.json', aws.registry())
    first = lifecycle.apply()
    state = json.loads(lifecycle.path.read_text())
    state['resources']['address'] = {'status': 'creating', 'outputs': {}}
    lifecycle.path.write_text(json.dumps(state))
    assert lifecycle.apply()['resources']['address']['outputs'] == first['resources']['address']['outputs']
    lifecycle.destroy()
    assert aws.test_session.client('ec2').describe_addresses()['Addresses'] == []


def test_external_eip_is_resolved_and_retained(aws, tmp_path):
    ec2 = aws.test_session.client('ec2')
    address = ec2.allocate_address(Domain='vpc')
    resource = Resource('address', 'aws.elastic_ip', scope(allocation_id=address['AllocationId']),
                        ownership='external', teardown='retain')
    lifecycle = Lifecycle(run([resource]), tmp_path / 'state.json', aws.registry())
    assert lifecycle.apply()['resources']['address']['outputs']['public_ip'] == address['PublicIp']
    lifecycle.destroy()
    assert len(ec2.describe_addresses()['Addresses']) == 1


def test_invalid_later_resource_prevents_earlier_mutations(aws, tmp_path):
    resources = [Resource('address', 'aws.elastic_ip', scope()), box(aws)]
    resources[1].config['disk_gb'] = -1
    lifecycle = Lifecycle(run(resources), tmp_path / 'state.json', aws.registry())
    with pytest.raises(ValueError, match='positive integer'):
        lifecycle.apply()
    assert aws.test_session.client('ec2').describe_addresses()['Addresses'] == []
    assert not lifecycle.path.exists()


@pytest.mark.parametrize('key', ['profile', 'region', 'account'])
def test_no_ambient_aws_scope(aws, tmp_path, key):
    config = scope()
    del config[key]
    lifecycle = Lifecycle(run([Resource('address', 'aws.elastic_ip', config)]), tmp_path / 'state.json', aws.registry())
    with pytest.raises(ValueError):
        lifecycle.apply()
    assert not aws.requested_scopes


def test_wrong_account_cannot_create_or_destroy(aws, tmp_path):
    resource = Resource('address', 'aws.elastic_ip', scope())
    resource = replace(resource, config=resource.config | {'account': '999999999999'})
    lifecycle = Lifecycle(run([resource]), tmp_path / 'state.json', aws.registry())
    with pytest.raises(ValueError, match='account differs'):
        lifecycle.apply()
    assert aws.test_session.client('ec2').describe_addresses()['Addresses'] == []


def test_address_attachment_tears_down_before_box_and_preserves_external_address(aws, tmp_path):
    ec2 = aws.test_session.client('ec2')
    allocated = ec2.allocate_address(Domain='vpc')
    address = Resource('address', 'aws.elastic_ip', scope(allocation_id=allocated['AllocationId']),
                       ownership='external', teardown='retain')
    attachment = Resource('attachment', 'aws.address_attachment', scope(box='box', address='address'),
                          depends_on=('box', 'address'))
    lifecycle = Lifecycle(run([box(aws), address, attachment]), tmp_path / 'state.json', aws.registry())
    state = lifecycle.apply()
    assert lifecycle.apply()['resources']['attachment']['outputs'] == state['resources']['attachment']['outputs']
    lifecycle.destroy()
    remaining = ec2.describe_addresses()['Addresses']
    assert len(remaining) == 1
    assert not remaining[0].get('AssociationId')


def test_attachment_refuses_address_already_used_by_another_box(aws, tmp_path):
    ec2 = aws.test_session.client('ec2')
    first = box(aws)
    foreign = ec2.run_instances(ImageId=first.config['image_id'], MinCount=1, MaxCount=1)['Instances'][0]
    allocated = ec2.allocate_address(Domain='vpc')
    ec2.associate_address(AllocationId=allocated['AllocationId'], InstanceId=foreign['InstanceId'])
    address = Resource('address', 'aws.elastic_ip', scope(allocation_id=allocated['AllocationId']),
                       ownership='external', teardown='retain')
    attachment = Resource('attachment', 'aws.address_attachment', scope(box='box', address='address'),
                          depends_on=('box', 'address'))
    lifecycle = Lifecycle(run([first, address, attachment]), tmp_path / 'state.json', aws.registry())
    with pytest.raises(ValueError, match='refusing to steal'):
        lifecycle.apply()
    assert ec2.describe_addresses()['Addresses'][0]['InstanceId'] == foreign['InstanceId']
