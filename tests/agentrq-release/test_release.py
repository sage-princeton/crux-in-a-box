"""The control-plane image pin: the tarball must match its pinned sha256 and load as the pinned image, and that image
must keep a task with more than 4,095 tool calls repliable and closable, which upstream cannot."""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIGURE = REPO / "src" / "ec2-control" / "configure-control.sh"
KEYS = ("AGENTRQ_IMAGE", "AGENTRQ_IMAGE_URL", "AGENTRQ_IMAGE_SHA256")
# The image crux-control ran before the CRUX build: upstream 45c3692, which freezes a task past 4,095 tool calls.
UPSTREAM_WITH_FREEZE = "agentrq/agentrq@sha256:a488cbf140991379114c96014fab010de4c2c88cd0d6e67a4db1f3714fe27a6a"
# One past the most tool-call rows SQLite can bind in one statement (32,766 variables / 8 columns).
TOOL_CALLS = 4_096
ROOT_TOKEN = "release-test"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker to load and run the image")


def read_pins() -> dict[str, str]:
    pins = {}
    for line in CONFIGURE.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(\S*)\s*$", line)
        if m and m.group(1) in KEYS:
            pins[m.group(1)] = m.group(2)
    missing = [k for k in KEYS if not pins.get(k)]
    if missing:
        raise RuntimeError(f"{CONFIGURE} has no value for {', '.join(missing)}")
    return pins


@pytest.fixture(scope="session")
def pins() -> dict[str, str]:
    return read_pins()


@pytest.fixture(scope="session")
def tarball(pins, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("release") / "agentrq-image.tar.gz"
    with urllib.request.urlopen(pins["AGENTRQ_IMAGE_URL"], timeout=300) as response:
        path.write_bytes(response.read())
    return path


@pytest.fixture(scope="session")
def pinned(pins, tarball) -> str:
    """Loads the way configure-control.sh does."""
    subprocess.run(["docker", "load", "-q", "-i", str(tarball)], check=True, capture_output=True, text=True)
    return pins["AGENTRQ_IMAGE"]


@pytest.fixture(scope="session")
def upstream() -> str:
    subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", UPSTREAM_WITH_FREEZE],
                   check=True, capture_output=True, text=True)
    return UPSTREAM_WITH_FREEZE


class Server:
    """One AgentRQ container on a throwaway SQLite volume, signed in as root."""

    def __init__(self, image: str, data: Path):
        self.image, self.data = image, data
        self.name = f"agentrq-release-test-{uuid.uuid4().hex[:8]}"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}/api/v1"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data.chmod(0o777)

    def start(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--platform", "linux/amd64", "--name", self.name,
                        "-p", f"127.0.0.1:{self.port}:3000", "-v", f"{self.data}:/_storage",
                        "-e", "AGENTRQ_SQLITE_DSN=/_storage/agentrq.db",
                        "-e", "AGENTRQ_AUTH_ROOT_LOGIN_ENABLED=true",
                        "-e", f"AGENTRQ_AUTH_ROOT_ACCESS_TOKEN={ROOT_TOKEN}",
                        "-e", "AGENTRQ_SMTP_ENABLED=false", self.image],
                       check=True, capture_output=True, text=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if urllib.request.urlopen(f"{self.base}/auth/config", timeout=2).status == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        else:
            logs = subprocess.run(["docker", "logs", "--tail", "40", self.name], capture_output=True, text=True)
            raise RuntimeError(f"{self.image} did not answer within 120s:\n{logs.stdout}{logs.stderr}")
        assert self.call("POST", "/auth/root/login", {"rootToken": ROOT_TOKEN})[0] == 200

    def stop(self):
        subprocess.run(["docker", "stop", self.name], check=True, capture_output=True)

    def logs(self) -> str:
        out = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        return out.stdout + out.stderr

    def remove(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def call(self, method: str, path: str, body=None) -> tuple[int, dict | str]:
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=120) as r:
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw.decode(errors="replace")


def long_task(image: str, data: Path) -> dict:
    """Creates a task over the API, gives it TOOL_CALLS tool calls the way the gateway's auto-approvals leave them,
    then replies to it and marks it completed, as the pilot's operator tried to."""
    server = Server(image, data)
    try:
        server.start()
        code, p = server.call("POST", "/workspaces", {"workspace": {"name": "release-test"}})
        assert code in (200, 201), p
        ws = p["workspace"]["id"]
        code, p = server.call("POST", f"/workspaces/{ws}/tasks",
                              {"task": {"title": "long task", "body": "run", "assignee": "agent",
                                        "createdBy": "human"}})
        assert code in (200, 201), p
        task = p["task"]["id"]

        server.stop()
        with sqlite3.connect(data / "agentrq.db") as db:
            task_id, ws_id = db.execute("SELECT id, workspace_id FROM tasks").fetchone()
            db.executemany(
                "INSERT INTO tool_calls (id, created_at, task_id, workspace_id, tool_name, description,"
                " input_preview, status) VALUES (?, datetime(?, 'unixepoch'), ?, ?, 'Bash', 'Run command', 'ls',"
                " 'auto_allowed')",
                ((task_id + 1 + i, 1_760_000_000 + i, task_id, ws_id) for i in range(TOOL_CALLS)))
        server.start()

        reply = server.call("POST", f"/workspaces/{ws}/tasks/{task}/respond",
                            {"response": {"action": "text", "text": "keep going"}})
        status = server.call("PATCH", f"/workspaces/{ws}/tasks/{task}/status", {"status": {"value": "completed"}})
        fetch = server.call("GET", f"/workspaces/{ws}/tasks/{task}")
        with sqlite3.connect(data / "agentrq.db") as db:
            stored = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
        return {"reply": reply, "status": status, "fetch": fetch, "stored": stored, "logs": server.logs()}
    finally:
        server.remove()


def test_the_tarball_matches_the_pinned_sha256(pins, tarball):
    assert hashlib.sha256(tarball.read_bytes()).hexdigest() == pins["AGENTRQ_IMAGE_SHA256"], \
        f"{pins['AGENTRQ_IMAGE_URL']} does not match AGENTRQ_IMAGE_SHA256; configure-control.sh would refuse it. " \
        "Copy the hash from the release's .sha256 asset."


def test_the_tarball_loads_as_the_pinned_image(pinned):
    subprocess.run(["docker", "image", "inspect", pinned], check=True, capture_output=True)


def test_the_long_task_freezes_upstream(upstream, tmp_path):
    """Guards the scenario itself: if this stops failing upstream, the next test proves nothing."""
    result = long_task(upstream, tmp_path)
    assert result["reply"][0] == 500
    assert result["status"][0] == 500
    assert "too many SQL variables" in result["logs"]
    assert result["stored"] == "notstarted"


def test_the_pinned_image_replies_to_and_closes_a_task_past_4095_tool_calls(pinned, tmp_path):
    result = long_task(pinned, tmp_path)
    assert result["reply"][0] == 200, result["reply"]
    assert result["status"][0] == 200, result["status"]
    assert result["stored"] == "completed"
    assert result["fetch"][0] == 200
    assert len(result["fetch"][1]["task"]["toolCalls"]) == 500
