import base64
import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/run_provision.py"
sys.path.insert(0, str(SCRIPT.parent))
import run_provision as provision


class Mailbox(BaseHTTPRequestHandler):
    def do_GET(self):
        allowed = (self.path == "/inbox" and self.headers.get("Authorization") == self.server.token
                   and self.headers.get("X-API-Key") == "fixture-private-api")
        self.send_response(200 if allowed else 401)
        self.end_headers()
        self.wfile.write(b'{"messages": 2}')

    def log_message(self, *args):
        pass


class ServiceLaunchTests(unittest.TestCase):
    def test_mailbox_authentication_gates_actual_agent_exec_and_preserves_refresh(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        workspace = home / "workspace"
        workspace.mkdir()
        (workspace / "workspace-marker").touch()
        server = HTTPServer(("127.0.0.1", 0), Mailbox)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server.token = "fixture-original-token"
        cli = home / "mailbox_cli.py"
        cli.write_text('''import json, os, pathlib, sys, urllib.request
assert pathlib.Path("workspace-marker").is_file()
token = json.loads(pathlib.Path(os.environ["MAILBOX_CONFIG"]).read_text())["token"]
print(token, "fixture-debug", flush=True)
print(os.environ["MAILBOX_API_KEY"], file=sys.stderr, flush=True)
request = urllib.request.Request(os.environ["MAILBOX_URL"], headers={
    "Authorization": token, "X-API-Key": os.environ["MAILBOX_API_KEY"]})
with urllib.request.urlopen(request, timeout=2) as response:
    assert json.load(response)["messages"] == 2
''')
        manifest, env_file, secrets_file = (home / name for name in ("provision.json", "run.env", "secrets.json"))
        manifest.write_text(json.dumps({"version": 1, "services": [{"name": "mailbox",
            "environment": {"MAILBOX_CONFIG": "{service_dir}/account.json"}, "required_env": ["MAILBOX_API_KEY"],
            "check": {"argv": [sys.executable, str(cli)], "hint": "Refresh the mailbox login."}}]}))
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            content = json.dumps({"token": server.token}).encode()
            member = tarfile.TarInfo("account.json")
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
        secrets_file.write_text(json.dumps({"SERVICE_AUTH": {"mailbox": base64.b64encode(archive.getvalue()).decode()}}))
        secrets_file.chmod(0o600)
        base_env = {"HOME": str(home), "PATH": os.environ["PATH"], "MAILBOX_API_KEY": "fixture-private-api",
                    "MAILBOX_URL": f"http://127.0.0.1:{server.server_port}/inbox"}

        def configure():
            env_file.write_text("".join(f"{key}={json.dumps(value)}\n" for key, value in base_env.items()))
            output = io.StringIO()
            user = SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(provision.pwd, "getpwnam", return_value=user), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(provision.main(["configure", "--manifest", str(manifest),
                    "--secrets", str(secrets_file), "--env-file", str(env_file)]), 0)
            self.assertEqual(output.getvalue(), "")
            return {key: json.loads(value) for key, value in
                    (line.split("=", 1) for line in env_file.read_text().splitlines())}

        def launch(action="exec", expected=0, environment=None):
            marker = workspace / "agent-started"
            marker.unlink(missing_ok=True)
            command = [sys.executable, str(SCRIPT), action, "--manifest", str(manifest)]
            if action == "exec":
                command += ["--", sys.executable, "-c", "from pathlib import Path; Path('agent-started').touch()"]
            result = subprocess.run(command, cwd=workspace, env=environment or env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(marker.exists(), action == "exec" and expected == 0)
            self.assertNotIn("fixture-", result.stdout + result.stderr)

        env = configure()
        credential = Path(env["MAILBOX_CONFIG"])
        self.assertNotIn("SERVICE_AUTH", env)
        launch("check")
        launch()
        server.token = "fixture-refreshed-token"
        launch(expected=2)
        credential.write_text(json.dumps({"token": server.token}))
        env = configure()
        self.assertEqual(json.loads(credential.read_text())["token"], server.token)
        launch()
        launch(expected=2, environment={key: value for key, value in env.items() if key != "MAILBOX_API_KEY"})
        credential.write_text('{"token":"fixture-invalid-token"}')
        launch(expected=2)
        credential.unlink()
        launch(expected=2)
