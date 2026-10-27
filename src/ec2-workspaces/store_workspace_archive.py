#!/usr/bin/env python3
"""Upload a local workspace bundle or verify/restore an S3 archive.

Usage::

    python3 store_workspace_archive.py upload CONFIG --bundle /private/tmp/run.tar.gz
    python3 store_workspace_archive.py verify CONFIG --receipt s3://BUCKET/runs/SLUG/ID/receipt.json
    python3 store_workspace_archive.py restore CONFIG --receipt S3_URI --destination /private/tmp/restored
    python3 store_workspace_archive.py grant-read CONFIG

SSH and rsync belong to archive-workspace.sh, not this storage controller.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aux_instance_access import load_config
from create_archive import ArchiveError, digest, restore


def command(args, **kwargs):
    """Execute an argument vector and replace upstream failures with safe diagnostics.

    :param args: Operation arguments.
    :param kwargs: Additional subprocess options.
    """
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=7500, **kwargs)
    except (OSError, subprocess.SubprocessError):
        raise ArchiveError(f"{args[0]} operation failed. Archive/restore blocked; no destructive teardown authorized.") from None


class Store:
    def __init__(self, config):
        """Initialize explicit account, region and run identity for this operation.

        :param config: Parsed workspace configuration.
        """
        self.profile, self.region, self.slug = (config.get(key, "") for key in ("AWS_PROFILE", "AWS_REGION", "RUN_SLUG"))
        if not self.profile or not self.region or not re.fullmatch(r"[A-Za-z0-9-]+", self.slug):
            raise ArchiveError("Archival requires AWS_PROFILE, AWS_REGION and a valid RUN_SLUG in the config.")
        self.account = self.aws("sts", "get-caller-identity")["Account"]
        if self.account == config.get("AUX_RESOURCE_ACCOUNT_ID"):
            raise ArchiveError("The archive must live in the main account, outside the auxiliary sweep.")
        self.bucket = f"crux-run-archives-{self.account}-{self.region}"

    def aws(self, *args):
        """Call AWS with the explicit profile and region and decode its JSON result.

        :param args: Operation arguments.
        """
        output = command(["aws", "--profile", self.profile, "--region", self.region,
                          "--no-cli-pager", "--output", "json", *args]).stdout
        return json.loads(output) if output.strip() else {}

    def ensure_bucket(self):
        """Create or harden the private, versioned, encrypted archive bucket."""
        owned = {item["Name"] for item in self.aws("s3api", "list-buckets")["Buckets"]}
        if self.bucket not in owned:
            options = [] if self.region == "us-east-1" else [
                "--create-bucket-configuration", f"LocationConstraint={self.region}"]
            self.aws("s3api", "create-bucket", "--bucket", self.bucket, *options)
        self.aws("s3api", "put-public-access-block", "--bucket", self.bucket, "--public-access-block-configuration",
                 "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true")
        self.aws("s3api", "put-bucket-ownership-controls", "--bucket", self.bucket, "--ownership-controls",
                 json.dumps({"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}))
        self.aws("s3api", "put-bucket-versioning", "--bucket", self.bucket,
                 "--versioning-configuration", "Status=Enabled")
        self.aws("s3api", "put-bucket-encryption", "--bucket", self.bucket, "--server-side-encryption-configuration",
                 json.dumps({"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}))
        policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Principal": "*",
                  "Action": "s3:*", "Resource": [f"arn:aws:s3:::{self.bucket}", f"arn:aws:s3:::{self.bucket}/*"],
                  "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}
        self.aws("s3api", "put-bucket-policy", "--bucket", self.bucket, "--policy", json.dumps(policy))
        if self.aws("s3api", "get-bucket-versioning", "--bucket", self.bucket).get("Status") != "Enabled":
            raise ArchiveError("Archive bucket versioning is not enabled.")

    def download(self, receipt, destination):
        """Download the exact archived object version and verify its checksum.

        :param receipt: Remote object version and expected checksum.
        :param destination: New directory receiving verified archive contents.
        """
        if receipt["bucket"] != self.bucket or not receipt["key"].startswith("runs/") or not receipt.get("version_id"):
            raise ArchiveError("Receipt must reference an immutable version in this main account's archive bucket.")
        self.aws("s3api", "get-object", "--bucket", self.bucket, "--key", receipt["key"],
                 "--version-id", receipt["version_id"], str(destination))
        if digest(destination) != receipt["sha256"]:
            raise ArchiveError("Downloaded archive checksum differs from the receipt.")


def archive_prefix(uri):
    """Validate one exact archive directory, excluding wildcard grants.

    :param uri: S3 URI ending in ``runs/slug/archive-id/``.
    :returns: Bucket and key prefix.
    """
    match = re.fullmatch(r"s3://([a-z0-9.-]+)/runs/([A-Za-z0-9-]+)/([A-Za-z0-9-]+)/?", uri)
    if not match:
        raise ArchiveError("ARCHIVE_READ_URIS must contain exact s3://bucket/runs/slug/archive-id/ directories.")
    return match[1], f"runs/{match[2]}/{match[3]}/"


def grant_read(config):
    """Provision read-only access for this workspace to explicitly selected archives.

    :param config: Workspace settings with optional comma-separated ARCHIVE_READ_URIS.
    """
    selectors = config.get("ARCHIVE_READ_URIS", "").strip()
    if not selectors:
        return
    prefixes = [archive_prefix(uri.strip()) for uri in selectors.split(",")]
    store = Store(config)
    if any(bucket != store.bucket for bucket, _ in prefixes):
        raise ArchiveError("Selected archives must be in this main account and region's archive bucket.")
    role = f"crux-run-{store.slug}"
    roles = store.aws("iam", "list-roles")["Roles"]
    if not any(item["RoleName"] == role for item in roles):
        store.aws("iam", "create-role", "--role-name", role, "--assume-role-policy-document", json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole"}]}))
        store.aws("iam", "put-role-policy", "--role-name", role, "--policy-name", "read-system-env",
                  "--policy-document", json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                  "Action": "ssm:GetParameter", "Resource": f"arn:aws:ssm:{store.region}:{store.account}:parameter/crux/system/env"}]}))
    profiles = store.aws("iam", "list-instance-profiles-for-role", "--role-name", role)["InstanceProfiles"]
    if not any(item["InstanceProfileName"] == role for item in profiles):
        store.aws("iam", "create-instance-profile", "--instance-profile-name", role)
        store.aws("iam", "add-role-to-instance-profile", "--instance-profile-name", role, "--role-name", role)
    statements = [{"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                   "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*" for bucket, prefix in prefixes]},
                  {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{store.bucket}",
                   "Condition": {"StringLike": {"s3:prefix": [prefix + "*" for _, prefix in prefixes]}}}]
    store.aws("iam", "put-role-policy", "--role-name", role, "--policy-name", "read-run-archives",
              "--policy-document", json.dumps({"Version": "2012-10-17", "Statement": statements}))


def read_receipt(store, uri, directory):
    """Download an S3 receipt into temporary storage, never a persistent local copy.

    :param store: Authenticated main-account store.
    :param uri: Receipt S3 URI printed by upload.
    :param directory: Temporary working directory.
    :returns: Parsed receipt.
    """
    match = re.fullmatch(r"s3://([^/]+)/(runs/[A-Za-z0-9-]+/[A-Za-z0-9-]+/receipt.json)", uri)
    if not match or match[1] != store.bucket:
        raise ArchiveError("Receipt must be an S3 receipt URI in this main account's archive bucket.")
    path = directory / "receipt.json"
    store.aws("s3api", "get-object", "--bucket", store.bucket, "--key", match[2], str(path))
    return json.loads(path.read_text())


def upload(config, bundle, keep_aux=False):
    """Store a local scanned bundle and verify its immutable remote version.

    :param config: Workspace settings with explicit main-account profile.
    :param bundle: Local bundle retrieved from the workspace using rsync.
    :param keep_aux: Whether external resources are being retained.
    :returns: Durable S3 receipt URI; no receipt persists locally.
    """
    store = Store(config)
    with tempfile.TemporaryDirectory(prefix="crux-store-") as temp:
        directory = Path(temp)
        restore(bundle, directory / "input")
        manifest = json.loads((directory / "input/manifest.json").read_text())
        if keep_aux and config.get("AUX_RESOURCE_ACCOUNT_ID") and not manifest["handoff"]["external_state"]:
            raise ArchiveError("--keep-aux requires external_state preservation evidence.")
        store.ensure_bucket()
        token = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
        key = f"runs/{store.slug}/{token}/bundle.tar.gz"
        store.aws("s3", "cp", str(bundle), f"s3://{store.bucket}/{key}", "--sse", "AES256", "--only-show-errors")
        head = store.aws("s3api", "head-object", "--bucket", store.bucket, "--key", key)
        if head.get("ContentLength") != bundle.stat().st_size or not head.get("VersionId"):
            raise ArchiveError("Uploaded size/version verification failed.")
        receipt = {"version": 1, "run_slug": store.slug, "bucket": store.bucket, "key": key,
                   "version_id": head["VersionId"], "sha256": digest(bundle), "bytes": bundle.stat().st_size,
                   "manifest": manifest, "restoration_verified": True}
        store.download(receipt, directory / "downloaded.tar.gz")
        restore(directory / "downloaded.tar.gz", directory / "verified")
        path = directory / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        receipt_key = key.rsplit("/", 1)[0] + "/receipt.json"
        store.aws("s3", "cp", str(path), f"s3://{store.bucket}/{receipt_key}", "--sse", "AES256", "--only-show-errors")
        head = store.aws("s3api", "head-object", "--bucket", store.bucket, "--key", receipt_key)
        if not head.get("VersionId"):
            raise ArchiveError("Receipt version missing.")
        copy = directory / "receipt-check.json"
        store.aws("s3api", "get-object", "--bucket", store.bucket, "--key", receipt_key,
                  "--version-id", head["VersionId"], str(copy))
        if digest(path) != digest(copy):
            raise ArchiveError("Receipt verification failed.")
        return f"s3://{store.bucket}/{receipt_key}"


def main(argv=None):
    """Dispatch storage commands without remote shell execution.

    :param argv: Optional CLI arguments.
    :returns: Zero on verified completion, one on failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upload", "restore", "verify", "grant-read"))
    parser.add_argument("config", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--receipt")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--keep-aux", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == "grant-read":
            grant_read(config)
        elif args.action == "upload" and args.bundle:
            print(upload(config, args.bundle, args.keep_aux))
        elif args.action in ("restore", "verify") and args.receipt:
            store = Store(config)
            with tempfile.TemporaryDirectory(prefix="crux-verify-") as temp:
                directory = Path(temp)
                receipt = read_receipt(store, args.receipt, directory)
                if args.action == "verify" and (receipt["run_slug"] != store.slug or not receipt.get("restoration_verified")):
                    raise ArchiveError("Receipt is not verified for this run.")
                store.download(receipt, directory / "bundle.tar.gz")
                if args.action == "restore" and not args.destination:
                    raise ArchiveError("Restore needs --destination PATH.")
                restore(directory / "bundle.tar.gz", args.destination or directory / "restored")
        else:
            raise ArchiveError("Upload needs --bundle PATH; verify/restore need --receipt S3_URI.")
    except (ArchiveError, OSError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, ArchiveError) else "Archive operation failed; teardown blocked.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
