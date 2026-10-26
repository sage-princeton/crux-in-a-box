"""SSH transport for the focused Box-side install/configure/systemd programs."""
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

EXTRACT = '''import hashlib,pathlib,sys,tarfile
archive=pathlib.Path(sys.argv[1]); expected=sys.argv[2]; target=pathlib.Path(sys.argv[3])
if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: raise ValueError("Bundle checksum mismatch")
target.mkdir(parents=True,exist_ok=True)
with tarfile.open(archive) as payload:
    for member in payload.getmembers():
        name=pathlib.PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts or not member.isfile(): raise ValueError("Invalid bundle member")
    payload.extractall(target,filter="data")
'''


class SSH:
    def __init__(self, key, log, known_hosts=None):
        self.key, self.log = Path(key).expanduser().resolve(), Path(log)
        if not self.key.is_file() or self.key.stat().st_mode & 0o077:
            raise ValueError('SSH private key must exist and have mode 0600 or stricter')
        self.options = ['-i', str(self.key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                        '-o', 'ConnectTimeout=15', '-o', 'IdentitiesOnly=yes']
        if known_hosts:
            known_hosts = Path(known_hosts).expanduser().resolve()
            if not known_hosts.is_file():
                raise ValueError('Known-hosts file does not exist')
            self.options += ['-o', 'UserKnownHostsFile='+str(known_hosts)]

    def command(self, host, argv):
        if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
            raise ValueError('Invalid Box SSH address')
        self.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'ab') as log:
            result = subprocess.run(['ssh', *self.options, 'ubuntu@'+host, shlex.join(list(map(str, argv)))],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=2400, check=False)
        if result.returncode:
            raise RuntimeError(f'Box operation failed ({result.returncode}); inspect private log {self.log}')

    def configure(self, *, host, bundle, secrets, settings):
        remote = '/tmp/crux-upload-'+str(uuid.uuid4())
        root = '/opt/crux-deployments/'+bundle['sha256']
        self.command(host, ['mkdir', '-m', '700', remote])
        try:
            with tempfile.TemporaryDirectory(prefix='crux-upload-') as directory:
                archive = Path(directory)/'bundle.tar.gz'
                shutil.copyfile(bundle['path'], archive)
                paths = [archive]
                for name, value in (('secrets.json', secrets), ('settings.json', settings)):
                    path = Path(directory)/name
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'w') as stream:
                        json.dump(value, stream)
                    paths.append(path)
                subprocess.run(['scp', *self.options, *map(str, paths), 'ubuntu@'+host+':'+remote+'/'],
                               check=True, capture_output=True, timeout=600)
            self.command(host, ['sudo', 'python3', '-c', EXTRACT, remote+'/bundle.tar.gz',
                                bundle['sha256'], root])
            self.command(host, ['sudo', 'bash', root+'/source/devops/box/apply.sh', root,
                                remote+'/settings.json', remote+'/secrets.json'])
        finally:
            self.command(host, ['rm', '-rf', '--', remote])

    def start(self, *, host, owner, bundle_sha256):
        self.command(host, ['sudo', 'bash', '/opt/crux-deployments/'+bundle_sha256+'/source/devops/box/operate.sh',
                            'start', owner, bundle_sha256])

    def stop(self, *, host, owner, bundle_sha256):
        self.command(host, ['sudo', 'bash', '/opt/crux-deployments/'+bundle_sha256+'/source/devops/box/operate.sh',
                            'stop', owner, bundle_sha256])

    def health(self, *, host, owner, bundle_sha256):
        self.command(host, ['sudo', 'bash', '/opt/crux-deployments/'+bundle_sha256+'/source/devops/box/operate.sh',
                            'health', owner, bundle_sha256])
