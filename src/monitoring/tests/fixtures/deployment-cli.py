#!/usr/bin/env python3
"""Local command boundary for executing the real deployment workflow in tests."""

import json
import os
import shutil
import sys
from pathlib import Path

root = Path(os.environ["DEPLOYMENT_FIXTURE"])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get("DEPLOYMENT_FAILURE", "")
with (root / "commands.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")


def value(flag):
    return args[args.index(flag) + 1]


def first(key):
    marker = root / key
    existed = marker.exists()
    marker.touch()
    return not existed


def failure(message):
    print(message, file=sys.stderr)
    sys.exit(1)


if name == "git":
    print(os.environ["GITHUB_SHA"])
elif name == "sleep":
    pass
elif name == "docker":
    pass
elif name == "trivy":
    if mode == "scan":
        failure("Vulnerability gate failed")
    if "--output" in args:
        Path(value("--output")).write_text("{}")
elif name == "aws":
    operation = args[:2]
    if operation == ["sts", "get-caller-identity"]:
        print("000000000000" if mode == "account" else "881004720495")
    elif operation == ["s3", "cp"]:
        source, destination = args[2:4]
        if source.startswith("s3://"):
            shutil.copyfile(root / source.rsplit("/", 1)[1], destination)
        else:
            shutil.copyfile(source, root / "published.json")
    elif operation == ["ecr", "describe-images"]:
        if mode == "ecr":
            failure("An error occurred (AccessDeniedException)")
        kind = value("--image-ids").split("=", 1)[1].split("-", 1)[0]
        if first("image-" + kind):
            failure("An error occurred (ImageNotFoundException)")
        print(json.dumps({"imageDetails": [{"imageDigest": "sha256:" + "a" * 64}]}))
    elif operation == ["ssm", "send-command"]:
        if first("ssm-send"):
            failure("An error occurred (InvalidInstanceId)")
        command = json.loads(Path(value("--parameters").removeprefix("file://")).read_text())
        (root / "web-command.json").write_text(json.dumps(command))
        print("fixture-command-id")
    elif operation == ["ssm", "get-command-invocation"]:
        if first("ssm-status"):
            failure("An error occurred (InvocationDoesNotExist)")
        print("Failed" if mode == "ssm" else "Success")
    else:
        failure("Unexpected AWS invocation")
elif name == "terraform":
    if args[0] == "plan":
        plan_path = next(arg.split("=", 1)[1] for arg in args if arg.startswith("-out="))
        Path(plan_path).write_text(
            json.dumps(
                {
                    "resource_changes": [
                        {"address": "aws_dynamodb_table.new", "change": {"actions": ["create"]}},
                        {"address": "aws_iam_role.old", "change": {"actions": ["delete"]}},
                        {
                            "address": "aws_instance.web",
                            "change": {"actions": ["delete", "create"]},
                        },
                    ]
                }
            )
        )
    elif args[0] == "apply":
        if mode == "apply":
            failure("Apply failed")
        shutil.copyfile(args[-1], root / "applied-plan.json")
    elif args[0] == "output":
        print(
            json.dumps(
                {
                    "incident_web_instance": {"value": "" if mode == "web-disabled" else "i-test"},
                    "incident_web_url": {
                        "value": "" if mode == "web-disabled" else "https://fixture"
                    },
                    "incident_web_configuration": {"value": "#!/bin/bash\necho '$literal'\n"},
                }
            )
        )
    elif args[0] != "init":
        failure("Unexpected Terraform invocation")
elif name == "curl":
    if args[-1].endswith("/healthz"):
        stale = first("health") or mode == "revision"
        print(
            json.dumps({"status": "ok", "revision": "old" if stale else os.environ["GITHUB_SHA"]})
        )
    else:
        Path(value("--dump-header")).write_bytes(b"HTTP/2 302\r\nLocation: /auth/login\r\n")
        print("200" if mode == "auth" else "302", end="")
else:
    failure("Unexpected command")
