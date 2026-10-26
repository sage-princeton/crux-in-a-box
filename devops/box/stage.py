"""Native filesystem/configuration work for a deployment on an idle Box."""
import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path


def replace_tree(source, target, preserve=()):
    target = Path(target)
    if target.resolve() != target.absolute():
        raise ValueError('Deployment target may not contain symlinks')
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.name not in preserve:
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=lambda path, names: list(preserve) if Path(path) == Path(source) else [])


def main():
    bundle, settings_path, secrets_path = map(Path, sys.argv[1:])
    manifest = json.loads((bundle/'bundle.json').read_text())
    for name, digest in manifest['files'].items():
        path = bundle/name
        if path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError('Invalid bundle member')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Bundle integrity mismatch')
    sys.path.insert(0, str(bundle/'source/devops'))
    from crux_devops.spec import Run
    run = Run.parse(manifest['run'])
    settings = json.loads(settings_path.read_text())
    source = bundle/'source'
    workspace = source/'devops/ec2-workspaces'
    shutil.copy2(workspace/'scaffolds'/f'{run.scaffold.module}.sh', workspace/'scaffold.sh')
    target = Path('/usr/local/lib/crux')
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workspace/'run_provision.py', target/'run_provision.py')
    if run.scaffold.type == 'sdk':
        replace_tree(source/'scaffolds/sdk', '/opt/crux-sdk-scaffold', preserve=('.venv',))
    elif run.scaffold.platform == 'codex':
        tracing = source/'monitoring/tracing/codex'
        for name in ('codex-flush-turns.py', 'codex-live-trace.py'):
            shutil.copy2(tracing/name, workspace/name)
        shutil.copytree(tracing/'live_trace', workspace/'live_trace', dirs_exist_ok=True)
    else:
        shutil.copy2(source/'monitoring/tracing/claude/langfuse_hook.py', workspace/'langfuse_hook.py')
    inputs = Path('/srv/crux-run/run-harness')
    for path in (inputs, inputs/'workspace', Path('/srv/crux-run/state')):
        if path.resolve() != path.absolute():
            raise ValueError('Run paths may not contain symlinks')
    preserved_workspace = (inputs/'workspace').exists()
    replace_tree(bundle/'input', inputs, preserve=('workspace',) if (inputs/'workspace').exists() else ())
    (inputs/'workspace').mkdir(exist_ok=True)
    Path('/srv/crux-run/state').mkdir(exist_ok=True)
    new_paths = [inputs/path.relative_to(bundle/'input') for path in (bundle/'input').rglob('*')
                 if not (preserved_workspace and path.relative_to(bundle/'input').parts[0] == 'workspace')]
    for path in [Path('/srv/crux-run'), inputs, inputs/'workspace', Path('/srv/crux-run/state'), *new_paths]:
        shutil.chown(path, user='ubuntu', group='ubuntu')
    provision = inputs/'provision.json'
    if provision.exists():
        shutil.copy2(provision, '/etc/crux-provision.json')
    else:
        Path('/etc/crux-provision.json').unlink(missing_ok=True)
    values = run.scaffold.environment() | {
        'CRUX_SOURCE_ROOT': str(source), 'RUN_SLUG': run.name,
        'RUN_SECRETS_PATH': str(secrets_path), 'AWS_REGION': settings['region'],
        'SYSTEM_SSM_PARAM': run.scaffold.configuration.get('system_parameter', '/crux/system/env'),
        'CONTROL_MCP_BASE': run.scaffold.configuration.get('control_mcp_base', ''),
        'CRUX_RUN_SERVICE': run.scaffold.service,
    }
    os.umask(0o077)
    Path('/etc/crux-deployment.env').write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in values.items()))
    Path('/etc/crux-deployment.json').write_text(json.dumps({
        'owner': settings['owner'], 'run_fingerprint': run.fingerprint,
        'bundle_sha256': settings['bundle_sha256'], 'service': run.scaffold.service,
    }))


if __name__ == '__main__':
    main()
