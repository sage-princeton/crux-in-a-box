"""Provisioning must apply the same model effort policy to both OpenAI scaffolds."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna", "future-model"])
@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max", "invalid"])
def test_openai_scaffolds_share_effort_validation(model, effort):
    results = []
    for platform, prefix in [("codex", "CODEX"), ("openai-agents", "OPENAI_AGENTS")]:
        result = subprocess.run(
            [
                "bash",
                "-c",
                """
set -eu
source "$1/src/ec2-workspaces/agent-config.sh"
cfg() { printf '%s' "${!1:-}"; }
warn() { printf '%s\\n' "$*" >&2; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
load_agent_config settings
""",
                "test",
                str(ROOT),
            ],
            env={
                **os.environ,
                "AGENT_PLATFORM": platform,
                "MODEL_PROVIDER": "direct",
                f"{prefix}_MODEL": model,
                f"{prefix}_REASONING_EFFORT": effort,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(result)
    allowed = effort in {"none", "low", "medium", "high", "xhigh", "max"}
    if model in {"gpt-6-astra", "gpt-6.1-sol"} and effort == "none":
        allowed = False
    assert [result.returncode == 0 for result in results] == [allowed, allowed]
    assert ["unchecked" in result.stderr for result in results] == [
        model == "future-model" and allowed,
    ] * 2


@pytest.mark.parametrize("mode", ["", "stop-hook", "live", "invalid"])
def test_codex_trace_preflight(mode):
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
set -eu
source "$1/src/ec2-workspaces/scaffolds/acp.sh"
SCRIPT_DIR="$1/src/ec2-workspaces"
die() { printf '%s\\n' "$*" >&2; exit 1; }
scaffold_preflight
printf '%s' "$CODEX_TRACE_MODE"
""",
            "test",
            str(ROOT),
        ],
        env={
            **os.environ,
            "AGENT_PLATFORM": "codex",
            "CONTROL_MCP_BASE": "https://control.invalid",
            "TRACING_PLUGIN_VERSION": "test",
            "TRACING_HOOK_TRUSTED_HASH": "test",
            "CODEX_TRACE_MODE": mode,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (mode != "invalid")
    if mode != "invalid":
        assert result.stdout == (mode or "live")


def test_needrestart_excludes_both_run_services():
    install_script = (ROOT / "src/ec2-workspaces/install-run.sh").read_text()
    rules = install_script.split("<<'PERL'\n", 1)[1].split("\nPERL", 1)[0]
    result = subprocess.run(
        [
            "perl",
            "-e",
            rules
            + """
for my $service (@ARGV) {
    my @rules = grep { $service =~ $_ } keys %{$nrconf{override_rc}};
    print "$service=", @rules ? $nrconf{override_rc}{$rules[0]} : 1, "\\n";
}
""",
            "crux-acp-gateway.service",
            "crux-sdk-run.service",
            "ssh.service",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "crux-acp-gateway.service=0",
        "crux-sdk-run.service=0",
        "ssh.service=1",
    ]


PRELUDE = r'''
set -euo pipefail
source src/ec2-workspaces/agent-config.sh
cfg() { local key="$1"; printf '%s' "${!key:-}"; }
warn() { printf '%s\n' "$*" >&2; }
die() { printf '%s\n' "$*" >&2; exit 1; }
'''
ACP = {"CODEX_MODEL": "gpt-6-astra", "CODEX_REASONING_EFFORT": "max",
           "CODEX_VERSION": "0.154.0", "CODEX_ACP_VERSION": "1.10.0",
           "TRACING_PLUGIN_VERSION": "0.4.0", "TRACING_HOOK_TRUSTED_HASH": "a" * 64,
           "ACP_GATEWAY_VERSION": "0.2.17-crux.1",
           "ACP_GATEWAY_TARBALL_URL": "https://example.com/gateway.tgz",
           "ACP_GATEWAY_SHA256": "b" * 64}
SDK = {"AGENT_PLATFORM": "openai-agents", "OPENAI_AGENTS_MODEL": "gpt-6-astra",
           "OPENAI_AGENTS_REASONING_EFFORT": "high"}


class AgentConfigTests(unittest.TestCase):
    def shell(self, script, values=None, success=True):
        result = subprocess.run(["bash", "-c", PRELUDE + script], cwd=ROOT,
                                env={"PATH": os.environ["PATH"], **(values or {})},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result.stdout + result.stderr

    def test_sdk_requires_no_acp_pins_or_agentrq(self):
        self.assertEqual(self.shell('load_agent_config all; '
                         'printf "%s|%s" "$SCAFFOLD_MODULE" "$ACP_COMMAND"; '
                         '! scaffold_needs agentrq', SDK), "sdk|")

    def test_acp_requires_release_pins(self):
        for key in ("ACP_GATEWAY_VERSION", "ACP_GATEWAY_TARBALL_URL", "ACP_GATEWAY_SHA256"):
            with self.subTest(key=key):
                values = {k: v for k, v in ACP.items() if k != key}
                self.assertIn(key, self.shell("load_agent_config all", values, False))

    def test_main_model_effort_rules_survive_sdk_merge(self):
        cases = [("gpt-6-astra", "max", True), ("gpt-6.1-sol", "xhigh", True),
                 ("gpt-6-astra", "none", False), ("gpt-6-luna", "none", True),
                 ("gpt-6-astra", "minimal", False)]
        for model, effort, valid in cases:
            with self.subTest(model=model, effort=effort):
                self.shell("load_agent_config all", {**ACP, "CODEX_MODEL": model,
                           "CODEX_REASONING_EFFORT": effort}, valid)
        claude = {"AGENT_PLATFORM": "claude", "CLAUDE_MODEL": "claude-opus-5-5",
                      "CLAUDE_EFFORT": "xhigh"}
        self.shell("load_agent_config settings", claude)
        self.shell("load_agent_config settings", {**claude, "CLAUDE_EFFORT": "none"}, False)

    def test_sdk_effort_and_provider_validation(self):
        self.shell("load_agent_config all", {**SDK, "OPENAI_AGENTS_REASONING_EFFORT": "low"})
        self.shell("load_agent_config all", {**SDK, "OPENAI_AGENTS_REASONING_EFFORT": "minimal"}, False)
        self.shell("load_agent_config all", {**SDK, "OPENAI_AGENTS_REASONING_EFFORT": "invalid"}, False)
        self.shell("load_agent_config all", {**SDK, "MODEL_PROVIDER": "openrouter"}, False)

    def test_settings_does_not_require_install_pins(self):
        self.shell("load_agent_config settings", {k: ACP[k] for k in
                   ("CODEX_MODEL", "CODEX_REASONING_EFFORT")})

    def test_run_service_secrets_pass_through_without_provider_or_agentrq_keys(self):
        secrets = {"OPENAI_API_KEY": "fixture-openai", "ANTHROPIC_API_KEY": "fixture-anthropic",
                       "OPENROUTER_API_KEY": "fixture-openrouter", "AGENTRQ_WORKSPACE_TOKEN": "fixture-token",
                       "AGENTRQ_WORKSPACE_ID": "fixture-workspace", "GH_TOKEN": "fixture-github",
                       "VERCEL_TOKEN": "fixture-vercel", "SERVICE_AUTH": {"review-mailbox": "fixture-bundle"},
                       "GOG_KEYRING_PASSWORD": 'a "fixture" passphrase'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.json"
            path.write_text(json.dumps(secrets))
            result = self.shell('validate_run_api_keys "$SECRETS_PATH"; '
                                'run_api_keys_json "$SECRETS_PATH"', {"SECRETS_PATH": str(path)})
        self.assertEqual(json.loads(result), {k: secrets[k] for k in
                         ("GH_TOKEN", "VERCEL_TOKEN", "GOG_KEYRING_PASSWORD")})

    def test_service_secrets_cannot_override_scaffold_environment(self):
        output = self.shell('check_run_api_key_clashes \'{"CRUX_MODEL":"fixture-private"}\' '
                            '\'{"OPENAI_API_KEY":"fixture-openai"}\' \'CRUX_MODEL="gpt-5.4"\'',
                            success=False)
        self.assertIn("CRUX_MODEL", output)
        self.assertNotIn("fixture-private", output)


if __name__ == "__main__":
    unittest.main()
