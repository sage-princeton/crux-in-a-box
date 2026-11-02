"""Idempotent shared account installation, separate from Run resource graphs."""
import json


def ensure_stack(client, template, parameters):
    """Ensure owned installation infrastructure and return CloudFormation outputs.

    :param client: Authenticated CloudFormation client.
    :param template: Installation template body (string or parsed JSON).
    :param parameters: SSH access/public-key parameters.
    :returns: Output-name to generated-value mapping.
    :raises ValueError: Remote ownership, settings or failed stack require inspection.
    """
    stacks = [stack for page in client.get_paginator('describe_stacks').paginate()
              for stack in page['Stacks'] if stack['StackName'] == 'crux-account']
    if len(stacks) > 1:
        raise ValueError('Ambiguous installation stack')
    if stacks:
        stack = stacks[0]
        if {tag['Key']: tag['Value'] for tag in stack.get('Tags', [])}.get('CruxInstallation') != 'crux-account':
            raise ValueError('Installation ownership differs; refusing to adopt an existing stack')
        status = stack['StackStatus']
        if status == 'CREATE_IN_PROGRESS':
            client.get_waiter('stack_create_complete').wait(StackName='crux-account')
        elif status not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE'):
            raise ValueError('Installation is not ready; inspect its CloudFormation events before retrying')
        existing = client.get_template(StackName='crux-account')['TemplateBody']
        if existing != template or {p['ParameterKey']: p['ParameterValue'] for p in stack.get('Parameters', [])} != {p['ParameterKey']: p['ParameterValue'] for p in parameters}:
            raise ValueError('Installation settings changed; inspect a CloudFormation change set before updating shared infrastructure')
    else:
        client.create_stack(StackName='crux-account', TemplateBody=template if isinstance(template,str) else json.dumps(template),
                            Parameters=parameters, Capabilities=['CAPABILITY_IAM'],
                            Tags=[{'Key':'CruxInstallation','Value':'crux-account'}],
                            EnableTerminationProtection=True)
        client.get_waiter('stack_create_complete').wait(StackName='crux-account')
    stack = client.describe_stacks(StackName='crux-account')['Stacks'][0]
    return {value['OutputKey']: value['OutputValue'] for value in stack['Outputs']}


AMI_PARAMETER = '/aws/service/canonical/ubuntu/server/noble/stable/current/amd64/hvm/ebs-gp3/ami-id'


def discover(session, profile, account):
    """Resolve shared prerequisites from the named/tagged AWS installation at runtime.

    :param session: Session using the operator's explicitly selected profile.
    :param profile: Selected profile name, carried into resource drivers.
    :param account: Account ID verified with STS by the caller.
    :returns: Live installation outputs and the current supported Ubuntu AMI.
    :raises ValueError: Installation is absent, foreign or not ready.
    """
    stack = session.client('cloudformation').describe_stacks(StackName='crux-account')['Stacks'][0]
    if {tag['Key']:tag['Value'] for tag in stack.get('Tags',[])}.get('CruxInstallation') != 'crux-account':
        raise ValueError('Installation ownership tag is missing; run account setup')
    if stack['StackStatus'] not in ('CREATE_COMPLETE','UPDATE_COMPLETE','UPDATE_ROLLBACK_COMPLETE'):
        raise ValueError('CRUX account installation is not ready')
    outputs = {entry['OutputKey']:entry['OutputValue'] for entry in stack['Outputs']}
    params = {entry['ParameterKey']:entry['ParameterValue'] for entry in stack['Parameters']}
    image = session.client('ssm').get_parameter(Name=AMI_PARAMETER)['Parameter']['Value']
    return {'profile':profile,'region':session.region_name,'account':account,
            'vpc_id':outputs['VpcId'],'availability_zone':outputs['AvailabilityZone'],
            'ssh_cidr':params.get('SshCidr'),
            'box':{'image_id':image,'subnet_id':outputs['SubnetId'],'security_group_ids':[outputs['GroupId']],
                   'key_name':outputs['KeyName'],'instance_profile':outputs['InstanceProfile']}}
