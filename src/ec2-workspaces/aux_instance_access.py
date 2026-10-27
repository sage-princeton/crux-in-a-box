#!/usr/bin/env python3
"""Reconcile workspace SSH access to preserved auxiliary EC2 instances.

Usage::

    python3 aux_instance_access.py reconcile CONFIG PUBLIC_IP
    python3 aux_instance_access.py cleanup CONFIG
"""

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path

PREFIX = "crux-workspace-ssh:"


class AccessError(Exception):
    pass


def load_config(path):
    """Read workspace key/value settings without evaluating shell expressions.

    :param path: Filesystem path to inspect.
    """
    config = {}
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


def aws(profile, region, *args):
    """Call AWS with the explicit profile and region and decode its JSON result.

    :param profile: Profile for this operation.
    :param region: Region for this operation.
    :param args: Operation arguments.
    """
    try:
        result = subprocess.run(["aws", "--profile", profile, "--region", region, "--output", "json",
                                 "--no-cli-pager", *args], capture_output=True, text=True, timeout=120, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise AccessError(f"AWS {' '.join(args[:2])} failed with profile {profile} in {region}. "
                          "Check permissions and authentication; if SSO expired, run "
                          "aws configure sso --use-device-code.") from None


def managed(rule):
    """Identify ingress rules owned by the CRUX auxiliary-access manager.

    :param rule: Rule for this operation.
    """
    return re.fullmatch(re.escape(PREFIX) + r"[A-Za-z0-9-]+", rule.get("Description", "")) is not None


def ssh_enabled(rule):
    """Return whether an ingress rule permits TCP port 22.

    :param rule: Rule for this operation.
    """
    return not rule.get("IsEgress") and (rule.get("IpProtocol") == "-1" or (
        rule.get("IpProtocol") in ("tcp", "6") and rule.get("FromPort", 0) <= 22 <= rule.get("ToPort", 0)))


def exact_ssh(rule, cidr):
    """Match an IPv4 rule restricted to TCP port 22 and the given source CIDR.

    :param rule: Rule for this operation.
    :param cidr: Cidr for this operation.
    """
    return (ssh_enabled(rule) and rule.get("FromPort") == rule.get("ToPort") == 22
            and rule.get("CidrIpv4") == cidr)


def reconcile(config, mode, public_ip=None):
    """Grant this workspace SSH access or remove its managed ingress rules.

    :param config: Parsed workspace configuration.
    :param mode: Mode for this operation.
    :param public_ip: Public ip for this operation.
    """
    expected = config.get("AUX_RESOURCE_ACCOUNT_ID")
    enabled = config.get("PROVISION_EC2") == "1"
    if not expected:
        if mode == "reconcile" and enabled:
            raise AccessError("Provision aux resources before launching an EC2-enabled workspace.")
        return
    required = ("AWS_PROFILE", "AWS_REGION", "RUN_SLUG", "AUX_RESOURCE_PROFILE")
    if any(not config.get(key) for key in required) or not re.fullmatch(r"\d{12}", expected):
        raise AccessError("Aux SSH reconciliation requires recorded account ID, explicit profiles, region and slug.")
    if mode not in ("reconcile", "cleanup"):
        raise AccessError("Invalid aux SSH reconciliation mode.")
    cidr = str(ipaddress.IPv4Address(public_ip)) + "/32" if mode == "reconcile" and enabled else None
    profile, region, slug = (config[key] for key in ("AUX_RESOURCE_PROFILE", "AWS_REGION", "RUN_SLUG"))
    main = aws(config["AWS_PROFILE"], region, "sts", "get-caller-identity")["Account"]
    actual = aws(profile, region, "sts", "get-caller-identity")["Account"]
    if actual != expected or actual == main:
        raise AccessError("Aux SSH account differs from the recorded account or is the main account; no rules changed.")
    if mode == "reconcile":
        role = aws(profile, region, "iam", "get-role", "--role-name", "crux-agent-devops")["Role"]
        statements = role["AssumeRolePolicyDocument"]["Statement"]
        owners = [s.get("Principal", {}).get("AWS") for s in statements]
        if owners != [f"arn:aws:iam::{main}:role/crux-run-{slug}"]:
            raise AccessError("Only the current aux-account owner can reconcile successor SSH access.")

    regions = aws(profile, region, "ec2", "describe-regions")["Regions"]
    plans = []
    description = PREFIX + slug
    for entry in regions:
        target_region = entry["RegionName"]
        rules = aws(profile, target_region, "ec2", "describe-security-group-rules")["SecurityGroupRules"]
        targets = set()
        if cidr:
            reservations = aws(profile, target_region, "ec2", "describe-instances", "--filters",
                               "Name=instance-state-name,Values=pending,running,stopping,stopped")["Reservations"]
            attached = {g["GroupId"] for r in reservations for i in r.get("Instances", [])
                        for g in i.get("SecurityGroups", [])}
            targets = {r["GroupId"] for r in rules if ssh_enabled(r) and r["GroupId"] in attached}
        plans.append((target_region, rules, targets))

    for target_region, rules, targets in plans:
        for group in sorted(targets):
            if not any(r["GroupId"] == group and exact_ssh(r, cidr) for r in rules):
                permission = [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                               "IpRanges": [{"CidrIp": cidr, "Description": description}]}]
                aws(profile, target_region, "ec2", "authorize-security-group-ingress", "--group-id", group,
                    "--ip-permissions", json.dumps(permission))
        if targets:
            current = aws(profile, target_region, "ec2", "describe-security-group-rules")["SecurityGroupRules"]
            if any(not any(r["GroupId"] == g and exact_ssh(r, cidr) for r in current) for g in targets):
                raise AccessError("Successor SSH allowlist verification failed; previous managed rules were kept.")

    for target_region, rules, targets in plans:
        owned = [r for r in rules if managed(r) and not r.get("IsEgress")]
        if mode == "cleanup":
            stale = [r for r in owned if r["Description"] == description]
        else:
            stale = [r for r in owned if not (
                r["Description"] == description and r["GroupId"] in targets and exact_ssh(r, cidr))]
        for group in sorted({r["GroupId"] for r in stale}):
            ids = [r["SecurityGroupRuleId"] for r in stale if r["GroupId"] == group]
            aws(profile, target_region, "ec2", "revoke-security-group-ingress", "--group-id", group,
                "--security-group-rule-ids", *ids)
    print(f"Aux SSH access {'removed for' if mode == 'cleanup' else 'reconciled for'} {slug}"
          + (f" ({cidr}, {sum(len(p[2]) for p in plans)} group(s))" if cidr else ""))


def main(argv=None):
    """Parse workspace access arguments and report reconciliation failures.

    :param argv: Optional CLI arguments; defaults to the process argument list.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("reconcile", "cleanup"))
    parser.add_argument("config", type=Path)
    parser.add_argument("public_ip", nargs="?")
    args = parser.parse_args(argv)
    try:
        reconcile(load_config(args.config), args.mode, args.public_ip)
    except (AccessError, ValueError, KeyError, OSError) as exc:
        print(f"Aux SSH access failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
