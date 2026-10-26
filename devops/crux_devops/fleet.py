"""Native shared SSH, ingress and narrowly scoped Box identity resources."""
import base64
import ipaddress
import json
import re

from botocore.exceptions import ClientError

from .aws import SCOPE, one, owned, tags
from .spec import fields


def identifier(resource, context):
    return context.run.name[:32]+'-'+resource.id[:16]+'-'+context.token(resource)[:8]


def managed(resource):
    if resource.ownership != 'managed':
        raise ValueError('Fleet drivers manage newly declared resources; reference existing IDs/names in Box config')


def absent(error, code):
    if error.response['Error']['Code'] != code:
        raise error


class SecurityGroup:
    def __init__(self, aws):
        self.aws = aws

    def find(self, resource, context):
        return one(self.aws.session(resource).client('ec2').describe_security_groups(Filters=[
            {'Name': 'group-name', 'Values': [identifier(resource, context)]},
            {'Name': 'vpc-id', 'Values': [resource.config['vpc_id']]}])['SecurityGroups'])

    def permissions(self, resource, context, dependencies=None):
        rules = resource.config['ingress']
        if not isinstance(rules, list):
            raise TypeError('Security group ingress must be a list')
        resources = {r.id: r for r in context.run.resources}
        result = []
        for rule in rules:
            fields(rule, ('port', 'cidr', 'source_group'), ('port',), 'ingress rule')
            port = rule['port']
            if type(port) is not int or not 1 <= port <= 65535 or ('cidr' in rule) == ('source_group' in rule):
                raise ValueError('Ingress requires a TCP port and exactly one CIDR or source_group resource')
            item = {'IpProtocol': 'tcp', 'FromPort': port, 'ToPort': port}
            if 'cidr' in rule:
                network = ipaddress.ip_network(rule['cidr'], strict=True)
                if network.version != 4:
                    raise ValueError('Ingress CIDR must be IPv4')
                item['IpRanges'] = [{'CidrIp': str(network)}]
            else:
                source = resources.get(rule['source_group'])
                if not source or source.type != 'aws.security_group' or source.id not in resource.depends_on:
                    raise ValueError('Ingress source_group must be a declared security group dependency')
                if any(source.config[k] != resource.config[k] for k in ('vpc_id', 'region', 'account')):
                    raise ValueError('Ingress source group must share its VPC and AWS scope')
                if dependencies is not None:
                    item['UserIdGroupPairs'] = [{'GroupId': dependencies[source.id]['group_id']}]
            result.append(item)
        return result

    def preflight(self, resource, context):
        managed(resource)
        fields(resource.config, (*SCOPE, 'vpc_id', 'ingress'), (*SCOPE, 'vpc_id', 'ingress'), 'security group')
        self.permissions(resource, context)
        self.aws.session(resource).client('ec2').describe_vpcs(VpcIds=[resource.config['vpc_id']])
        current = self.find(resource, context)
        if current:
            owned(current, resource, context)

    def ensure(self, resource, previous, dependencies, context):
        ec2 = self.aws.session(resource).client('ec2')
        group = self.find(resource, context)
        if group:
            owned(group, resource, context)
            group_id = group['GroupId']
        else:
            group_id = ec2.create_security_group(GroupName=identifier(resource, context),
                Description='CRUX declared ingress', VpcId=resource.config['vpc_id'],
                TagSpecifications=[{'ResourceType': 'security-group', 'Tags': tags(resource, context)}])['GroupId']
        for permission in self.permissions(resource, context, dependencies):
            try:
                ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[permission])
            except ClientError as error:
                absent(error, 'InvalidPermission.Duplicate')
        return {'group_id': group_id}

    def destroy(self, resource, receipt, context):
        current = self.find(resource, context)
        if current:
            owned(current, resource, context)
            self.aws.session(resource).client('ec2').delete_security_group(GroupId=current['GroupId'])


class Identity:
    def __init__(self, aws):
        self.aws = aws

    def find(self, resource, context, kind):
        iam = self.aws.session(resource).client('iam')
        try:
            return (iam.get_role(RoleName=identifier(resource, context))['Role'] if kind == 'role' else
                    iam.get_instance_profile(InstanceProfileName=identifier(resource, context))['InstanceProfile'])
        except ClientError as error:
            absent(error, 'NoSuchEntity')
            return None

    def preflight(self, resource, context):
        managed(resource)
        fields(resource.config, (*SCOPE, 'parameters', 'kms_keys'), (*SCOPE, 'parameters'), 'Box identity')
        parameters = resource.config['parameters']
        if not isinstance(parameters, list) or not parameters or any(not isinstance(p, str) or not re.fullmatch('/[A-Za-z0-9_/.-]+', p) for p in parameters):
            raise ValueError('Box identity requires explicit SSM parameter paths without wildcards')
        keys = resource.config.get('kms_keys', [])
        if not isinstance(keys, list) or any(not isinstance(k, str) or not re.fullmatch(r'arn:aws:kms:[a-z0-9-]+:\d{12}:key/[a-zA-Z0-9-]+', k) for k in keys):
            raise ValueError('KMS keys must be explicit key ARNs')
        for kind in ('role', 'profile'):
            value = self.find(resource, context, kind)
            if value:
                owned(value, resource, context)

    def ensure(self, resource, previous, dependencies, context):
        iam = self.aws.session(resource).client('iam')
        name = identifier(resource, context)
        role = self.find(resource, context, 'role')
        if role:
            owned(role, resource, context)
        else:
            iam.create_role(RoleName=name, Tags=tags(resource, context), AssumeRolePolicyDocument=json.dumps({
                'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'ec2.amazonaws.com'},'Action':'sts:AssumeRole'}]}))
        config = resource.config
        statement = [{'Effect': 'Allow', 'Action': ['ssm:GetParameter'],
                      'Resource': [f'arn:aws:ssm:{config["region"]}:{config["account"]}:parameter{p}' for p in config['parameters']]}]
        if config.get('kms_keys'):
            statement.append({'Effect': 'Allow', 'Action': ['kms:Decrypt'], 'Resource': config['kms_keys'],
                              'Condition': {'StringEquals': {'kms:ViaService': f'ssm.{config["region"]}.amazonaws.com'}}})
        iam.put_role_policy(RoleName=name, PolicyName='crux-parameters', PolicyDocument=json.dumps({'Version':'2012-10-17','Statement':statement}))
        profile = self.find(resource, context, 'profile')
        if profile:
            owned(profile, resource, context)
            if any(r['RoleName'] != name for r in profile['Roles']):
                raise ValueError('Box instance profile role changed ownership')
        else:
            profile = iam.create_instance_profile(InstanceProfileName=name, Tags=tags(resource, context))['InstanceProfile']
        if not profile['Roles']:
            iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
        iam.get_waiter('instance_profile_exists').wait(InstanceProfileName=name)
        return {'instance_profile': name}

    def destroy(self, resource, receipt, context):
        iam = self.aws.session(resource).client('iam')
        name = identifier(resource, context)
        role, profile = (self.find(resource, context, kind) for kind in ('role', 'profile'))
        for value in (role, profile):
            if value:
                owned(value, resource, context)
        if profile:
            if any(r['RoleName'] != name for r in profile['Roles']):
                raise ValueError('Box instance profile role changed ownership')
            if profile['Roles']:
                iam.remove_role_from_instance_profile(InstanceProfileName=name, RoleName=name)
            iam.delete_instance_profile(InstanceProfileName=name)
        if role:
            try:
                iam.delete_role_policy(RoleName=name, PolicyName='crux-parameters')
            except ClientError as error:
                absent(error, 'NoSuchEntity')
            iam.delete_role(RoleName=name)


class KeyPair:
    def __init__(self, aws):
        self.aws = aws

    def find(self, resource, context):
        try:
            return one(self.aws.session(resource).client('ec2').describe_key_pairs(KeyNames=[identifier(resource, context)])['KeyPairs'])
        except ClientError as error:
            absent(error, 'InvalidKeyPair.NotFound')
            return None

    def preflight(self, resource, context):
        managed(resource)
        fields(resource.config, (*SCOPE, 'public_key'), (*SCOPE, 'public_key'), 'SSH public key')
        value = resource.config['public_key']
        if not isinstance(value, str) or not re.fullmatch(r'ssh-(?:rsa|ed25519) [A-Za-z0-9+/=]+(?: [^\r\n]+)?', value):
            raise ValueError('Provide an OpenSSH RSA or Ed25519 public key; private keys stay on the Invoker')
        base64.b64decode(value.split()[1], validate=True)
        current = self.find(resource, context)
        if current:
            owned(current, resource, context)

    def ensure(self, resource, previous, dependencies, context):
        current = self.find(resource, context)
        if current:
            owned(current, resource, context)
        else:
            self.aws.session(resource).client('ec2').import_key_pair(KeyName=identifier(resource, context),
                PublicKeyMaterial=resource.config['public_key'].encode(),
                TagSpecifications=[{'ResourceType': 'key-pair', 'Tags': tags(resource, context)}])
        return {'key_name': identifier(resource, context)}

    def destroy(self, resource, receipt, context):
        current = self.find(resource, context)
        if current:
            owned(current, resource, context)
            self.aws.session(resource).client('ec2').delete_key_pair(KeyName=current['KeyName'])
