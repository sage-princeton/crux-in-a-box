"""Operator configuration, independent of provider-generated resource identities."""
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from .spec import Resource, fields, name


@dataclass(frozen=True)
class RunConfiguration:
    """Hold one Run's inputs and execution settings; omit shared account configuration."""

    name: str
    scaffold: str
    inputs: str
    model: str
    effort: str
    provider: dict = field(default_factory=dict)
    box: dict = field(default_factory=dict)
    resources: tuple[Resource, ...] = ()

    @classmethod
    def parse(cls, value):
        """Validate a Run's settings without network access.

        :param value: JSON object supplied by the operator.
        :returns: Configuration with validated resource declarations.
        :raises ValueError: Required values, scope or scaffold settings are invalid.
        """
        fields(value, cls.__dataclass_fields__, ('name', 'scaffold', 'inputs', 'model', 'effort'), 'Run')
        name(value['name'], 'Run name')
        if value['scaffold'] not in ('sdk', 'codex', 'claude'):
            raise ValueError('Scaffold must be sdk, codex or claude')
        for key in ('inputs', 'model', 'effort'):
            if not isinstance(value[key], str) or not value[key] or any(ord(c) < 32 for c in value[key]):
                raise ValueError(f'Run {key} must be a nonempty string without control characters')
        allowed_effort = ('none', 'low', 'medium', 'high', 'xhigh', 'max')
        if value['effort'] not in allowed_effort:
            raise ValueError('Unsupported Run effort')
        box = value.get('box', {})
        fields(box, ('instance_type', 'disk_gb'), (), 'Box')
        if 'disk_gb' in box and (type(box['disk_gb']) is not int or box['disk_gb'] < 20):
            raise ValueError('Box disk_gb must be an integer of at least 20')
        if 'instance_type' in box and (not isinstance(box['instance_type'], str) or not box['instance_type']):
            raise ValueError('Box instance_type must be a nonempty string')
        provider = value.get('provider', {})
        fields(provider, ('type', 'api', 'base_url', 'api_key_env', 'extra_body'), (), 'Provider')
        if provider:
            if provider.get('type') != 'openai-compatible' or provider.get('api') not in ('responses', 'chat-completions'):
                raise ValueError('Custom provider requires openai-compatible type and an explicit API')
            key = provider.get('api_key_env')
            import re
            if not isinstance(key, str) or not re.fullmatch('[A-Z][A-Z0-9_]*', key):
                raise ValueError('Provider requires an api_key_env credential name')
            if key in ('PATH', 'HOME', 'PYTHONPATH', 'CODEX_HOME') or key.startswith(('CRUX_', 'AWS_', 'LANGFUSE_', 'AGENTRQ_')):
                raise ValueError('Provider api_key_env is reserved')
            if not isinstance(provider.get('extra_body', {}), dict):
                raise TypeError('Provider extra_body must be an object')
            url = urlsplit(provider.get('base_url', ''))
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('Provider base_url must be a credential-free HTTPS URL')
            if value['scaffold'] != 'sdk' and (url.hostname != 'openrouter.ai' or provider.get('api_key_env') != 'OPENROUTER_API_KEY'):
                raise ValueError('ACP custom provider must be OpenRouter with OPENROUTER_API_KEY')
        declarations = value.get('resources', [])
        if not isinstance(declarations, list):
            raise TypeError('Run resources must be a list')
        resources = tuple(Resource.parse(r) for r in declarations)
        ids = [r.id for r in resources]
        if len(ids) != len(set(ids)) or set(ids) & {'box', 'workspace'}:
            raise ValueError('Resource IDs must be unique and cannot use box or workspace')
        if any(set(r.config) & {'profile', 'region', 'account'} for r in resources):
            raise ValueError('Resource AWS scope comes from account setup, not Run settings')
        return cls(**dict(value, resources=resources))

    @classmethod
    def load(cls, path):
        """Read a Run document with inputs relative to its location.

        :param path: Operator's JSON file.
        :returns: Validated configuration with an absolute input path.
        """
        path = Path(path).expanduser().resolve()
        result = cls.parse(json.loads(path.read_text()))
        return replace(result, inputs=str((path.parent/Path(result.inputs).expanduser()).resolve()))
