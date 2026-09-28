"""Real OpenSSH boundary test; requires Docker, never touches an EC2 target."""

import io
import json
import subprocess
import time
from pathlib import Path

import paramiko
import pytest

from review import CoverageError, collect_sftp


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


@pytest.fixture(scope="module")
def server():
    root = Path(__file__).resolve().parents[1]
    # A separate build context includes only the fixture, never operator configs.
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory() as context:
        shutil.copytree(root / "tests", Path(context) / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        docker("build", "-q", "-t", "crux-inspection-fixture", "-f", str(Path(context) / "tests/sshd.Dockerfile"), context)
    container = docker("run", "-d", "--rm", "-p", "127.0.0.1::22", "crux-inspection-fixture")
    try:
        info = json.loads(docker("inspect", container))[0]
        port = int(info["NetworkSettings"]["Ports"]["22/tcp"][0]["HostPort"])
        for _ in range(30):
            ready = subprocess.run(["docker", "exec", container, "test", "-s", "/etc/ssh/authorized_keys/crux-inspect"], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        private = docker("exec", container, "cat", "/tmp/inspection-key") + "\n"
        host = " ".join(docker("exec", container, "cat", "/etc/ssh/ssh_host_ed25519_key.pub").split()[:2])
        yield {"boundary_verified": True, "host_key": host, "port": port,
               "paths": ["/exports/activity.txt"]}, private
    finally:
        docker("stop", container)


def test_real_sftp_read_succeeds_and_symlinks_are_rejected(server):
    config, private = server
    evidence = collect_sftp(config, "127.0.0.1", private)
    assert evidence[0]["data"] == "fixture evidence\n"
    with pytest.raises(CoverageError):
        collect_sftp({**config, "paths": ["/exports/escape"]}, "127.0.0.1", private)
    wrong_host = paramiko.RSAKey.generate(2048)
    with pytest.raises(paramiko.SSHException):
        collect_sftp({**config, "host_key": "ssh-rsa " + wrong_host.get_base64()}, "127.0.0.1", private)


def test_server_refuses_mutations_shell_and_forwarding(server):
    config, private = server
    client = paramiko.SSHClient()
    # This test attacks server permissions; the production collector tests pinning.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=config["port"], username="crux-inspect",
                   pkey=paramiko.Ed25519Key.from_private_key(io.StringIO(private)),
                   look_for_keys=False, allow_agent=False)
    try:
        with client.open_sftp() as sftp:
            for operation in [lambda: sftp.open("/exports/new", "w"),
                              lambda: sftp.remove("/exports/activity.txt"),
                              lambda: sftp.mkdir("/exports/newdir"),
                              lambda: sftp.rename("/exports/activity.txt", "/exports/renamed"),
                              lambda: sftp.chmod("/exports/activity.txt", 0o777)]:
                with pytest.raises(OSError):
                    operation()
            with pytest.raises(OSError):
                sftp.open("/etc/passwd")
        transport = client.get_transport()
        with pytest.raises(paramiko.SSHException):
            transport.open_channel("direct-tcpip", ("127.0.0.1", 22), ("127.0.0.1", 12345))
        _, stdout, stderr = client.exec_command("touch /exports/shell-executed", timeout=5)
        assert stdout.channel.recv_exit_status() != 0
        with client.open_sftp() as sftp:
            with pytest.raises(OSError):
                sftp.stat("/exports/shell-executed")
    finally:
        client.close()
