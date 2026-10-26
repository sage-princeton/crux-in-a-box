import argparse
import json
import subprocess
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .spec import Run


def main():
    parser = argparse.ArgumentParser(description='Provision a CRUX Box, configure its Scaffold, then explicitly start its Run')
    parser.add_argument('command', choices=['validate', 'plan', 'preflight', 'create', 'bundle',
                                            'provision', 'configure', 'health', 'start', 'destroy'])
    parser.add_argument('specification')
    parser.add_argument('--state', type=Path, help='private deployment receipt outside source')
    parser.add_argument('--source', type=Path, help='selected source checkout for bundling/configuration')
    parser.add_argument('--output', type=Path, help='bundle output outside source')
    parser.add_argument('--secrets', type=Path, help='private Run credential JSON outside source')
    parser.add_argument('--ssh-key', type=Path)
    parser.add_argument('--known-hosts', type=Path, help='trusted SSH host keys; defaults to OpenSSH configuration')
    args = parser.parse_args()
    try:
        run = Run.load(args.specification)
        if args.command in ('validate', 'plan'):
            print(json.dumps(run.plan() if args.command == 'plan' else {'valid': True, 'run': run.name}, indent=2))
            return
        from .bundle import assemble
        if args.command == 'bundle':
            if not args.source or not args.output:
                raise ValueError('bundle requires --source and --output')
            print(json.dumps(assemble(args.source, run, args.output), indent=2))
            return
        if not args.state:
            raise ValueError('--state is required')
        if args.source and args.state.resolve().is_relative_to(args.source.resolve()):
            raise ValueError('Keep deployment state outside source')
        from .agentrq import Workspace
        from .aws import AWS
        from .invoker import Invoker, validate_secrets
        from .lifecycle import Lifecycle
        from .ssh import SSH
        registry = AWS().registry() | {'agentrq.workspace': Workspace()}
        lifecycle = Lifecycle(run, args.state, registry)
        if args.command == 'preflight':
            lifecycle.check()
            print(json.dumps({'valid': True, 'run': run.name}))
            return
        if args.command in ('provision', 'configure'):
            if not args.source or not args.secrets or not args.ssh_key:
                raise ValueError('provision/configure require --source, --secrets and --ssh-key')
            for private in (args.secrets, args.ssh_key):
                if private.expanduser().resolve().is_relative_to(args.source.resolve()):
                    raise ValueError('Keep credentials and SSH private keys outside source')
            workspace_id = run.scaffold.configuration.get('workspace_resource')
            workspace = next((r for r in run.resources if r.id == workspace_id), None)
            if workspace_id and (workspace is None or workspace.type != 'agentrq.workspace'):
                raise ValueError('workspace_resource must identify a declared AgentRQ workspace')
            if workspace is not None and (run.scaffold.type != 'agentrq' or
                    workspace.config.get('url', '').rstrip('/') != run.scaffold.configuration.get('control_mcp_base', '').rstrip('/')):
                raise ValueError('Scaffold controller must match its declared AgentRQ workspace URL')
            values = validate_secrets(run, json.loads(args.secrets.expanduser().read_text()), workspace_required=not workspace_id)
            transport = SSH(args.ssh_key, args.state.parent/'configure.log', args.known_hosts)
            bundle = assemble(args.source, run, args.output or args.state.parent/'bundle.tar.gz')
            if args.command == 'provision':
                lifecycle.apply()
            if workspace is not None:
                with lifecycle.locked(create=False) as (state, context):
                    values.update(registry[workspace.type].bindings(workspace, state['resources'][workspace.id]['outputs'], context))
            result = Invoker(lifecycle, transport).configure(bundle, values)
        elif args.command in ('start', 'health'):
            if not args.ssh_key:
                raise ValueError(f'{args.command} requires --ssh-key')
            invoker = Invoker(lifecycle, SSH(args.ssh_key, args.state.parent/(args.command+'.log'), args.known_hosts))
            result = invoker.start() if args.command == 'start' else invoker.health()
        elif args.command == 'destroy':
            with lifecycle.locked(create=False) as (state, context):
                deployment = state.get('deployment')
                box = next(r for r in run.resources if r.id == run.box)
                state['phase'] = 'destroying'
                lifecycle.save(state)
                if box.type == 'aws.ec2' and box.ownership == 'managed':
                    lifecycle.drivers[box.type].stop(box, state['resources'].get(box.id, {}).get('outputs', {}), context)
                elif deployment:
                    if not args.ssh_key:
                        raise ValueError('destroy of a configured Box requires --ssh-key to stop its Run before resource cleanup')
                    SSH(args.ssh_key, args.state.parent/'stop.log', args.known_hosts).stop(
                        host=deployment['host'], owner=context.owner, bundle_sha256=deployment['sha256'])
            result = lifecycle.destroy()
        else:
            result = lifecycle.apply()
        print(json.dumps({'run': run.name, 'phase': result['phase'], 'state': str(args.state)}, indent=2))
    except (ValueError, TypeError, OSError, RuntimeError, BotoCoreError, ClientError, subprocess.SubprocessError) as error:
        parser.exit(2, f'crux: {error}\n')


if __name__ == '__main__':
    main()
