"""Native controller bundle assembly and Invoker operations."""
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from .bundle import package, private_path
from .controller_box import validate_environment
from .invoker import Invoker


class ControllerInvoker(Invoker):
    def configure(self, bundle):
        lifecycle = self.lifecycle
        with lifecycle.locked(create=False) as (state, context):
            if state['phase'] in ('destroying', 'destroyed'):
                raise ValueError('Cannot configure a destroyed controller')
            self.verify_box(state, context)
            controller = lifecycle.run
            box = next(r for r in controller.resources if r.id == controller.box)
            entry = state['resources'].get(box.id, {})
            if entry.get('status') != 'ready' or not entry.get('outputs', {}).get('public_ip'):
                raise ValueError('Provision a reachable Box before configuring the controller')
            box_output = entry['outputs']
            host = box_output['public_ip']
            for resource in controller.resources:
                if resource.type == 'aws.address_attachment' and resource.config['box'] == box.id:
                    host = state['resources'][resource.id]['outputs']['public_ip']
            data = next(r for r in controller.resources if r.id == controller.configuration['data_resource'])
            settings = {'owner':context.owner,'bundle_sha256':bundle['sha256'],'region':box.config['region'],
                'private_ip':box_output['private_ip'],
                'hostname':controller.configuration.get('hostname',host.replace('.','-')+'.sslip.io'),
                'data_volume_id':state['resources'][data.id]['outputs']['volume_id'],
                'initialize_data':controller.configuration.get('initialize_data',False) and data.ownership=='managed' and not state.get('deployment')}
            state.update(phase='configuring',pending_deployment=bundle|{'host':host})
            lifecycle.save(state)
            try:
                self.transport.configure(host=host,bundle=bundle,secrets={},settings=settings)
            except BaseException:
                state['phase']='configuration_failed'
                lifecycle.save(state)
                raise
            state.update(phase='configured',deployment=bundle|{'host':host,'hostname':settings['hostname']})
            state.pop('pending_deployment',None)
            lifecycle.save(state)
            return state


def assemble(source, controller, output):
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.resolve().is_relative_to(source):
        raise ValueError('Keep controller bundles outside source')
    def git(*args):
        return subprocess.check_output(['git','-C',str(source),*args])
    commit, diff = git('rev-parse','HEAD').decode().strip(), git('diff','--binary','HEAD')
    required = ['devops/ec2-control/configure-control.sh','devops/ec2-control/controller_stage.py',
                'devops/ec2-control/compose.yaml','devops/box/controller-apply.sh',
                'devops/box/operate.sh','devops/requirements-bootstrap.txt']
    payload = {}
    for name in git('ls-files','-z').decode().split('\0'):
        if name not in required and not name.startswith(('devops/crux_devops/', 'devops/apt/')):
            continue
        path = source/name
        if private_path(Path(name)):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError('Controller bundle may not contain symlinks')
        payload['source/'+name] = path.read_bytes()
    if any('source/'+name not in payload for name in required):
        raise ValueError('Controller bundle source is missing or untracked')
    payload['source/devops/box/apply.sh'] = payload['source/devops/box/controller-apply.sh']
    manifest = {'version':1,'controller':asdict(controller),'source_commit':commit,
                'source_diff_sha256':hashlib.sha256(diff).hexdigest(),
                'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(payload.items())}}
    if git('rev-parse','HEAD').decode().strip()!=commit or git('diff','--binary','HEAD')!=diff:
        raise ValueError('Controller source changed during bundling')
    return package(payload,manifest,output)


def main():
    import argparse
    import socket
    import time
    import urllib.error
    import urllib.request

    from botocore.exceptions import BotoCoreError, ClientError

    from .aws import AWS
    from .controller_spec import Controller
    from .dependencies import controller_dependencies
    from .lifecycle import Lifecycle
    from .ssh import SSH

    parser=argparse.ArgumentParser(description='Manage a declarative AgentRQ controller deployment')
    parser.add_argument('command',choices=['plan','preflight','put-secrets','create','provision','configure','health','start','destroy'])
    parser.add_argument('specification',type=Path)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--ssh-key',type=Path)
    parser.add_argument('--known-hosts',type=Path)
    parser.add_argument('--secrets',type=Path,help='private controller env file, only for put-secrets')
    parser.add_argument('--confirm-idle',action='store_true',help='confirm no active Runs depend on this controller before configuration or teardown')
    args=parser.parse_args()
    try:
        controller=Controller.load(args.specification)
        if args.command=='plan':
            print(json.dumps(controller.plan(),indent=2))
            return
        if not args.state:
            raise ValueError('--state is required')
        if args.source and args.state.resolve().is_relative_to(args.source.resolve()):
            raise ValueError('Keep controller receipts outside source')
        aws=AWS()
        box=next(r for r in controller.resources if r.id==controller.box)
        client=aws.session(box).client('ssm')
        if args.command=='put-secrets':
            if not args.secrets:
                raise ValueError('put-secrets requires --secrets outside source')
            if args.source and args.secrets.resolve().is_relative_to(args.source.resolve()):
                raise ValueError('Keep controller credentials outside source')
            value=validate_environment(args.secrets.read_text())
            client.put_parameter(Name=controller.configuration['parameter'],Value=value,Type='SecureString',Overwrite=True)
            print('Stored controller environment securely in the declared SSM parameter.')
            return
        lifecycle=Lifecycle(controller,args.state,aws.registry())
        if args.command=='preflight':
            lifecycle.check()
            validate_environment(client.get_parameter(Name=controller.configuration['parameter'],WithDecryption=True)['Parameter']['Value'])
            print('Controller resources and SSM parameter are accessible.')
            return
        if args.command in ('provision','configure','destroy') and not args.confirm_idle:
            raise ValueError('Inspect dependent Runs and pass --confirm-idle before controller configuration or teardown')
        transport=SSH(args.ssh_key,args.state.parent/'controller.log',args.known_hosts) if args.ssh_key else None
        invoker=ControllerInvoker(lifecycle,transport)
        if args.command in ('provision','configure'):
            if not args.source or transport is None:
                raise ValueError('Controller configuration requires --source and --ssh-key')
            if args.ssh_key.resolve().is_relative_to(args.source.resolve()):
                raise ValueError('SSH private keys stay outside source')
            controller_dependencies(args.source)
            bundle=assemble(args.source,controller,args.state.parent/'controller-bundle.tar.gz')
            validate_environment(client.get_parameter(Name=controller.configuration['parameter'],WithDecryption=True)['Parameter']['Value'])
            if args.command=='provision':
                lifecycle.apply()
            state=invoker.configure(bundle)
        elif args.command=='create':
            state=lifecycle.apply()
        elif args.command=='destroy':
            state=invoker.destroy()
        else:
            if transport is None:
                raise ValueError('Controller start/health requires --ssh-key')
            if args.command=='start':
                with lifecycle.locked(create=False) as (saved,_context):
                    deployment=saved.get('deployment',{})
                    host,hostname=deployment.get('host'),deployment.get('hostname')
                    if not host or not hostname or host not in socket.gethostbyname_ex(hostname)[2]:
                        raise ValueError('Controller hostname must resolve to its public IP before certificate issuance')
                state=invoker.start()
            else:
                state=invoker.health()
            if state['phase']=='running':
                url='https://'+state['deployment']['hostname']+'/'
                for attempt in range(30):
                    try:
                        with urllib.request.urlopen(url,timeout=5) as response:
                            if response.status!=200:
                                raise ValueError('Controller HTTPS health failed')
                        break
                    except (urllib.error.URLError,TimeoutError):
                        if attempt==29:
                            raise RuntimeError('Controller HTTPS health timed out; inspect its private log and Caddy journal') from None
                        time.sleep(2)
        print(json.dumps({'controller':controller.name,'phase':state['phase'],'state':str(args.state)},indent=2))
    except (ValueError,TypeError,OSError,RuntimeError,BotoCoreError,ClientError,subprocess.SubprocessError) as error:
        parser.exit(2,f'crux-controller: {error}\n')


if __name__=='__main__':
    main()
