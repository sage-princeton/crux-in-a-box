"""Verify deployment effects using the rendered host startup script."""

import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def deploy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    template = (root / "terraform/web-service.sh.tftpl").read_text()
    service = tmp_path / "service"
    units = tmp_path / "units"
    units.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands"
    stub = """#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as f:
    f.write(json.dumps([name, *args]) + '\\n')
if name == 'aws': print('fixture-password')
if name == 'docker':
    if args[0] == 'login': sys.stdin.read()
    if args[0] == 'run' and args[-3:] == ['alembic','upgrade','head'] and os.environ.get('MIGRATION_FAILS') == 'true': sys.exit(1)
    if args[0] == 'inspect':
        print(os.environ['APP_IMAGE' if args[-1] != 'crux-incident-proxy' else 'PROXY_IMAGE'])
if name == 'systemctl' and args[0] == 'restart':
    subprocess.run(['bash', os.environ['START_SCRIPT']], check=True)
"""
    for name in ("aws", "docker", "systemctl"):
        path = binaries / name
        path.write_text(stub)
        path.chmod(0o700)

    def run(version, migration_fails):
        values = dict(
            origin=f"https://{version}.example",
            region="us-east-1",
            repository="fixture",
            image=version,
            proxy_image=version + "-proxy",
            db_host=version + "-db",
            db_secret=version + "-secret",
            bucket="private-bucket",
            document="copy-workspace",
            upload_role="arn:aws:iam::123456789012:role/workspace-upload",
            revision=version,
        )
        script = (
            template.replace("$${", "${")
            .replace("/opt/crux-incidents", str(service))
            .replace("/etc/systemd/system", str(units))
        )
        for key, value in values.items():
            script = script.replace("${" + key + "}", value)
        env = {
            **os.environ,
            "PATH": str(binaries) + ":" + os.environ["PATH"],
            "COMMAND_LOG": str(log),
            "START_SCRIPT": str(service / "start"),
            "MIGRATION_FAILS": str(migration_fails).lower(),
            "APP_IMAGE": "fixture@" + version,
            "PROXY_IMAGE": "fixture@" + version + "-proxy",
        }
        return subprocess.run(["bash"], input=script, text=True, env=env, capture_output=True)

    return run, service, log


def test_release_reconciles_existing_host_and_migrates_before_starting_services(deploy):
    run, service, log = deploy
    previous = run("old", False)
    assert previous.returncode == 0, previous.stderr
    log.write_text("")
    release = run("new", False)
    assert release.returncode == 0, release.stderr
    assert "https://new.example" in (service / "Caddyfile").read_text()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    containers = [args for args in commands if args[:2] == ["docker", "run"]]
    assert containers[0][-4:] == ["fixture@new", "alembic", "upgrade", "head"]
    assert {args[args.index("--name") + 1] for args in containers[1:]} == {
        "crux-incidents",
        "crux-monitor-worker",
        "crux-incident-proxy",
    }
    for args in containers[:-1]:
        assert "fixture@new" in args
        assert "MONITORING_DB_HOST=new-db" in args
        assert "MONITORING_DB_SECRET=new-secret" in args
        assert "MONITORING_REVISION=new" in args
    assert containers[-1][-1] == "fixture@new-proxy"


def test_failed_migration_prevents_service_startup(deploy):
    run, _, log = deploy
    release = run("new", True)
    assert release.returncode != 0
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(args[:3] == ["docker", "run", "-d"] for args in commands)
