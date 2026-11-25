#!/usr/bin/env python3
"""Grant a provisioned workspace access to an existing, dedicated auxiliary AWS account."""

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path

from run_environment import role_settings, write_settings
from aux_instance_access import AccessError, load_config, reconcile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "devops/ec2-workspaces"))
from workspace_identity import Aws, AwsError, workspace_role  # noqa: E402

POLICIES = Path(__file__).with_name("policies")
MANAGED = {
    "POSTGRES": ["AmazonRDSFullAccess"],
    "S3": ["AmazonS3FullAccess"],
    "EC2": ["AmazonEC2FullAccess"],
    "DNS": ["AmazonRoute53FullAccess", "AmazonRoute53DomainsFullAccess"],
    "CLOUDFRONT": ["CloudFrontFullAccess"],
    "ACM": ["AWSCertificateManagerFullAccess"],
}
AUX_ROLE = "crux-agent-devops"


class AuxiliaryAccount:
    """Reconcile both sides of the role handoff for a single provisioned workspace."""

    def __init__(self, config):
        """Validate settings and resolve explicit account identities before making changes."""
        self.config = config
        self.slug = config["RUN_SLUG"]
        self.role = workspace_role(self.slug)
        region = config["AWS_REGION"]
        self.main = Aws(config["AWS_PROFILE"], region)
        self.aux = Aws(config["AUX_RESOURCE_PROFILE"], region)
        self.enabled = {key for key in MANAGED if config.get("PROVISION_" + key) == "1"}
        if not self.enabled:
            raise ValueError("Select at least one PROVISION_* service in the workspace config.")
        self.main_id, self.aux_id = (
            client.call("sts", "get-caller-identity")["Account"] for client in (self.main, self.aux)
        )
        if self.aux_id == self.main_id or (config.get("AUX_RESOURCE_ACCOUNT_ID") or self.aux_id) != self.aux_id:
            raise ValueError("The auxiliary profile must match the dedicated account recorded for this workspace.")
        self.settings = role_settings(self.aux_id)
        self.role_arn = self.settings["AUX_RESOURCE_ROLE_ARN"]
        self.boundary = f"arn:aws:iam::{self.aux_id}:policy/crux-app-boundary"

    def workspace_ip(self):
        """Return the running workspace's public IP after verifying its dedicated instance profile."""
        result = self.main.call(
            "ec2",
            "describe-instances",
            "--filters",
            "Name=tag:Name,Values=" + self.slug,
            "Name=instance-state-name,Values=running",
        )
        boxes = [box for reservation in result["Reservations"] for box in reservation["Instances"]]
        expected = f"arn:aws:iam::{self.main_id}:instance-profile/{self.role}"
        if len(boxes) != 1 or boxes[0].get("IamInstanceProfile", {}).get("Arn") != expected:
            raise ValueError("Provision one running workspace with ISOLATED_RUN_IDENTITY=1 first.")
        roles = self.main.iam("get-instance-profile", "--instance-profile-name", self.role)["InstanceProfile"]["Roles"]
        if [role["RoleName"] for role in roles] != [self.role]:
            raise ValueError("Unexpected role in the workspace instance profile.")
        return str(ipaddress.IPv4Address(boxes[0]["PublicIpAddress"]))

    def policy(self, name):
        """Render an existing policy template for the verified main and auxiliary accounts."""
        values = {
            **self.settings,
            "AUX_ACCOUNT_ID": self.aux_id,
            "AUX_ROLE_ARN": self.role_arn,
            "RUN_ROLE_ARN": f"arn:aws:iam::{self.main_id}:role/{self.role}",
            "BOUNDARY_POLICY_ARN": self.boundary,
        }
        template = (POLICIES / (name + ".json")).read_text()
        return re.sub(r"\{\{(\w+)\}\}", lambda match: values[match[1]], template)

    def reconcile_boundary(self):
        """Reuse the active application-role boundary or install one updated policy version."""
        args = ["--policy-arn", self.boundary]
        desired = self.policy("crux-app-boundary")
        policy = self.aux.optional("get-policy", *args)
        if policy is None:
            self.aux.iam("create-policy", "--policy-name", "crux-app-boundary", "--policy-document", desired)
            return
        current = self.aux.iam("get-policy-version", *args, "--version-id", policy["Policy"]["DefaultVersionId"])
        if current["PolicyVersion"]["Document"] == json.loads(desired):
            return
        versions = self.aux.iam("list-policy-versions", *args)["Versions"]
        if len(versions) >= 5:
            oldest = min((v for v in versions if not v["IsDefaultVersion"]), key=lambda v: v["CreateDate"])
            self.aux.iam("delete-policy-version", *args, "--version-id", oldest["VersionId"])
        self.aux.iam("create-policy-version", *args, "--policy-document", desired, "--set-as-default")

    def reconcile_permissions(self):
        """Converge inline grants and managed service policies to the selected resource flags."""
        args = ["--role-name", AUX_ROLE]
        desired = {"read-cost-explorer": "aux-read-cost-explorer"}
        if "EC2" in self.enabled:
            desired["instance-access"] = "aux-instance-access"
            if "S3" in self.enabled:
                self.reconcile_boundary()
                desired["create-app-roles"] = "aux-create-app-roles"
        existing = self.aux.iam("list-role-policies", *args)["PolicyNames"]
        for name in {"instance-access", "create-app-roles"}.intersection(existing) - desired.keys():
            self.aux.iam("delete-role-policy", *args, "--policy-name", name)
        for name, template in desired.items():
            self.aux.iam("put-role-policy", *args, "--policy-name", name, "--policy-document", self.policy(template))
        wanted = {f"arn:aws:iam::aws:policy/{name}" for key in self.enabled for name in MANAGED[key]}
        attached = {p["PolicyArn"] for p in self.aux.iam("list-attached-role-policies", *args)["AttachedPolicies"]}
        for arn in sorted(attached - wanted):
            self.aux.iam("detach-role-policy", *args, "--policy-arn", arn)
        for arn in sorted(wanted - attached):
            self.aux.iam("attach-role-policy", *args, "--policy-arn", arn)

    def provision(self, path):
        """Reconcile IAM, retained-host SSH access and the run environment, without restarting services."""
        ip = self.workspace_ip()
        args = ["--role-name", AUX_ROLE]
        trust = self.policy("aux-role-trust")
        existing = self.aux.optional("get-role", *args)
        if existing is None:
            self.aux.iam("create-role", *args, "--assume-role-policy-document", trust)
        elif existing["Role"]["AssumeRolePolicyDocument"] != json.loads(trust):
            self.aux.iam("update-assume-role-policy", *args, "--policy-document", trust)
        self.reconcile_permissions()
        self.main.iam(
            "put-role-policy",
            "--role-name",
            self.role,
            "--policy-name",
            "assume-aux-resource-role",
            "--policy-document",
            self.policy("run-assume-aux-role"),
        )
        write_settings(path, self.settings)
        reconcile({**self.config, **self.settings}, "reconcile", ip)
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "--", self.slug, f"sudo python3 - {self.aux_id}"],
            input=Path(__file__).with_name("run_environment.py").read_text(),
            text=True,
            check=True,
            timeout=60,
        )


def main():
    """Run the manual operator command with the generated workspace configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="check account and workspace identity without changes")
    args = parser.parse_args()
    try:
        target = AuxiliaryAccount(load_config(args.config))
        if args.dry_run:
            print(f"Would grant {', '.join(sorted(target.enabled))} to {target.role} on {target.workspace_ip()}")
        else:
            target.provision(args.config)
            print("Auxiliary access configured. Start the run service to load its environment.")
    except (ValueError, KeyError, AccessError, AwsError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Auxiliary provisioning failed: {exc}\n")


if __name__ == "__main__":
    main()
