import json
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError
from crux_devops.aws import launch
from crux_devops.fleet import Identity, KeyPair, SecurityGroup
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Resource
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from devops.tests.test_aws import box, run, scope


def test_launch_retries_only_profile_propagation_with_same_request():
    ec2=Mock()
    transient=ClientError({'Error':{'Code':'InvalidParameterValue','Message':'Invalid IAM Instance Profile name'}},'RunInstances')
    ec2.run_instances.side_effect=[transient, {'Instances':[{'InstanceId':'instance'}]}]
    with patch('crux_devops.aws.time.sleep'):
        assert launch(ec2,ClientToken='stable')['Instances'][0]['InstanceId']=='instance'
    assert ec2.run_instances.call_args_list[0]==ec2.run_instances.call_args_list[1]
    ec2.run_instances.side_effect=ClientError({'Error':{'Code':'UnauthorizedOperation','Message':'denied'}},'RunInstances')
    with pytest.raises(ClientError):
        launch(ec2,ClientToken='stable')


def test_security_group_lifecycle_and_cidr_validation(aws, tmp_path):
    ec2 = aws.test_session.client('ec2')
    vpc = ec2.create_vpc(CidrBlock='10.1.0.0/16')['Vpc']['VpcId']
    resource = Resource('ingress', 'aws.security_group', scope(vpc_id=vpc, ingress=[{'port': 22, 'cidr': '192.0.2.1/32'}]))
    lifecycle = Lifecycle(run([resource]), tmp_path/'state.json', {resource.type: SecurityGroup(aws)})
    first = lifecycle.apply()
    second = lifecycle.apply()
    assert first['resources']['ingress']['outputs'] == second['resources']['ingress']['outputs']
    group_id = first['resources']['ingress']['outputs']['group_id']
    assert len(ec2.describe_security_groups(GroupIds=[group_id])['SecurityGroups'][0]['IpPermissions']) == 1
    lifecycle.destroy()
    assert not ec2.describe_security_groups(Filters=[{'Name':'group-id','Values':[group_id]}])['SecurityGroups']


def test_identity_policy_is_limited_and_changed_owner_blocks_cleanup(aws, tmp_path):
    resource = Resource('identity', 'aws.identity', scope(parameters=['/crux/control/env']))
    lifecycle = Lifecycle(run([resource]), tmp_path/'state.json', {resource.type: Identity(aws)})
    first = lifecycle.apply()
    second = lifecycle.apply()
    assert first['resources']['identity']['outputs'] == second['resources']['identity']['outputs']
    outputs = first['resources']['identity']['outputs']
    iam = aws.test_session.client('iam')
    role = iam.get_role(RoleName=outputs['instance_profile'])['Role']
    assert role['AssumeRolePolicyDocument']['Statement'][0]['Principal'] == {'Service': 'ec2.amazonaws.com'}
    policy = iam.get_role_policy(RoleName=outputs['instance_profile'], PolicyName='crux-parameters')['PolicyDocument']
    assert policy['Statement'][0]['Resource'] == ['arn:aws:ssm:us-east-1:123456789012:parameter/crux/control/env']
    iam.tag_role(RoleName=outputs['instance_profile'], Tags=[{'Key':'CruxOwner','Value':'successor'}])
    with pytest.raises(ValueError, match='ownership'):
        lifecycle.destroy()


def test_keypair_import_never_materializes_a_private_key(aws, tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    resource = Resource('ssh', 'aws.key_pair', scope(public_key=public))
    lifecycle = Lifecycle(run([resource]), tmp_path/'state.json', {resource.type: KeyPair(aws)})
    first = lifecycle.apply()
    second = lifecycle.apply()
    assert first['resources']['ssh']['outputs'] == second['resources']['ssh']['outputs']
    assert 'PRIVATE' not in json.dumps(first)
    lifecycle.destroy()


def test_box_consumes_declared_ingress_and_identity_dependencies(aws, tmp_path):
    compute = box(aws)
    ec2 = aws.test_session.client('ec2')
    subnet = ec2.describe_subnets(SubnetIds=[compute.config['subnet_id']])['Subnets'][0]
    group = Resource('ingress', 'aws.security_group', scope(vpc_id=subnet['VpcId'], ingress=[]))
    identity = Resource('identity', 'aws.identity', scope(parameters=['/crux/system/env']))
    config = {k:v for k,v in compute.config.items() if k not in ('security_group_ids','instance_profile')}
    config.update(security_group_resources=['ingress'], identity_resource='identity')
    compute = replace(compute, config=config, depends_on=('ingress','identity'))
    lifecycle = Lifecycle(replace(run([group,identity,compute]), box='box'), tmp_path/'state.json', aws.registry())
    state = lifecycle.apply()
    instance = ec2.describe_instances(InstanceIds=[state['resources']['box']['outputs']['instance_id']])['Reservations'][0]['Instances'][0]
    assert instance['NetworkInterfaces'][0]['Groups'][0]['GroupId'] == state['resources']['ingress']['outputs']['group_id']
    lifecycle.destroy()
