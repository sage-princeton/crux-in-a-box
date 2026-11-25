#!/usr/bin/env python3
"""Reconcile the main-account identity owned by a single EC2 workspace."""

import argparse
import json
import re
import subprocess
import sys


class AwsError(RuntimeError):
    """Report an AWS CLI failure by operation and error code."""

    def __init__(self, operation, code):
        """Keep the failed operation and AWS error code for conditional retries."""
        super().__init__(f"{operation} failed ({code})")
        self.code = code


class Aws:
    """Invoke the AWS CLI using one explicit operator profile and region."""

    def __init__(self, profile, region):
        """Bind the client to the supplied profile and region."""
        if not profile or not region:
            raise ValueError("Set an explicit AWS profile and region.")
        self.prefix = ["aws", "--profile", profile, "--region", region, "--no-cli-pager", "--output", "json"]

    def call(self, service, operation, *args):
        """Return decoded JSON for one operation or raise a sanitized AWS error.

        :param service: AWS CLI service name.
        :param operation: AWS CLI operation name.
        :param args: Additional CLI arguments.
        """
        result = subprocess.run([*self.prefix, service, operation, *args], text=True, capture_output=True, timeout=180)
        if result.returncode:
            match = re.search(r"An error occurred \(([^)]+)\)", result.stderr)
            raise AwsError(operation, match[1] if match else f"exit {result.returncode}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def iam(self, operation, *args):
        """Return the result of an IAM operation using this account."""
        return self.call("iam", operation, *args)

    def optional(self, operation, *args):
        """Read an IAM entity or return None for an absent entity."""
        try:
            return self.iam(operation, *args)
        except AwsError as exc:
            if exc.code != "NoSuchEntity":
                raise
            return None


def workspace_role(slug):
    """Return the dedicated role/profile name for a valid workspace slug."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,54}", slug):
        raise ValueError("The slug must fit a 64-character IAM role name and start with a letter or digit.")
    return f"crux-run-{slug}"


def provision_identity(client, role, region):
    """Create or complete the workspace role and its single-role instance profile.

    :param client: Main-account AWS client.
    :param role: Workspace role/profile name.
    :param region: Region containing the system environment parameter.
    """
    account = client.call("sts", "get-caller-identity")["Account"]
    trust = {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
    access = {
        "Effect": "Allow",
        "Action": ["ssm:GetParameter"],
        "Resource": f"arn:aws:ssm:{region}:{account}:parameter/crux/system/env",
    }
    args = ["--role-name", role]
    if not client.optional("get-role", *args):
        client.iam(
            "create-role",
            *args,
            "--assume-role-policy-document",
            json.dumps({"Version": "2012-10-17", "Statement": [trust]}),
        )
    client.iam(
        "put-role-policy",
        *args,
        "--policy-name",
        "read-system-env",
        "--policy-document",
        json.dumps({"Version": "2012-10-17", "Statement": [access]}),
    )
    profile = ["--instance-profile-name", role]
    result = client.optional("get-instance-profile", *profile) or client.iam("create-instance-profile", *profile)
    roles = [item["RoleName"] for item in result["InstanceProfile"]["Roles"]]
    if not roles:
        client.iam("add-role-to-instance-profile", *profile, *args)
    elif roles != [role]:
        raise ValueError("The workspace instance profile belongs to a different role.")


def remove_identity(client, role):
    """Remove an isolated workspace identity after its instance has terminated."""
    if client.optional("get-role", "--role-name", role):
        profile = client.optional("get-instance-profile", "--instance-profile-name", role)
        if profile:
            if profile["InstanceProfile"]["Roles"]:
                client.iam("remove-role-from-instance-profile", "--role-name", role, "--instance-profile-name", role)
            client.iam("delete-instance-profile", "--instance-profile-name", role)
        policies = client.iam("list-role-policies", "--role-name", role)["PolicyNames"]
        for policy in policies:
            client.iam("delete-role-policy", "--role-name", role, "--policy-name", policy)
        client.iam("delete-role", "--role-name", role)


def main():
    """Reconcile or remove the selected workspace identity, returning an exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("provision", "remove"))
    parser.add_argument("profile")
    parser.add_argument("region")
    parser.add_argument("slug")
    args = parser.parse_args()
    try:
        client = Aws(args.profile, args.region)
        role = workspace_role(args.slug)
        if args.action == "provision":
            provision_identity(client, role, args.region)
        else:
            remove_identity(client, role)
    except (ValueError, OSError, AwsError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    sys.exit(main())
