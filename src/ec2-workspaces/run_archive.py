#!/usr/bin/env python3
"""Snapshot and verify a run on its box, before any archive leaves the box."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

EXCLUDED = (".mcp.json", ".codex", ".claude", ".aws", ".ssh", "state/codex", "state/sessions",
            "state/sessions.sqlite", "state/sessions.sqlite-wal", "state/sessions.sqlite-shm")


class ArchiveError(Exception):
    pass


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(value):
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ArchiveError("Archive paths must be nonempty relative paths without '..'.")
    return path


def excluded(path):
    return any(path == name or path.startswith(name + "/") for name in EXCLUDED)


def plan_for(root):
    plan = json.loads((root / "ARCHIVE.json").read_text())
    if (not isinstance(plan, dict) or plan.get("version") != 1
            or not isinstance(plan.get("required_paths"), list) or not plan["required_paths"]
            or not isinstance(plan.get("restore_checks"), list) or not plan["restore_checks"]
            or not all(isinstance(value, str) and value for value in plan["required_paths"] + plan["restore_checks"])
            or not isinstance(plan.get("external_state"), list) or not isinstance(plan.get("secret_refs"), list)):
        raise ArchiveError("ARCHIVE.json needs version=1, required_paths, restore_checks, external_state and secret_refs.")
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
    for item in plan["secret_refs"]:
        if (not isinstance(item, dict) or set(item) != {"arn", "region", "version_id"}
                or not all(isinstance(value, str) and value for value in item.values())):
            raise ArchiveError("Each Secrets Manager reference needs arn, region and version_id; never include values.")
    return plan


def inventory(root, skip_operational=False):
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
    plan = plan_for(root)
    if payload.exists():
        raise ArchiveError("Snapshot destination already exists.")
    payload.mkdir(mode=0o700)
    def ignore(directory, names):
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
    manifest = json.loads((root / "manifest.json").read_text())
    actual = inventory(root)
    actual.pop("manifest.json", None)
    if actual != manifest["files"]:
        raise ArchiveError("Restored content does not match the file manifest.")
    plan_for(root / "run")
    return len(actual)


def scan(root):
    commands = [["filesystem", str(root)]]
    for git in root.rglob(".git"):
        if not git.is_dir():
            raise ArchiveError("Git worktrees must be made self-contained before archiving (.git is not a directory).")
        commands.append(["git", git.parent.as_uri()])
    for command in commands:
        result = subprocess.run(["trufflehog", *command, "--json", "--no-update", "--no-verification",
                                 "--fail", "--fail-on-scan-errors"], capture_output=True, text=True, timeout=7200,
                                check=False)
        locations = set()
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)["SourceMetadata"]["Data"]
                for value in data.values():
                    if isinstance(value, dict):
                        location = value.get("file", "(history or unknown path)")
                        locations.add(str(location).replace(str(root) + "/", ""))
            except (KeyError, TypeError, ValueError):
                raise ArchiveError("TruffleHog returned invalid output; archive blocked.") from None
        if locations or result.returncode == 183:
            safe = ", ".join(json.dumps(value) for value in sorted(locations)) or "(unknown path)"
            raise ArchiveError(f"Secrets detected in {safe}. Save credentials in a secret manager, remove them "
                               "from files AND Git history, then retry. No secret values were printed.")
        if result.returncode:
            raise ArchiveError("TruffleHog failed; archive blocked. Check installation, permissions and scan limits.")


def restore(bundle, destination):
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
                          "trufflehog": ["trufflehog", "--version"]}.items():
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        provenance["runtime"][name] = (result.stdout + result.stderr).strip()
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


if __name__ == "__main__":
    try:
        if sys.argv[1] == "capture":
            remote_capture(Path("/srv/crux-run"), Path(sys.argv[2]))
        elif sys.argv[1] == "restore":
            print(restore(Path(sys.argv[2]), Path(sys.argv[3])))
        else:
            raise ArchiveError("Expected capture or restore.")
    except (ArchiveError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as exc:
        # Only ArchiveError carries messages designed to be safe for operator logs.
        print("CRUX_ARCHIVE_ERROR: " + (str(exc) if isinstance(exc, ArchiveError)
              else "Archive operation failed; no teardown performed."),
              file=sys.stderr)
        sys.exit(1)
