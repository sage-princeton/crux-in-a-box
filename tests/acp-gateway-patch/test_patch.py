"""The acp-gateway patch provisioning applies: it must exist for the pinned version, apply cleanly to the
exact npm artifact boxes install, and fix the race it is there for."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WORKSPACES = REPO / "src" / "ec2-workspaces"
PLACEHOLDERS = WORKSPACES / "placeholders-base.txt.example"
PATCHES = WORKSPACES / "acp-gateway-patches"
APPLY = WORKSPACES / "apply-acp-gateway-patch.sh"
PACKAGE = "@agentrq/acp-gateway"

pytestmark = pytest.mark.skipif(shutil.which("npm") is None, reason="needs npm to install the gateway")


def pinned_version() -> str:
    for line in PLACEHOLDERS.read_text().splitlines():
        m = re.match(r"^ACP_GATEWAY_VERSION=(\S+)\s*$", line)
        if m:
            return m.group(1)
    raise RuntimeError(f"{PLACEHOLDERS} has no ACP_GATEWAY_VERSION")


@pytest.fixture(scope="session")
def version() -> str:
    return pinned_version()


@pytest.fixture(scope="session")
def patch_file(version) -> Path:
    path = PATCHES / f"{version}.patch"
    if not path.is_file():
        have = sorted(p.name for p in PATCHES.glob("*.patch"))
        pytest.fail(f"ACP_GATEWAY_VERSION={version} has no patch (expected {path.relative_to(REPO)}). "
                    f"Patches exist for: {', '.join(have) or 'nothing'}. Port the patch before moving the pin.")
    return path


@pytest.fixture(scope="session")
def pristine(version, tmp_path_factory) -> Path:
    """The package as `npm install -g` lays it out on a box, untouched."""
    prefix = tmp_path_factory.mktemp("npm-global")
    subprocess.run(["npm", "install", "-g", "--prefix", str(prefix), "--no-audit", "--no-fund",
                    f"{PACKAGE}@{version}"], check=True, capture_output=True, text=True)
    root = subprocess.run(["npm", "root", "-g", "--prefix", str(prefix)],
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(root) / PACKAGE


@pytest.fixture
def gateway(pristine, tmp_path) -> Path:
    """A fresh copy of the installed package for one test to patch."""
    copy = tmp_path / "acp-gateway"
    shutil.copytree(pristine, copy, symlinks=True)
    return copy


def apply(package: Path, patch: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(APPLY), str(package), str(patch)], capture_output=True, text=True)


def replay(package: Path, scenario: str) -> dict:
    out = subprocess.run(["node", str(HERE / "replay.mjs"), str(package / "dist" / "acpClient.js"), scenario],
                         check=True, capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_the_unpatched_gateway_drops_a_verdict_that_beats_the_acknowledgement(gateway):
    """Guards the replay itself: if this stops failing the gateway, the next test proves nothing."""
    assert replay(gateway, "early-verdict") == {"outcome": {"outcome": "cancelled"}, "turnCancelled": True}


def test_the_patched_gateway_keeps_a_verdict_that_beats_the_acknowledgement(gateway, patch_file):
    assert apply(gateway, patch_file).returncode == 0
    assert replay(gateway, "early-verdict") == {
        "outcome": {"outcome": "selected", "optionId": "allow"}, "turnCancelled": False}


def test_the_patched_gateway_still_answers_a_failed_send_with_cancelled_and_leaves_the_turn(gateway, patch_file):
    assert apply(gateway, patch_file).returncode == 0
    assert replay(gateway, "send-fails") == {"outcome": {"outcome": "cancelled"}, "turnCancelled": False}


def test_applying_twice_is_a_no_op(gateway, patch_file):
    assert apply(gateway, patch_file).returncode == 0
    patched = (gateway / "dist" / "acpClient.js").read_text()

    second = apply(gateway, patch_file)

    assert second.returncode == 0, second.stderr
    assert (gateway / "dist" / "acpClient.js").read_text() == patched


def test_a_patch_that_does_not_fit_fails_and_leaves_the_gateway_untouched(gateway, patch_file):
    target = gateway / "dist" / "acpClient.js"
    drifted = target.read_text().replace("await this.mcpBridge.sendNotification(\"notifications/claude/channel/"
                                         "permission_request\", payload);", "await this.send(payload);")
    target.write_text(drifted)

    result = apply(gateway, patch_file)

    assert result.returncode != 0
    assert target.read_text() == drifted
    assert not list(gateway.rglob("*.rej")) and not list(gateway.rglob("*.orig"))


def test_a_missing_patch_fails(gateway, tmp_path):
    assert apply(gateway, tmp_path / "no-such.patch").returncode != 0
