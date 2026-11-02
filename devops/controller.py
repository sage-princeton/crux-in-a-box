"""Deploy the independent AgentRQ controller using named AWS installation resources."""
import argparse
import json
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crux import SOURCE
from crux_devops.account import discover
from crux_devops.aws import AWS
from crux_devops.controller import ControllerInvoker, assemble
from crux_devops.controller_box import validate_environment
from crux_devops.controller_spec import resolve
from crux_devops.host_keys import trust_box
from crux_devops.lifecycle import Lifecycle
from crux_devops.ssh import SSH


def check_https(deployment):
    """Verify DNS and HTTPS readiness for the controller's observed hostname/address.

    :param deployment: Live Box metadata including hostname and public address.
    :raises ValueError: DNS does not point to the controller.
    :raises RuntimeError: HTTPS fails to become ready within one minute.
    """
    hostname = deployment['hostname']
    if deployment['host'] not in socket.gethostbyname_ex(hostname)[2]:
        raise ValueError('Controller hostname must resolve to its public address')
    for attempt in range(30):
        try:
            with urllib.request.urlopen('https://'+hostname+'/',timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError,TimeoutError):
            pass
        if attempt != 29:
            time.sleep(2)
    raise RuntimeError('Controller HTTPS did not become healthy; inspect Caddy and AgentRQ logs')


def execute(args):
    """Reconcile the controller against AWS and systemd, with no local state handoff.

    :param args: Operator command, options file, explicit profile and credentials.
    :returns: Live controller state, including retained resources on teardown.
    """
    session = boto3.Session(profile_name=args.profile)
    if not session.region_name:
        raise ValueError('Configure a region for the selected AWS profile')
    account = discover(session,args.profile,session.client('sts').get_caller_identity()['Account'])
    controller = resolve(json.loads(args.configuration.read_text()),account)
    if args.command == 'plan':
        return controller.plan()
    if args.command in ('deploy','destroy') and not args.confirm_idle:
        raise ValueError('Confirm no active Runs depend on this controller with --confirm-idle')
    aws = AWS()
    lifecycle = Lifecycle(controller,aws.registry())
    ssm = session.client('ssm')
    with tempfile.TemporaryDirectory(prefix='crux-controller-') as temporary:
        directory = Path(temporary)
        hosts = directory/'known_hosts'
        hosts.touch(mode=0o600)
        key = args.ssh_key.expanduser().resolve()
        if key.is_relative_to(SOURCE):
            raise ValueError('Keep SSH keys outside source')
        transport = SSH(key,Path.home()/'.cache/crux/logs/controller.log',hosts)
        invoker = ControllerInvoker(lifecycle,transport)
        if args.command == 'deploy':
            if not args.secrets or args.secrets.resolve().is_relative_to(SOURCE):
                raise ValueError('Controller deploy requires --secrets outside source')
            environment = validate_environment(args.secrets.read_text())
            bundle = assemble(SOURCE,controller,directory/'bundle.tar.gz')
            lifecycle.check()
            ssm.put_parameter(Name='/crux/control/env',Value=environment,Type='SecureString',Overwrite=True)
            lifecycle.apply()
            trust_box(aws,lifecycle,hosts)
            result = invoker.configure(bundle)
        else:
            trust_box(aws,lifecycle,hosts)
            result = {'start':invoker.start,'status':invoker.status,'health':invoker.health,'destroy':invoker.destroy}[args.command]()
            if args.command in ('start','health') and result['phase']=='running':
                check_https(result['deployment'])
                ssm.put_parameter(Name='/crux/controller/url',Value='https://'+result['deployment']['hostname'],Type='String',Overwrite=True)
    return result


def main():
    """Parse controller arguments and report current status without credential values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','deploy','start','status','health','destroy'))
    parser.add_argument('configuration',type=Path)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--ssh-key',type=Path,default=Path.home()/'.ssh/id_ed25519')
    parser.add_argument('--secrets',type=Path)
    parser.add_argument('--confirm-idle',action='store_true')
    try:
        print(json.dumps(execute(parser.parse_args()),indent=2))
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,BotoCoreError,ClientError,subprocess.SubprocessError) as error:
        parser.exit(2,f'controller: {error}\n')


if __name__ == '__main__':
    main()
