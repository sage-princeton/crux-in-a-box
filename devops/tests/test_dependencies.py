import json

import pytest
from crux_devops.dependencies import controller_dependencies, scaffold_dependencies


def manifest(root, platform, dependencies):
    path = root / 'scaffolds' / platform
    path.mkdir(parents=True)
    (path / 'package.json').write_text(json.dumps({'dependencies': dependencies}))
    (path / 'package-lock.json').write_text(json.dumps({
        'lockfileVersion': 3, 'packages': {'': {'dependencies': dependencies},
        **{'node_modules/'+name: {'version': version, 'integrity': 'sha512-fixture'}
           for name, version in dependencies.items()}}}))
    return path


def test_selected_platform_versions_come_from_consumed_npm_manifest(tmp_path):
    manifest(tmp_path, 'claude', {'@anthropic-ai/claude-code': '2.1.272',
             '@agentclientprotocol/claude-agent-acp': '0.77.0', '@agentrq/acp-gateway': '0.4.1'})
    actual = scaffold_dependencies(tmp_path, 'claude')
    assert actual['CLAUDE_VERSION'] == '2.1.272'
    assert actual['CLAUDE_ACP_VERSION'] == '0.77.0'
    assert actual['ACP_GATEWAY_VERSION'] == '0.4.1'
    assert 'CODEX_VERSION' not in actual


def test_out_of_sync_lockfile_fails_before_installation(tmp_path):
    path = manifest(tmp_path, 'claude', {'@anthropic-ai/claude-code': '2.1.272',
                    '@agentclientprotocol/claude-agent-acp': '0.77.0', '@agentrq/acp-gateway': '0.4.1'})
    package = json.loads((path/'package.json').read_text())
    package['dependencies']['@agentrq/acp-gateway'] = '0.4.2'
    (path/'package.json').write_text(json.dumps(package))
    with pytest.raises(ValueError, match='lockfile'):
        scaffold_dependencies(tmp_path, 'claude')


def test_sdk_does_not_require_cli_manifests(tmp_path):
    assert scaffold_dependencies(tmp_path, 'openai-agents') == {}


def test_controller_image_comes_from_deployed_compose_manifest(tmp_path):
    path = tmp_path/'devops/ec2-control'
    path.mkdir(parents=True)
    image = 'agentrq/agentrq:0.13.4@sha256:'+'a'*64
    (path/'compose.yaml').write_text(json.dumps({'services': {'agentrq': {'image': image}}}))
    assert controller_dependencies(tmp_path)['AGENTRQ_IMAGE'] == image
