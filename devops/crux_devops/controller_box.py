"""Native controller filesystem and SSM operations; OS administration stays in Bash."""
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

from .controller_spec import Controller


def write(path, text, mode=0o644):
    if path.resolve() != path.absolute():
        raise ValueError('Controller configuration paths may not contain symlinks')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(text)


def validate_environment(parameter):
    lines = parameter.splitlines()
    values = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch('[A-Z][A-Z0-9_]*', key) or key in values:
            raise ValueError('Controller SSM value requires unique env keys')
        values[key] = value
    token = values.get('AGENTRQ_AUTH_ROOT_ACCESS_TOKEN', '')
    if not token or any(c.isspace() for c in token) or any(p in token for p in ('CHANGE_ME','REPLACE_ME','<your')):
        raise ValueError('Controller SSM value requires a real root access token')
    return parameter


def render(controller, settings, parameter, source, root):
    root, source = Path(root), Path(source)
    lines = validate_environment(parameter).splitlines()
    overrides = {'AGENTRQ_BASE_URL':'https://'+settings['hostname'], 'AGENTRQ_DOMAIN':settings['hostname'],
                 'AGENTRQ_DB_SQLITE_DSN':'file:/_storage/agentrq.db?_txlock=immediate'}
    text = '\n'.join(line for line in lines if line.partition('=')[0] not in overrides)+'\n'
    text += ''.join(key+'='+value+'\n' for key,value in overrides.items())
    write(root/'srv/agentrq/agentrq.env',text,0o600)
    write(root/'srv/agentrq/compose.yaml',(source/'devops/ec2-control/compose.yaml').read_text())
    port=controller.configuration.get('port',2026)
    write(root/'srv/agentrq/compose.env',f'PRIVATE_IP={settings["private_ip"]}\nAGENTRQ_PORT={port}\n',0o600)
    write(root/'etc/systemd/system/agentrq.service','''[Unit]
Description=AgentRQ control plane
After=docker.service network-online.target srv-agentrq.mount
Requires=docker.service
RequiresMountsFor=/srv/agentrq

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/usr/bin/docker compose --project-name crux-control --env-file /srv/agentrq/compose.env --file /srv/agentrq/compose.yaml up --abort-on-container-exit
ExecStop=/usr/bin/docker compose --project-name crux-control --env-file /srv/agentrq/compose.env --file /srv/agentrq/compose.yaml down

[Install]
WantedBy=multi-user.target
''')
    email=controller.configuration.get('email')
    caddy=('{\n\temail '+email+'\n}\n' if email else '')+settings['hostname']+' {\n\treverse_proxy 127.0.0.1:'+str(port)+'\n}\n'
    write(root/'etc/caddy/Caddyfile',caddy)
    write(root/'etc/crux-deployment.json',json.dumps({'owner':settings['owner'],'bundle_sha256':settings['bundle_sha256'],
          'service':'agentrq','run_fingerprint':controller.fingerprint}),0o600)


def main():
    action,bundle_name,settings_name=sys.argv[1:]
    bundle, settings_path=Path(bundle_name).resolve(),Path(settings_name)
    manifest=json.loads((bundle/'bundle.json').read_text())
    for name,digest in manifest['files'].items():
        path=bundle/name
        if path.is_symlink() or not path.resolve().is_relative_to(bundle) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Controller bundle integrity mismatch')
    controller=Controller.parse(manifest['controller'])
    settings=json.loads(settings_path.read_text())
    if action=='prepare':
        values={'CRUX_CONTROLLER_BUNDLE':str(bundle),'CRUX_CONTROLLER_SETTINGS':str(settings_path),
                'DATA_VOLUME_ID':settings['data_volume_id'], 'INITIALIZE_DATA':'true' if settings.get('initialize_data') is True else 'false'}
        if not re.fullmatch('vol-[a-f0-9]+',values['DATA_VOLUME_ID']):
            raise ValueError('Invalid controller data volume identity')
        write(Path('/etc/crux-controller.env'),''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in values.items()),0o600)
    elif action=='configure':
        import boto3
        region=next(r for r in controller.resources if r.id==controller.box).config['region']
        parameter=boto3.client('ssm',region_name=region).get_parameter(Name=controller.configuration['parameter'],WithDecryption=True)['Parameter']['Value']
        render(controller,settings,parameter,bundle/'source',Path('/'))
        uuid=os.environ['CRUX_VOLUME_UUID']
        if not re.fullmatch('[a-fA-F0-9-]+',uuid):
            raise ValueError('Invalid mounted data filesystem UUID')
        fstab=Path('/etc/fstab')
        lines=[line for line in fstab.read_text().splitlines() if len(line.split())<2 or line.split()[1]!='/srv/agentrq']
        write(fstab,'\n'.join(lines)+f'\nUUID={uuid} /srv/agentrq ext4 defaults,nofail 0 2\n')
    else:
        raise ValueError('Unknown controller Box operation')


if __name__=='__main__':
    main()
