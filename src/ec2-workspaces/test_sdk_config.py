"""Provider preflight and credential selection without AWS or model calls."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlunsplit

DIRECTORY = Path(__file__).resolve().parent
DIRECT = '[runtime]\ntype = "openai-agents"\n'
COMPATIBLE = DIRECT + '''
[runtime.model_provider]
type = "openai-compatible"
api = "responses"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
'''


class SDKConfigTests(unittest.TestCase):
    def inspect(self, text, provider="direct", success=True, model="google/gemini-4-argon"):
        result = subprocess.run(
            [sys.executable, str(DIRECTORY / "sdk_config.py"), "--model-provider", provider, "--model", model],
            input=text, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_default_provider_needs_only_openai_and_no_coding_probe(self):
        self.assertEqual(self.inspect(DIRECT), {"api_key_env": "OPENAI_API_KEY", "coding_agent": None})

    def test_compatible_provider_selects_its_credential_with_either_legacy_setting(self):
        for provider in ("direct", "openrouter"):
            with self.subTest(provider=provider):
                self.assertEqual(self.inspect(COMPATIBLE, provider)["api_key_env"], "OPENROUTER_API_KEY")
        custom = COMPATIBLE.replace("https://openrouter.ai/api/v1", "https://models.example.test/v1")
        custom = custom.replace("OPENROUTER_API_KEY", "MODEL_GATEWAY_KEY")
        self.assertEqual(self.inspect(custom)["api_key_env"], "MODEL_GATEWAY_KEY")

    def test_openrouter_setting_cannot_hide_missing_or_mismatched_drop_in_provider(self):
        for config in (DIRECT, COMPATIBLE.replace("openrouter.ai", "models.example.test")):
            with self.subTest(config=config):
                self.assertIn("MODEL_PROVIDER=openrouter", self.inspect(config, "openrouter", success=False))

    def test_invalid_provider_settings_fail_without_echoing_values(self):
        cases = [
            COMPATIBLE.replace('api = "responses"', 'api = "invalid"'),
            COMPATIBLE.replace('api = "responses"', ''),
            COMPATIBLE.replace('base_url = "https://openrouter.ai/api/v1"', ''),
            COMPATIBLE.replace("https://openrouter.ai/api/v1",
                               urlunsplit(("https", "user:fixture-private@example.test", "/v1", "", ""))),
            COMPATIBLE.replace("https://openrouter.ai/api/v1", "http://example.test/v1"),
            COMPATIBLE.replace("OPENROUTER_API_KEY", "bad-name"),
            COMPATIBLE.replace("OPENROUTER_API_KEY", "CRUX_MODEL"),
            COMPATIBLE.replace('type = "openai-compatible"', 'type = "openai"'),
            COMPATIBLE + '\nunknown = "fixture-private"\n',
        ]
        for config in cases:
            with self.subTest(config=config):
                self.assertNotIn("fixture-private", self.inspect(config, success=False))

    def test_only_declared_coding_harnesses_are_probed(self):
        config = DIRECT + '\n[coding_agents.worker]\ntype = "codex"\ndescription = "work"\n'
        self.assertEqual(self.inspect(config)["coding_agent"], "codex")

    def test_codex_inherits_responses_endpoint_and_requires_presets_for_routing(self):
        worker = '\n[coding_agents.worker]\ntype = "codex"\ndescription = "work"\n'
        routing = '\n[runtime.model_provider.extra_body.provider]\nonly = ["google-vertex"]\nallow_fallbacks = false\n'
        self.assertEqual(self.inspect(COMPATIBLE + worker), {
            "api_key_env": "OPENROUTER_API_KEY", "coding_agent": "codex",
            "codex_base_url": "https://openrouter.ai/api/v1", "codex_api_key_env": "OPENROUTER_API_KEY",
        })
        config = COMPATIBLE + routing + worker
        self.assertIn("preset", self.inspect(config, success=False))
        self.assertEqual(self.inspect(config, model="google/gemini-4-argon@preset/vertex-only")["coding_agent"], "codex")
        self.assertIn("Responses", self.inspect(config.replace('api = "responses"', 'api = "chat-completions"'),
                                               success=False))
        self.assertIsNone(self.inspect(COMPATIBLE.replace('api = "responses"', 'api = "chat-completions"'))["coding_agent"])

    def test_compatible_provider_rejects_openai_only_compaction(self):
        config = COMPATIBLE + '\n[context]\ntype = "openai_compaction"\ntrigger_items = 200\n'
        self.assertIn("trim_recent", self.inspect(config, success=False))
        self.assertEqual(self.inspect(DIRECT + '\n[context]\ntype = "openai_compaction"\n')["api_key_env"], "OPENAI_API_KEY")

    def test_model_probe_loads_drop_in_and_only_declared_coding_harnesses(self):
        with tempfile.TemporaryDirectory() as directory:
            for coding_agent in (None, "codex"):
                for probe_exit in (0, 1):
                    with self.subTest(coding_agent=coding_agent, probe_exit=probe_exit):
                        result = subprocess.run(
                            ["bash", "-c", '''
set -euo pipefail
source "$1/scaffolds/sdk.sh"
RUN_USER=ubuntu
RUN_ENV_FILE=/etc/crux-run.env
WORK_DIR=/srv/crux-run
PROBE_LOG="$2/probe.log"
info() { :; }
ok() { :; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
systemd-run() { printf '%s\\n' "$@"; return "$PROBE_EXIT"; }
scaffold_probe_model
''', "test", str(DIRECTORY), directory],
                            env={"PATH": os.environ["PATH"], "PROBE_EXIT": str(probe_exit),
                                 "SDK_CONFIG_JSON": json.dumps({"coding_agent": coding_agent})},
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode == 0, probe_exit == 0, result.stderr)
                        args = (Path(directory) / "probe.log").read_text().splitlines()
                        self.assertEqual(args[args.index("--drop-in") + 1], "/srv/crux-run/run-harness")
                        self.assertIn("--property=EnvironmentFile=/etc/crux-run.env", args)
                        self.assertEqual("--coding-agent" in args, coding_agent is not None)
                        if coding_agent:
                            self.assertEqual(args[args.index("--coding-agent") + 1], coding_agent)

    def test_custom_selected_credential_is_not_duplicated_as_a_tool_key(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = Path(directory) / "secrets.json"
            secrets.write_text(json.dumps({"MODEL_GATEWAY_KEY": "fixture-provider", "GH_TOKEN": "fixture-tool",
                                           "OPENROUTER_API_KEY": "fixture-unused"}))
            result = subprocess.run(
                ["bash", "-c", '''
set -euo pipefail
source "$1/agent-config.sh"
API_KEY_NAME=MODEL_GATEWAY_KEY
die() { printf '%s\\n' "$*" >&2; exit 1; }
validate_agent_key "$2"
run_api_keys_json "$2"
''', "test", str(DIRECTORY), str(secrets)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"GH_TOKEN": "fixture-tool"})

    def test_committed_config_selects_credentials_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "drop-in/workspace").mkdir(parents=True)
            (repo / "drop-in/workspace/AGENTS.md").write_text("context")
            (repo / "drop-in/scaffold.toml").write_text(COMPATIBLE)
            subprocess.run(["git", "init", "-q", directory], check=True)
            subprocess.run(["git", "-C", directory, "add", "."], check=True)
            subprocess.run(["git", "-C", directory, "-c", "user.name=Test", "-c",
                            "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)
            # The dirty working copy must not change what provisioning will stage.
            (repo / "drop-in/scaffold.toml").write_text(DIRECT)
            secrets = repo / "secrets.json"
            secrets.write_text(json.dumps({"OPENROUTER_API_KEY": "fixture-router", "GH_TOKEN": "fixture-tool",
                                           "OPENAI_API_KEY": "fixture-unused"}))
            result = subprocess.run(
                ["bash", "-c", '''
set -euo pipefail
source "$1/agent-config.sh"
cfg() { printf '%s' "${!1:-}"; }
warn() { :; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
load_agent_config settings
load_drop_in_path "$2"
validate_agent_key "$3"
jq -n --arg key "$API_KEY_NAME" --argjson extra "$(run_api_keys_json "$3")" '{key: $key, extra: $extra}'
''', "test", str(DIRECTORY), directory, str(secrets)],
                env={"PATH": os.environ["PATH"], "AGENT_PLATFORM": "openai-agents",
                     "OPENAI_AGENTS_MODEL": "google/gemini-4-argon", "OPENAI_AGENTS_REASONING_EFFORT": "high",
                     "DROP_IN_PATH": "drop-in", "MODEL_PROVIDER": "openrouter"},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"key": "OPENROUTER_API_KEY", "extra": {"GH_TOKEN": "fixture-tool"}})
            secrets.write_text(json.dumps({"OPENAI_API_KEY": "fixture-wrong-provider"}))
            missing = subprocess.run(result.args, env={"PATH": os.environ["PATH"], "AGENT_PLATFORM": "openai-agents",
                                     "OPENAI_AGENTS_MODEL": "google/gemini-4-argon",
                                     "OPENAI_AGENTS_REASONING_EFFORT": "high", "DROP_IN_PATH": "drop-in",
                                     "MODEL_PROVIDER": "openrouter"}, capture_output=True, text=True, check=False)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("OPENROUTER_API_KEY", missing.stderr)
            self.assertNotIn("fixture-wrong-provider", missing.stdout + missing.stderr)


if __name__ == "__main__":
    unittest.main()
