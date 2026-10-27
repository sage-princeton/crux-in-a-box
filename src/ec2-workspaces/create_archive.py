#!/usr/bin/env python3
"""Create and verify a stopped workspace archive before transferring it.

Usage::

    sudo /opt/crux-archive/bin/python create_archive.py capture /var/tmp/run.tar.gz
    python3 create_archive.py restore run.tar.gz --destination /private/tmp/restored

The capture command runs on the workspace. Restore never executes archived code.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from importlib.metadata import version
from pathlib import Path

EXCLUDED = (".mcp.json", ".aws", ".ssh", "state/sessions",
            "state/sessions.sqlite", "state/sessions.sqlite-wal", "state/sessions.sqlite-shm")


class ArchiveError(Exception):
    pass


def digest(path):
    """Compute the SHA-256 digest without loading the whole file into memory.

    :param path: Filesystem path to inspect.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(value):
    """Validate a nonempty relative archive path and reject traversal.

    :param value: Value for this operation.
    """
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ArchiveError("Archive paths must be nonempty relative paths without '..'.")
    return path


def excluded(path):
    """Return whether a path belongs to an intentionally omitted operational store.

    :param path: Filesystem path to inspect.
    """
    return any(path == name or path.startswith(name + "/") for name in EXCLUDED)


def plan_for(root):
    """Validate required paths and external-state evidence in ARCHIVE.json.

    :param root: Root directory of the workspace or snapshot.
    """
    plan = json.loads((root / "ARCHIVE.json").read_text())
    if (not isinstance(plan, dict) or plan.get("version") != 1
            or not isinstance(plan.get("required_paths"), list) or not plan["required_paths"]
            or not isinstance(plan.get("restore_checks"), list) or not plan["restore_checks"]
            or not all(isinstance(value, str) and value for value in plan["required_paths"] + plan["restore_checks"])
            or not isinstance(plan.get("external_state"), list)):
        raise ArchiveError("ARCHIVE.json needs version=1, required_paths, restore_checks, external_state.")
    for value in plan["required_paths"]:
        path = relative(value)
        source = root / path
        if excluded(str(path)) or not source.exists() or (source.is_file() and not source.stat().st_size):
            raise ArchiveError(f"Required archive path is missing, empty or excluded: {path}")
        if source.is_dir() and not any(source.iterdir()):
            raise ArchiveError(f"Required archive directory is empty: {path}")
    for item in plan["external_state"]:
        if not isinstance(item, dict) or not all(item.get(key) for key in (
                "description", "location", "preservation", "evidence_path")):
            raise ArchiveError("Each external resource needs location, preservation plan and verification evidence.")
        evidence = root / relative(item["evidence_path"])
        if not evidence.is_file() or not evidence.stat().st_size or excluded(item["evidence_path"]):
            raise ArchiveError("External state verification evidence is missing or empty.")
    return plan


def inventory(root, skip_operational=False):
    """Describe files, directories and internal links for integrity verification.

    :param root: Root directory of the workspace or snapshot.
    :param skip_operational: Whether to omit excluded operational stores.
    """
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if skip_operational and excluded(name):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ArchiveError(f"Symlink escapes archive boundary: {name}. Declare external state in ARCHIVE.json.")
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


def snapshot(root, payload, provenance=None):
    """Copy a stable run tree and write its content manifest and provenance.

    :param root: Root directory of the workspace or snapshot.
    :param payload: New private snapshot directory.
    :param provenance: Provisioning and software metadata.
    """
    plan = plan_for(root)
    if payload.exists():
        raise ArchiveError("Snapshot destination already exists.")
    payload.mkdir(mode=0o700)
    def ignore(directory, names):
        """Select operational paths to omit from the private snapshot.

        :param directory: Directory for this operation.
        :param names: Names for this operation.
        """
        return [name for name in names if excluded((Path(directory) / name).relative_to(root).as_posix())]
    shutil.copytree(root, payload / "run", symlinks=True, ignore=ignore)
    # Compare both sides after copying: another writer must not change the run during archival.
    before = inventory(root, skip_operational=True)
    after = inventory(payload / "run")
    if before != after:
        raise ArchiveError("Run changed while copying. Stop all writers and retry.")
    repositories = {}
    for git in (payload / "run").rglob(".git"):
        if git.is_dir():
            def query(*args, directory=git.parent):
                """Read repository metadata without optional Git index writes.

                :param directory: Directory for this operation.
                :param args: Operation arguments.
                """
                result = subprocess.run(["git", "--no-optional-locks", "-C", str(directory), *args], capture_output=True,
                                        text=True, check=False, timeout=120)
                return result.stdout.strip() if result.returncode == 0 else None
            repositories[git.parent.relative_to(payload).as_posix()] = {
                "revision": query("rev-parse", "HEAD"), "dirty": bool(query("status", "--porcelain"))}
    metadata = {"version": 1, "excluded_operational_paths": list(EXCLUDED), "handoff": plan,
                "provenance": provenance or {}, "repositories": repositories, "files": inventory(payload)}
    (payload / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def verify_tree(root):
    """Compare restored contents with the manifest and required-path contract.

    :param root: Root directory of the workspace or snapshot.
    """
    manifest = json.loads((root / "manifest.json").read_text())
    actual = inventory(root)
    actual.pop("manifest.json", None)
    if actual != manifest["files"]:
        raise ArchiveError("Restored content does not match the file manifest.")
    plan_for(root / "run")
    return len(actual)


def scan(root):
    """Scan current files and every reachable Git blob through native Python APIs.

    :param root: Private snapshot directory; failures abort archival.
    :raises ArchiveError: A pattern matches or any read/scanner operation fails.
    """
    try:
        import git
        from trufflehog3 import DEFAULT_RULES_FILE
        from trufflehog3.core import load_rules
        from trufflehog3.models import File, Pattern
        from trufflehog3.search import search
        rules = [rule for rule in load_rules(DEFAULT_RULES_FILE) if isinstance(rule, Pattern)]
        if not rules:
            raise ValueError("No scanner rules")
        def inspect(name, data):
            """Inspect bytes without allowing inline suppressions or printing secrets."""
            if search(File(path=name, content=data.decode("utf-8", errors="replace")),
                      rules, ignore_nosecret=True):
                raise ArchiveError("Secrets detected in " + json.dumps(name) +
                                   ". Preserve credentials privately, remove values from files/history, then retry.")
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(root).parts:
                inspect(path.relative_to(root).as_posix(), path.read_bytes())
        for gitdir in root.rglob(".git"):
            if not gitdir.is_dir():
                raise ArchiveError("Make linked Git worktrees self-contained before archiving.")
            repo = git.Repo(gitdir.parent)
            seen = set()
            # Read local objects only: no fetch, checkout or repository scan configuration.
            for commit in repo.iter_commits("--all"):
                for blob in commit.tree.traverse():
                    if blob.type == "blob" and blob.hexsha not in seen:
                        seen.add(blob.hexsha)
                        inspect(gitdir.parent.relative_to(root).as_posix() + "/" + blob.path,
                                blob.data_stream.read())
    except ArchiveError:
        raise
    except Exception:  # noqa: BLE001 - never expose scanner diagnostics containing secrets.
        raise ArchiveError("Secret scan failed; archive blocked. Check scanner installation and readable Git objects.") from None


def restore(bundle, destination):
    """Extract into a new directory after rejecting unsafe archive members.

    :param bundle: Local archive file.
    :param destination: New directory receiving verified archive contents.
    """
    if destination.exists():
        raise ArchiveError("Restore destination must not exist.")
    destination.mkdir(mode=0o700)
    with tarfile.open(bundle, "r:gz") as tar:
        for item in tar.getmembers():
            relative(item.name)
            if not (item.isfile() or item.isdir() or item.issym()):
                raise ArchiveError("Archive contains an unsupported entry.")
            if item.issym() and (Path(item.linkname).is_absolute()
                                or not (destination / item.name).parent.joinpath(item.linkname).resolve()
                                .is_relative_to(destination.resolve())):
                raise ArchiveError("Archive contains an unsafe link.")
        tar.extractall(destination, filter="data")
    return verify_tree(destination)


def remote_capture(root, output):
    """Create, scan and restore-check a stopped run while preventing service starts.

    :param root: Root directory of the workspace or snapshot.
    :param output: New archive output path.
    """
    os.umask(0o077)
    for service in ("crux-sdk-run", "crux-acp-gateway"):
        state = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, check=False)
        if state.stdout.strip() not in ("inactive", "failed", "unknown"):
            raise ArchiveError(f"{service} is active or changing state. Finish/pause the task safely, stop it, then retry.")
    # A runtime condition also applies to locally installed units, unlike a /run mask shadowed by /etc.
    Path("/run/crux-archive.lock").touch(mode=0o600)
    for service in ("crux-sdk-run", "crux-acp-gateway"):
        drop_in = Path(f"/run/systemd/system/{service}.service.d")
        drop_in.mkdir(parents=True, exist_ok=True)
        (drop_in / "archive.conf").write_text("[Unit]\nConditionPathExists=!/run/crux-archive.lock\n")
    subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
    provenance = {}
    for name, source in (("provision", "/etc/crux-source.json"), ("software", "/etc/crux-provision.json")):
        if Path(source).is_file():
            provenance[name] = json.loads(Path(source).read_text())
    provenance["runtime"] = {}
    for name, command in {"python": ["python3", "--version"], "node": ["node", "--version"],
                          "git": ["git", "--version"]}.items():
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        provenance["runtime"][name] = (result.stdout + result.stderr).strip()
    provenance["runtime"]["trufflehog3"] = version("trufflehog3")
    with tempfile.TemporaryDirectory(prefix="crux-archive-") as temp:
        payload = Path(temp) / "payload"
        snapshot(root, payload, provenance)
        scan(payload)
        verify_tree(payload)
        with output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for path in sorted(payload.rglob("*")):
                tar.add(path, arcname=path.relative_to(payload).as_posix(), recursive=False)
        restored = Path(temp) / "restored"
        restore(output, restored)
    os.chmod(output, 0o600)
    print(json.dumps({"sha256": digest(output), "bytes": output.stat().st_size}))


def main(argv=None):
    """Create on the stopped box or extract locally; see ``--help`` for arguments.

    :param argv: Optional CLI argument sequence.
    :returns: Zero on verified completion, one on failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "restore"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/srv/crux-run"))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "capture":
            remote_capture(args.root, args.bundle)
        elif args.destination:
            restore(args.bundle, args.destination)
        else:
            raise ArchiveError("Restore requires --destination PATH.")
    except (ArchiveError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(str(exc) if isinstance(exc, ArchiveError) else "Archive operation failed; teardown blocked.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
