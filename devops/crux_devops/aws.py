"""Native AWS resource drivers; shared infrastructure must be declared explicitly."""
import re

import boto3
from botocore.exceptions import ClientError

from .spec import fields

SCOPE = ('profile', 'region', 'account')


class AWS:
    def __init__(self, session_factory=boto3.Session):
        self.factory = session_factory
        self.sessions = {}

    def session(self, resource):
        config = resource.config
        for key in SCOPE:
            if not isinstance(config.get(key), str) or not config[key]:
                raise ValueError(f'{resource.id} requires explicit AWS {key}')
        if not re.fullmatch(r'\d{12}', config['account']):
            raise ValueError('AWS account must be 12 digits')
        scope = tuple(config[k] for k in SCOPE)
        if scope not in self.sessions:
            session = self.factory(profile_name=config['profile'], region_name=config['region'])
            identity = session.client('sts').get_caller_identity()
            if identity['Account'] != config['account']:
                raise ValueError('Authenticated AWS account differs from the declared account')
            self.sessions[scope] = session
        return self.sessions[scope]

    def registry(self):
        from .fleet import Identity, KeyPair, SecurityGroup
        from .storage import Attachment, Volume
        return {'aws.ec2': EC2(self), 'aws.elastic_ip': ElasticIP(self),
                'aws.address_attachment': AddressAttachment(self), 'aws.volume': Volume(self),
                'aws.volume_attachment': Attachment(self), 'aws.security_group': SecurityGroup(self),
                'aws.identity': Identity(self), 'aws.key_pair': KeyPair(self)}


def tags(resource, context):
    return [{'Key': k, 'Value': v} for k, v in {
        'Name': context.run.name, 'CruxRole': 'run', 'MonitorWithCruxMonitor': '1',
        'CruxOwner': context.owner, 'CruxResource': resource.id,
        'CruxToken': context.token(resource),
    }.items()]


def filters(resource, context):
    return [{'Name': 'tag:' + t['Key'], 'Values': [t['Value']]}
            for t in tags(resource, context) if t['Key'] in ('CruxOwner', 'CruxToken')]


def owned(value, resource, context):
    actual = {t['Key']: t['Value'] for t in value.get('Tags', [])}
    if actual.get('CruxOwner') != context.owner or actual.get('CruxToken') != context.token(resource):
        raise ValueError(f'Refusing to mutate {resource.id}: remote ownership changed')


def one(values):
    if len(values) > 1:
        raise ValueError('Multiple resources match the deployment identity; inspect before continuing')
    return values[0] if values else None


class EC2:
    def __init__(self, aws):
        self.aws = aws

    def settings(self, resource, context, outputs=None):
        config = dict(resource.config)
        resources = {r.id: r for r in context.run.resources}
        for field, reference, kind, output in (
            ('key_name', 'key_resource', 'aws.key_pair', 'key_name'),
            ('instance_profile', 'identity_resource', 'aws.identity', 'instance_profile'),
            ('security_group_ids', 'security_group_resources', 'aws.security_group', 'group_id'),
        ):
            if (field in config) == (reference in config):
                raise ValueError(f'Box requires exactly one of {field} or {reference}')
            if reference not in config:
                value = config[field]
                if field == 'security_group_ids':
                    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
                        raise ValueError('Box security_group_ids must be a nonempty list')
                elif not isinstance(value, str) or not value:
                    raise ValueError(f'Box {field} must be a nonempty string')
                continue
            ids = config[reference] if field == 'security_group_ids' else [config[reference]]
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
                raise ValueError('Box resource references must be nonempty resource IDs')
            for key in ids:
                dependency = resources.get(key)
                if not dependency or dependency.type != kind or key not in resource.depends_on:
                    raise ValueError(f'{reference} must be a declared {kind} dependency')
                if any(dependency.config.get(k) != config[k] for k in ('account', 'region')):
                    raise ValueError('Box resource dependencies must share its AWS scope')
            values = [outputs[key][output] for key in ids] if outputs is not None else None
            config[field] = values if field == 'security_group_ids' or values is None else values[0]
        return config

    def find(self, resource, context, receipt=None):
        ec2 = self.aws.session(resource).client('ec2')
        instance = (receipt or {}).get('instance_id') or resource.config.get('instance_id')
        try:
            result = ec2.describe_instances(**({'InstanceIds': [instance]} if instance else
                                               {'Filters': filters(resource, context)}))
        except ClientError as error:
            if error.response['Error']['Code'] == 'InvalidInstanceID.NotFound':
                return None
            raise
        return one([i for r in result['Reservations'] for i in r['Instances']])

    def preflight(self, resource, context):
        config = resource.config
        if resource.ownership == 'external':
            fields(config, (*SCOPE, 'instance_id'), (*SCOPE, 'instance_id'), 'external EC2')
            if not self.find(resource, context):
                raise ValueError('External Box does not exist')
            return
        required = (*SCOPE, 'image_id', 'instance_type', 'subnet_id')
        fields(config, (*required, 'disk_gb', 'iops', 'throughput', 'security_group_ids', 'key_name',
                        'instance_profile', 'security_group_resources', 'key_resource', 'identity_resource'), required, 'EC2')
        config = self.settings(resource, context)
        for key in ('image_id', 'instance_type', 'subnet_id'):
            if not isinstance(config[key], str) or not config[key]:
                raise ValueError(f'EC2 {key} must be a nonempty string')
        groups = config['security_group_ids']
        if groups is not None and (not isinstance(groups, list) or not groups or any(not isinstance(v, str) or not v for v in groups)):
            raise ValueError('EC2 security_group_ids must be a nonempty list')
        for key, default in (('disk_gb', 100), ('iops', 6000), ('throughput', 250)):
            value = config.get(key, default)
            if type(value) is not int or value <= 0:
                raise ValueError(f'EC2 {key} must be a positive integer')
        session = self.aws.session(resource)
        ec2 = session.client('ec2')
        images = ec2.describe_images(ImageIds=[config['image_id']])['Images']
        if not images or images[0].get('State') != 'available':
            raise ValueError('Selected AMI is not available')
        subnet = ec2.describe_subnets(SubnetIds=[config['subnet_id']])['Subnets'][0]
        security = ec2.describe_security_groups(GroupIds=groups)['SecurityGroups'] if groups else [
            {'VpcId': r.config['vpc_id']} for r in context.run.resources if r.id in config['security_group_resources']]
        if any(g['VpcId'] != subnet['VpcId'] for g in security):
            raise ValueError('Box subnet and security groups must belong to the same VPC')
        if config['key_name'] is not None:
            ec2.describe_key_pairs(KeyNames=[config['key_name']])
        if config['instance_profile'] is not None:
            session.client('iam').get_instance_profile(InstanceProfileName=config['instance_profile'])
        existing = self.find(resource, context)
        if existing:
            owned(existing, resource, context)
            if existing['State']['Name'] in ('shutting-down', 'terminated'):
                raise ValueError('Box was terminated; create a new deployment')

    def ensure(self, resource, previous, dependencies, context):
        config = resource.config if resource.ownership == 'external' else self.settings(resource, context, dependencies)
        ec2 = self.aws.session(resource).client('ec2')
        instance = self.find(resource, context, previous)
        if resource.ownership == 'external':
            if instance is None or instance['State']['Name'] != 'running':
                raise ValueError('External Box must already be running')
        else:
            if instance:
                owned(instance, resource, context)
                if instance['State']['Name'] == 'stopped':
                    ec2.start_instances(InstanceIds=[instance['InstanceId']])
                elif instance['State']['Name'] not in ('running', 'pending'):
                    raise ValueError('Box is not startable; inspect its current state')
            else:
                instance = ec2.run_instances(
                    ImageId=config['image_id'], InstanceType=config['instance_type'],
                    KeyName=config['key_name'], MinCount=1, MaxCount=1,
                    ClientToken=context.token(resource),
                    NetworkInterfaces=[{'DeviceIndex': 0, 'AssociatePublicIpAddress': True,
                                        'SubnetId': config['subnet_id'], 'Groups': config['security_group_ids']}],
                    IamInstanceProfile={'Name': config['instance_profile']},
                    MetadataOptions={'HttpTokens': 'required'},
                    BlockDeviceMappings=[{'DeviceName': '/dev/sda1', 'Ebs': {
                        'VolumeType': 'gp3', 'VolumeSize': config.get('disk_gb', 100),
                        'Iops': config.get('iops', 6000), 'Throughput': config.get('throughput', 250),
                        'Encrypted': True, 'DeleteOnTermination': True}}],
                    TagSpecifications=[{'ResourceType': kind, 'Tags': tags(resource, context)}
                                       for kind in ('instance', 'volume')],
                )['Instances'][0]
            for waiter in ('instance_running', 'instance_status_ok'):
                ec2.get_waiter(waiter).wait(InstanceIds=[instance['InstanceId']],
                                            WaiterConfig={'Delay': 10, 'MaxAttempts': 90})
        instance = self.find(resource, context, {'instance_id': instance['InstanceId']})
        return {'instance_id': instance['InstanceId'], 'public_ip': instance.get('PublicIpAddress'),
                'private_ip': instance.get('PrivateIpAddress')}

    def stop(self, resource, receipt, context):
        ec2 = self.aws.session(resource).client('ec2')
        instance = self.find(resource, context, receipt)
        if not instance:
            return
        owned(instance, resource, context)
        status = instance['State']['Name']
        if status == 'shutting-down':
            ec2.get_waiter('instance_terminated').wait(InstanceIds=[instance['InstanceId']])
            return
        if status in ('terminated', 'stopped'):
            return
        if status == 'pending':
            ec2.get_waiter('instance_running').wait(InstanceIds=[instance['InstanceId']])
        if status != 'stopping':
            ec2.stop_instances(InstanceIds=[instance['InstanceId']])
        ec2.get_waiter('instance_stopped').wait(InstanceIds=[instance['InstanceId']],
                                              WaiterConfig={'Delay': 10, 'MaxAttempts': 90})

    def destroy(self, resource, receipt, context):
        ec2 = self.aws.session(resource).client('ec2')
        instance = self.find(resource, context, receipt)
        if not instance:
            return
        owned(instance, resource, context)
        if instance['State']['Name'] != 'terminated':
            ec2.terminate_instances(InstanceIds=[instance['InstanceId']])
            ec2.get_waiter('instance_terminated').wait(InstanceIds=[instance['InstanceId']],
                                                     WaiterConfig={'Delay': 10, 'MaxAttempts': 90})


class ElasticIP:
    def __init__(self, aws):
        self.aws = aws

    def find(self, resource, context, receipt=None):
        ec2 = self.aws.session(resource).client('ec2')
        allocation = (receipt or {}).get('allocation_id') or resource.config.get('allocation_id')
        try:
            return one(ec2.describe_addresses(**({'AllocationIds': [allocation]} if allocation else
                                                  {'Filters': filters(resource, context)}))['Addresses'])
        except ClientError as error:
            if error.response['Error']['Code'] == 'InvalidAllocationID.NotFound':
                return None
            raise

    def preflight(self, resource, context):
        required = (*SCOPE, 'allocation_id') if resource.ownership == 'external' else SCOPE
        fields(resource.config, required, required, 'Elastic IP')
        address = self.find(resource, context)
        if resource.ownership == 'external' and not address:
            raise ValueError('External Elastic IP does not exist')
        if address and resource.ownership == 'managed':
            owned(address, resource, context)

    def ensure(self, resource, previous, dependencies, context):
        address = self.find(resource, context, previous)
        if address is None:
            if resource.ownership == 'external':
                raise ValueError('External Elastic IP disappeared')
            address = self.aws.session(resource).client('ec2').allocate_address(
                Domain='vpc', TagSpecifications=[{'ResourceType': 'elastic-ip', 'Tags': tags(resource, context)}])
        elif resource.ownership == 'managed':
            owned(address, resource, context)
        return {'allocation_id': address['AllocationId'], 'public_ip': address['PublicIp']}

    def destroy(self, resource, receipt, context):
        address = self.find(resource, context, receipt)
        if address is None:
            return
        owned(address, resource, context)
        if address.get('AssociationId'):
            raise ValueError('Elastic IP is still attached; remove its attachment first')
        self.aws.session(resource).client('ec2').release_address(AllocationId=address['AllocationId'])


class AddressAttachment:
    def __init__(self, aws):
        self.aws = aws

    def dependencies(self, resource, context):
        fields(resource.config, (*SCOPE, 'box', 'address'), (*SCOPE, 'box', 'address'), 'address attachment')
        by_id = {r.id: r for r in context.run.resources}
        box = by_id.get(resource.config['box'])
        address = by_id.get(resource.config['address'])
        if not box or not address or box.type != 'aws.ec2' or address.type != 'aws.elastic_ip':
            raise ValueError('Address attachment requires an EC2 Box and an Elastic IP resource')
        if box.ownership != 'managed' or resource.ownership != 'managed':
            raise ValueError('An address attachment requires a managed Box and managed attachment')
        if sum(r.type == 'aws.address_attachment' and r.config.get('box') == box.id for r in context.run.resources) > 1:
            raise ValueError('Only one Elastic IP attachment is supported per Box')
        if not {box.id, address.id} <= set(resource.depends_on):
            raise ValueError('Address attachment must depend on its Box and address')
        for other in (box, address):
            if any(other.config.get(key) != resource.config.get(key) for key in ('region', 'account')):
                raise ValueError('Address attachment dependencies must share its AWS account and region')
        return box, address

    def preflight(self, resource, context):
        box, address = self.dependencies(resource, context)
        self.aws.session(resource)
        current = ElasticIP(self.aws).find(address, context)
        if current and current.get('AssociationId'):
            instance = EC2(self.aws).find(box, context)
            if instance is None or current.get('InstanceId') != instance['InstanceId']:
                raise ValueError('Elastic IP is attached to another Box; refusing to steal it')

    def ensure(self, resource, previous, dependencies, context):
        box, address = self.dependencies(resource, context)
        instance = EC2(self.aws).find(box, context, dependencies[box.id])
        owned(instance, box, context)
        current = ElasticIP(self.aws).find(address, context, dependencies[address.id])
        if current.get('AssociationId') and current.get('InstanceId') != instance['InstanceId']:
            raise ValueError('Elastic IP is attached to another Box; refusing to steal it')
        association = current.get('AssociationId')
        if previous.get('association_id') and association != previous['association_id']:
            raise ValueError('Address attachment changed outside this deployment')
        if not association:
            association = self.aws.session(resource).client('ec2').associate_address(
                AllocationId=current['AllocationId'], InstanceId=instance['InstanceId'],
                AllowReassociation=False)['AssociationId']
        return {'association_id': association, 'allocation_id': current['AllocationId'],
                'instance_id': instance['InstanceId'], 'public_ip': current['PublicIp']}

    def destroy(self, resource, receipt, context):
        box, address = self.dependencies(resource, context)
        current = ElasticIP(self.aws).find(address, context, receipt)
        if current is None or not current.get('AssociationId'):
            return
        instance = EC2(self.aws).find(box, context)
        if not receipt and (instance is None or current.get('InstanceId') != instance['InstanceId']):
            return
        if instance is None:
            raise ValueError('Cannot prove ownership of the attached Box')
        owned(instance, box, context)
        if current.get('InstanceId') != instance['InstanceId'] or (
                receipt.get('association_id') and current['AssociationId'] != receipt['association_id']):
            raise ValueError('Address attachment changed ownership; refusing to detach it')
        self.aws.session(resource).client('ec2').disassociate_address(AssociationId=current['AssociationId'])
