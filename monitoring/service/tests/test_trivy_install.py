"""Exercise the CI installer without downloading or running a real scanner."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "ci/install-trivy.sh"


@pytest.fixture
def installer(tmp_path):
    archive = tmp_path / "scanner.tar.gz"
    program = b"#!/usr/bin/env bash\nprintf 'fixture scanner\\n'\n"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("trivy")
        info.size, info.mode = len(program), 0o755
        bundle.addfile(info, io.BytesIO(program))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    commands = tmp_path / "commands"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(os.environ['CURL_ARGS']).write_text(json.dumps(args))\n"
        "if int(os.environ['CURL_EXIT']): sys.exit(int(os.environ['CURL_EXIT']))\n"
        "shutil.copyfile(os.environ['FIXTURE_ARCHIVE'], args[args.index('--output') + 1])\n"
    )
    curl.chmod(0o700)
    args_file = tmp_path / "curl-args.json"
    binary_dir = tmp_path / "installed"

    def run(*, download_exit=0, checksum=digest):
        env = {
            **os.environ,
            "PATH": str(commands) + ":" + os.environ["PATH"],
            "CURL_ARGS": str(args_file),
            "CURL_EXIT": str(download_exit),
            "FIXTURE_ARCHIVE": str(archive),
        }
        return subprocess.run(
            ["bash", str(INSTALLER), "0.75.0", checksum, str(binary_dir)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return run, binary_dir, args_file


def test_installs_verified_release_with_bounded_download_retries(installer):
    run, binary_dir, args_file = installer
    result = run()
    assert result.returncode == 0, result.stderr
    assert (binary_dir / "trivy").is_file()
    assert "fixture scanner" in result.stdout
    args = json.loads(args_file.read_text())
    assert "https://github.com/aquasecurity/trivy/releases/download/v0.75.0/trivy_0.75.0_Linux-64bit.tar.gz" in args
    assert "--retry-all-errors" in args
    for option in ("--retry", "--retry-max-time", "--connect-timeout", "--max-time"):
        assert int(args[args.index(option) + 1]) > 0


@pytest.mark.parametrize("download_exit,checksum", [(6, None), (7, None), (0, "0" * 64)])
def test_failed_download_or_checksum_preserves_existing_binary(installer, download_exit, checksum):
    run, binary_dir, _ = installer
    binary_dir.mkdir()
    previous = binary_dir / "trivy"
    previous.write_text("previous scanner")
    result = run(download_exit=download_exit, **({"checksum": checksum} if checksum else {}))
    assert result.returncode == (download_exit or 1)
    assert previous.read_text() == "previous scanner"
