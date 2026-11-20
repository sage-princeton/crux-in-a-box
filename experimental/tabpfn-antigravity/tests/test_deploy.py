"""Verify immutable instance identity and the EC2 request's secret boundary."""
import sys
import unittest
from unittest.mock import patch

from deploy import Launch, main


class LaunchTests(unittest.TestCase):
    """Check the actual boto3 payload for a reproducible one-off instance."""

    def test_launch_payload_requires_no_instance_credentials(self):
        """Check one machine, explicit storage, IMDSv2 and a repeatable client token."""
        launch = Launch("tabpfn-run", "ami-123", "subnet-1", "sg-1", "key", "m7i.large", 64)
        request = launch.request("/dev/sda1")
        self.assertEqual(request["ClientToken"], launch.request("/dev/sda1")["ClientToken"])
        self.assertEqual(request["MaxCount"], 1)
        self.assertTrue(request["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(request["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("UserData", request)
        self.assertNotIn("IamInstanceProfile", request)

    def test_cli_launch_sizing(self):
        """Check default and explicit instance and root volume sizes at the deployment entry point."""
        argv = ["deploy.py", "--profile", "fixture", "--slug", "study", "launch",
                "--subnet", "subnet-1", "--security-group", "sg-1", "--key-name", "key"]
        for extra, expected in (([], ("m7i.8xlarge", 512)),
                                (["--instance-type", "m7i.large", "--gib", "64"], ("m7i.large", 64))):
            with self.subTest(extra=extra), patch.object(sys, "argv", argv + extra), patch("deploy.Deployment") as deploy:
                main()
                (options,) = deploy.return_value.launch.call_args.args
                self.assertEqual((options.instance_type, options.gib), expected)
