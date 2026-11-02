"""Controller deployments share resource lifecycle, without a research Scaffold."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .spec import Resource, fields, name, ordered_resources


@dataclass(frozen=True)
class Controller:
    """Describe the independent controller resource graph and service configuration."""
    name: str
    box: str
    resources: tuple[Resource, ...]
    configuration: dict
    version: int = 1
    kind: str = 'controller'
    settings: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, value):
        """Validate a configuration object and return its typed declaration.

        :param value: Configuration value to validate.
        """
        fields(value, cls.__dataclass_fields__, ('version','kind','name','box','resources','configuration'), 'controller')
        if type(value['version']) is not int or value['version'] != 1 or value['kind'] != 'controller':
            raise ValueError('Unsupported controller specification')
        name(value['name'], 'controller.name')
        name(value['box'], 'controller.box')
        if not isinstance(value['resources'], list):
            raise TypeError('Controller resources must be a list')
        resources = tuple(Resource.parse(v) for v in value['resources'])
        ids = [r.id for r in resources]
        if len(set(ids)) != len(ids) or value['box'] not in ids:
            raise ValueError('Controller resources need unique IDs and a declared Box')
        config = value['configuration']
        fields(config, ('parameter','hostname','email','port','data_resource','initialize_data'), ('parameter','data_resource'), 'controller configuration')
        if type(config.get('initialize_data', False)) is not bool:
            raise ValueError('initialize_data must be a boolean')
        for key, pattern in (('parameter',r'/[A-Za-z0-9_/.-]+'),('hostname',r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?'),('email',r'[^\s@;{}]+@[^\s@;{}]+')):
            if key in config and (not isinstance(config[key], str) or not re.fullmatch(pattern, config[key])):
                raise ValueError(f'Invalid controller {key}')
        if type(config.get('port', 2026)) is not int or not 1 <= config.get('port', 2026) <= 65535:
            raise ValueError('Invalid controller port')
        box = next(r for r in resources if r.id == value['box'])
        data = next((r for r in resources if r.id == config['data_resource']), None)
        if box.type != 'aws.ec2' or box.ownership != 'managed' or data is None or data.type != 'aws.volume':
            raise ValueError('Controller requires a managed EC2 Box and declared data volume')
        if not any(r.type == 'aws.volume_attachment' and r.config.get('box') == box.id and
                    r.config.get('volume') == data.id and {box.id, data.id} <= set(r.depends_on) for r in resources):
            raise ValueError('Controller requires an explicit data volume attachment')
        result = cls(value['name'], value['box'], resources, config, settings=value.get('settings', {}))
        result.ordered()
        return result

    @classmethod
    def load(cls, path):
        """Read and validate a JSON declaration from the supplied path.

        :param path: Selected filesystem path.
        """
        return cls.parse(json.loads(Path(path).read_text()))

    def ordered(self):
        """Return resources in dependency order; reject missing references and cycles."""
        return ordered_resources(self.resources)

    @property
    def fingerprint(self):
        """Return the stable digest of the declared deployment settings."""
        return hashlib.sha256(json.dumps(self.settings or asdict(self), sort_keys=True).encode()).hexdigest()

    def plan(self):
        """Return phase and resource-policy information without creating resources."""
        return {'deployment':'controller','name':self.name,
                'steps':['validate','create-resources','assemble-bundle','configure-controller','health'],
                'resources':[{'id':r.id,'type':r.type,'depends_on':list(r.depends_on),'ownership':r.ownership,'teardown':r.teardown} for r in self.ordered()]}


def resolve(configuration, account):
    """Expand controller options using live installation resources.

    :param configuration: Optional hostname/email; defaults use the Box's sslip.io name.
    :param account: Discovered shared AWS installation.
    :returns: Independent controller graph with retained data volume and address.
    """
    fields(configuration,('hostname','email'),(), 'controller options')
    scope = {k:account[k] for k in ('profile','region','account')}
    box = account['box'] | scope | {'instance_type':'t3.small','disk_gb':20,
        'identity_resource':'identity','security_group_resources':['ingress']}
    box.pop('instance_profile')
    box.pop('security_group_ids')
    resources = [
        Resource('ingress','aws.security_group',scope | {'vpc_id':account['vpc_id'],
            'ingress':[{'port':22,'cidr':account['ssh_cidr']},{'port':80,'cidr':'0.0.0.0/0'},
                       {'port':443,'cidr':'0.0.0.0/0'}]}),
        Resource('identity','aws.identity',scope | {'parameters':['/crux/control/env']}),
        Resource('box','aws.ec2',box,depends_on=('ingress','identity')),
        Resource('address','aws.elastic_ip',scope,teardown='retain'),
        Resource('address-binding','aws.address_attachment',scope | {'box':'box','address':'address'},depends_on=('box','address')),
        Resource('data','aws.volume',scope | {'zone':account['availability_zone'],'size_gb':20},teardown='retain'),
        Resource('data-binding','aws.volume_attachment',scope | {'box':'box','volume':'data'},depends_on=('box','data')),
    ]
    value = Controller('crux-control','box',tuple(resources),
                       configuration | {'parameter':'/crux/control/env','data_resource':'data','initialize_data':True},
                       settings={'controller':configuration})
    return Controller.parse(json.loads(json.dumps(asdict(value))))
