import copy
import importlib.util
import ipaddress
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "src/ec2-workspaces/aux_instance_access.py"
SPEC = importlib.util.spec_from_file_location("aux_instance_access", SCRIPT)
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


def rule(identifier, group="sg-cms", cidr="192.0.2.10/32", description="operator", port=22):
    """Rule.

    :param identifier: Identifier for this operation.
    :param group: Group for this operation.
    :param cidr: Cidr for this operation.
    :param description: Description for this operation.
    :param port: Port for this operation.
    """
    return {"SecurityGroupRuleId": identifier, "GroupId": group, "IsEgress": False,
            "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
            "CidrIpv4": cidr, "Description": description}


class AwsFixture:
    def __init__(self):
        """Initialize explicit account, region and run identity for this operation."""
        self.calls = []
        self.account = "222222222222"
        self.owner = "arn:aws:iam::111111111111:role/crux-run-new"
        self.groups = {"us-east-1": [], "us-east-2": ["sg-cms", "sg-web"]}
        self.rules = {"us-east-1": [], "us-east-2": [
            rule("sgr-manual"), rule("sgr-http", port=443, cidr="0.0.0.0/0"),
            rule("sgr-web", group="sg-web", port=443, cidr="0.0.0.0/0"),
            rule("sgr-unattached", group="sg-unused")]}
        self.fail = None
        self.next_rule = 0
        self.suppress_authorization = False

    def __call__(self, profile, region, *args):
        """Call.

        :param profile: Profile for this operation.
        :param region: Region for this operation.
        :param args: Operation arguments.
        """
        self.calls.append((profile, region, *args))
        service, action = args[:2]
        if self.fail == action:
            raise access.AccessError("fixture API failure")
        if action == "get-caller-identity":
            return {"Account": "111111111111" if profile == "main" else self.account}
        if action == "get-role":
            return {"Role": {"AssumeRolePolicyDocument": {"Statement": [
                {"Principal": {"AWS": self.owner}}]}}}
        if action == "describe-regions":
            return {"Regions": [{"RegionName": r} for r in self.groups]}
        if action == "describe-instances":
            return {"Reservations": [{"Instances": [{"SecurityGroups": [{"GroupId": g} for g in self.groups[region]]}]}]}
        if action == "describe-security-group-rules":
            return {"SecurityGroupRules": copy.deepcopy(self.rules[region])}
        if action == "authorize-security-group-ingress":
            if self.suppress_authorization:
                return {}
            group = args[args.index("--group-id") + 1]
            permissions = json.loads(args[args.index("--ip-permissions") + 1])[0]
            source = permissions["IpRanges"][0]
            self.next_rule += 1
            self.rules[region].append(rule(f"sgr-new-{self.next_rule}", group, source["CidrIp"], source["Description"]))
            return {"SecurityGroupRules": [self.rules[region][-1]]}
        if action == "revoke-security-group-ingress":
            assert "--group-id" in args, "RevokeSecurityGroupIngress requires groupId"
            group = args[args.index("--group-id") + 1]
            ids = args[args.index("--security-group-rule-ids") + 1:]
            assert all(r["GroupId"] == group for r in self.rules[region] if r["SecurityGroupRuleId"] in ids)
            self.rules[region] = [r for r in self.rules[region] if r["SecurityGroupRuleId"] not in ids]
            return {}
        raise AssertionError((service, action))

    def mutations(self):
        """Mutations."""
        return [c for c in self.calls if c[3] in {"authorize-security-group-ingress", "revoke-security-group-ingress"}]


class AuxInstanceAccessTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture state for each regression case."""
        self.aws = AwsFixture()
        self.config = {"AWS_PROFILE": "main", "AWS_REGION": "us-east-1", "RUN_SLUG": "new",
                       "AUX_RESOURCE_PROFILE": "aux", "AUX_RESOURCE_ACCOUNT_ID": "222222222222",
                       "PROVISION_EC2": "1"}

    def run_access(self, mode="reconcile", ip="198.51.100.42"):
        """Run access.

        :param mode: Mode for this operation.
        :param ip: Ip for this operation.
        """
        with patch.object(access, "aws", side_effect=self.aws):
            access.reconcile(self.config, mode, ip)

    def test_successor_gets_ssh_in_other_region_without_opening_web_only_or_unused_groups(self):
        """Verify successor gets ssh in other region without opening web only or unused groups."""
        self.run_access()
        managed = [r for r in self.aws.rules["us-east-2"] if r["Description"] == "crux-workspace-ssh:new"]
        self.assertEqual([(r["GroupId"], r["CidrIpv4"], r["FromPort"], r["ToPort"]) for r in managed],
                         [("sg-cms", "198.51.100.42/32", 22, 22)])
        self.assertEqual(len(self.aws.rules["us-east-2"]), 5)

    def test_handoff_adds_and_verifies_before_removing_predecessor_and_preserves_manual_rules(self):
        """Verify handoff adds and verifies before removing predecessor and preserves manual rules."""
        self.aws.rules["us-east-2"].append(rule("sgr-old", description="crux-workspace-ssh:old"))
        self.run_access()
        actions = [c[3] for c in self.aws.calls]
        self.assertLess(actions.index("authorize-security-group-ingress"), actions.index("revoke-security-group-ingress"))
        between = actions[actions.index("authorize-security-group-ingress") + 1:actions.index("revoke-security-group-ingress")]
        self.assertIn("describe-security-group-rules", between)
        self.assertNotIn("sgr-old", [r["SecurityGroupRuleId"] for r in self.aws.rules["us-east-2"]])
        self.assertIn("sgr-manual", [r["SecurityGroupRuleId"] for r in self.aws.rules["us-east-2"]])

    def test_repeated_provisioning_is_idempotent_and_ip_change_removes_old_managed_cidr(self):
        """Verify repeated provisioning is idempotent and ip change removes old managed cidr."""
        self.run_access()
        self.aws.calls.clear()
        self.run_access()
        self.assertEqual(self.aws.mutations(), [])
        self.run_access(ip="198.51.100.43")
        self.assertEqual([r["CidrIpv4"] for r in self.aws.rules["us-east-2"]
                          if r["Description"] == "crux-workspace-ssh:new"], ["198.51.100.43/32"])

    def test_manual_matching_rule_is_left_unmodified(self):
        """Verify manual matching rule is left unmodified."""
        self.aws.rules["us-east-2"][0]["CidrIpv4"] = "198.51.100.42/32"
        self.run_access()
        self.assertEqual(self.aws.mutations(), [])

    def test_unverified_allowlist_does_not_remove_previous_access(self):
        """Verify unverified allowlist does not remove previous access."""
        self.aws.suppress_authorization = True
        self.aws.rules["us-east-2"].append(rule("sgr-old", description="crux-workspace-ssh:old"))
        with self.assertRaises(access.AccessError):
            self.run_access()
        self.assertIn("sgr-old", [r["SecurityGroupRuleId"] for r in self.aws.rules["us-east-2"]])
        self.assertFalse(any(c[3] == "revoke-security-group-ingress" for c in self.aws.calls))

    def test_old_teardown_cannot_remove_reconciled_successor_access(self):
        """Verify old teardown cannot remove reconciled successor access."""
        self.run_access()
        self.config["RUN_SLUG"] = "successor"
        self.aws.owner = "arn:aws:iam::111111111111:role/crux-run-successor"
        self.run_access(ip="198.51.100.43")
        self.config["RUN_SLUG"] = "new"
        self.run_access(mode="cleanup", ip=None)
        managed_rules = [r for r in self.aws.rules["us-east-2"] if access.managed(r)]
        self.assertEqual([(r["Description"], r["CidrIpv4"]) for r in managed_rules],
                         [("crux-workspace-ssh:successor", "198.51.100.43/32")])
        self.assertTrue(all(c[0] == "aux" for c in self.aws.mutations()))

    def test_existing_port_range_only_grants_new_workspace_port_22_and_egress_is_ignored(self):
        """Verify existing port range only grants new workspace port 22 and egress is ignored."""
        self.aws.rules["us-east-2"][0]["FromPort"] = 20
        self.aws.rules["us-east-2"][0]["ToPort"] = 30
        outgoing = rule("sgr-out", group="sg-web")
        outgoing["IsEgress"] = True
        self.aws.rules["us-east-2"].append(outgoing)
        self.run_access()
        managed_rules = [r for r in self.aws.rules["us-east-2"] if access.managed(r)]
        self.assertEqual([(r["GroupId"], r["FromPort"], r["ToPort"]) for r in managed_rules],
                         [("sg-cms", 22, 22)])

    def test_cleanup_removes_only_this_slug_including_rules_on_detached_groups(self):
        """Verify cleanup removes only this slug including rules on detached groups."""
        self.aws.owner = "arn:aws:iam::111111111111:role/crux-run-successor"
        self.aws.rules["us-east-2"] += [rule("sgr-own", group="sg-unused", description="crux-workspace-ssh:new"),
                                         rule("sgr-successor", description="crux-workspace-ssh:successor")]
        self.run_access(mode="cleanup", ip=None)
        ids = [r["SecurityGroupRuleId"] for r in self.aws.rules["us-east-2"]]
        self.assertNotIn("sgr-own", ids)
        self.assertIn("sgr-successor", ids)
        self.assertIn("sgr-manual", ids)

    def test_disabling_ec2_removes_managed_access_without_creating_it(self):
        """Verify disabling ec2 removes managed access without creating it."""
        self.config["PROVISION_EC2"] = ""
        self.aws.rules["us-east-2"].append(rule("sgr-old", description="crux-workspace-ssh:old"))
        self.run_access()
        self.assertFalse(any(c[3] == "authorize-security-group-ingress" for c in self.aws.calls))
        self.assertNotIn("sgr-old", [r["SecurityGroupRuleId"] for r in self.aws.rules["us-east-2"]])

    def test_no_aux_config_is_a_noop(self):
        """Verify no aux config is a noop."""
        self.config = {"AWS_REGION": "us-east-1", "RUN_SLUG": "plain"}
        self.run_access()
        self.assertEqual(self.aws.calls, [])

    def test_wrong_account_or_stale_owner_cannot_change_rules(self):
        """Verify wrong account or stale owner cannot change rules."""
        for account, owner in [("111111111111", self.aws.owner), ("333333333333", self.aws.owner),
                               ("222222222222", "arn:aws:iam::111111111111:role/crux-run-other")]:
            with self.subTest(account=account, owner=owner):
                self.aws.account, self.aws.owner = account, owner
                with self.assertRaises(access.AccessError):
                    self.run_access()
                self.assertEqual(self.aws.mutations(), [])

    def test_missing_explicit_profile_or_invalid_ip_fails_before_mutations(self):
        """Verify missing explicit profile or invalid ip fails before mutations."""
        for key in ("AWS_PROFILE", "AUX_RESOURCE_PROFILE"):
            config = self.config.copy()
            self.config[key] = ""
            with self.assertRaises(access.AccessError):
                self.run_access()
            self.config = config
        for ip in ("0.0.0.0/0", "bad", "2001:db8::1"):
            with self.subTest(ip=ip), self.assertRaises((access.AccessError, ipaddress.AddressValueError)):
                self.run_access(ip=ip)
        self.assertEqual(self.aws.mutations(), [])

    def test_discovery_or_authorization_failure_is_not_reported_as_success(self):
        """Verify discovery or authorization failure is not reported as success."""
        for action in ("describe-regions", "describe-instances", "describe-security-group-rules",
                       "authorize-security-group-ingress"):
            with self.subTest(action=action), self.assertRaises(access.AccessError):
                self.aws.fail = action
                self.run_access()

    def test_cli_and_config_parser_accept_only_runtime_ip_and_one_config_source(self):
        """Verify cli and config parser accept only runtime ip and one config source."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.txt"
            path.write_text("AWS_REGION=us-east-1\nRUN_SLUG=first\nRUN_SLUG=last # comment\n")
            self.assertEqual(access.load_config(path)["RUN_SLUG"], "last")
            result = subprocess.run(["python3", str(SCRIPT), "reconcile", str(path), "198.51.100.42"],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
