"""Launch a tagged EC2 study box and provision its committed source over operator-authenticated SSH."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from prepare import HERE

CANONICAL_AMI = "/aws/service/canonical/ubuntu/server/noble/stable/current/amd64/hvm/ebs-gp3/ami-id"
SOURCE = HERE.parents[1]
PATHS = ["experimental/tabpfn-antigravity", "monitoring/tracing/codex/live_trace", "scaffolds/sdk/crux_scaffold",
         "scaffolds/sdk/requirements.txt", "scaffolds/sdk/run-harness/tabpfn-full"]
DEST = "/opt/tabpfn-source"


@dataclass(frozen=True)
class Launch:
    """Inputs to one idempotent EC2 launch with an encrypted persistent root disk.

    :ivar slug: Idempotent study identity.
    """

    slug: str
    image: str
    subnet: str
    security_group: str
    key: str
    instance_type: str
    gib: int

    def request(self, root_device: str) -> dict:
        """Build the secret-free boto3 request; the study identifier is the idempotency key.

        :param root_device: Root volume device named by the selected AMI.
        :returns: Complete RunInstances parameters.
        """
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.slug) or self.gib < 32:
            raise ValueError("Invalid study name or root disk size")
        tags = [{"Key": "Name", "Value": self.slug}, {"Key": "TabPFNStudy", "Value": self.slug}]
        return {"ImageId": self.image, "InstanceType": self.instance_type, "KeyName": self.key,
                "SubnetId": self.subnet, "SecurityGroupIds": [self.security_group], "MinCount": 1, "MaxCount": 1,
                "ClientToken": hashlib.sha256(("tabpfn-antigravity/" + self.slug).encode()).hexdigest(),
                "MetadataOptions": {"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1},
                "BlockDeviceMappings": [{"DeviceName": root_device, "Ebs": {"VolumeType": "gp3",
                    "Encrypted": True, "VolumeSize": self.gib, "DeleteOnTermination": False}}],
                "TagSpecifications": [{"ResourceType": resource, "Tags": tags} for resource in ("instance", "volume")]}


class Deployment:
    """Use a named AWS profile to resolve the run box and its immutable source revision.

    :ivar ec2: EC2 client in the requested region.
    """

    def __init__(self, profile: str, region: str):
        """Verify caller identity before any EC2 read or write operation.

        :param profile: Explicit authenticated AWS profile.
        :param region: Target AWS region.
        """
        import boto3
        from botocore.config import Config

        self.session = boto3.Session(profile_name=profile, region_name=region)
        config = Config(retries={"mode": "standard", "total_max_attempts": 4})
        self.session.client("sts", config=config).get_caller_identity()
        self.ec2 = self.session.client("ec2", config=config)

    def instance(self, slug: str) -> dict | None:
        """Resolve exactly one live or stopped box for this study, across all result pages.

        :param slug: Unique study and EC2 ownership tag.
        :returns: Matching EC2 instance, or None if none exists.
        """
        filters = [{"Name": "tag:TabPFNStudy", "Values": [slug]}, {"Name": "instance-state-name",
                   "Values": ["pending", "running", "stopping", "stopped"]}]
        rows = [i for page in self.ec2.get_paginator("describe_instances").paginate(Filters=filters)
                for r in page["Reservations"] for i in r["Instances"]]
        if len(rows) > 1:
            raise ValueError("Multiple instances have the requested study tag")
        return rows[0] if rows else None

    def launch(self, args) -> None:
        """Resolve Canonical's image, verify permissions or submit the idempotent launch.

        :param args: Parsed command arguments.
        """
        from botocore.exceptions import ClientError

        prior = self.instance(args.slug)
        if prior:
            print(prior["InstanceId"], prior["State"]["Name"])
            return
        image = args.ami or self.session.client("ssm").get_parameter(Name=CANONICAL_AMI)["Parameter"]["Value"]
        root = self.ec2.describe_images(ImageIds=[image])["Images"][0]["RootDeviceName"]
        spec = Launch(args.slug, image, args.subnet, args.security_group, args.key_name, args.instance_type, args.gib)
        try:
            result = self.ec2.run_instances(**spec.request(root), DryRun=args.dry_run)
        except ClientError as exc:
            if args.dry_run and exc.response["Error"]["Code"] == "DryRunOperation":
                print("AWS authorized the dry-run launch for", image, args.instance_type)
                return
            raise
        instance = result["Instances"][0]["InstanceId"]
        self.ec2.get_waiter("instance_status_ok").wait(InstanceIds=[instance])
        print(instance)

    def configure(self, slug: str, key: Path, host: str | None, known_hosts: Path | None) -> None:
        """Install a clean Git archive and transfer scoped credentials inside the encrypted SSH channel.

        :param slug: Unique study and EC2 ownership tag.
        :param key: SSH private identity file.
        :param host: Optional explicit SSH hostname.
        :param known_hosts: File of operator-verified SSH host keys.
        """
        values = {"GEMINI_API_KEY": os.environ["AI_STUDIO_API_DEV_KEY"]}
        values.update({k: os.environ[k] for k in ("OPENROUTER_API_KEY", "LANGFUSE_BASE_URL",
                                                  "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")})
        instance = self.instance(slug)
        if not instance or instance["State"]["Name"] != "running":
            raise ValueError("The tagged study instance must be running")
        address = host or instance.get("PublicDnsName") or instance["PrivateIpAddress"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+", address):
            raise ValueError("Invalid SSH hostname")
        revision = subprocess.check_output(["git", "-C", SOURCE, "rev-parse", "HEAD"], text=True).strip()
        archive = subprocess.check_output(["git", "-C", SOURCE, "archive", revision, *PATHS])
        self.install(address, key, slug, revision, archive, values, known_hosts)

    @staticmethod
    def install(address: str, key: Path, slug: str, revision: str, archive: bytes, values: dict,
                known_hosts: Path | None = None) -> None:
        """Upload a committed revision, install Linux services, then deliver the credentials on stdin.

        :param address: EC2 hostname verified through the operator's SSH known_hosts.
        :param revision: Full Git commit ID identifying this installation.
        :param key: SSH private identity file.
        :param slug: Unique study and EC2 ownership tag.
        :param archive: Git archive of the committed runtime and inputs.
        :param values: Credential fields received over SSH stdin.
        :param known_hosts: File of operator-verified SSH host keys.
        """
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug) or not re.fullmatch(r"[a-f0-9]{40,64}", revision):
            raise ValueError("Invalid revision or study identifier")
        remote = f"{DEST}/{revision}"
        ssh = ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
        if known_hosts:
            ssh.extend(["-o", f"UserKnownHostsFile={known_hosts}"])
        ssh.append(f"ubuntu@{address}")
        for attempt in range(12):
            if subprocess.run([*ssh, "true"], check=False).returncode == 0:
                break
            if attempt == 11:
                raise RuntimeError("SSH not ready; verify the instance host key and network reachability")
            time.sleep(5)
        check = "if systemctl is-active --quiet tabpfn-study.target; then exit 1; fi"
        subprocess.run([*ssh, check], check=True)
        subprocess.run([*ssh, f"sudo install -d -m 0755 {remote} && sudo tar xf - --no-same-owner -C {remote}"],
                       input=archive, check=True)
        program = f"sudo python3 {remote}/experimental/tabpfn-antigravity/host.py"
        subprocess.run([*ssh, f"{program} install {slug}"], check=True)
        subprocess.run([*ssh, f"{program} credentials"], input=json.dumps(values).encode(), check=True)
        print(f"Configured {slug} at {revision}. To start on the verified host: {program} start")


def main() -> None:
    """Launch, dry-run or configure an EC2 box without implicitly starting paid research.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--slug", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    launch = actions.add_parser("launch")
    for arg in ("subnet", "security-group", "key-name"):
        launch.add_argument("--" + arg, required=True)
    launch.add_argument("--instance-type", default="m7i.2xlarge")
    launch.add_argument("--gib", type=int, default=160)
    launch.add_argument("--ami")
    launch.add_argument("--dry-run", action="store_true")
    configure = actions.add_parser("configure")
    configure.add_argument("--ssh-key", required=True, type=Path)
    configure.add_argument("--known-hosts", type=Path, help="Operator-verified SSH host key file")
    configure.add_argument("--host", help="Verified SSH hostname for a private-network route")
    args = parser.parse_args()
    deploy = Deployment(args.profile, args.region)
    if args.action == "launch":
        deploy.launch(args)
    else:
        deploy.configure(args.slug, args.ssh_key, args.host, args.known_hosts)


if __name__ == "__main__":
    main()
