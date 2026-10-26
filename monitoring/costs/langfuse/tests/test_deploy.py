import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


DEPLOY = Path(__file__).resolve().parents[1] / "deploy.sh"
FAKE_AWS = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import stat
import sys
from zipfile import ZipFile

arguments = sys.argv[1:]
assert arguments[:4] == ["--profile", "test-profile", "--region", "us-west-2"]
args = arguments[4:]
log = Path(os.environ["DEPLOY_TEST_LOG"])
previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(arguments) + "\n")
mode = os.environ["DEPLOY_TEST_MODE"]
existing = mode == "update"
operation = tuple(args[:2])
def value(flag):
    return args[args.index(flag) + 1]
def fail(code, message="test failure"):
    print(f"An error occurred ({code}): {message}", file=sys.stderr)
    sys.exit(255)

if operation == ("sts", "get-caller-identity"):
    if mode == "unauthenticated":
        fail("ExpiredToken")
    print("123456789012")
elif operation == ("iam", "get-role"):
    if mode == "iam_denied":
        fail("AccessDenied")
    if not existing:
        fail("NoSuchEntity")
    print("arn:aws:iam::123456789012:role/test-cost-role")
elif operation == ("iam", "create-role"):
    assert value("--role-name") == "test-cost-role"
    print("arn:aws:iam::123456789012:role/test-cost-role")
elif operation in (("iam", "wait"), ("iam", "attach-role-policy"), ("lambda", "wait")):
    pass
elif operation == ("lambda", "get-function"):
    if not existing:
        fail("ResourceNotFoundException")
    print("arn:aws:lambda:us-west-2:123456789012:function:test-cost")
elif operation in (("lambda", "create-function"), ("lambda", "update-function-configuration")):
    config_path = Path(value("--environment").removeprefix("file://"))
    assert str(config_path).startswith("/tmp/langfuse-cost-tracker.")
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    environment = json.loads(config_path.read_text())["Variables"]
    assert environment == {
        "LANGFUSE_PUBLIC_KEY": "test-public",
        "LANGFUSE_SECRET_KEY": "test-secret",
        "COST_TRACKER_TOKEN": "test-bearer",
        "LANGFUSE_BASE_URL": "https://us.cloud.langfuse.com",
    }
    Path(os.environ["DEPLOY_TEST_CONFIG"]).write_text(str(config_path))
    assert value("--runtime") == "python3.12"
    assert value("--handler") == "lambda_function.lambda_handler"
    if operation[1] == "create-function":
        with ZipFile(value("--zip-file").removeprefix("fileb://")) as package:
            assert package.namelist() == ["lambda_function.py"]
    print("arn:aws:lambda:us-west-2:123456789012:function:test-cost")
elif operation == ("lambda", "update-function-code"):
    print("arn:aws:lambda:us-west-2:123456789012:function:test-cost")
elif operation == ("lambda", "add-permission"):
    assert value("--source-arn") == "arn:aws:execute-api:us-west-2:123456789012:api123/*/POST/cost"
    if mode == "permission_denied":
        fail("AccessDeniedException")
    if existing and not any(row[4:6] == ["lambda", "add-permission"] for row in previous):
        fail("ResourceConflictException")
elif operation == ("lambda", "remove-permission"):
    assert existing
elif args[0] == "apigatewayv2":
    operations = {
        "get-apis": "api123" if existing else "None",
        "get-integrations": "integration123" if existing else "None",
        "get-routes": "route123" if existing else "None",
        "get-stages": "$default" if existing else "None",
        "create-api": "api123", "create-integration": "integration123",
        "create-route": "route123", "create-stage": "$default",
        "update-integration": "integration123", "update-route": "route123", "update-stage": "$default",
        "get-api": "https://api123.execute-api.us-west-2.amazonaws.com",
    }
    if args[1] in ("create-route", "update-route"):
        assert value("--target") == "integrations/integration123"
        assert value("--authorization-type") == "NONE"
    print(operations[args[1]])
else:
    raise AssertionError(f"Unexpected AWS operation: {args}")
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.script = self.directory / "deploy.sh"
        shutil.copyfile(DEPLOY, self.script)
        (self.directory / "lambda_function.py").write_text("def lambda_handler(event, context): pass\n")
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        (self.bin / "aws").write_text(FAKE_AWS)
        (self.bin / "aws").chmod(0o755)
        self.log = self.directory / "aws.jsonl"
        self.config = self.directory / "config-path"
        self.environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "AWS_PROFILE": "",
            "LANGFUSE_PUBLIC_KEY": "test-public",
            "LANGFUSE_SECRET_KEY": "test-secret",
            "COST_TRACKER_TOKEN": "test-bearer",
            "LANGFUSE_BASE_URL": "",
            "DEPLOY_TEST_LOG": str(self.log),
            "DEPLOY_TEST_CONFIG": str(self.config),
        }

    def deploy(self, mode="create", profile=True):
        arguments = ["bash", str(self.script), "--region", "us-west-2", "--function-name", "test-cost"]
        if profile:
            arguments += ["--profile", "test-profile"]
        result = subprocess.run(arguments, env={**self.environment, "DEPLOY_TEST_MODE": mode},
                                text=True, capture_output=True, timeout=20)
        output = result.stdout + result.stderr + (self.log.read_text() if self.log.exists() else "")
        for secret in ("test-public", "test-secret", "test-bearer"):
            self.assertNotIn(secret, output)
        if self.config.exists():
            self.assertFalse(Path(self.config.read_text()).parent.exists(), "Temporary credentials were not removed")
        return result

    def operations(self):
        return [tuple(json.loads(row)[4:6]) for row in self.log.read_text().splitlines()]

    def test_creates_resources_packages_handler_and_removes_private_credentials(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("COST_TRACKER_URL=https://api123.execute-api.us-west-2.amazonaws.com/cost", result.stdout)
        self.assertEqual(self.operations()[0], ("sts", "get-caller-identity"))
        self.assertIn(("lambda", "create-function"), self.operations())
        self.assertNotIn(("lambda", "update-function-code"), self.operations())

    def test_updates_existing_resources_and_replaces_only_conflicting_permission(self):
        result = self.deploy("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = self.operations()
        self.assertIn(("lambda", "update-function-code"), operations)
        self.assertIn(("lambda", "update-function-configuration"), operations)
        self.assertIn(("apigatewayv2", "update-route"), operations)
        self.assertIn(("apigatewayv2", "update-stage"), operations)
        self.assertEqual(operations.count(("lambda", "add-permission")), 2)
        self.assertEqual(operations.count(("lambda", "remove-permission")), 1)
        self.assertNotIn(("lambda", "create-function"), operations)

    def test_requires_explicit_profile_before_any_aws_call(self):
        result = self.deploy(profile=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--profile or AWS_PROFILE", result.stderr)
        self.assertFalse(self.log.exists())

    def test_accepts_explicit_environment_profile(self):
        self.environment["AWS_PROFILE"] = "test-profile"
        self.assertEqual(self.deploy(profile=False).returncode, 0)

    def test_authentication_failure_stops_without_resource_requests(self):
        result = self.deploy("unauthenticated")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("aws configure sso --use-device-code", result.stderr)
        self.assertEqual(self.operations(), [("sts", "get-caller-identity")])

    def test_permission_denied_is_not_treated_as_missing_role(self):
        result = self.deploy("iam_denied")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDenied", result.stderr)
        self.assertNotIn(("iam", "create-role"), self.operations())

    def test_add_permission_failure_is_not_treated_as_conflict(self):
        result = self.deploy("permission_denied")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDeniedException", result.stderr)
        self.assertNotIn(("lambda", "remove-permission"), self.operations())


if __name__ == "__main__":
    unittest.main()
