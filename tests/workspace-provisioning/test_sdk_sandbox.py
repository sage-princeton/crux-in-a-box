"""The SDK box must prepare and exercise its sandbox before any paid probe."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("restriction", ["1", "0", None])
@pytest.mark.parametrize("existing", [False, True])
def test_install_loads_bubblewrap_profile_only_when_required(tmp_path, restriction, existing):
    source = (ROOT / "src/ec2-workspaces/scaffolds/sdk.sh").read_text()
    for path in ("/proc/sys/kernel", "/usr/share/apparmor", "/etc/apparmor.d"):
        source = source.replace(path, str(tmp_path / path.lstrip("/")))
    module = tmp_path / "sdk.sh"
    module.write_text(source)
    setting = tmp_path / "proc/sys/kernel/apparmor_restrict_unprivileged_userns"
    setting.parent.mkdir(parents=True)
    if restriction is not None:
        setting.write_text(restriction)
    packaged = tmp_path / "usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("packaged profile")
    installed = tmp_path / "etc/apparmor.d/bwrap-userns-restrict"
    installed.parent.mkdir(parents=True)
    if existing:
        installed.write_text("operator profile")
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
set -eu
source "$1"
info() { :; }
ok() { :; }
die() { echo "$*" >&2; exit 1; }
apt-get() { printf 'packages:%s\\n' "$*" >&2; }
apparmor_parser() { printf 'load:%s\\n' "$*"; }
scaffold_install_sandbox
""",
            "test",
            str(module),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "bubblewrap" in result.stderr and "apparmor-profiles" in result.stderr
    assert ("load:" in result.stdout) == (restriction == "1")
    if existing:
        assert installed.read_text() == "operator profile"
    elif restriction == "1":
        assert installed.read_text() == "packaged profile"
    else:
        assert not installed.exists()


@pytest.mark.parametrize("probe_exit", [0, 1])
def test_sandbox_probe_uses_service_environment_and_propagates_failures(tmp_path, probe_exit):
    commands = tmp_path / "commands.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "fake-python"
    python.write_text("#!/bin/sh\nprintf '%s\\n' /opt/pinned-codex\n")
    python.chmod(0o755)
    systemd = bin_dir / "systemd-run"
    systemd.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["COMMAND_LOG"]).write_text(json.dumps(sys.argv[1:]))
raise SystemExit(int(os.environ["PROBE_EXIT"]))
""")
    systemd.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
set -eu
source "$1/src/ec2-workspaces/scaffolds/sdk.sh"
SCAFFOLD_PYTHON="$2"
RUN_USER=ubuntu
RUN_ENV_FILE=/etc/crux-run.env
PROBE_LOG="$3"
info() { :; }
ok() { :; }
die() { echo "$*" >&2; exit 1; }
scaffold_probe_sandbox
""",
            "test",
            str(ROOT),
            str(python),
            str(tmp_path / "probe.log"),
        ],
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "COMMAND_LOG": str(commands),
            "PROBE_EXIT": str(probe_exit),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (probe_exit == 0), result.stderr
    args = json.loads(commands.read_text())
    assert "--uid=ubuntu" in args
    assert "--property=EnvironmentFile=/etc/crux-run.env" in args
    assert "--working-directory=/srv/crux-run/run-harness/workspace" in args
    assert 'sandbox_mode="workspace-write"' in args
    assert "/opt/pinned-codex" in args
    assert "mktemp" in args[-1] and "trap" in args[-1] and "cat" in args[-1]
    assert "full-access" not in " ".join(args)
    if probe_exit:
        assert "sandbox" in result.stderr.lower()
