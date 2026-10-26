from dataclasses import replace

import pytest
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Resource
from crux_devops.storage import Attachment, Volume
from devops.tests.test_aws import box, run, scope

pytest_plugins = ["devops.tests.test_aws"]


def test_controller_data_survives_box_teardown_and_attachment_is_removed(aws, tmp_path):
    compute = box(aws)
    ec2 = aws.test_session.client('ec2')
    az = ec2.describe_subnets(SubnetIds=[compute.config['subnet_id']])['Subnets'][0]['AvailabilityZone']
    volume = Resource('data', 'aws.volume', scope(zone=az, size_gb=20), teardown='retain')
    attachment = Resource('mount', 'aws.volume_attachment', scope(box='box', volume='data'), ('box', 'data'))
    drivers = aws.registry() | {'aws.volume': Volume(aws), 'aws.volume_attachment': Attachment(aws)}
    lifecycle = Lifecycle(run([compute, volume, attachment]), tmp_path/'receipt.json', drivers)
    first = lifecycle.apply()
    second = lifecycle.apply()
    assert first['resources']['data']['outputs'] == second['resources']['data']['outputs']
    volume_id = first['resources']['data']['outputs']['volume_id']
    assert ec2.describe_volumes(VolumeIds=[volume_id])['Volumes'][0]['Encrypted']
    lifecycle.destroy()
    remote = ec2.describe_volumes(VolumeIds=[volume_id])['Volumes'][0]
    assert not remote['Attachments']
    assert lifecycle.destroy()['retained'] == ['data']


def test_changed_volume_owner_blocks_deletion(aws, tmp_path):
    volume = Resource('data', 'aws.volume', scope(zone='us-east-1a', size_gb=20))
    lifecycle = Lifecycle(run([volume]), tmp_path/'receipt.json', {'aws.volume': Volume(aws)})
    state = lifecycle.apply()
    volume_id = state['resources']['data']['outputs']['volume_id']
    aws.test_session.client('ec2').create_tags(Resources=[volume_id], Tags=[{'Key': 'CruxOwner', 'Value': 'successor'}])
    with pytest.raises(ValueError, match='ownership'):
        lifecycle.destroy()


def test_volume_attachment_requires_same_scope_and_declared_dependencies(aws, tmp_path):
    compute = box(aws)
    volume = Resource('data', 'aws.volume', scope(zone='us-east-1a', size_gb=20))
    attachment = Resource('mount', 'aws.volume_attachment', scope(box='box', volume='data'))
    drivers = aws.registry() | {'aws.volume': Volume(aws), 'aws.volume_attachment': Attachment(aws)}
    with pytest.raises(ValueError, match='depend'):
        Lifecycle(run([compute, volume, attachment]), tmp_path/'receipt.json', drivers).apply()
    other = replace(volume, config=scope(zone='us-west-2a', size_gb=20) | {'region': 'us-west-2'})
    attachment = replace(attachment, depends_on=('box', 'data'))
    with pytest.raises(ValueError, match='scope'):
        Lifecycle(run([compute, other, attachment]), tmp_path/'receipt.json', drivers).apply()
