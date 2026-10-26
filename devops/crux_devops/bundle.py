"""Assemble a reproducible, secret-free deployment payload."""
import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import tomllib
from dataclasses import asdict
from pathlib import Path

from .dependencies import scaffold_dependencies


def private_path(path):
    return any(part in ('.git', '.aws', '.ssh', '.direnv', '.venv', '__pycache__', 'node_modules')
               or part.startswith(('.env', 'run-secrets', 'placeholders-')) for part in path.parts)


def assemble(source, run, output, *, drop_in=None):
    source, output = Path(source).resolve(), Path(output).absolute()
    inputs = Path(drop_in).resolve() if drop_in is not None else (source/run.scaffold.drop_in).resolve()
    if drop_in is None and not inputs.is_relative_to(source):
        raise ValueError('Run inputs leave the selected source')
    if not inputs.is_dir():
        raise ValueError('Run input directory does not exist')
    if output.resolve().is_relative_to(source) or output.resolve().is_relative_to(inputs):
        raise ValueError('Write deployment bundles outside source and Run inputs')
    scaffold_dependencies(source, run.scaffold.platform)
    if run.scaffold.type == 'sdk':
        tomllib.loads((inputs/'scaffold.toml').read_text())
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    commit = git('rev-parse', 'HEAD').decode().strip()
    diff = git('diff', '--binary', 'HEAD')
    payload = {}
    selected = ['devops/apt/', 'devops/crux_devops/', 'devops/box/', 'devops/requirements-bootstrap.txt']
    if run.scaffold.type == 'sdk':
        selected.append('scaffolds/sdk/')
    else:
        selected += ['scaffolds/'+run.scaffold.platform+'/', 'monitoring/tracing/'+run.scaffold.platform+'/']
    workspace = ('install-run.sh', 'configure-run.sh', 'agent-config.sh', 'run_provision.py',
                 'scaffolds/'+run.scaffold.module+'.sh')
    selected += ['devops/ec2-workspaces/'+name for name in workspace]
    for name in git('ls-files', '-z').decode().split('\0'):
        relative = Path(name)
        if not name or private_path(relative) or any(p in ('tests', 'docker', 'run-harness') for p in relative.parts):
            continue
        if not any(name.startswith(prefix) if prefix.endswith('/') else name == prefix for prefix in selected):
            continue
        path = source/relative
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError('Source payload may not contain symlinks')
        if path.is_file():
            payload['source/'+name] = path.read_bytes()
    for path in sorted(inputs.rglob('*')):
        relative = path.relative_to(inputs)
        if private_path(relative):
            continue
        if path.is_symlink():
            raise ValueError('Run inputs may not contain symlinks')
        if path.is_file():
            payload['input/'+relative.as_posix()] = path.read_bytes()
    for name in ('devops/ec2-workspaces/install-run.sh', 'devops/ec2-workspaces/configure-run.sh',
                 'devops/box/apply.sh'):
        if 'source/'+name not in payload:
            raise ValueError(f'Required bundle source is missing or untracked: {name}')
    manifest = {'version': 1, 'run': asdict(run), 'run_fingerprint': run.fingerprint,
                'source_commit': commit, 'source_diff_sha256': hashlib.sha256(diff).hexdigest(),
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}}
    if git('rev-parse', 'HEAD').decode().strip() != commit or git('diff', '--binary', 'HEAD') != diff:
        raise ValueError('Selected source changed during bundle assembly')
    return package(payload, manifest, output)


def package(payload, manifest, output):
    output = Path(output)
    payload = payload | {'bundle.json': (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode()}
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.crux-bundle-', dir=output.parent)
    try:
        with (
            os.fdopen(descriptor, 'wb') as stream,
            gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode='w') as archive,
        ):
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mtime = len(data), 0
                info.mode = 0o755 if name.endswith('.sh') else 0o644
                archive.addfile(info, io.BytesIO(data))
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'source_commit': manifest['source_commit'],
            'source_diff_sha256': manifest['source_diff_sha256'], 'path': str(output)}
