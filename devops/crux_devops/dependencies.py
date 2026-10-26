"""Read the package manifests used to install Box software."""
import argparse
import json
import re
from pathlib import Path

PACKAGES = {
    'codex': {'@openai/codex': 'CODEX_VERSION', '@agentclientprotocol/codex-acp': 'CODEX_ACP_VERSION',
              '@agentrq/acp-gateway': 'ACP_GATEWAY_VERSION',
              '@langfuse/codex-observability-plugin': 'TRACING_PLUGIN_VERSION'},
    'claude': {'@anthropic-ai/claude-code': 'CLAUDE_VERSION',
               '@agentclientprotocol/claude-agent-acp': 'CLAUDE_ACP_VERSION',
               '@agentrq/acp-gateway': 'ACP_GATEWAY_VERSION'},
}


def scaffold_dependencies(source, platform):
    if platform == 'openai-agents':
        return {}
    if platform not in PACKAGES:
        raise ValueError('Unsupported dependency platform')
    directory = Path(source) / 'scaffolds' / platform
    declared = json.loads((directory/'package.json').read_text())['dependencies']
    lock = json.loads((directory/'package-lock.json').read_text())
    if lock.get('lockfileVersion') not in (2, 3) or lock['packages'][''].get('dependencies') != declared:
        raise ValueError('Package manifest and lockfile differ; run npm install --package-lock-only')
    result = {}
    for package, key in PACKAGES[platform].items():
        version = declared.get(package, '')
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
            raise ValueError(f'{package} must declare an exact release version')
        resolved = lock['packages'].get('node_modules/'+package, {})
        if resolved.get('version') != version or not resolved.get('integrity'):
            raise ValueError(f'{package} lockfile version/integrity is missing or inconsistent')
        result[key] = version
    if platform == 'codex':
        tracing = json.loads((directory/'tracing.json').read_text())
        if tracing['mode'] not in ('live', 'stop-hook'):
            raise ValueError('Tracing mode must be live or stop-hook')
        trusted = tracing['hook_trusted_hash']
        if not re.fullmatch('sha256:[a-f0-9]{64}', trusted):
            raise ValueError('Invalid tracing hook trust hash')
        result.update(TRACING_HOOK_TRUSTED_HASH=trusted, CODEX_TRACE_MODE=tracing['mode'])
    return result


def controller_dependencies(source):
    data = json.loads((Path(source)/'devops/ec2-control/compose.yaml').read_text())
    image = data['services']['agentrq']['image']
    if not re.fullmatch(r'[^\s@]+@sha256:[a-f0-9]{64}', image):
        raise ValueError('Controller image must be pinned by digest')
    return {'AGENTRQ_IMAGE': image, 'AGENTRQ_SQLITE_DSN': 'file:/_storage/agentrq.db?_txlock=immediate'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--platform', choices=tuple(PACKAGES)+('openai-agents',))
    args = parser.parse_args()
    values = scaffold_dependencies(args.source, args.platform) if args.platform else controller_dependencies(args.source)
    for key, value in values.items():
        print(key+'='+value)


if __name__ == '__main__':
    main()
