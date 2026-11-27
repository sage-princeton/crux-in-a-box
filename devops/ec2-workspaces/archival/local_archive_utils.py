"""Build and verify workspace bundles for the manual archive CLI."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, NotRequired, TypedDict

DETECTORS_FILE = Path(__file__).with_name("detectors.yml")
EXCLUDED_NAMES = (".ssh", ".aws", ".mcp.json", "auth.json", ".credentials.json")
EXCLUDED = (
    "state/sessions",
    "state/sessions.sqlite",
    "state/sessions.sqlite-wal",
    "state/sessions.sqlite-shm",
)


class CopyOptions(TypedDict, total=False):
    """Describe optional selection and symlink handling during Run archival."""

    excluded_paths: list[str]
    portable_symlinks: bool


class ArchivePlan(CopyOptions):
    """Describe the workspace paths and restore checks declared by the operator."""

    plan_version: int
    required_paths: list[str]
    restore_checks: list[str]


class RepositoryState(TypedDict):
    """Record the current Git revision and whether the working tree has changes."""

    revision: str | None
    dirty: bool


class Manifest(TypedDict):
    """Record the restore contract, file inventory, and repository state for a snapshot."""

    version: int
    excluded_operational_paths: list[str]
    handoff: ArchivePlan
    provenance: dict[str, object]
    files: dict[str, dict[str, str | int]]
    repositories: NotRequired[dict[str, RepositoryState]]


class ArchiveError(Exception):
    """Report an archive contract violation without including file contents."""


class SecretsFound(ArchiveError):
    """Signal a finding that requires operator redaction before upload."""


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


def copy_options(plan: Mapping[str, object]) -> CopyOptions:
    """Validate optional transfer settings and check exclusions against required paths.

    :param plan: Operator's archive plan or the handoff in a restored manifest.
    :returns: Non-default settings to persist in the stage and verified manifest.
    :raises ArchiveError: An option is malformed or a pattern is unsafe or omits required evidence.
    """
    paths = plan.get("excluded_paths", [])
    required = plan.get("required_paths")
    if (
        not isinstance(paths, list)
        or not isinstance(required, list)
        or not all(isinstance(item, str) for item in required)
    ):
        raise ArchiveError("excluded_paths and required_paths must be lists of strings.")
    required_paths = [Path("/") / relative(item) for item in required]
    for name in paths:
        if not isinstance(name, str) or not name.isprintable() or "**" in name or any(c in name for c in "?[]\\"):
            raise ArchiveError("excluded_paths supports relative path components with a single-level '*' wildcard.")
        path = relative(name)
        if path.as_posix() != name or ".git" in path.parts:
            raise ArchiveError("Use normalized relative exclusions outside Git metadata.")
        if any(part.match("/" + name) for target in required_paths for part in (target, *target.parents)):
            raise ArchiveError(f"Required archive path overlaps an exclusion: {name}")
    portable = plan.get("portable_symlinks", False)
    if not isinstance(portable, bool):
        raise ArchiveError("portable_symlinks must be a boolean.")
    options: CopyOptions = {}
    if portable:
        options["portable_symlinks"] = True
    if paths:
        options["excluded_paths"] = paths
    return options


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
    :raises ArchiveError: The plan is malformed, an excluded path is present or required evidence is unsafe or missing.
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
    for omitted in copy_options(plan).get("excluded_paths", []):
        if next(root.glob(omitted), None) is not None:
            raise ArchiveError(f"Excluded path is present in the archive: {omitted}")
    for value in plan["required_paths"]:
        path = relative(value)
        source = root / path
        if is_excluded(str(path)) or not source.exists() or (source.is_file() and not source.stat().st_size):
            raise ArchiveError(f"Required archive path is missing, empty or excluded: {path}")
        if source.is_dir() and not any(source.iterdir()):
            raise ArchiveError(f"Required archive directory is empty: {path}")


def inventory(
    root: Path, skip_operational: bool = False, *, allow_dangling: bool = False
) -> dict[str, dict[str, str | int]]:
    """Describe files, directories and internal links for integrity verification.

    :param root: Root directory of the workspace or snapshot.
    :param skip_operational: Whether to omit excluded operational stores.
    :param allow_dangling: Whether to record internal links whose targets are absent.
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
            if not allow_dangling and not path.exists():
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
    portable = plan.get("portable_symlinks", False)
    before = inventory(root, skip_operational=True, allow_dangling=portable)
    after = inventory(payload / "run", allow_dangling=portable)
    if before != after:
        raise ArchiveError("Run changed while copying. Stop all writers and retry.")
    metadata: Manifest = {
        "version": 1,
        "excluded_operational_paths": [*EXCLUDED_NAMES, *EXCLUDED],
        "handoff": plan,
        "provenance": provenance or {},
        "files": inventory(payload, allow_dangling=portable),
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
    actual = inventory(root, allow_dangling=manifest["handoff"].get("portable_symlinks") is True)
    actual.pop("manifest.json", None)
    if actual != manifest["files"]:
        raise ArchiveError("Restored content does not match the file manifest.")
    validate_plan(root / "run", manifest["handoff"])
    return len(actual)


def scan_repository(gitdir: Path, decoded: Path) -> RepositoryState:
    """Inspect all Git objects using a private Git directory with no copied configuration.

    :param gitdir: Self-contained metadata directory in the snapshot.
    :param decoded: Private directory receiving decoded objects for TruffleHog.
    :returns: HEAD revision and whether the copied worktree has changes.
    :raises ArchiveError: The repository depends on external metadata or an object cannot be read completely.
    :raises subprocess.CalledProcessError: Git integrity or status checks fail.
    :raises OSError: Git cannot start or the repository or temporary files cannot be accessed.
    """
    objects = gitdir / "objects"
    if (
        not objects.is_dir()
        or (objects / "info/alternates").exists()
        or (gitdir / "commondir").exists()
        or any(p.is_symlink() for p in gitdir.rglob("*"))
    ):
        raise ArchiveError("Make Git metadata and objects self-contained before archiving.")
    with tempfile.TemporaryDirectory(prefix="crux-archive-git-") as temp:
        view = Path(temp)
        (view / "objects").mkdir()
        shutil.copytree(gitdir / "refs", view / "refs")
        for name in ("HEAD", "packed-refs", "shallow", "index"):
            if (gitdir / name).is_file():
                shutil.copyfile(gitdir / name, view / name)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": temp,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OBJECT_DIRECTORY": str(objects.resolve()),
        }
        git = ["git", "--no-optional-locks", "--git-dir", temp, "--work-tree", str(gitdir.parent)]
        subprocess.run([*git, "fsck", "--full", "--no-reflogs"], env=env, capture_output=True, check=True)
        with subprocess.Popen(
            [*git, "cat-file", "--batch-all-objects", "--batch"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as proc:
            assert proc.stdout is not None  # Opened with stdout=PIPE.
            try:
                for header in iter(proc.stdout.readline, b""):
                    oid, _, size = header.split()
                    remaining = int(size)
                    with (decoded / oid.decode("ascii")).open("wb") as out:
                        while remaining:
                            chunk = proc.stdout.read(min(remaining, 1024 * 1024))
                            if not chunk:
                                raise ArchiveError("Truncated Git object.")
                            out.write(chunk)
                            remaining -= len(chunk)
                    if proc.stdout.read(1) != b"\n":
                        raise ArchiveError("Invalid Git object boundary.")
            finally:
                proc.stdout.close()
            if proc.wait():
                raise ArchiveError("Git object scan failed.")
        head = subprocess.run([*git, "rev-parse", "--verify", "HEAD"], env=env, capture_output=True, check=False)
        status = subprocess.run(
            [*git, "status", "--porcelain", "--ignore-submodules=all"], env=env, capture_output=True, check=True
        )
        return {"revision": head.stdout.decode().strip() or None, "dirty": bool(status.stdout)}


def scanner_config(values: Sequence[str]) -> bytes:
    """Combine shared and literal detectors for the CLI's private configuration pipe.

    :param values: Provisioned credential values to match anywhere in the archive.
    :returns: UTF-8 JSON accepted by TruffleHog's YAML reader.
    :raises ArchiveError: The shared detector configuration is missing or malformed.
    """
    import yaml

    try:
        rules = yaml.safe_load(DETECTORS_FILE.read_text())["detectors"]
        if not isinstance(rules, list) or not rules:
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
        raise ArchiveError("Cannot read shared detectors.yml.") from None
    for index, value in enumerate(sorted(set(values))):
        # RE2 quotes the whole value literally, including embedded end-quote sequences.
        literal = r"\Q" + value.replace(r"\E", r"\E\\E\Q") + r"\E"
        rules.append({"name": f"ProvisionedSecret{index}", "keywords": [value], "regex": {"token": literal}})
    return json.dumps({"detectors": rules}).encode()


def trufflehog(paths: Sequence[Path], config: bytes, exclude: Path, output: int | IO[bytes]) -> bool:
    """Scan local paths offline and direct native findings to a pipe or the null device.

    :param paths: Paths to inspect without following symlinks.
    :param config: Scanner configuration passed only on standard input.
    :param exclude: File of native filesystem exclusion patterns.
    :param output: Private destination for native JSON findings.
    :returns: Whether any detector reported a credential.
    :raises ArchiveError: The CLI cannot start or reports an incomplete scan.
    """
    args = [
        "trufflehog",
        "filesystem",
        "--no-verification",
        "--no-update",
        "--no-ignore-tag",
        "--json",
        "--results=verified,unknown,unverified,filtered_unverified",
        "--fail",
        "--fail-on-scan-errors",
        "--max-symlink-depth=0",
        "--config=/dev/stdin",
        "--exclude-paths",
        str(exclude),
        *map(str, paths),
    ]
    try:
        result = subprocess.run(args, input=config, stdout=output, stderr=subprocess.PIPE, check=False)
        # The filesystem source logs some per-file errors without setting the exit status.
        for line in result.stderr.splitlines():
            entry = json.loads(line)
            if entry.get("level") == "error" and entry.get("msg") not in {
                "error cleaning temporary artifacts",
                "error cleaning temp artifacts",
            }:
                raise ArchiveError("TruffleHog reported an incomplete scan.")
    except (ValueError, OSError):
        raise ArchiveError("Cannot run TruffleHog. Check the CLI installation and detectors.yml.") from None
    if result.returncode not in (0, 183):
        raise ArchiveError("TruffleHog failed. Check detectors.yml and staged file permissions.")
    return result.returncode == 183


def redact_files(root: Path, config: bytes) -> None:
    """Pipe native working-file findings to the interactive redactor.

    :param root: Local Run directory the operator may edit.
    :param config: The same detector configuration used for upload.
    :raises ArchiveError: A file, scanner or redactor could not be used.
    :raises OSError: Private scratch storage or the redactor is unavailable.
    """
    if root.is_symlink() or not root.is_dir():
        raise ArchiveError("Expected a staged workspace directory.")
    with tempfile.TemporaryDirectory() as temp:
        exclude = Path(temp) / "exclude"
        names = "|".join(re.escape(name) for name in (".git", *EXCLUDED_NAMES))
        patterns = [rf"/({names})(/|$)", *(rf"^{re.escape(str(root / path))}(/|$)" for path in EXCLUDED)]
        exclude.write_text("\n".join(patterns) + "\n")
        with subprocess.Popen(["trufflehog-redactor"], stdin=subprocess.PIPE) as redactor:
            assert redactor.stdin is not None
            try:
                trufflehog([root], config, exclude, redactor.stdin)
            except BaseException:
                redactor.terminate()
                raise
            finally:
                redactor.stdin.close()
            if redactor.wait():
                raise ArchiveError("Redaction did not finish. Retry from a private interactive terminal.")


def scan(root: Path, config: bytes) -> dict[str, RepositoryState]:
    """Scan the snapshot, including metadata and decoded objects from every Git worktree.

    :param root: Private snapshot to inspect.
    :param config: Shared and exact-value TruffleHog detectors.
    :returns: Revision and working-tree status for each self-contained repository.
    :raises SecretsFound: A finding requires redaction.
    :raises ArchiveError: A file, rule or Git object could not be inspected.
    """
    try:
        repositories = {}
        with tempfile.TemporaryDirectory(prefix="crux-objects-") as temp:
            decoded = Path(temp) / "objects"
            decoded.mkdir()
            exclude = Path(temp) / "exclude"
            exclude.write_text(r"/\.git/objects/([a-f0-9]{2}/[a-f0-9]{38,62}|pack/pack-[0-9a-f]+\.pack)$" + "\n")
            for path in root.rglob("*"):
                if path.name == ".git":
                    if not path.is_dir() or path.is_symlink():
                        raise ArchiveError("Make the Git repository self-contained before archiving.")
                    repositories[str(path.parent.relative_to(root))] = scan_repository(path, decoded)
                elif path.name == "objects" and path.parent.name != ".git" and (path.parent / "HEAD").is_file():
                    raise ArchiveError("Convert bare or separate Git object stores to self-contained worktrees.")
            targets = [(root, "staged files or metadata")]
            if repositories:
                targets.append((decoded, "Git history"))
            for target, location in targets:
                if trufflehog([target], config, exclude, subprocess.DEVNULL):
                    raise SecretsFound(
                        f"Secret detected in {location}. Review staged files with trufflehog-redactor, "
                        "clean affected metadata or Git history, then retry upload."
                    )
        return repositories
    except ArchiveError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ArchiveError("Cannot inspect staged files or Git objects.") from None


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


def create_bundle(root: Path, output: Path, plan: ArchivePlan, provenance: dict[str, object], config: bytes) -> None:
    """Scan a private snapshot, package it and test its restoration locally.

    :param root: Staged workspace, including any operator redactions.
    :param output: New bundle file in private scratch storage.
    :param plan: Required paths and restore checks.
    :param provenance: Metadata collected during the copy.
    :param config: Shared and exact-value TruffleHog detectors.
    :raises SecretsFound: A working file or Git object requires redaction.
    :raises ArchiveError: Snapshot, scanner or restore verification fails.
    :raises OSError: The output already exists or required files cannot be read or written.
    :raises tarfile.TarError: The bundle cannot be written or extracted.
    :raises TypeError: Provenance contains a value that cannot be serialized to JSON.
    """
    with tempfile.TemporaryDirectory(prefix="crux-archive-") as temp:
        payload = Path(temp) / "payload"
        metadata = snapshot(root, payload, plan, provenance)
        metadata["repositories"] = scan(payload, config)
        (payload / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
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
