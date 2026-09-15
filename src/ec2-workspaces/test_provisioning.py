"""Behavior checks for provisioning; AWS, SSH, model calls, and root commands are stubbed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
BASH = shutil.which("bash")


class ProvisioningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="crux-provision-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scripts = self.root / "src/ec2-workspaces"
        self.scripts.mkdir(parents=True)
        for path in SCRIPTS.glob("*.sh"):
            shutil.copy(path, self.scripts / path.name)
        (self.root / "src/ec2-control").mkdir()
        hook = self.root / "agentrq/claude/.claude/hooks/langfuse_hook.py"
        hook.parent.mkdir(parents=True)
        hook.write_text("# fixture hook\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.home = self.root / "home"
        (self.home / ".ssh").mkdir(parents=True)
        (self.home / ".ssh/test-key.pem").touch()
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}",
                    "HOME": str(self.home), "TEST_ROOT": str(self.root),
                    "TEST_CALLS": str(self.calls)}
        for command in ("ssh", "scp", "curl"):
            self.stub(command, f'echo "{command} $*" >> "$TEST_CALLS"\n')
        self.stub("aws", '''echo "aws $*" >> "$TEST_CALLS"
case "$*" in
  *get-caller-identity*) echo 123456789012 ;;
  *describe-security-groups*) echo sg-test ;;
  *describe-instances*) echo None ;;
esac
''')

    def stub(self, command, body):
        path = self.bin / command
        path.write_text(f"#!{BASH}\nset -eu\n{body}")
        path.chmod(0o755)

    def config(self, platform="claude", effort="high", **overrides):
        values = dict(AWS_REGION="us-east-1", KEY_NAME="test-key",
                      CONTROL_MCP_BASE="https://control.example.com",
                      OPERATOR_CIDR="203.0.113.1/32", INSTANCE_TYPE="t3.large",
                      ROOT_DISK_GB="80", ACP_GATEWAY_VERSION="0.2.17")
        if platform == "claude":
            values.update(AGENT_PLATFORM="claude", CLAUDE_MODEL="claude-opus-4-6",
                          CLAUDE_EFFORT=effort, CLAUDE_VERSION="2.1.272",
                          CLAUDE_ACP_VERSION="0.77.0")
        else:
            values.update(CODEX_MODEL="gpt-5.5", CODEX_REASONING_EFFORT=effort,
                          CODEX_VERSION="0.154.0", CODEX_ACP_VERSION="1.10.0",
                          TRACING_PLUGIN_VERSION="0.3.0",
                          TRACING_HOOK_TRUSTED_HASH="sha256:fixture")
        values.update(overrides)
        text = "".join(f"{key}={value}\n" for key, value in values.items())
        (self.scripts / "placeholders-base.txt").write_text(text)
        path = self.scripts / "placeholders-test.txt"
        path.write_text(text + "RUN_SLUG=test-box\n")
        key = "ANTHROPIC_API_KEY" if platform == "claude" else "OPENAI_API_KEY"
        self.secrets = self.scripts / "run-secrets-base.json"
        self.secrets.write_text(json.dumps({key: "fixture-key"}))
        return path, values

    def run_script(self, name, *args, env=None):
        return subprocess.run([BASH, str(self.scripts / name), *map(str, args)],
                              env=self.env | (env or {}), text=True,
                              capture_output=True, timeout=15)

    def test_entry_points_accept_claude_and_legacy_codex(self):
        # Both entry points accept platform-specific efforts and Codex configs without a selector.
        for platform, effort in (("claude", "max"), ("claude", "xhigh"),
                                 ("codex", "minimal")):
            with self.subTest(platform=platform, effort=effort):
                config, _ = self.config(platform, effort)
                for script, args in (
                    ("make-new-workspace.sh", ("fresh-box", "--dry-run")),
                    ("provision-workspace-aws-resources.sh", ("--dry-run", config)),
                ):
                    result = self.run_script(script, *args)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(platform, result.stdout)
                    self.assertIn(effort, result.stdout)

    def test_invalid_settings_fail_before_external_calls(self):
        # Invalid or unsafe settings must fail before any AWS or SSH command runs.
        for platform, effort, extra in (
            ("claude", "minimal", {}), ("codex", "max", {}),
            ("claude", "", {}), ("claude", "high", {"CLAUDE_MODEL": ""}),
            ("claude", "high", {"CLAUDE_ACP_VERSION": ""}),
            ("claude", "high", {"AGENT_PLATFORM": "other"}),
            ("claude", "high", {"CLAUDE_MODEL": "model'bad"}),
        ):
            with self.subTest(platform=platform, effort=effort, extra=extra):
                config, _ = self.config(platform, effort, **extra)
                for script, args in (
                    ("make-new-workspace.sh", ("fresh-box", "--dry-run")),
                    ("provision-workspace-aws-resources.sh", ("--dry-run", config)),
                ):
                    self.calls.unlink(missing_ok=True)
                    result = self.run_script(script, *args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(self.calls.exists(), result.stdout + result.stderr)

    def test_wrong_provider_key_fails_before_mint(self):
        # A Claude workspace requires an Anthropic key and must not expose a rejected key.
        self.config()
        self.secrets.write_text('{"OPENAI_API_KEY":"fixture-wrong-provider"}')
        result = self.run_script("make-new-workspace.sh", "fresh-box")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ANTHROPIC_API_KEY", result.stderr)
        self.assertNotIn("fixture-wrong-provider", result.stdout + result.stderr)
        self.assertFalse(self.calls.exists())

    def test_mint_preserves_selected_platform_and_secret(self):
        # Generated files retain the chosen settings and store workspace credentials privately.
        self.config(effort="max")
        bootstrap = self.root / "src/ec2-control/bootstrap-workspace.sh"
        bootstrap.write_text('#!/bin/sh\necho \'{"id":"workspace-test","token":"fixture-token"}\'\n')
        bootstrap.chmod(0o755)
        (self.scripts / "provision-workspace-aws-resources.sh").write_text("#!/bin/sh\nexit 0\n")
        result = self.run_script("make-new-workspace.sh", "fresh-box")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (self.scripts / "placeholders-fresh-box.txt").read_text()
        self.assertIn("AGENT_PLATFORM=claude", config)
        self.assertIn("CLAUDE_EFFORT=max", config)
        secrets = self.scripts / "run-secrets-fresh-box.json"
        self.assertEqual(json.loads(secrets.read_text()), {
            "ANTHROPIC_API_KEY": "fixture-key", "AGENTRQ_WORKSPACE_ID": "workspace-test",
            "AGENTRQ_WORKSPACE_TOKEN": "fixture-token"})
        self.assertEqual(secrets.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fixture-key", result.stdout + result.stderr)

    def test_explicit_base_files_leave_codex_defaults_intact(self):
        # Separate Claude base files must work without overwriting the existing Codex defaults.
        self.config("claude", "max")
        config = self.scripts / "placeholders-claude-base.txt"
        secrets = self.scripts / "run-secrets-claude-base.json"
        (self.scripts / "placeholders-base.txt").rename(config)
        self.secrets.rename(secrets)
        self.config("codex")
        original = (self.scripts / "placeholders-base.txt").read_text()
        result = self.run_script("make-new-workspace.sh", "fresh-box", "--dry-run",
                                 "--base-config", config, "--base-secrets", secrets)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("platform claude", result.stdout)
        self.assertIn("effort max", result.stdout)
        self.assertEqual((self.scripts / "placeholders-base.txt").read_text(), original)

    def test_install_selects_only_the_requested_pinned_adapter(self):
        # Each platform installs its pinned ACP adapter alongside the shared pinned gateway.
        script = self.scripts / "install-run.sh"
        script.write_text(script.read_text().replace('RUN_HOME="/home/$RUN_USER"',
                                                   f'RUN_HOME="{self.home}"'))
        uv = self.home / ".local/bin/uv"
        uv.parent.mkdir(parents=True)
        uv.touch()
        uv.chmod(0o755)
        for command in ("apt-get", "codex-acp", "claude-agent-acp", "acp-gateway"):
            self.stub(command, ":\n")
        self.stub("node", "echo v22.0.0\n")
        self.stub("codex", "echo 'codex-cli 0.154.0'\n")
        self.stub("claude", "echo '2.1.272 (Claude Code)'\n")
        self.stub("npm", 'echo "npm $*" >> "$TEST_CALLS"\n')
        for platform, package in (("claude", "claude-agent-acp@0.77.0"),
                                  ("codex", "codex-acp@1.10.0")):
            with self.subTest(platform=platform):
                _, values = self.config(platform)
                self.calls.unlink(missing_ok=True)
                result = self.run_script("install-run.sh", env=values)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = self.calls.read_text()
                self.assertIn("@agentclientprotocol/" + package, calls)
                self.assertIn("@agentrq/acp-gateway@0.2.17", calls)
                other = "codex-acp@" if platform == "claude" else "claude-agent-acp@"
                self.assertNotIn(other, calls)

    def test_provision_transfers_claude_config_and_hook(self):
        # SSH transfers Claude settings and its hook while excluding inactive Codex values.
        config, _ = self.config(CODEX_MODEL="ignored'unsafe")
        bundle = json.loads(self.secrets.read_text()) | {
            "AGENTRQ_WORKSPACE_ID": "workspace-test", "AGENTRQ_WORKSPACE_TOKEN": "fixture-token"}
        self.secrets.write_text(json.dumps(bundle))
        self.stub("ssh-keygen", ":\n")
        self.stub("aws", '''echo "aws $*" >> "$TEST_CALLS"
case "$*" in
  *get-caller-identity*) echo 123456789012 ;;
  *PrivateDnsName*) echo 'ip-10-0-0-1.ec2.internal 10.0.0.1' ;;
  *State.Name*) echo running ;;
  *PrivateIpAddress*) echo 10.0.0.2 ;;
  *describe-instances*) echo i-test ;;
  *describe-security-groups*) echo sg-test ;;
  *describe-vpcs*) echo vpc-test ;;
  *describe-subnets*) echo subnet-test ;;
  *get-parameters*) echo ami-test ;;
  *AllocationId*) echo eipalloc-test ;;
  *PublicIp*) echo 203.0.113.2 ;;
esac
''')
        result = self.run_script("provision-workspace-aws-resources.sh",
                                 "--secrets", self.secrets, config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text()
        self.assertIn("CLAUDE_VERSION='2.1.272'", calls)
        self.assertIn("CLAUDE_ACP_VERSION='0.77.0'", calls)
        self.assertIn("CLAUDE_MODEL='claude-opus-4-6'", calls)
        self.assertIn("CLAUDE_EFFORT='high'", calls)
        self.assertIn("AGENT_PLATFORM='claude'", calls)
        self.assertIn("agent-config.sh", calls)
        self.assertIn("langfuse_hook.py", calls)
        self.assertNotIn("ignored'unsafe", calls)

    def configure_box(self, platform="claude", probe="ok"):
        _, values = self.config(platform, "max" if platform == "claude" else "high")
        work = self.root / "work"
        etc = self.root / "etc"
        (etc / "systemd/system").mkdir(parents=True, exist_ok=True)
        script = self.scripts / "configure-run.sh"
        # Redirect machine paths only in the fixture copy; production has no test flags.
        script.write_text(script.read_text().replace('RUN_HOME="/home/$RUN_USER"',
                          f'RUN_HOME="{self.home}"').replace("/srv/crux-run", str(work))
                          .replace("/etc/crux-run.env", str(etc / "crux-run.env"))
                          .replace("/etc/systemd/system/", str(etc / "systemd/system") + "/"))
        (self.scripts / "langfuse_hook.py").write_text("# fixture hook\n")
        bundle = json.loads(self.secrets.read_text()) | {
            "AGENTRQ_WORKSPACE_ID": "workspace-test", "AGENTRQ_WORKSPACE_TOKEN": "fixture-token"}
        self.secrets.write_text(json.dumps(bundle))
        for command in ("chown", "sleep"):
            self.stub(command, ":\n")
        self.stub("aws", 'echo \'{"LANGFUSE_PUBLIC_KEY":"fixture-pk",'
                  '"LANGFUSE_SECRET_KEY":"fixture-sk","LANGFUSE_BASE_URL":"https://trace.example.com"}\'\n')
        self.stub("systemctl", '''echo "systemctl $*" >> "$TEST_CALLS"
case "$1" in is-active) echo active ;; show) echo 0 ;; esac
''')
        self.stub("su", 'while [ "$1" != -c ]; do shift; done\nshift\nexec bash -c "$1"\n')
        self.stub("timeout", 'shift\nexec "$@"\n')
        self.stub("claude", '''case "$TEST_PROBE" in
  fail) exit 1 ;;
  stale) ;;
  *) mkdir -p "$TEST_ROOT/work/.claude/state"
     echo "Processed 1 turns in 0.01s" >> "$TEST_ROOT/work/.claude/state/langfuse_hook.log" ;;
esac
echo '{"is_error":false,"result":"HOOK-PROBE"}'
''')
        self.stub("codex", '''case "$*" in
  "login --with-api-key") cat >/dev/null; echo '{}' > "$HOME/.codex/auth.json" ;;
  "login status") echo Logged-in ;;
  exec*) echo 'hook: Stop' ;;
esac
''')
        if platform == "codex":
            entry = self.home / ".codex/plugins/cache/codex-observability-plugin/tracing/0.3.0/dist/index.mjs"
            entry.parent.mkdir(parents=True)
            entry.touch()
        if probe == "stale":
            state = work / ".claude/state"
            state.mkdir(parents=True)
            (state / "langfuse_hook.log").write_text("Processed 1 turns in 0.01s\n")
        env = values | {"RUN_SECRETS_PATH": str(self.secrets),
                        "SYSTEM_SSM_PARAM": "/crux/system/env", "RUN_SLUG": "test-box",
                        "TEST_PROBE": probe}
        return self.run_script("configure-run.sh", env=env), work, etc

    def test_claude_config_secrets_tracing_and_gateway(self):
        # Claude gets private credentials, tracing, and the correct service after a successful probe.
        result, work, etc = self.configure_box()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = self.home / ".claude/settings.json"
        settings = json.loads(path.read_text())
        self.assertEqual(settings["model"], "claude-opus-4-6")
        self.assertEqual(settings["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "max")
        self.assertEqual(settings["env"]["ANTHROPIC_API_KEY"], "fixture-key")
        self.assertEqual(settings["env"]["TRACE_TO_LANGFUSE"], "true")
        self.assertEqual(settings["env"]["LANGFUSE_TRACING_ENVIRONMENT"], "test-box")
        self.assertIn("langfuse_hook.py", settings["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertNotIn("permissions", settings)  # AgentRQ owns YOLO approvals.
        self.assertFalse((self.home / ".codex").exists())
        unit = (etc / "systemd/system/crux-acp-gateway.service").read_text()
        self.assertIn("-- claude-agent-acp", unit)
        self.assertNotIn("fixture-key", unit + result.stdout + result.stderr)
        for private in (path, work / ".mcp.json", etc / "crux-run.env"):
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.secrets.exists())

    def test_legacy_codex_config_and_gateway_still_work(self):
        # Legacy Codex provisioning keeps its model and adapter without creating Claude config.
        result, _, etc = self.configure_box("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('model = "gpt-5.5"', (self.home / ".codex/config.toml").read_text())
        self.assertIn("-- codex-acp", (etc / "systemd/system/crux-acp-gateway.service").read_text())
        self.assertFalse((self.home / ".claude").exists())

    def test_stale_hook_success_cannot_start_gateway(self):
        # An old successful hook log cannot substitute for tracing the current probe.
        result, _, etc = self.configure_box(probe="stale")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not process", result.stderr)
        self.assertFalse((etc / "systemd/system/crux-acp-gateway.service").exists())

    def test_failed_claude_probe_cannot_start_gateway(self):
        # A failed model probe must stop provisioning before the gateway service is installed.
        result, _, etc = self.configure_box(probe="fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Claude probe failed", result.stderr)
        self.assertFalse((etc / "systemd/system/crux-acp-gateway.service").exists())


if __name__ == "__main__":
    unittest.main()
