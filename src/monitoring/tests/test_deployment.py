"""Exercise reconciliation twice against a host with existing service files."""

import os
import subprocess
from pathlib import Path


def test_release_reconciles_existing_proxy_and_complete_boot_configuration(tmp_path):
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
    if args[0] == 'inspect':
        print(os.environ['APP_IMAGE' if args[-1] == 'crux-incidents' else 'PROXY_IMAGE'])
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
            table=version + "-table",
            status_table=version + "-status",
            revision=version,
        )
        script = template.replace("/opt/crux-incidents", str(service)).replace(
            "/etc/systemd/system", str(units)
        )
        for key, value in values.items():
            script = script.replace("${" + key + "}", value)
        env = {
            **os.environ,
            "PATH": str(binaries) + ":" + os.environ["PATH"],
            "COMMAND_LOG": str(log),
            "START_SCRIPT": str(service / "start"),
            "APP_IMAGE": "fixture@" + version,
            "PROXY_IMAGE": "fixture@" + version + "-proxy",
        }
        subprocess.run(["bash"], input=script, text=True, env=env, check=True)
    assert "https://new.example" in (service / "Caddyfile").read_text()
    assert "MONITORING_TABLE=new-table" in (service / "start").read_text()
    assert "STATUS_TABLE=new-status" in (service / "start").read_text()
    assert "MONITORING_REVISION=new" in (service / "start").read_text()
    import json

    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands.count(["docker", "rm", "-f", "crux-incident-proxy"]) == 2
    pulls = [args[-1] for args in commands if args[:2] == ["docker", "pull"]]
    assert pulls == ["fixture@old", "fixture@old-proxy", "fixture@new", "fixture@new-proxy"]


def test_deployment_role_cannot_change_iam_permissions():
    import json

    policy = json.loads(
        (Path(__file__).resolve().parents[1] / "ci/deployment-policy.json").read_text()
    )
    actions = {
        a.lower() for s in policy["Statement"] if s["Effect"] == "Allow" for a in s["Action"]
    }
    assert {a for a in actions if a.startswith("iam:")} <= {
        "iam:getrole",
        "iam:getrolepolicy",
        "iam:listrolepolicies",
        "iam:listattachedrolepolicies",
        "iam:listinstanceprofilesforrole",
        "iam:getinstanceprofile",
        "iam:passrole",
    }
