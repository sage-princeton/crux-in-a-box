#!/usr/bin/env python3
"""Copy a stopped workspace to the laptop, scan locally and store a verified S3 version.

Usage from this directory::

    python archive_workspace.py copy CONFIG NEW_STAGE --plan PLAN.json
    python archive_workspace.py redact CONFIG STAGE  # if upload requests redaction
    python archive_workspace.py upload CONFIG STAGE
    python archive_workspace.py verify CONFIG RECEIPT_URI

See ``README.md`` for redaction and retry instructions.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from local_archive_utils import (
    EXCLUDED,
    EXCLUDED_NAMES,
    ArchiveError,
    ArchivePlan,
    SecretsFound,
    create_bundle,
    digest,
    read_plan_version,
    redact_files,
    restore,
)


class StagePlan(ArchivePlan):
    """Persist the operator's plan, Run identity and retry state on the laptop."""

    run_slug: str
    status: NotRequired[str]
    receipt_uri: NotRequired[str]


class BundleVersion(TypedDict):
    """Describe the object and checksum needed to verify a downloaded S3 version."""

    bucket: str
    key: str
    sha256: str
    version_id: NotRequired[str]


class ArchiveReceipt(BundleVersion):
    """Record the verified immutable object version, size and originating Run."""

    version: int
    run_slug: str
    bytes: int
    restoration_verified: bool


def command(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Execute an argument vector and replace upstream failures with safe diagnostics.

    :param args: Nonempty command and its arguments, including ``--output json`` for AWS CLI calls.
    :returns: Completed process with captured output.
    :raises ArchiveError: The command cannot start, times out or exits unsuccessfully.
    """
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=7500)
    except (OSError, subprocess.SubprocessError) as exc:
        # AWS errors may include request contents. Report the operation and error code only.
        diagnostic = getattr(exc, "stderr", b"") or b""
        code = re.search(rb"An error occurred \(([a-zA-Z0-9.]+)\)", diagnostic)
        detail = f" ({code[1].decode()})" if code else ""
        operation = " ".join(args[args.index("json") + 1 :][:2]) if args[0] == "aws" else args[0]
        raise ArchiveError(f"{operation} failed{detail}. Check credentials, service state and prerequisites.") from None


def load_config(path: Path) -> dict[str, str]:
    """Read literal workspace settings with the last value winning.

    :param path: Workspace configuration file.
    :returns: Setting names and their literal values.
    :raises OSError: The configuration file cannot be read.
    """
    config = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("#")[0].partition("=")
        if separator:
            config[key.strip()] = value.strip()
    return config


class Store:
    """Store a run's content-addressed bundles and receipts in its main account."""

    def __init__(self, config: Mapping[str, str]) -> None:
        """Resolve the archive bucket from the operator profile, AWS region and workspace slug.

        :param config: Parsed workspace configuration.
        :raises ArchiveError: The configuration is incomplete, AWS rejects the lookup or the account is auxiliary.
        :raises KeyError: The identity response lacks an account ID.
        :raises json.JSONDecodeError: The identity response is not valid JSON.
        """
        self.profile, self.region, self.slug = (
            config.get(key, "") for key in ("AWS_PROFILE", "AWS_REGION", "RUN_SLUG")
        )
        if not self.profile or not self.region or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", self.slug):
            raise ArchiveError("Archival requires AWS_PROFILE, AWS_REGION and a valid RUN_SLUG in the config.")
        self.account = self.aws("sts", "get-caller-identity")["Account"]
        if self.account == config.get("AUX_RESOURCE_ACCOUNT_ID"):
            raise ArchiveError("The archive must live in the main account, outside the auxiliary sweep.")
        self.bucket = f"crux-run-archives-{self.account}-{self.region}"

    def aws(self, *args: str) -> dict[str, Any]:
        """Call AWS with the explicit profile and region and decode its JSON result.

        :param args: Operation arguments.
        :returns: The AWS response object, or an empty dictionary if the command emits no output.
        :raises ArchiveError: The AWS CLI operation fails.
        :raises json.JSONDecodeError: The AWS CLI output is not valid JSON.
        """
        output = command(
            ["aws", "--profile", self.profile, "--region", self.region, "--no-cli-pager", "--output", "json", *args]
        ).stdout
        response: dict[str, Any] = json.loads(output) if output.strip() else {}
        return response

    def ensure_bucket(self) -> None:
        """Create or harden the private, versioned, encrypted archive bucket.

        :raises ArchiveError: An AWS operation fails or versioning is not enabled after configuration.
        """
        owned = {item["Name"] for item in self.aws("s3api", "list-buckets")["Buckets"]}
        if self.bucket not in owned:
            options = (
                []
                if self.region == "us-east-1"
                else ["--create-bucket-configuration", f"LocationConstraint={self.region}"]
            )
            self.aws("s3api", "create-bucket", "--bucket", self.bucket, *options)
        self.aws(
            "s3api",
            "put-public-access-block",
            "--bucket",
            self.bucket,
            "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true",
        )
        self.aws(
            "s3api",
            "put-bucket-ownership-controls",
            "--bucket",
            self.bucket,
            "--ownership-controls",
            json.dumps({"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}),
        )
        self.aws(
            "s3api", "put-bucket-versioning", "--bucket", self.bucket, "--versioning-configuration", "Status=Enabled"
        )
        self.aws(
            "s3api",
            "put-bucket-encryption",
            "--bucket",
            self.bucket,
            "--server-side-encryption-configuration",
            json.dumps({"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}),
        )
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [f"arn:aws:s3:::{self.bucket}", f"arn:aws:s3:::{self.bucket}/*"],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                }
            ],
        }
        self.aws("s3api", "put-bucket-policy", "--bucket", self.bucket, "--policy", json.dumps(policy))
        if self.aws("s3api", "get-bucket-versioning", "--bucket", self.bucket).get("Status") != "Enabled":
            raise ArchiveError("Archive bucket versioning is not enabled.")

    def download(self, receipt: BundleVersion, destination: Path) -> None:
        """Download the exact archived object version and verify its checksum.

        :param receipt: Remote object version and expected checksum.
        :param destination: Local file path where the downloaded bundle is written.
        :raises ArchiveError: The receipt lacks an allowed bucket, key or version, the download fails, or the hashes differ.
        :raises KeyError: A required receipt field is missing.
        :raises OSError: The downloaded file cannot be read for verification.
        """
        if receipt["bucket"] != self.bucket or not receipt["key"].startswith("runs/") or not receipt.get("version_id"):
            raise ArchiveError("Receipt must reference an immutable version in this main account's archive bucket.")
        self.aws(
            "s3api",
            "get-object",
            "--bucket",
            self.bucket,
            "--key",
            receipt["key"],
            "--version-id",
            receipt["version_id"],
            str(destination),
        )
        if digest(destination) != receipt["sha256"]:
            raise ArchiveError("Downloaded archive checksum differs from the receipt.")


def read_receipt(store: Store, uri: str, directory: Path) -> ArchiveReceipt:
    """Download an S3 receipt into temporary storage, never a persistent local copy.

    :param store: Authenticated main-account store.
    :param uri: Receipt S3 URI printed by upload.
    :param directory: Temporary working directory.
    :returns: Parsed receipt.
    :raises ArchiveError: The URI is outside the configured bucket, is malformed, or cannot be downloaded.
    :raises OSError: The downloaded receipt cannot be read.
    :raises json.JSONDecodeError: The receipt is not valid JSON.
    """
    match = re.fullmatch(r"s3://([^/]+)/(runs/[A-Za-z0-9-]+/[A-Za-z0-9-]+/receipt.json)", uri)
    if not match or match[1] != store.bucket:
        raise ArchiveError("Receipt must be an S3 receipt URI in this main account's archive bucket.")
    path = directory / "receipt.json"
    store.aws("s3api", "get-object", "--bucket", store.bucket, "--key", match[2], str(path))
    receipt: ArchiveReceipt = json.loads(path.read_text())
    return receipt


def has_object(store: Store, key: str) -> bool:
    """Return whether the exact S3 key exists, propagating permission failures.

    :param store: Authenticated archive store.
    :param key: Full object key.
    :returns: True when the listing contains the full key.
    :raises ArchiveError: S3 cannot list the prefix.
    """
    listing = store.aws("s3api", "list-objects-v2", "--bucket", store.bucket, "--prefix", key, "--max-keys", "1")
    return any(item["Key"] == key for item in listing.get("Contents", []))


def publish_once(store: Store, key: str, path: Path) -> None:
    """Publish a local file once, including bundles that need multipart upload.

    :param store: Authenticated archive store.
    :param key: Immutable destination key derived from the archive digest.
    :param path: File to publish.
    :raises ArchiveError: The initial S3 lookup fails, or publication fails without another writer creating the key.
    :raises OSError: The source file or temporary multipart files cannot be accessed.
    """
    if has_object(store, key):
        return
    target = ["--bucket", store.bucket, "--key", key]
    try:
        if path.stat().st_size <= 64 * 1024**2:
            store.aws("s3api", "put-object", *target, "--if-none-match", "*", "--body", str(path))
            return
        upload_id = store.aws("s3api", "create-multipart-upload", *target)["UploadId"]
        parts: list[dict[str, str | int]] = []
        part_size = max(64 * 1024**2, (path.stat().st_size + 9999) // 10000)
        part = path.parent / "upload-part"
        try:
            with path.open("rb") as stream:
                while data := stream.read(part_size):
                    part.write_bytes(data)
                    number = len(parts) + 1
                    result = store.aws(
                        "s3api",
                        "upload-part",
                        *target,
                        "--upload-id",
                        upload_id,
                        "--part-number",
                        str(number),
                        "--body",
                        str(part),
                    )
                    parts.append({"ETag": result["ETag"], "PartNumber": number})
            plan = path.parent / "multipart.json"
            plan.write_text(json.dumps({"Parts": parts}))
            store.aws(
                "s3api",
                "complete-multipart-upload",
                *target,
                "--upload-id",
                upload_id,
                "--if-none-match",
                "*",
                "--multipart-upload",
                "file://" + str(plan),
            )
        except BaseException:
            store.aws("s3api", "abort-multipart-upload", *target, "--upload-id", upload_id)
            raise
    except ArchiveError:
        if not has_object(store, key):
            raise


def check_shared_detectors(plan: Mapping[str, object], *, staged: bool = False) -> None:
    """Require migration of legacy per-archive rules before copying or uploading.

    :param plan: Supplied archive plan or existing stage metadata.
    :param staged: Accept an empty detector list written by an older stage.
    :raises ArchiveError: A new plan declares per-archive rules or a legacy stage still selects them.
    """
    if "custom_detectors" in plan and not (staged and plan["custom_detectors"] == []):
        raise ArchiveError(
            "Move per-archive rules into archival/detectors.yml and remove custom_detectors from the plan and stage."
        )


class ArchiveStage:
    """Keep the local copy, scan plan and retry state together in a private directory."""

    def __init__(self, directory: Path, config: Mapping[str, str]) -> None:
        """Bind a staging directory to the configured Run and its storage settings.

        :param directory: Private directory on the laptop.
        :param config: Parsed operator workspace configuration.
        :raises ArchiveError: RUN_SLUG is missing or invalid.
        """
        self.directory = directory.absolute()
        self.config = config
        self.slug = config.get("RUN_SLUG", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", self.slug):
            raise ArchiveError("A valid RUN_SLUG is required.")

    @property
    def state_file(self) -> Path:
        """Return the path to the local operator plan and status.

        :returns: Path to the stage's ARCHIVE.json file.
        """
        return self.directory / "ARCHIVE.json"

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusively lock the operator-owned staging directory for this operation.

        :yields: Control to the caller while the directory lock is held.
        :raises ArchiveError: The directory is unsafe or another operation holds its lock.
        :raises OSError: The directory cannot be opened or locked.
        """
        stage = self.directory
        if stage.is_symlink() or stage.stat().st_uid != os.getuid() or stage.stat().st_mode & 0o077:
            raise ArchiveError("Use an operator-owned private staging directory (mode 0700).")
        fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ArchiveError("Another archive command is using this staging directory.") from None
            yield
        finally:
            os.close(fd)

    def read_plan(self) -> StagePlan:
        """Read the operator plan and its latest state from the staging directory.

        :returns: Staged archive settings and progress for this Run.
        :raises ArchiveError: The state file is missing, is a link, has an invalid plan version or identifies another Run.
        :raises OSError: The state file cannot be read.
        :raises json.JSONDecodeError: The state file is not valid JSON.
        """
        if self.state_file.is_symlink() or not self.state_file.is_file():
            raise ArchiveError("Expected an archive staging directory with a local ARCHIVE.json.")
        plan: StagePlan = json.loads(self.state_file.read_text())
        read_plan_version(plan)
        check_shared_detectors(plan, staged=True)
        if plan.get("run_slug") != self.slug:
            raise ArchiveError("Staged workspace belongs to a different RUN_SLUG.")
        return plan

    def set_status(self, plan: StagePlan, status: str, receipt: str | None = None) -> None:
        """Atomically record a phase and optional verified receipt in ARCHIVE.json.

        :param plan: Operator settings updated in memory and written to the state file.
        :param status: Current archival phase.
        :param receipt: Remote receipt URI after verification.
        :raises OSError: The temporary state file cannot be written or replaced.
        """
        plan["status"] = status
        plan.pop("receipt_uri", None)
        if receipt:
            plan["receipt_uri"] = receipt
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as stream:
            json.dump(plan, stream, indent=2)
            stream.write("\n")
        os.replace(stream.name, self.state_file)

    def copy(self, plan_path: Path) -> None:
        """Copy the stopped Run, or resume an interrupted copy before redaction.

        :param plan_path: Operator-authored JSON with required paths and restore checks.
        :raises ArchiveError: The stage, plan or lock is invalid, or the transfer fails.
        :raises OSError: The plan or local staging state cannot be read or written.
        :raises json.JSONDecodeError: The supplied plan or existing staging state is not valid JSON.
        :raises KeyError: A required plan or resume field is missing.
        """
        stage = self.directory
        if any((parent / ".git").exists() for parent in (stage.resolve(), *stage.resolve().parents)):
            raise ArchiveError("Choose a private staging directory outside Git checkouts.")
        supplied = json.loads(plan_path.read_text())
        check_shared_detectors(supplied)
        plan = StagePlan(
            plan_version=read_plan_version(supplied),
            required_paths=supplied["required_paths"],
            restore_checks=supplied["restore_checks"],
            run_slug=self.slug,
        )
        stage.mkdir(mode=0o700, exist_ok=True)
        with self.lock():
            if any(stage.iterdir()):
                previous = self.read_plan()
                if any(previous.get(key) != value for key, value in plan.items()):
                    raise ArchiveError("Stage already has a different plan. Use a new staging directory.")
                if previous["status"] not in ("copying", "copy_failed"):
                    return
            self.set_status(plan, "copying")
            try:
                for name in ("run", "provenance"):
                    path = stage / name
                    if path.is_symlink():
                        raise ArchiveError("Unsafe staging directory.")
                    path.mkdir(exist_ok=True)
                filters = [f"--exclude={name}" for name in EXCLUDED_NAMES]
                filters.extend(f"--exclude=/{name}" for name in EXCLUDED)
                command(["bash", str(Path(__file__).with_name("copy_workspace.sh")), self.slug, str(stage), *filters])
            except (OSError, ArchiveError):
                self.set_status(plan, "copy_failed")
                raise ArchiveError(
                    "Copy failed. Check stopped services, SSH, rsync and free space, then retry copy."
                ) from None
            self.set_status(plan, "copied")

    def redact(self) -> None:
        """Review working-file findings in trufflehog-redactor and retain the mandatory rescan.

        :raises ArchiveError: The stage cannot be locked, is not awaiting redaction, or the redactor fails.
        :raises OSError: The staging directory or plan is inaccessible.
        :raises json.JSONDecodeError: The stored plan is not valid JSON.
        """
        with self.lock():
            plan = self.read_plan()
            if plan.get("status") != "redaction_required":
                raise ArchiveError("Run upload first to scan the staged archive.")
            redact_files(self.directory / "run")

    def upload(self, scratch: Path) -> str:
        """Rescan the staged bytes, publish them, verify restoration and record the receipt.

        :param scratch: Private temporary directory for the bundle and verification.
        :returns: Verified remote receipt URI.
        :raises SecretsFound: A staged file or Git object needs redaction before upload.
        :raises ArchiveError: The stage is incomplete or locking, scanning, storage or verification fails.
        :raises OSError: Local state, scratch files or the verified bundle cannot be accessed.
        :raises ValueError: Staged metadata or a receipt cannot be decoded.
        :raises KeyError: A required plan, receipt or service-response field is absent.
        :raises PackageNotFoundError: The pinned scanner is not installed.
        :raises tarfile.TarError: The bundle cannot be written or restored.
        """
        with self.lock():
            plan = self.read_plan()
            if plan.get("status") not in {
                "copied",
                "scanning",
                "scan_failed",
                "redaction_required",
                "uploading",
                "upload_failed",
                "verified",
            }:
                raise ArchiveError("A completed workspace copy is required before upload.")
            self.set_status(plan, "scanning")
            try:
                handoff = ArchivePlan(
                    plan_version=plan["plan_version"],
                    required_paths=plan["required_paths"],
                    restore_checks=plan["restore_checks"],
                )
                provenance = {}
                for key, name in (("software", "crux-provision.json"), ("provision", "crux-source.json")):
                    path = self.directory / "provenance" / name
                    if path.is_file() and not path.is_symlink():
                        provenance[key] = json.loads(path.read_text())
                provenance["scanner"] = version("trufflehog3")
                create_bundle(
                    self.directory / "run",
                    scratch / "bundle.tar.gz",
                    handoff,
                    provenance,
                )
            except SecretsFound:
                self.set_status(plan, "redaction_required")
                raise
            except Exception:
                self.set_status(plan, "scan_failed")
                raise
            self.set_status(plan, "uploading")
            try:
                uri = archive_workspace(Store(self.config), scratch)
            except Exception:
                self.set_status(plan, "upload_failed")
                raise
            self.set_status(plan, "verified", uri)
            return uri


def archive_workspace(store: Store, directory: Path) -> str:
    """Publish a freshly scanned bundle and verify its exact stored version.

    :param store: Authenticated destination.
    :param directory: Private workspace containing the bundle.
    :returns: Reusable S3 receipt URI.
    :raises ArchiveError: Publishing fails, the stored version differs or restoration cannot be verified.
    :raises OSError: Local bundle, receipt or verification files cannot be accessed.
    :raises tarfile.TarError: The downloaded bundle cannot be extracted.
    :raises ValueError: A receipt or manifest cannot be decoded.
    :raises KeyError: A required response or manifest field is missing.
    """
    bundle = directory / "bundle.tar.gz"
    checksum = digest(bundle)
    prefix = f"runs/{store.slug}/{checksum}/"
    key = prefix + "bundle.tar.gz"
    store.ensure_bucket()
    publish_once(store, key, bundle)
    head = store.aws("s3api", "head-object", "--bucket", store.bucket, "--key", key)
    if head.get("ContentLength") != bundle.stat().st_size or head.get("VersionId") in (None, "", "null"):
        raise ArchiveError("Archive size/version verification failed.")
    receipt: ArchiveReceipt = {
        "version": 1,
        "bucket": store.bucket,
        "run_slug": store.slug,
        "key": key,
        "version_id": head["VersionId"],
        "sha256": checksum,
        "bytes": bundle.stat().st_size,
        "restoration_verified": True,
    }
    store.download(receipt, directory / "downloaded.tar.gz")
    restore(directory / "downloaded.tar.gz", directory / "verified")
    receipt_key = prefix + "receipt.json"
    path = directory / "new-receipt.json"
    path.write_text(json.dumps(receipt, sort_keys=True))
    publish_once(store, receipt_key, path)
    uri = f"s3://{store.bucket}/{receipt_key}"
    if read_receipt(store, uri, directory) != receipt:
        raise ArchiveError("S3 receipt differs from the verified bundle.")
    return uri


def verify_receipt(store: Store, uri: str, directory: Path) -> None:
    """Verify Run ownership and extract the exact object version into temporary storage.

    :param store: Archive bucket for the configured Run.
    :param uri: Remote receipt printed by upload.
    :param directory: Temporary space for downloads.
    :raises ArchiveError: The receipt belongs to another Run, or the download or verified restore fails.
    :raises OSError: The bundle, destination or receipt cannot be accessed.
    :raises tarfile.TarError: The downloaded bundle cannot be extracted.
    :raises ValueError: A receipt or manifest cannot be decoded.
    :raises KeyError: A required receipt or manifest field is missing.
    """
    receipt = read_receipt(store, uri, directory)
    expected = f"s3://{store.bucket}/" + receipt["key"].removesuffix("bundle.tar.gz") + "receipt.json"
    if (
        expected != uri
        or receipt["run_slug"] != store.slug
        or not receipt["key"].startswith(f"runs/{store.slug}/")
        or not receipt["restoration_verified"]
    ):
        raise ArchiveError("Receipt does not identify a verified archive for this workspace.")
    bundle = directory / "bundle.tar.gz"
    store.download(receipt, bundle)
    restore(bundle, directory / "verified")


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the operator's action and its required paths or receipt URI.

    :param argv: Optional command-line argument sequence.
    :returns: Arguments for the selected operation.
    :raises SystemExit: The caller requests help or supplies invalid arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("copy", "redact", "upload", "verify"):
        action = actions.add_parser(name)
        action.add_argument("config", type=Path)
        if name in ("copy", "redact", "upload"):
            action.add_argument("stage", type=Path)
        if name == "copy":
            action.add_argument("--plan", type=Path, required=True)
        if name == "verify":
            action.add_argument("receipt")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the manual operator command.

    :param argv: Optional command-line arguments.
    :returns: Process status, zero on success.
    :raises SystemExit: The caller requests help or supplies invalid arguments.
    """
    os.umask(0o077)
    args = parse_arguments(argv)
    try:
        config = load_config(args.config)
        if args.action == "copy":
            ArchiveStage(args.stage, config).copy(args.plan)
            return 0
        if args.action == "redact":
            ArchiveStage(args.stage, config).redact()
            return 0
        with tempfile.TemporaryDirectory(prefix="crux-archive-") as temp:
            directory = Path(temp)
            if args.action == "upload":
                print(ArchiveStage(args.stage, config).upload(directory))
            else:
                verify_receipt(Store(config), args.receipt, directory)
    except (ArchiveError, OSError, KeyError, ValueError, TypeError, PackageNotFoundError, tarfile.TarError) as exc:
        print(
            str(exc) if isinstance(exc, ArchiveError) else "Invalid archive, destination or configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
