"""Configure an owned Box and start a Run explicitly."""
import re
from urllib.parse import urlparse


def validate_secrets(run, values, *, workspace_required=True):
    if not isinstance(values, dict):
        raise TypeError('Run secrets must be a JSON object')
    for key, value in values.items():
        if not re.fullmatch('[A-Z_][A-Z0-9_]*', key) or not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError('Run secret names must be environment keys and values nonempty strings without whitespace')
    required = [run.scaffold.credential]
    if run.scaffold.type == 'agentrq':
        if workspace_required:
            required += ['AGENTRQ_WORKSPACE_ID', 'AGENTRQ_WORKSPACE_TOKEN']
        url = urlparse(run.scaffold.configuration.get('control_mcp_base', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('AgentRQ requires a control_mcp_base HTTPS origin')
    for key in required:
        if not isinstance(values.get(key), str) or not values[key] or any(word in values[key] for word in ('CHANGE_ME', 'REPLACE_ME', '<your')):
            raise ValueError(f'Run secrets require {key}')
    return values


class Invoker:
    def __init__(self, lifecycle, transport):
        self.lifecycle, self.transport = lifecycle, transport

    def health(self):
        lifecycle = self.lifecycle
        with lifecycle.locked(create=False) as (state, context):
            if state['phase'] not in ('configured', 'running') or not state.get('deployment'):
                raise ValueError('Configure the Scaffold before checking its health')
            deployment = state['deployment']
            self.transport.health(host=deployment['host'], owner=context.owner,
                                  bundle_sha256=deployment['sha256'])
            return state

    def configure(self, bundle, secrets):
        lifecycle = self.lifecycle
        validate_secrets(lifecycle.run, secrets)
        with lifecycle.locked(create=False) as (state, context):
            if state['phase'] in ('destroying', 'destroyed'):
                raise ValueError('Cannot configure a destroyed deployment')
            box = state['resources'].get(lifecycle.run.box, {})
            if box.get('status') != 'ready' or not box.get('outputs', {}).get('public_ip'):
                raise ValueError('Provision a reachable Box before configuring its Scaffold')
            resource = next(r for r in lifecycle.run.resources if r.id == lifecycle.run.box)
            host = box['outputs']['public_ip']
            for attachment in lifecycle.run.resources:
                if attachment.type == 'aws.address_attachment' and attachment.config['box'] == resource.id:
                    host = state['resources'][attachment.id]['outputs']['public_ip']
            deployment = dict(bundle, host=host)
            state.update(phase='configuring', pending_deployment=deployment)
            lifecycle.save(state)
            try:
                self.transport.configure(host=deployment['host'], bundle=bundle, secrets=secrets,
                                         settings={'owner': context.owner, 'bundle_sha256': bundle['sha256'],
                                                   'region': resource.config['region']})
            except BaseException:
                state['phase'] = 'configuration_failed'
                lifecycle.save(state)
                raise
            state.update(phase='configured', deployment=deployment)
            state.pop('pending_deployment', None)
            lifecycle.save(state)
            return state

    def start(self):
        lifecycle = self.lifecycle
        with lifecycle.locked(create=False) as (state, context):
            if state['phase'] not in ('configured', 'running') or not state.get('deployment'):
                raise ValueError('The Scaffold must be successfully configured before starting a Run')
            deployment = state['deployment']
            self.transport.start(host=deployment['host'], owner=context.owner,
                                 bundle_sha256=deployment['sha256'])
            state['phase'] = 'running'
            lifecycle.save(state)
            return state
