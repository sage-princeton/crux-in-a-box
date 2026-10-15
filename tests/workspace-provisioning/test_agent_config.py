import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = r'''
set -euo pipefail
source src/ec2-workspaces/agent-config.sh
cfg() { local key="$1"; printf '%s' "${!key:-}"; }
warn() { printf '%s\n' "$*" >&2; }
die() { printf '%s\n' "$*" >&2; exit 1; }
'''
ACP = dict(CODEX_MODEL="gpt-6-astra", CODEX_REASONING_EFFORT="max",
           CODEX_VERSION="0.154.0", CODEX_ACP_VERSION="1.10.0",
           TRACING_PLUGIN_VERSION="0.4.0", TRACING_HOOK_TRUSTED_HASH="a" * 64,
           ACP_GATEWAY_VERSION="0.2.17-crux.1",
           ACP_GATEWAY_TARBALL_URL="https://example.com/gateway.tgz",
           ACP_GATEWAY_SHA256="b" * 64)
SDK = dict(AGENT_PLATFORM="openai-agents", OPENAI_AGENTS_MODEL="gpt-5.4",
           OPENAI_AGENTS_REASONING_EFFORT="high")


class AgentConfigTests(unittest.TestCase):
    def shell(self, script, values=None, success=True):
        result = subprocess.run(["bash", "-c", PRELUDE + script], cwd=ROOT,
                                env={"PATH": os.environ["PATH"], **(values or {})},
                                capture_output=True, text=True)
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
        claude = dict(AGENT_PLATFORM="claude", CLAUDE_MODEL="claude-opus-5-5",
                      CLAUDE_EFFORT="xhigh")
        self.shell("load_agent_config settings", claude)
        self.shell("load_agent_config settings", {**claude, "CLAUDE_EFFORT": "none"}, False)

    def test_sdk_effort_and_provider_validation(self):
        self.shell("load_agent_config all", {**SDK, "OPENAI_AGENTS_REASONING_EFFORT": "minimal"})
        self.shell("load_agent_config all", {**SDK, "OPENAI_AGENTS_REASONING_EFFORT": "invalid"}, False)
        self.shell("load_agent_config all", {**SDK, "MODEL_PROVIDER": "openrouter"}, False)

    def test_settings_does_not_require_install_pins(self):
        self.shell("load_agent_config settings", {k: ACP[k] for k in
                   ("CODEX_MODEL", "CODEX_REASONING_EFFORT")})

    def test_run_service_secrets_pass_through_without_provider_or_agentrq_keys(self):
        secrets = dict(OPENAI_API_KEY="fixture-openai", ANTHROPIC_API_KEY="fixture-anthropic",
                       OPENROUTER_API_KEY="fixture-openrouter", AGENTRQ_WORKSPACE_TOKEN="fixture-token",
                       AGENTRQ_WORKSPACE_ID="fixture-workspace", GH_TOKEN="fixture-github",
                       VERCEL_TOKEN="fixture-vercel")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.json"
            path.write_text(json.dumps(secrets))
            result = self.shell('validate_run_api_keys "$SECRETS_PATH"; '
                                'run_api_keys_json "$SECRETS_PATH"', {"SECRETS_PATH": str(path)})
        self.assertEqual(json.loads(result), {k: secrets[k] for k in ("GH_TOKEN", "VERCEL_TOKEN")})

    def test_service_secrets_cannot_override_scaffold_environment(self):
        output = self.shell('check_run_api_key_clashes \'{"CRUX_MODEL":"fixture-private"}\' '
                            '\'{"OPENAI_API_KEY":"fixture-openai"}\' \'CRUX_MODEL="gpt-5.4"\'',
                            success=False)
        self.assertIn("CRUX_MODEL", output)
        self.assertNotIn("fixture-private", output)


if __name__ == "__main__":
    unittest.main()
