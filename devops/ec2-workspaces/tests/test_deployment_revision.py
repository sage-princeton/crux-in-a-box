"""Exercise the deployment CLI with real Git archives and local command boundaries."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMAND = r'''#!/usr/bin/env python3
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

root = Path(os.environ["DEPLOY_FIXTURE"])
name, args = Path(sys.argv[0]).name, sys.argv[1:]
data = sys.stdin.buffer.read() if name == "ssh" else b""
with (root / "commands.jsonl").open("a") as log:
    log.write(json.dumps([name, args]) + "\n")
box = root / "box"
if name == "aws":
    service, operation = args[2:4] if args[0] == "--region" else args[:2]
    if os.environ.get("ADVANCE_HEAD") == "1" and not (root / "advanced").exists():
        (root / "advanced").touch()
        (root / "drop-in/workspace/revision.txt").write_text("new")
        (root / "drop-in/scaffold.toml").write_text("invalid new configuration")
        (root / "scaffolds/sdk/revision.txt").write_text("new")
        (root / "devops/ec2-workspaces/install-run.sh").write_text("new install script")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "test: advance deployment fixture"], check=True)
    values = {
        ("sts", "get-caller-identity"): "123456789012",
        ("ssm", "get-parameters"): "ami-fixture",
        ("ssm", "get-parameter"): "{}",
        ("iam", "get-instance-profile"): "{}",
        ("ec2", "describe-key-pairs"): "{}",
        ("ec2", "describe-vpcs"): "vpc-fixture",
        ("ec2", "describe-subnets"): "subnet-fixture",
        ("ec2", "describe-security-groups"): "sg-fixture",
        ("ec2", "run-instances"): "i-fixture",
        ("ec2", "create-tags"): "",
        ("ec2", "delete-tags"): "",
        ("ec2", "wait"): "",
    }
    if (service, operation) == ("ec2", "describe-instances"):
        query = args[args.index("--query") + 1]
        value = ("running" if "State.Name" in query else
                 "203.0.113.1" if "PublicIpAddress" in query else
                 "10.0.0.1" if "PrivateIpAddress" in query else
                 "i-fixture" if os.environ.get("EXISTING") == "1" else "None")
    else:
        value = values[(service, operation)]
    print(value)
elif name == "scp":
    paths = [arg for arg in args if arg != "-q"]
    destination = paths[-1].split(":", 1)[1]
    for source in paths[:-1]:
        target = box / destination.lstrip("/")
        if destination.endswith("/"):
            target /= Path(source).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
elif name == "ssh":
    command = args[-1]
    if "tar -x" in command:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(box, filter="data")
    if command.endswith("/tmp/configure-run.sh") and os.environ.get("FAIL_CONFIGURE") == "1":
        sys.exit(1)
elif name == "curl":
    print("203.0.113.1")
elif name != "ssh-keygen":
    raise AssertionError("Unexpected command: " + name)
'''


@pytest.fixture
def deployment(tmp_path):
    scripts = tmp_path / "devops/ec2-workspaces"
    scripts.mkdir(parents=True)
    for name in ("provision-workspace-aws-resources.sh", "agent-config.sh", "sdk_config.py", "run_provision.py"):
        shutil.copyfile(ROOT / "devops/ec2-workspaces" / name, scripts / name)
    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    (ssh_dir / "fixture.pem").touch()
    script = scripts / "provision-workspace-aws-resources.sh"
    script.write_text(script.read_text().replace("$HOME/.ssh", str(ssh_dir)))
    (scripts / "scaffolds").mkdir()
    (scripts / "scaffolds/sdk.sh").write_text("committed SDK setup")
    (scripts / "install-run.sh").write_text("committed install script")
    (scripts / "configure-run.sh").write_text("committed configuration script")
    (tmp_path / "monitoring/tracing").mkdir(parents=True)
    (tmp_path / "monitoring/tracing/marker").touch()
    for directory in ("drop-in/workspace", "scaffolds/sdk"):
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "revision.txt").write_text("committed")
    (tmp_path / "drop-in/scaffold.toml").write_text('[runtime]\ntype = "openai-agents"\n')
    for args in (("init", "-q"), ("config", "user.name", "Test"),
                 ("config", "user.email", "test@example.test"), ("add", "."),
                 ("commit", "-qm", "test: create deployment fixture")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    sha = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    config = tmp_path / "config.txt"
    config.write_text("""AWS_REGION=us-east-1
RUN_SLUG=fixture
OPERATOR_CIDR=203.0.113.1/32
INSTANCE_TYPE=t3.small
ROOT_DISK_GB=20
KEY_NAME=fixture
USE_ELASTIC_IP=false
AGENT_PLATFORM=openai-agents
OPENAI_AGENTS_MODEL=gpt-6-luna
OPENAI_AGENTS_REASONING_EFFORT=low
DROP_IN_PATH=drop-in
""")
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"OPENAI_API_KEY": "fixture-provider"}))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("aws", "ssh", "scp", "ssh-keygen", "curl"):
        path = binaries / name
        path.write_text(COMMAND)
        path.chmod(0o755)

    def run(*args, **settings):
        result = subprocess.run(
            ["bash", str(script), "--secrets", str(secrets), *args, str(config)],
            env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                 "DEPLOY_FIXTURE": str(tmp_path), **settings},
            stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30, check=False,
        )
        log = tmp_path / "commands.jsonl"
        commands = [json.loads(row) for row in log.read_text().splitlines()] if log.exists() else []
        return result, commands

    return tmp_path, sha, run


@pytest.mark.parametrize("existing", ["0", "1"])
def test_success_records_the_commit_actually_staged_even_if_head_moves(deployment, existing):
    root, sha, run = deployment
    (root / "devops/ec2-workspaces/configure-run.sh").write_text("uncommitted configuration script")
    result, commands = run(EXISTING=existing, ADVANCE_HEAD="1")
    assert result.returncode == 0, result.stderr
    assert (root / "box/run-harness/workspace/revision.txt").read_text() == "committed"
    assert (root / "box/crux-sdk-scaffold/revision.txt").read_text() == "committed"
    assert (root / "box/tmp/install-run.sh").read_text() == "committed install script"
    assert (root / "box/tmp/configure-run.sh").read_text() == "committed configuration script"
    assert commands[-1] == ["aws", ["--region", "us-east-1", "ec2", "create-tags", "--resources",
                                      "i-fixture", "--tags", f"Key=CruxGitSha,Value={sha}"]]
    assert sha in result.stdout


def test_failed_setup_clears_the_old_revision_without_publishing_a_new_one(deployment):
    _, _, run = deployment
    result, commands = run(EXISTING="1", FAIL_CONFIGURE="1")
    assert result.returncode != 0
    assert any(args[2:4] == ["ec2", "delete-tags"] and "Key=CruxGitSha" in args
               for name, args in commands if name == "aws")
    assert not any("Key=CruxGitSha,Value=" in arg for name, args in commands for arg in args)


def test_dry_run_reports_the_revision_without_changing_resources(deployment):
    _, sha, run = deployment
    result, commands = run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert sha in result.stdout and "CruxGitSha" in result.stdout
    assert all(name == "aws" and args[2:4] == ["sts", "get-caller-identity"] for name, args in commands)


def test_uncommitted_provisioner_is_rejected_before_resource_calls(deployment):
    root, _, run = deployment
    script = root / "devops/ec2-workspaces/provision-workspace-aws-resources.sh"
    script.write_text(script.read_text() + "\n# uncommitted change\n")
    result, commands = run()
    assert result.returncode != 0 and "Commit changes" in result.stderr
    assert commands == []
