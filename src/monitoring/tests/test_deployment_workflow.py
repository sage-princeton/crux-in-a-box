"""Exercise YAML deployment steps through CLI boundaries, without AWS or Docker."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
STEPS = yaml.safe_load((ROOT / ".github/workflows/monitoring-deploy.yml").read_text())["jobs"][
    "deploy"
]["steps"]


@pytest.fixture
def deployment(tmp_path):
    remote, temporary, binaries = (tmp_path / name for name in ("remote", "temporary", "bin"))
    for directory in (remote, temporary, binaries):
        directory.mkdir()
    config = {
        "name": "crux-monitoring-ae211",
        "account_id": "881004720495",
        "region": "us-east-1",
        "status_provisioned": False,
        "status_registry_file": "stale-status.json",
        "status_secrets_parameter_arn": "stale-parameter",
        "registry_file": "old-local-path",
    }
    (remote / "deployment.json").write_text(json.dumps(config))
    (remote / "registry.json").write_text("{}")
    stub = (Path(__file__).parent / "fixtures/deployment-cli.py").read_text()
    for name in ("aws", "terraform", "docker", "trivy", "curl", "git", "sleep"):
        path = binaries / name
        path.write_text(stub.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        path.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(binaries) + ":" + os.environ["PATH"],
        "RUNNER_TEMP": str(temporary),
        "DEPLOYMENT_FIXTURE": str(remote),
        "GITHUB_SHA": "b" * 40,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "AWS_DEFAULT_REGION": "us-east-1",
        "MONITORING_CONFIG_BUCKET": "fixture",
    }
    return remote, env


def execute(env):
    for step in STEPS:
        if "run" not in step:
            continue
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", step["run"]],
            cwd=ROOT / step.get("working-directory", "."),
            env=env,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            return step["name"], result
    return None, None


def commands(remote):
    return [json.loads(line) for line in (remote / "commands.jsonl").read_text().splitlines()]


def test_yaml_release_applies_complete_plan_and_reuses_verified_images(deployment):
    remote, env = deployment
    for _ in range(2):
        step, error = execute(env)
        assert step is None, (step, error.stderr)
    actions = json.loads((remote / "applied-plan.json").read_text())["resource_changes"]
    assert [r["change"]["actions"] for r in actions] == [
        ["create"],
        ["delete"],
        ["delete", "create"],
    ]
    published = json.loads((remote / "published.json").read_text())
    assert published["revision"] == env["GITHUB_SHA"]
    assert published["registry_file"] == "registry.json"
    assert not any(key.startswith("status_") for key in published)
    assert (
        published["image_digest"]
        == published["web_image_digest"]
        == published["proxy_image_digest"]
    )
    assert json.loads((remote / "web-command.json").read_text())["commands"] == [
        "#!/bin/bash\necho '$literal'\n"
    ]
    calls = commands(remote)
    assert len([c for c in calls if c[:2] == ["docker", "build"]]) == 3
    assert len([c for c in calls if c[:2] == ["docker", "push"]]) == 3
    assert len([c for c in calls if c[:2] == ["trivy", "image"]]) == 12


@pytest.mark.parametrize(
    "mode,failed_step",
    [
        ("account", "Load deployed inputs"),
        ("ecr", "Build and scan immutable images"),
        ("scan", "Build and scan immutable images"),
        ("apply", "Apply infrastructure"),
        ("ssm", "Reconcile web service"),
        ("revision", "Verify HTTPS revision and authentication"),
        ("auth", "Verify HTTPS revision and authentication"),
    ],
)
def test_release_failure_never_publishes_successful_inputs(deployment, mode, failed_step):
    remote, env = deployment
    step, _ = execute({**env, "DEPLOYMENT_FAILURE": mode})
    assert step == failed_step
    assert not (remote / "published.json").exists()
    calls = commands(remote)
    if mode in {"account", "ecr", "scan"}:
        assert not any(c[:2] == ["terraform", "apply"] for c in calls)
    if mode == "ecr":
        assert not any(c[:2] == ["docker", "build"] for c in calls)


def test_release_can_apply_a_plan_without_a_web_instance(deployment):
    remote, env = deployment
    step, error = execute({**env, "DEPLOYMENT_FAILURE": "web-disabled"})
    assert step is None, (step, error.stderr)
    assert (remote / "published.json").exists()
    assert not any(c[:2] == ["aws", "ssm"] or c[0] == "curl" for c in commands(remote))
