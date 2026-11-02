"""Owned EBS data volumes and attachments with independent retention."""
import re

from botocore.exceptions import ClientError

from .aws import EC2, SCOPE, filters, one, owned, tags
from .spec import fields


class Volume:
    """Manage encrypted EBS storage with independent retention."""
    def observe(self, resource, context):
        """Resolve the live volume and verify ownership for managed storage.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        current = self.find(resource, context)
        if current is None:
            return None
        if resource.ownership == 'managed':
            owned(current, resource, context)
        return {'volume_id':current['VolumeId'],'zone':current['AvailabilityZone']}

    def __init__(self, aws):
        """Bind collaborators and configuration for subsequent operations.

        :param aws: Explicit-profile native AWS backend.
        """
        self.aws = aws

    def find(self, resource, context, observed=None):
        """Find the declared remote resource by stable identity or explicit external ID.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        """
        volume_id = (observed or {}).get('volume_id') or resource.config.get('volume_id')
        try:
            return one(self.aws.session(resource).client('ec2').describe_volumes(
                **({'VolumeIds': [volume_id]} if volume_id else {'Filters': filters(resource, context)}))['Volumes'])
        except ClientError as error:
            if error.response['Error']['Code'] == 'InvalidVolume.NotFound':
                return None
            raise

    def preflight(self, resource, context):
        """Validate declaration, access and live ownership before any creation.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        required = (*SCOPE, 'volume_id') if resource.ownership == 'external' else (*SCOPE, 'zone', 'size_gb')
        fields(resource.config, required, required, 'data volume')
        if resource.ownership == 'managed':
            if not isinstance(resource.config['zone'], str) or not resource.config['zone'].startswith(resource.config['region']):
                raise ValueError('Data volume zone must belong to its region')
            if type(resource.config['size_gb']) is not int or resource.config['size_gb'] < 1:
                raise ValueError('Data volume size_gb must be a positive integer')
        current = self.find(resource, context)
        if resource.ownership == 'external' and current is None:
            raise ValueError('External data volume does not exist')
        if current and resource.ownership == 'managed':
            owned(current, resource, context)

    def ensure(self, resource, previous, dependencies, context):
        """Reconcile the declared resource idempotently and return its current outputs.

        :param resource: Resource declaration being reconciled.
        :param previous: Current provider outputs from observation, not a local receipt.
        :param dependencies: Observed outputs of declared resource dependencies.
        :param context: Stable deployment ownership and creation-token context.
        """
        ec2 = self.aws.session(resource).client('ec2')
        volume = self.find(resource, context, previous)
        if volume:
            if resource.ownership == 'managed':
                owned(volume, resource, context)
        elif resource.ownership == 'external':
            raise ValueError('External data volume disappeared')
        else:
            volume = ec2.create_volume(AvailabilityZone=resource.config['zone'],
                Size=resource.config['size_gb'], VolumeType='gp3', Encrypted=True,
                ClientToken=context.token(resource), TagSpecifications=[{'ResourceType': 'volume', 'Tags': tags(resource, context)}])
        if volume['State'] == 'creating':
            ec2.get_waiter('volume_available').wait(VolumeIds=[volume['VolumeId']])
        return {'volume_id': volume['VolumeId'], 'zone': volume['AvailabilityZone']}

    def destroy(self, resource, observed, context):
        """Delete the currently owned resource; absence is already a successful result.

        :param resource: Resource declaration being reconciled.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        :param context: Stable deployment ownership and creation-token context.
        """
        volume = self.find(resource, context, observed)
        if volume is None:
            return
        owned(volume, resource, context)
        if volume.get('Attachments'):
            raise ValueError('Data volume is attached; remove its attachment first')
        self.aws.session(resource).client('ec2').delete_volume(VolumeId=volume['VolumeId'])


class Attachment(Volume):
    """Manage an owned Box-to-volume attachment without changing volume retention."""
    def observe(self, resource, context):
        """Read the declared volume attachment, rejecting another Box's attachment.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        box, volume = self.dependencies(resource, context)
        current = Volume(self.aws).find(volume, context)
        if not current or not current.get('Attachments'):
            return None
        instance = EC2(self.aws).find(box, context)
        device = resource.config.get('device','/dev/sdf')
        if not instance or any(a['InstanceId'] != instance['InstanceId'] or a['Device'] != device for a in current['Attachments']):
            raise ValueError('Data volume attachment changed ownership')
        owned(instance, box, context)
        if volume.ownership == 'managed':
            owned(current, volume, context)
        return {'volume_id':current['VolumeId'],'instance_id':instance['InstanceId'],'device':device}

    def dependencies(self, resource, context):
        """Validate and return the declared attachment endpoints.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        fields(resource.config, (*SCOPE, 'box', 'volume', 'device'), (*SCOPE, 'box', 'volume'), 'volume attachment')
        resources = {r.id: r for r in context.run.resources}
        box, volume = resources.get(resource.config['box']), resources.get(resource.config['volume'])
        if not box or not volume or box.type != 'aws.ec2' or volume.type != 'aws.volume':
            raise ValueError('Volume attachment requires EC2 and volume resources')
        if box.ownership != 'managed' or resource.ownership != 'managed':
            raise ValueError('Volume attachment requires a managed Box and attachment')
        if not {box.id, volume.id} <= set(resource.depends_on):
            raise ValueError('Volume attachment must depend on Box and volume')
        if any(other.config.get(k) != resource.config.get(k) for other in (box, volume) for k in ('account', 'region')):
            raise ValueError('Volume attachment dependencies must share its AWS scope')
        if not re.fullmatch('/dev/sd[f-p]', resource.config.get('device', '/dev/sdf')):
            raise ValueError('Volume device must be /dev/sdf through /dev/sdp')
        return box, volume

    def preflight(self, resource, context):
        """Validate declaration, access and live ownership before any creation.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        box, volume = self.dependencies(resource, context)
        ec2 = self.aws.session(resource).client('ec2')
        zone = ec2.describe_subnets(SubnetIds=[box.config['subnet_id']])['Subnets'][0]['AvailabilityZone']
        current = Volume(self.aws).find(volume, context)
        if zone != (current['AvailabilityZone'] if current else volume.config['zone']):
            raise ValueError('Data volume and Box must share their availability zone')
        if current and current.get('Attachments'):
            instance = EC2(self.aws).find(box, context)
            if instance is None or any(a['InstanceId'] != instance['InstanceId'] for a in current['Attachments']):
                raise ValueError('Data volume is attached to another Box')

    def ensure(self, resource, previous, dependencies, context):
        """Reconcile the declared resource idempotently and return its current outputs.

        :param resource: Resource declaration being reconciled.
        :param previous: Current provider outputs from observation, not a local receipt.
        :param dependencies: Observed outputs of declared resource dependencies.
        :param context: Stable deployment ownership and creation-token context.
        """
        box, volume = self.dependencies(resource, context)
        instance = EC2(self.aws).find(box, context, dependencies[box.id])
        owned(instance, box, context)
        current = Volume(self.aws).find(volume, context, dependencies[volume.id])
        if volume.ownership == 'managed':
            owned(current, volume, context)
        device = resource.config.get('device', '/dev/sdf')
        if any(a['InstanceId'] != instance['InstanceId'] or a['Device'] != device for a in current.get('Attachments', [])):
            raise ValueError('Data volume attachment changed ownership')
        ec2 = self.aws.session(resource).client('ec2')
        if not current.get('Attachments'):
            ec2.attach_volume(VolumeId=current['VolumeId'], InstanceId=instance['InstanceId'], Device=device)
        ec2.get_waiter('volume_in_use').wait(VolumeIds=[current['VolumeId']])
        return {'volume_id': current['VolumeId'], 'instance_id': instance['InstanceId'], 'device': device}

    def destroy(self, resource, observed, context):
        """Delete the currently owned resource; absence is already a successful result.

        :param resource: Resource declaration being reconciled.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        :param context: Stable deployment ownership and creation-token context.
        """
        box, volume = self.dependencies(resource, context)
        current = Volume(self.aws).find(volume, context, observed)
        if current is None or not current.get('Attachments'):
            return
        instance = EC2(self.aws).find(box, context)
        if instance is None:
            raise ValueError('Cannot prove ownership of attached Box')
        owned(instance, box, context)
        if volume.ownership == 'managed':
            owned(current, volume, context)
        device = resource.config.get('device', '/dev/sdf')
        if any(a['InstanceId'] != instance['InstanceId'] or a['Device'] != device for a in current['Attachments']):
            raise ValueError('Data volume attachment changed ownership')
        ec2 = self.aws.session(resource).client('ec2')
        ec2.detach_volume(VolumeId=current['VolumeId'], InstanceId=instance['InstanceId'], Device=device, Force=False)
        ec2.get_waiter('volume_available').wait(VolumeIds=[current['VolumeId']])
