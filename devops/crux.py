"""Deploy a Run from one JSON file, then start it explicitly."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crux_devops.account import discover
from crux_devops.agentrq import Workspace
from crux_devops.aws import AWS
from crux_devops.bundle import assemble
from crux_devops.configuration import RunConfiguration
from crux_devops.deployment import Deployment, resolve
from crux_devops.host_keys import trust_box
from crux_devops.invoker import Invoker, validate_secrets
from crux_devops.lifecycle import Lifecycle
from crux_devops.ssh import SSH

SOURCE = Path(__file__).resolve().parent.parent


def execute(args):
    """Reconcile a Run against live AWS and Box state without local receipts.

    :param args: Command, Run JSON, explicit profile and optional credential paths.
    :returns: Current phase and retained resource outputs; no credential values.
    """
    config = RunConfiguration.load(args.run)
    if args.command == 'deploy' and not args.secrets:
        raise ValueError('deploy requires --secrets alongside the Run JSON')
    if config.scaffold != 'sdk' and args.command != 'plan' and not args.secrets:
        raise ValueError('ACP operations require --secrets for AgentRQ workspace management')
    key = args.ssh_key.expanduser().resolve()
    values = {}
    if args.secrets:
        secret_path = args.secrets.expanduser().resolve()
        if secret_path.is_relative_to(SOURCE):
            raise ValueError('Keep secrets outside source')
        values = json.loads(secret_path.read_text())
    if key.is_relative_to(SOURCE):
        raise ValueError('Keep SSH private keys outside source')
    session = boto3.Session(profile_name=args.profile)
    if not session.region_name:
        raise ValueError('Configure a region for the explicitly selected AWS profile')
    account = discover(session,args.profile,session.client('sts').get_caller_identity()['Account'])
    if config.scaffold != 'sdk':
        account['controller_url'] = session.client('ssm').get_parameter(Name='/crux/controller/url')['Parameter']['Value']
    run = resolve(config,account)
    if args.command == 'plan':
        return run.plan()
    if args.command == 'deploy':
        validate_secrets(run,values,workspace_required=config.scaffold=='sdk')
        session.client('ssm').get_parameter(Name='/crux/system/env')
    aws = AWS()
    lifecycle = Lifecycle(run,aws.registry() | {'agentrq.workspace':Workspace(credentials=values)})
    log = Path.home()/'.cache/crux/logs'/f'{run.name}-{args.command}.log'
    with tempfile.TemporaryDirectory(prefix='crux-') as temporary:
        directory = Path(temporary)
        known_hosts = directory/'known_hosts'
        known_hosts.touch(mode=0o600)
        transport = SSH(key,log,known_hosts)
        invoker = Invoker(lifecycle,transport)
        deployment = Deployment(lifecycle,invoker,lambda: trust_box(aws,lifecycle,known_hosts))
        if args.command == 'deploy':
            bundle = assemble(SOURCE,run,directory/'bundle.tar.gz',drop_in=config.inputs,provider=config.provider)
            result = deployment.deploy(bundle,values)
        else:
            trust_box(aws,lifecycle,known_hosts)
            result = {'start':invoker.start,'status':invoker.status,'health':invoker.health,'destroy':invoker.destroy}[args.command]()
    return {'run':run.name,'phase':result['phase'],'resources':result['resources'],'log':str(log)}


def main():
    """Parse the direct Python CLI and print current status or an operational error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','deploy','start','status','health','destroy'))
    parser.add_argument('run',type=Path,help='the Run JSON, for every operation')
    parser.add_argument('--secrets',type=Path,help='required for deploy and all ACP operations')
    parser.add_argument('--profile',required=True,help='explicit AWS profile; region comes from that profile')
    parser.add_argument('--ssh-key',type=Path,default=Path.home()/'.ssh/id_ed25519')
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args),indent=2))
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,BotoCoreError,ClientError,subprocess.SubprocessError) as error:
        parser.exit(2,f'crux: {error}\n')


if __name__ == '__main__':
    main()
