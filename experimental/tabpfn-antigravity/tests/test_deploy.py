"""Verify immutable instance identity and the EC2 request's secret boundary."""
import unittest

from deploy import Launch


class LaunchTests(unittest.TestCase):
    """Check the actual boto3 payload for a reproducible one-off instance."""

    def test_launch_payload_requires_no_instance_credentials(self):
        """Check one machine, explicit storage, IMDSv2 and a repeatable client token."""
        launch = Launch("tabpfn-run", "ami-123", "subnet-1", "sg-1", "key", "m7i.2xlarge", 160)
        request = launch.request("/dev/sda1")
        self.assertEqual(request["ClientToken"], launch.request("/dev/sda1")["ClientToken"])
        self.assertEqual(request["MaxCount"], 1)
        self.assertTrue(request["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(request["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("UserData", request)
        self.assertNotIn("IamInstanceProfile", request)
