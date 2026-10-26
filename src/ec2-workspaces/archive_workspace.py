#!/usr/bin/env python3
"""Archive a stopped run to its main account, or restore one into a successor's handoff directory."""

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aux_instance_access import load_config
from run_archive import ArchiveError, digest, restore

REMOTE = Path(__file__).with_name("run_archive.py")


def command(args, **kwargs):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=7500, **kwargs)
    except (OSError, subprocess.SubprocessError):
        raise ArchiveError(f"{args[0]} operation failed. Archive/restore blocked; no destructive teardown authorized.") from None


class Store:
    def __init__(self, config):
        self.profile, self.region, self.slug = (config.get(key, "") for key in ("AWS_PROFILE", "AWS_REGION", "RUN_SLUG"))
        if not self.profile or not self.region or not re.fullmatch(r"[A-Za-z0-9-]+", self.slug):
            raise ArchiveError("Archival requires AWS_PROFILE, AWS_REGION and a valid RUN_SLUG in the config.")
        self.account = self.aws("sts", "get-caller-identity")["Account"]
        if self.account == config.get("AUX_RESOURCE_ACCOUNT_ID"):
            raise ArchiveError("The archive must live in the main account, outside the auxiliary sweep.")
        self.bucket = f"crux-run-archives-{self.account}-{self.region}"

    def aws(self, *args):
        output = command(["aws", "--profile", self.profile, "--region", self.region,
                          "--no-cli-pager", "--output", "json", *args]).stdout
        return json.loads(output) if output.strip() else {}

    def ensure_bucket(self):
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

    def grant_read(self, config):
        role = f"crux-run-{self.slug}" if config.get("AUX_RESOURCE_ACCOUNT_ID") else "crux-system-role"
        policy = {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
             "Resource": f"arn:aws:s3:::{self.bucket}/runs/*"},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{self.bucket}",
             "Condition": {"StringLike": {"s3:prefix": "runs/*"}}}]}
        self.aws("iam", "put-role-policy", "--role-name", role, "--policy-name", "read-run-archives",
                 "--policy-document", json.dumps(policy))

    def download(self, receipt, destination):
        if receipt["bucket"] != self.bucket or not receipt["key"].startswith("runs/") or not receipt.get("version_id"):
            raise ArchiveError("Receipt must reference an immutable version in this main account's archive bucket.")
        self.aws("s3api", "get-object", "--bucket", self.bucket, "--key", receipt["key"],
                 "--version-id", receipt["version_id"], str(destination))
        if digest(destination) != receipt["sha256"]:
            raise ArchiveError("Downloaded archive checksum differs from the receipt.")


def ssh(slug, script, args=()):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", slug, "sudo python3 - " + shlex.join(args)],
                            input=script.encode(), capture_output=True, timeout=7500, check=False)
    if result.returncode:
        for line in result.stderr.decode(errors="replace").splitlines():
            if line.startswith("CRUX_ARCHIVE_ERROR: "):
                raise ArchiveError(line.removeprefix("CRUX_ARCHIVE_ERROR: "))
        raise ArchiveError("Remote archive operation failed; check SSH, sudo and the stopped run. Teardown blocked.")
    return result.stdout


def verify_secrets(config, plan, grant_successor=False):
    for ref in plan["secret_refs"]:
        arn = ref["arn"]
        if not re.fullmatch(r"arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+", arn):
            raise ArchiveError("Only Secrets Manager ARNs may be stored as secret references.")
        identity = arn.split(":")[4]
        main = Store(config)
        if identity == main.account:
            profile = config["AWS_PROFILE"]
        elif identity == config.get("AUX_RESOURCE_ACCOUNT_ID") and config.get("AUX_RESOURCE_PROFILE"):
            profile = config["AUX_RESOURCE_PROFILE"]
        else:
            raise ArchiveError("Secret reference account has no explicitly configured profile.")
        # Capture and discard values; neither stdout nor upstream exceptions reach operator logs.
        command(["aws", "--profile", profile, "--region", ref["region"], "--no-cli-pager",
                 "secretsmanager", "get-secret-value", "--secret-id", arn, "--version-id", ref["version_id"]])
        if grant_successor:
            role = (f"crux-run-{config['RUN_SLUG']}" if config.get("AUX_RESOURCE_ACCOUNT_ID") else "crux-system-role"
                    ) if identity == main.account else "crux-agent-devops"
            policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                      "Action": "secretsmanager:GetSecretValue", "Resource": arn}]}
            command(["aws", "--profile", profile, "--region", ref["region"], "iam", "put-role-policy",
                     "--role-name", role, "--policy-name", "handoff-secret-" + uuid.uuid5(uuid.NAMESPACE_URL, arn).hex,
                     "--policy-document", json.dumps(policy)])


def capture(config, receipt_path, keep_aux):
    store = Store(config)
    store.ensure_bucket()
    token = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    remote_path = f"/var/tmp/crux-archive-{token}.tar.gz"
    script = REMOTE.read_text()
    try:
        result = ssh(store.slug, script, ["capture", remote_path])
        metadata = json.loads(result)
        with tempfile.TemporaryDirectory(prefix="crux-run-archive-") as temp:
            bundle, downloaded = (Path(temp) / name for name in ("bundle.tar.gz", "downloaded.tar.gz"))
            # Only the already-scanned archive crosses the SSH boundary.
            with bundle.open("wb") as stream:
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", store.slug, "sudo cat " + shlex.quote(remote_path)],
                                   stdout=stream, stderr=subprocess.DEVNULL, check=True, timeout=7200)
                except (OSError, subprocess.SubprocessError):
                    raise ArchiveError("Could not retrieve scanned archive; teardown blocked.") from None
            if digest(bundle) != metadata["sha256"] or bundle.stat().st_size != metadata["bytes"]:
                raise ArchiveError("Archive transfer integrity check failed.")
            restore(bundle, Path(temp) / "local-restore")
            manifest = json.loads((Path(temp) / "local-restore/manifest.json").read_text())
            if keep_aux and config.get("AUX_RESOURCE_ACCOUNT_ID") and not manifest["handoff"]["external_state"]:
                raise ArchiveError("--keep-aux requires external_state inventory and preservation evidence in ARCHIVE.json.")
            verify_secrets(config, manifest["handoff"])
            key = f"runs/{store.slug}/{token}/bundle.tar.gz"
            # High-level transfer supports multipart uploads for large workspaces.
            store.aws("s3", "cp", str(bundle), f"s3://{store.bucket}/{key}", "--sse", "AES256", "--only-show-errors")
            head = store.aws("s3api", "head-object", "--bucket", store.bucket, "--key", key)
            if head.get("ContentLength") != bundle.stat().st_size or not head.get("VersionId"):
                raise ArchiveError("Uploaded object size/version verification failed.")
            receipt = {"version": 1, "run_slug": store.slug, "bucket": store.bucket, "key": key,
                       "version_id": head["VersionId"], "sha256": metadata["sha256"], "bytes": metadata["bytes"],
                       "created_at": datetime.now(UTC).isoformat(), "keep_aux": keep_aux,
                       "aux_account_id": config.get("AUX_RESOURCE_ACCOUNT_ID"),
                       "manifest": manifest}
            store.download(receipt, downloaded)
            restore(downloaded, Path(temp) / "s3-restore")
            receipt["restoration_verified"] = True
            durable_receipt = Path(temp) / "receipt.json"
            durable_receipt.write_text(json.dumps(receipt, indent=2) + "\n")
            receipt_key = key.rsplit("/", 1)[0] + "/receipt.json"
            store.aws("s3", "cp", str(durable_receipt), f"s3://{store.bucket}/{receipt_key}",
                      "--sse", "AES256", "--only-show-errors")
            receipt_head = store.aws("s3api", "head-object", "--bucket", store.bucket, "--key", receipt_key)
            if not receipt_head.get("VersionId"):
                raise ArchiveError("Durable archive receipt has no S3 version; teardown blocked.")
            copied_receipt = Path(temp) / "downloaded-receipt.json"
            store.aws("s3api", "get-object", "--bucket", store.bucket, "--key", receipt_key,
                      "--version-id", receipt_head["VersionId"], str(copied_receipt))
            if digest(copied_receipt) != digest(durable_receipt):
                raise ArchiveError("Durable archive receipt integrity check failed.")
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            with receipt_path.open("x") as stream:
                json.dump(receipt, stream, indent=2)
                stream.write("\n")
            print(f"Archive verified: s3://{store.bucket}/{key} (version {head['VersionId']})")
            print(f"Keep this receipt outside the checkout: {receipt_path}")
            print(f"Durable receipt: s3://{store.bucket}/{receipt_key} (version {receipt_head['VersionId']})")
    finally:
        command(["ssh", "-o", "BatchMode=yes", store.slug, "sudo rm -f " + shlex.quote(remote_path)])


def restore_successor(config, receipt_path):
    store = Store(config)
    receipt = json.loads(receipt_path.read_text())
    with tempfile.TemporaryDirectory(prefix="crux-restore-") as temp:
        bundle = Path(temp) / "bundle.tar.gz"
        store.download(receipt, bundle)
        restored = Path(temp) / "restored"
        restore(bundle, restored)
        verify_secrets(config, json.loads((restored / "manifest.json").read_text())["handoff"], grant_successor=True)
        destination = "/srv/crux-run/run-harness/workspace/handoff/" + receipt["key"].split("/")[2]
        setup = ("import pathlib; p=pathlib.Path(" + repr(destination) + "); "
                 "p.mkdir(parents=True, exist_ok=False)")
        ssh(store.slug, setup)
        command(["scp", "-q", str(bundle), f"{store.slug}:/tmp/crux-restore-{store.slug}.tar.gz"])
        remote_bundle = f"/tmp/crux-restore-{store.slug}.tar.gz"
        try:
            # restore() requires an absent destination; the parent was checked before transfer.
            code = REMOTE.read_text().split('if __name__ == "__main__":')[0]
            code += f"\nPath({destination!r}).rmdir()\nrestore(Path({remote_bundle!r}), Path({destination!r}))\n"
            code += f"\nshutil.chown({destination!r}, user='ubuntu', group='ubuntu')\n"
            code += f"\nsubprocess.run(['chown','-R','ubuntu:ubuntu',{destination!r}],check=True)\n"
            ssh(store.slug, code)
        finally:
            command(["ssh", store.slug, "rm -f " + shlex.quote(remote_bundle)])
        print(f"Restored and verified on {store.slug}: {destination}")
        print("Run the manifest's rebuild/check commands and verify secret retrieval and external access before launch.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "restore", "verify", "grant-read"))
    parser.add_argument("config", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--keep-aux", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == "grant-read":
            Store(config).grant_read(config)
        elif args.action == "capture":
            path = args.receipt or Path.home() / ".local/state/crux/archives" / (
                config["RUN_SLUG"] + "-" + uuid.uuid4().hex + ".json")
            capture(config, path, args.keep_aux)
        elif args.action == "verify" and args.receipt:
            store = Store(config)
            receipt = json.loads(args.receipt.read_text())
            if receipt["run_slug"] != config["RUN_SLUG"] or not receipt.get("restoration_verified"):
                raise ArchiveError("Receipt is not a verified archive for this run.")
            with tempfile.TemporaryDirectory() as temp:
                bundle = Path(temp) / "bundle.tar.gz"
                store.download(receipt, bundle)
                restore(bundle, Path(temp) / "restored")
        elif args.action == "restore" and args.receipt:
            restore_successor(config, args.receipt)
        else:
            raise ArchiveError("Restore requires --receipt PATH.")
    except (ArchiveError, OSError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, ArchiveError) else "Archive operation failed; teardown blocked.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
