"""Provisioning must apply the same model effort policy to both OpenAI scaffolds."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('model', ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna', 'future-model'])
@pytest.mark.parametrize('effort', ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'invalid'])
def test_openai_scaffolds_share_effort_validation(model, effort):
    results = []
    for platform, prefix in [('codex', 'CODEX'), ('openai-agents', 'OPENAI_AGENTS')]:
        result = subprocess.run(
            ['bash', '-c', '''
set -eu
source "$1/src/ec2-workspaces/agent-config.sh"
cfg() { printf '%s' "${!1:-}"; }
warn() { printf '%s\\n' "$*" >&2; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
load_agent_config settings
''', 'test', str(ROOT)],
            env={**os.environ, 'AGENT_PLATFORM': platform, 'MODEL_PROVIDER': 'direct',
                 f'{prefix}_MODEL': model, f'{prefix}_REASONING_EFFORT': effort},
            capture_output=True, text=True, check=False,
        )
        results.append(result)
    allowed = effort in {'none', 'low', 'medium', 'high', 'xhigh', 'max'}
    if model in {'gpt-6-astra', 'gpt-6.1-sol'} and effort == 'none':
        allowed = False
    assert [result.returncode == 0 for result in results] == [allowed, allowed]
    assert ['unchecked' in result.stderr for result in results] == [
        model == 'future-model' and allowed,
    ] * 2


@pytest.mark.parametrize('mode', ['', 'stop-hook', 'live', 'invalid'])
def test_codex_trace_preflight(mode):
    result = subprocess.run(
        ['bash', '-c', '''
set -eu
source "$1/src/ec2-workspaces/scaffolds/acp.sh"
SCRIPT_DIR="$1/src/ec2-workspaces"
die() { printf '%s\\n' "$*" >&2; exit 1; }
scaffold_preflight
printf '%s' "$CODEX_TRACE_MODE"
''', 'test', str(ROOT)],
        env={**os.environ, 'AGENT_PLATFORM': 'codex', 'CONTROL_MCP_BASE': 'https://control.invalid',
             'TRACING_PLUGIN_VERSION': 'test', 'TRACING_HOOK_TRUSTED_HASH': 'test', 'CODEX_TRACE_MODE': mode},
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == (mode != 'invalid')
    if mode != 'invalid':
        assert result.stdout == (mode or 'live')


def test_needrestart_excludes_both_run_services():
    install_script = (ROOT / 'src/ec2-workspaces/install-run.sh').read_text()
    rules = install_script.split("<<'PERL'\n", 1)[1].split('\nPERL', 1)[0]
    result = subprocess.run(
        ['perl', '-e', rules + '''
for my $service (@ARGV) {
    my @rules = grep { $service =~ $_ } keys %{$nrconf{override_rc}};
    print "$service=", @rules ? $nrconf{override_rc}{$rules[0]} : 1, "\\n";
}
''', 'crux-acp-gateway.service', 'crux-sdk-run.service', 'ssh.service'],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == [
        'crux-acp-gateway.service=0', 'crux-sdk-run.service=0', 'ssh.service=1',
    ]
