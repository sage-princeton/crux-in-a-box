"""The gateway release provisioning pins: the tarball must match its pinned sha256 and version, and fix the race
that made the pilot's gateway drop auto-approvals and cancel the turn."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLACEHOLDERS = REPO / "src" / "ec2-workspaces" / "placeholders-base.txt.example"
KEYS = ("ACP_GATEWAY_VERSION", "ACP_GATEWAY_TARBALL_URL", "ACP_GATEWAY_SHA256")
PACKAGE = "@agentrq/acp-gateway"
# The last upstream release known to drop a verdict that beats the acknowledgement.
UPSTREAM_WITH_RACE = "0.2.17"

pytestmark = pytest.mark.skipif(shutil.which("npm") is None, reason="needs npm to install the gateway")


def read_pins() -> dict[str, str]:
    pins = {}
    for line in PLACEHOLDERS.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(\S*)\s*$", line)
        if m and m.group(1) in KEYS:
            pins[m.group(1)] = m.group(2)
    missing = [k for k in KEYS if not pins.get(k)]
    if missing:
        raise RuntimeError(f"{PLACEHOLDERS} has no value for {', '.join(missing)}")
    return pins


@pytest.fixture(scope="session")
def pins() -> dict[str, str]:
    return read_pins()


@pytest.fixture(scope="session")
def tarball(pins, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("release") / "acp-gateway.tgz"
    with urllib.request.urlopen(pins["ACP_GATEWAY_TARBALL_URL"], timeout=60) as response:
        path.write_bytes(response.read())
    return path


def install(spec: str, prefix: Path) -> Path:
    """Installs the way install-run.sh does, into a throwaway global prefix."""
    subprocess.run(["npm", "install", "-g", "--prefix", str(prefix), "--no-audit", "--no-fund", spec],
                   check=True, capture_output=True, text=True)
    root = subprocess.run(["npm", "root", "-g", "--prefix", str(prefix)],
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(root) / PACKAGE


@pytest.fixture(scope="session")
def pinned(tarball, tmp_path_factory) -> Path:
    return install(str(tarball), tmp_path_factory.mktemp("npm-global"))


@pytest.fixture(scope="session")
def upstream(tmp_path_factory) -> Path:
    return install(f"{PACKAGE}@{UPSTREAM_WITH_RACE}", tmp_path_factory.mktemp("npm-upstream"))


def replay(package: Path, scenario: str) -> dict:
    out = subprocess.run(["node", str(HERE / "replay.mjs"), str(package / "dist" / "acpClient.js"), scenario],
                         check=True, capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_the_tarball_matches_the_pinned_sha256(pins, tarball):
    assert hashlib.sha256(tarball.read_bytes()).hexdigest() == pins["ACP_GATEWAY_SHA256"], \
        f"{pins['ACP_GATEWAY_TARBALL_URL']} does not match ACP_GATEWAY_SHA256; install-run.sh would refuse it. " \
        "Copy the hash from the release's .sha256 asset."


def test_the_tarball_installs_the_pinned_version(pins, pinned):
    assert json.loads((pinned / "package.json").read_text())["version"] == pins["ACP_GATEWAY_VERSION"]


def test_the_replay_catches_the_race_in_upstream(upstream):
    """Guards the replay itself: if this stops failing upstream, the next test proves nothing."""
    assert replay(upstream, "early-verdict") == {"outcome": {"outcome": "cancelled"}, "turnCancelled": True}


def test_the_pinned_gateway_keeps_a_verdict_that_beats_the_acknowledgement(pinned):
    assert replay(pinned, "early-verdict") == {
        "outcome": {"outcome": "selected", "optionId": "allow"}, "turnCancelled": False}


def test_the_pinned_gateway_answers_a_failed_send_with_cancelled_and_leaves_the_turn(pinned):
    assert replay(pinned, "send-fails") == {"outcome": {"outcome": "cancelled"}, "turnCancelled": False}
