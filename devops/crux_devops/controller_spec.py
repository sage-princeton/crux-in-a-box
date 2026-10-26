"""Controller deployments share resource lifecycle, without a research Scaffold."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .spec import Resource, fields, name, ordered_resources


@dataclass(frozen=True)
class Controller:
    name: str
    box: str
    resources: tuple[Resource, ...]
    configuration: dict
    version: int = 1
    kind: str = 'controller'

    @classmethod
    def parse(cls, value):
        fields(value, cls.__dataclass_fields__, cls.__dataclass_fields__, 'controller')
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
        result = cls(value['name'], value['box'], resources, config)
        result.ordered()
        return result

    @classmethod
    def load(cls, path):
        return cls.parse(json.loads(Path(path).read_text()))

    def ordered(self):
        return ordered_resources(self.resources)

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def plan(self):
        return {'deployment':'controller','name':self.name,
                'steps':['validate','create-resources','assemble-bundle','configure-controller','health'],
                'resources':[{'id':r.id,'type':r.type,'depends_on':list(r.depends_on),'ownership':r.ownership,'teardown':r.teardown} for r in self.ordered()]}
