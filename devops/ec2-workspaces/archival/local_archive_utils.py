"""Build and verify workspace bundles for the manual archive CLI."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import TypedDict

EXCLUDED_NAMES = (".ssh", ".aws", ".mcp.json", "auth.json", ".credentials.json")
EXCLUDED = (
    "state/sessions",
    "state/sessions.sqlite",
    "state/sessions.sqlite-wal",
    "state/sessions.sqlite-shm",
)


class ArchivePlan(TypedDict):
    """Describe the workspace paths and restore checks declared by the operator."""

    plan_version: int
    required_paths: list[str]
    restore_checks: list[str]


class Manifest(TypedDict):
    """Record the restore contract and file inventory for a snapshot."""

    version: int
    excluded_operational_paths: list[str]
    handoff: ArchivePlan
    provenance: dict[str, object]
    files: dict[str, dict[str, str | int]]


class ArchiveError(Exception):
    """Report an archive contract violation without including file contents."""


def digest(path: Path) -> str:
    """Compute the SHA-256 digest without loading the whole file into memory.

    :param path: Filesystem path to inspect.
    :returns: Lowercase hexadecimal SHA-256 digest.
    :raises OSError: The file cannot be opened or read.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(value: str) -> Path:
    """Validate a nonempty relative archive path and reject traversal.

    :param value: Manifest path or tar member name relative to the archive root.
    :returns: Path relative to the archive root.
    :raises ArchiveError: The path is empty, absolute, the root itself, or contains a parent traversal.
    """
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ArchiveError("Archive paths must be nonempty relative paths without '..'.")
    return path


def is_excluded(path: str) -> bool:
    """Return whether a path belongs to an intentionally omitted operational store.

    :param path: Filesystem path to inspect.
    :returns: True for an operational path omitted from the snapshot.
    """
    return any(part in EXCLUDED_NAMES for part in Path(path).parts) or any(
        path == name or path.startswith(name + "/") for name in EXCLUDED
    )


def read_plan_version(plan: object) -> int:
    """Require the supported integer plan_version.

    :param plan: Decoded plan, including a staged plan or a manifest's handoff.
    :returns: The supported plan version.
    :raises ArchiveError: The plan is malformed, uses an obsolete field or has an unsupported version.
    """
    if isinstance(plan, dict) and "version" not in plan:
        value = plan.get("plan_version")
        if type(value) is int and value == 1:
            return value
    raise ArchiveError("Expected plan_version=1 as the archive plan's only version field.")


def validate_plan(root: Path, plan: object) -> None:
    """Check the restore contract against a copied or restored workspace.

    :param plan: Archive plan supplied by the operator.
    :param root: Root directory of the workspace or snapshot.
    :raises ArchiveError: The plan is malformed or a required path is unsafe, excluded, absent or empty.
    :raises OSError: A required path cannot be inspected.
    """
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("required_paths"), list)
        or not plan["required_paths"]
        or not isinstance(plan.get("restore_checks"), list)
        or not plan["restore_checks"]
        or not all(isinstance(value, str) and value for value in plan["required_paths"] + plan["restore_checks"])
    ):
        raise ArchiveError("ARCHIVE.json needs nonempty required_paths and restore_checks.")
    read_plan_version(plan)
    for value in plan["required_paths"]:
        path = relative(value)
        source = root / path
        if is_excluded(str(path)) or not source.exists() or (source.is_file() and not source.stat().st_size):
            raise ArchiveError(f"Required archive path is missing, empty or excluded: {path}")
        if source.is_dir() and not any(source.iterdir()):
            raise ArchiveError(f"Required archive directory is empty: {path}")


def inventory(root: Path, skip_operational: bool = False) -> dict[str, dict[str, str | int]]:
    """Describe files, directories and internal links for integrity verification.

    :param root: Root directory of the workspace or snapshot.
    :param skip_operational: Whether to omit excluded operational stores.
    :returns: Relative names mapped to entry types, file sizes and hashes, or symlink targets.
    :raises ArchiveError: An entry is unsupported or a symlink is broken or escapes the root.
    :raises OSError: An entry cannot be inspected or read.
    """
    result: dict[str, dict[str, str | int]] = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if skip_operational and is_excluded(name):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ArchiveError(f"Symlink escapes archive boundary: {name}.")
            if not path.exists():
                raise ArchiveError(f"Broken symlink: {name}")
            result[name] = {"type": "link", "target": target}
        elif path.is_file():
            result[name] = {"type": "file", "size": path.stat().st_size, "sha256": digest(path)}
        elif path.is_dir():
            result[name] = {"type": "dir"}
        else:
            raise ArchiveError(f"Unsupported file type: {name}")
    return result


def snapshot(root: Path, payload: Path, plan: ArchivePlan, provenance: dict[str, object] | None = None) -> Manifest:
    """Copy a stable run tree and write its content manifest and provenance.

    :param root: Root directory of the workspace or snapshot.
    :param payload: New private snapshot directory.
    :param plan: Required paths and restore checks.
    :param provenance: Provisioning and software metadata.
    :returns: The manifest written to the snapshot directory.
    :raises ArchiveError: The workspace or plan is invalid, the destination exists, or the copy differs.
    :raises OSError: The workspace cannot be read or the snapshot cannot be written.
    :raises TypeError: Provenance contains a value that cannot be serialized to JSON.
    """
    if root.is_symlink() or not root.is_dir():
        raise ArchiveError("Expected a staged workspace directory.")
    validate_plan(root, plan)
    if payload.exists():
        raise ArchiveError("Snapshot destination already exists.")
    payload.mkdir(mode=0o700)

    def ignore(directory: str, names: list[str]) -> list[str]:
        """Select operational paths to omit from the private snapshot.

        :param directory: Directory currently visited by copytree.
        :param names: Child entries available to copy.
        :returns: Child names that copytree should omit.
        """
        return [name for name in names if is_excluded((Path(directory) / name).relative_to(root).as_posix())]

    shutil.copytree(root, payload / "run", symlinks=True, ignore=ignore)
    # Compare both sides after copying: another writer must not change the run during archival.
    before = inventory(root, skip_operational=True)
    after = inventory(payload / "run")
    if before != after:
        raise ArchiveError("Run changed while copying. Stop all writers and retry.")
    metadata: Manifest = {
        "version": 1,
        "excluded_operational_paths": [*EXCLUDED_NAMES, *EXCLUDED],
        "handoff": plan,
        "provenance": provenance or {},
        "files": inventory(payload),
    }
    (payload / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def verify_tree(root: Path) -> int:
    """Compare restored contents with the manifest and required-path contract.

    :param root: Root directory of the workspace or snapshot.
    :returns: Number of verified entries, excluding the manifest itself.
    :raises ArchiveError: The inventory or required paths fail verification.
    :raises OSError: The restored files or manifest cannot be read.
    :raises ValueError: The manifest is not valid JSON.
    :raises KeyError: A required manifest field is missing.
    """
    manifest = json.loads((root / "manifest.json").read_text())
    actual = inventory(root)
    actual.pop("manifest.json", None)
    if actual != manifest["files"]:
        raise ArchiveError("Restored content does not match the file manifest.")
    validate_plan(root / "run", manifest["handoff"])
    return len(actual)


def restore(bundle: Path, destination: Path) -> int:
    """Extract into a new directory after rejecting unsafe archive members.

    :param bundle: Local archive file.
    :param destination: New directory receiving verified archive contents.
    :returns: Number of verified entries restored, excluding the manifest.
    :raises ArchiveError: The destination exists or an entry, manifest or required path is unsafe or inconsistent.
    :raises tarfile.TarError: The bundle is unreadable as a tar archive or extraction rejects a member.
    :raises OSError: The archive cannot be read or the restored tree cannot be written.
    :raises ValueError: The manifest is not valid JSON.
    :raises KeyError: A required manifest field is missing.
    """
    if destination.exists():
        raise ArchiveError("Restore destination must not exist.")
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".crux-restore-") as temp:
        requested = destination
        destination = Path(temp) / "archive"
        destination.mkdir(mode=0o700)
        with tarfile.open(bundle, "r:gz") as tar:
            for item in tar.getmembers():
                relative(item.name)
                if not (item.isfile() or item.isdir() or item.issym()):
                    raise ArchiveError("Archive contains an unsupported entry.")
                if item.issym() and (
                    Path(item.linkname).is_absolute()
                    or not (destination / item.name)
                    .parent.joinpath(item.linkname)
                    .resolve()
                    .is_relative_to(destination.resolve())
                ):
                    raise ArchiveError("Archive contains an unsafe link.")
            tar.extractall(destination, filter="data")
        count = verify_tree(destination)
        destination.rename(requested)
        return count


def create_bundle(root: Path, output: Path, plan: ArchivePlan, provenance: dict[str, object]) -> None:
    """Package a private snapshot and test its restoration locally.

    :param root: Operator-reviewed staged workspace.
    :param output: New bundle file in private scratch storage.
    :param plan: Required paths and restore checks.
    :param provenance: Metadata collected during the copy.
    :raises ArchiveError: Snapshot or restore verification fails.
    :raises OSError: The output already exists or required files cannot be read or written.
    :raises tarfile.TarError: The bundle cannot be written or extracted.
    :raises TypeError: Provenance contains a value that cannot be serialized to JSON.
    """
    with tempfile.TemporaryDirectory(prefix="crux-archive-") as temp:
        payload = Path(temp) / "payload"
        snapshot(root, payload, plan, provenance)
        verify_tree(payload)
        with (
            output.open("xb") as stream,
            gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as tar,
        ):
            for path in sorted(payload.rglob("*")):
                entry = tar.gettarinfo(str(path), path.relative_to(payload).as_posix())
                entry.uid = entry.gid = 0
                entry.uname = entry.gname = ""
                if path == payload / "manifest.json":
                    entry.mtime = 0
                if entry.isfile():
                    with path.open("rb") as source:
                        tar.addfile(entry, source)
                else:
                    tar.addfile(entry)
        restored = Path(temp) / "restored"
        restore(output, restored)
    os.chmod(output, 0o600)
