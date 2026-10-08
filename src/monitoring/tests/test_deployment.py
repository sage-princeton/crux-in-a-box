"""Exercise reconciliation twice against a host with existing service files."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("migration_fails", [False, True])
def test_release_migrates_before_starting_services_and_reconciles_existing_host(
    tmp_path, migration_fails
):
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
    for version in ("old", "new"):
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
            template.replace("${env_args[@]}", "${env_args[@]}")
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
        result = subprocess.run(["bash"], input=script, text=True, env=env, capture_output=True)
        if migration_fails:
            assert result.returncode != 0
            import json

            commands = [json.loads(line) for line in log.read_text().splitlines()]
            assert not any(c[:3] == ["docker", "run", "-d"] for c in commands)
            return
        assert result.returncode == 0, result.stderr
    assert "https://new.example" in (service / "Caddyfile").read_text()
    assert "MONITORING_DB_HOST=new-db" in (service / "start").read_text()
    assert "MONITORING_DB_SECRET=new-secret" in (service / "start").read_text()
    assert "MONITORING_REVISION=new" in (service / "start").read_text()
    import json

    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands.count(["docker", "rm", "-f", "crux-incident-proxy"]) == 2
    pulls = [args[-1] for args in commands if args[:2] == ["docker", "pull"]]
    assert pulls == ["fixture@old", "fixture@old-proxy", "fixture@new", "fixture@new-proxy"]
