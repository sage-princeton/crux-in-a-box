import copy
import importlib.util
import json
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

spec = importlib.util.spec_from_file_location(
    "pr_plan", Path(__file__).resolve().parents[1] / "ci/pr_plan.py"
)
pr_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_plan)
SHA = "a" * 40
REPO = "owner/repo"
URL = "https://github.com/owner/repo/actions/runs/123"
PR = {
    "state": "open",
    "base": {"ref": "main"},
    "head": {"sha": SHA, "repo": {"full_name": REPO}},
    "user": {"login": "member"},
}


def test_resource_summary_preserves_actions_and_drops_private_values():
    resources = []
    for address, actions in [
        ("new", ["create"]),
        ("updated", ["update"]),
        ("gone", ["delete"]),
        ("replace1", ["delete", "create"]),
        ("replace2", ["create", "delete"]),
        ("unchanged", ["no-op"]),
    ]:
        resources.append(
            {
                "address": "aws_instance." + address,
                "mode": "managed",
                "change": {
                    "actions": actions,
                    "before": {"secret": "PRIVATE_VALUE"},
                    "after": {"token": "PRIVATE_VALUE"},
                },
            }
        )
    resources.append(
        {"address": "data.aws_ami.latest", "mode": "data", "change": {"actions": ["read"]}}
    )
    result = pr_plan.resource_changes(
        {"resource_changes": resources, "outputs": {"secret": "PRIVATE_VALUE"}}
    )
    assert len(result) == 5
    assert [r["action"] for r in result].count("replace") == 2
    assert "PRIVATE_VALUE" not in json.dumps(result)
    assert pr_plan.resource_changes({"resource_changes": []}) == []
    with pytest.raises(ValueError):
        pr_plan.resource_changes(
            {
                "resource_changes": [
                    {"address": "x", "mode": "managed", "change": {"actions": ["future"]}}
                ]
            }
        )


def test_imports_and_moves_are_visible_even_without_attribute_changes():
    resources = [
        {
            "address": "aws_instance.imported",
            "mode": "managed",
            "change": {"actions": ["no-op"], "importing": {"id": "PRIVATE"}},
        },
        {
            "address": "aws_instance.moved",
            "mode": "managed",
            "previous_address": "aws_instance.old",
            "change": {"actions": ["no-op"]},
        },
    ]
    assert [r["action"] for r in pr_plan.resource_changes({"resource_changes": resources})] == [
        "import",
        "move",
    ]


class GitHub:
    def __init__(self):
        self.pr = copy.deepcopy(PR)
        self.comments = []
        self.writes = []

    def __call__(self, path, method="GET", body=None):
        if method == "GET":
            return self.pr if "/pulls/" in path else self.comments
        self.writes.append((path, method, body))
        if "/issues/" in path and method == "POST":
            self.comments.append({"id": 42, "user": {"login": "github-actions[bot]"}, **body})
        if method == "PATCH":
            self.comments[0].update(body)


def test_comments_are_upserted_and_clear_stale_changes_on_noop_or_failure():
    api = GitHub()
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    noop = {"status": "success", "changes": []}
    pr_plan.publish(noop, 1, SHA, REPO, URL, api)
    assert not api.comments
    changed = {
        "status": "success",
        "changes": [{"address": "aws_s3_bucket.evidence", "action": "update"}],
    }
    pr_plan.publish(changed, 1, SHA, REPO, URL, api)
    pr_plan.publish(changed, 1, SHA, REPO, URL, api)
    assert len(api.comments) == 1
    assert "aws_s3_bucket.evidence" in api.comments[0]["body"]
    assert summary.read_text() == api.comments[0]["body"]
    pr_plan.publish(noop, 1, SHA, REPO, URL, api)
    assert "No resource changes" in api.comments[0]["body"]
    assert summary.read_text() == api.comments[0]["body"]
    pr_plan.publish({"status": "error", "changes": []}, 1, SHA, REPO, URL, api)
    assert "Plan failed" in api.comments[0]["body"]
    assert summary.read_text() == api.comments[0]["body"]
    assert api.writes[-1][2]["state"] == "error"


def test_stale_results_and_forks_cannot_publish_or_be_eligible():
    api = GitHub()
    api.pr["head"]["sha"] = "b" * 40
    pr_plan.publish({"status": "success", "changes": []}, 1, SHA, REPO, URL, api)
    assert not api.writes
    api.pr["head"]["repo"]["full_name"] = "fork/repo"
    assert not pr_plan.eligible(api.pr, REPO)
    assert pr_plan.context(1, REPO, URL, api)["eligible"] == "false"
    api.pr = copy.deepcopy(PR)
    api.pr["user"]["login"] = "dependabot[bot]"
    assert not pr_plan.eligible(api.pr, REPO)


def test_report_is_bounded_and_rendered_as_literal_resource_names(tmp_path):
    changes = [{"address": f"aws_instance.node[{n}]", "action": "create"} for n in range(101)]
    changes[0]["address"] = 'aws_instance.node["<script>|bad\n"]'
    body = pr_plan.render({"status": "success", "changes": changes}, SHA, URL)
    assert "<script>" not in body and "&#124;" in body
    assert "1 more resources" in body
    long_changes = [{"address": "<" * 1000, "action": "create"} for _ in range(100)]
    assert len(pr_plan.render({"status": "success", "changes": long_changes}, SHA, URL)) < 60000
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"status": "success", "changes": changes}))
    assert pr_plan.read_report(path, "failure")["status"] == "error"
    assert pr_plan.read_report(tmp_path / "missing.json", "success")["status"] == "error"
    path.write_text(
        json.dumps({"status": "success", "changes": [{"action": "shell", "address": "x"}]})
    )
    with pytest.raises(ValueError):
        pr_plan.read_report(path, "success")


@pytest.mark.parametrize("exit_code,expected", [(2, 0), (1, 1)])
def test_real_plan_wrapper_handles_terraform_exit_codes_without_leaking_values(
    tmp_path, monkeypatch, capsys, exit_code, expected
):
    stub = tmp_path / "terraform"
    stub.write_text(
        """#!/usr/bin/env python3
import json, pathlib, sys
command = sys.argv[1]
if command == 'plan':
    plan = next(x[5:] for x in sys.argv if x.startswith('-out='))
    pathlib.Path(plan).write_text('PRIVATE_BINARY_STATE')
    print('PRIVATE_DIAGNOSTIC_VALUE')
    sys.exit(EXIT_CODE)
if command == 'show':
    print(json.dumps({'resource_changes':[{'address':'aws_s3_bucket.evidence','mode':'managed','change':{'actions':['update'],'after':{'secret':'PRIVATE_SECRET'}}}]}))
""".replace("EXIT_CODE", str(exit_code))
    )
    stub.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + __import__("os").environ["PATH"])
    report = tmp_path / "result/report.json"
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="plan-config")
        s3.put_object(
            Bucket="plan-config",
            Key="config/deployment.json",
            Body=json.dumps(
                {"account_id": "123456789012", "revision": SHA, "image_digest": "deployed-image"}
            ),
        )
        s3.put_object(Bucket="plan-config", Key="config/registry.json", Body="{}")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        assert pr_plan.plan(tmp_path, "plan-config", report) == expected
    data = json.loads(report.read_text())
    assert data["status"] == ("success" if expected == 0 else "error")
    assert "PRIVATE" not in report.read_text() + capsys.readouterr().out


def test_plan_roles_have_no_apply_or_secret_read_permissions():
    directory = Path(__file__).resolve().parents[1] / "ci"
    policy = json.loads((directory / "plan-policy.json").read_text())
    actions = {a for s in policy["Statement"] for a in s["Action"]}
    assert all(
        a == "sts:AssumeRole" or a.split(":")[1].startswith(("Get", "List", "Describe"))
        for a in actions
    )
    assert not actions & {
        "ssm:GetParameter",
        "ssm:GetParameters",
        "secretsmanager:GetSecretValue",
        "kms:Decrypt",
        "iam:PassRole",
    }
    state = json.loads((directory / "plan-state-policy.json").read_text())
    assert {a for s in state["Statement"] for a in s["Action"]} == {
        "s3:ListBucket",
        "s3:GetObject",
        "kms:Decrypt",
    }
