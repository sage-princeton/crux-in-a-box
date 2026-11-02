"""Create shared AWS prerequisites once; repeating identical setup is safe."""
import argparse
import ipaddress
import json
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crux_devops.account import ensure_stack

SOURCE = Path(__file__).resolve().parent.parent


def setup(args):
    """Install the named shared AWS prerequisites without writing a local receipt.

    :param args: Explicit profile, operator key/network and shared secrets.
    :returns: Success message; credentials remain in SSM.
    """
    cidr = ipaddress.ip_network(args.ssh_cidr, strict=True)
    if cidr.version != 4 or cidr.prefixlen == 0:
        raise ValueError('SSH access requires a restricted IPv4 network')
    key, secrets = args.ssh_key.expanduser().resolve(), args.secrets.expanduser().resolve()
    if any(path.is_relative_to(SOURCE) for path in (key,secrets)):
        raise ValueError('Keep SSH keys and secrets outside source')
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise ValueError('SSH key must exist with mode 0600 or stricter')
    public_key = subprocess.check_output(['ssh-keygen','-y','-f',str(key)], text=True).strip()
    values = json.loads(secrets.read_text())
    if not isinstance(values,dict) or any(not isinstance(values.get(k),str) or not values[k]
            for k in ('LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL')):
        raise ValueError('Shared secrets require Langfuse public/secret keys and base URL')
    session = boto3.Session(profile_name=args.profile)
    session.client('sts').get_caller_identity()
    if not session.region_name:
        raise ValueError('Configure a region for the explicitly selected AWS profile')
    template = (SOURCE/'devops/account/template.yaml').read_text()
    ensure_stack(session.client('cloudformation'), template,
                 [{'ParameterKey':'SshCidr','ParameterValue':str(cidr)},
                  {'ParameterKey':'PublicKey','ParameterValue':public_key}])
    session.client('ssm').put_parameter(Name='/crux/system/env',Value=json.dumps(values),Type='SecureString',Overwrite=True)
    return 'CRUX account installation is ready; deploy discovers crux-account directly in AWS.'


def main():
    """Parse installation arguments and report completion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--ssh-key',type=Path,default=Path.home()/'.ssh/id_ed25519')
    parser.add_argument('--ssh-cidr',required=True)
    parser.add_argument('--secrets',required=True,type=Path,help='shared Langfuse JSON outside source')
    args = parser.parse_args()
    try:
        print(setup(args))
    except (ValueError,TypeError,OSError,RuntimeError,BotoCoreError,ClientError,subprocess.SubprocessError) as error:
        parser.exit(2,f'account setup: {error}\n')


if __name__ == '__main__':
    main()
