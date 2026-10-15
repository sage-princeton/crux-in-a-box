#!/usr/bin/env python3
"""Install declared software, seed protected authentication, and check a run."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

from run_service_auth import ServiceError, configure_services, service_checks, validate_auth, validate_services


DEFAULT_TOOLS_DIR = "/opt/crux-run-tools/bin"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?(?:=[A-Za-z0-9.+:~_-]+)?\Z")


class ProvisionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ProvisionError(message)


def fields(value, allowed, required=()):
    require(isinstance(value, dict) and not value.keys() - set(allowed)
            and set(required) <= value.keys(), "Invalid provision.json fields.")


def load_manifest(path):
    if not Path(path).exists():
        return {"version": 1}
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ProvisionError("Cannot read provision.json as JSON.") from None
    fields(manifest, ("version", "apt_packages", "tools", "checks", "services"), ("version",))
    require(type(manifest["version"]) is int and manifest["version"] == 1,
            "provision.json version must be 1.")
    for key in ("apt_packages", "tools", "checks"):
        require(isinstance(manifest.get(key, []), list), f"{key} must be a list.")
    for package in manifest.get("apt_packages", []):
        require(isinstance(package, str) and PACKAGE.fullmatch(package), "Invalid apt package specification.")
    for kind in ("tools", "checks"):
        names = set()
        for item in manifest.get(kind, []):
            allowed = ("name", "url", "sha256", "member") if kind == "tools" else ("name", "argv", "timeout_seconds", "hint")
            required = ("name", "url", "sha256") if kind == "tools" else ("name", "argv")
            fields(item, allowed, required)
            name = item["name"]
            require(isinstance(name, str) and IDENTIFIER.fullmatch(name) and name not in names,
                    f"Invalid or duplicate {kind} name.")
            names.add(name)
            if kind == "tools":
                require(isinstance(item["url"], str), f"Tool {name}: invalid download URL.")
                try:
                    url = urllib.parse.urlsplit(item["url"])
                    valid_url = url.scheme == "https" and url.hostname and not (url.username or url.password or url.query or url.fragment)
                except ValueError:
                    valid_url = False
                require(valid_url, f"Tool {name}: use an HTTPS URL without credentials, query, or fragment.")
                require(isinstance(item["sha256"], str) and re.fullmatch(r"[a-fA-F0-9]{64}", item["sha256"]),
                        f"Tool {name}: a SHA-256 digest is required.")
                if "member" in item:
                    member = item["member"]
                    require(isinstance(member, str) and member and not member.startswith("/")
                            and ".." not in PurePosixPath(member).parts, f"Tool {name}: invalid archive member.")
            else:
                argv = item["argv"]
                require(isinstance(argv, list) and argv and all(isinstance(arg, str) and "\0" not in arg for arg in argv)
                        and argv[0], f"Check {name}: argv must contain an executable and string arguments.")
                timeout = item.get("timeout_seconds", 30)
                require(type(timeout) is int and 1 <= timeout <= 300, f"Check {name}: timeout must be 1–300 seconds.")
                require(isinstance(item.get("hint", ""), str), f"Check {name}: hint must be a string.")
    validate_services(manifest.get("services", []))
    return manifest


def environment(tools_dir):
    env = os.environ.copy()
    paths = env.get("PATH", os.defpath).split(os.pathsep)
    env["PATH"] = os.pathsep.join([str(tools_dir), *(path for path in paths if path != str(tools_dir))])
    return env


def run_quiet(argv, *, timeout, env=None):
    subprocess.run(argv, check=True, timeout=timeout, env=env,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def download(url, target):
    with urllib.request.urlopen(url, timeout=60) as response, Path(target).open("wb") as output:
        require(urllib.parse.urlsplit(response.geturl()).scheme == "https", "Download redirected away from HTTPS.")
        shutil.copyfileobj(response, output)


def install_tool(tool, tools_dir):
    target = tools_dir / tool["name"]
    receipt = tools_dir / ("." + tool["name"] + ".json")
    source_digest = tool["sha256"].lower()
    try:
        saved = json.loads(receipt.read_text())
        if (saved.get("source_sha256") == source_digest and saved.get("member") == tool.get("member")
                and not target.is_symlink() and target.is_file() and os.access(target, os.X_OK)
                and saved.get("binary_sha256") == digest(target)):
            return
    except (OSError, ValueError, AttributeError):
        pass
    with tempfile.TemporaryDirectory(prefix=".install-", dir=tools_dir) as temporary:
        temporary = Path(temporary)
        archive, binary = temporary / "download", temporary / "binary"
        download(tool["url"], archive)
        require(digest(archive) == source_digest, "Download SHA-256 mismatch.")
        if "member" in tool:
            with tarfile.open(archive, "r:gz") as bundle:
                matches = [entry for entry in bundle if entry.name == tool["member"]]
                require(len(matches) == 1 and matches[0].isreg(), "Archive must contain one regular file matching member.")
                with bundle.extractfile(matches[0]) as source, binary.open("wb") as output:
                    shutil.copyfileobj(source, output)
        else:
            archive.rename(binary)
        saved = {"source_sha256": source_digest, "member": tool.get("member"), "binary_sha256": digest(binary)}
        temporary_receipt = temporary / "receipt"
        temporary_receipt.write_text(json.dumps(saved))
        binary.chmod(0o755)
        os.replace(binary, target)
        os.replace(temporary_receipt, receipt)


def install(manifest, tools_dir):
    packages = manifest.get("apt_packages", [])
    if packages:
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        try:
            run_quiet(["apt-get", "update"], timeout=600, env=env)
            run_quiet(["apt-get", "install", "--yes", "--no-install-recommends", "--", *packages], timeout=600, env=env)
        except (OSError, subprocess.SubprocessError):
            raise ProvisionError("Run apt packages could not be installed. Re-run install-run.sh.") from None
    if manifest.get("tools"):
        tools_dir = Path(tools_dir)
        tools_dir.mkdir(parents=True, exist_ok=True)
        for tool in manifest["tools"]:
            try:
                install_tool(tool, tools_dir)
            except (OSError, ValueError, http.client.HTTPException, tarfile.TarError, ProvisionError):
                raise ProvisionError(f"Tool {tool['name']} could not be installed; verify its URL, digest, and member. Re-run install-run.sh.") from None


def check(manifest, tools_dir):
    env = environment(tools_dir)
    for tool in manifest.get("tools", []):
        require(shutil.which(tool["name"], path=env["PATH"]),
                f"Tool {tool['name']} is unavailable. Re-run install-run.sh.")
    for probe in [*manifest.get("checks", []), *service_checks(manifest.get("services", []), env)]:
        try:
            run_quiet(probe["argv"], timeout=probe.get("timeout_seconds", 30), env=env)
        except (OSError, subprocess.SubprocessError):
            raise ProvisionError(f"Check {probe['name']} failed. {probe.get('hint', 'Re-run install-run.sh.')}".rstrip()) from None
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "install", "configure", "check", "exec"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--tools-dir", default=DEFAULT_TOOLS_DIR)
    parser.add_argument("--secrets")
    parser.add_argument("--run-user", default="ubuntu")
    parser.add_argument("--env-file", default="/etc/crux-run.env")
    args, command = parser.parse_known_args(argv)
    if args.action == "exec":
        if len(command) < 2 or command[0] != "--":
            parser.error("exec requires -- followed by the agent command")
    elif command:
        parser.error("unexpected arguments")
    try:
        manifest = load_manifest(args.manifest)
        secrets = None
        if args.secrets:
            try:
                secrets = json.loads(Path(args.secrets).read_text())
            except (OSError, ValueError):
                raise ProvisionError("Cannot read service authentication JSON.") from None
            validate_auth(manifest.get("services", []), secrets)
        if args.action == "configure":
            require(secrets is not None, "configure requires --secrets (use /dev/stdin for protected delivery).")
            try:
                user = pwd.getpwnam(args.run_user)
            except KeyError:
                raise ProvisionError("The run user does not exist.") from None
            configure_services(manifest.get("services", []), secrets, Path(user.pw_dir),
                               user.pw_uid, user.pw_gid, Path(args.env_file))
        elif args.action == "install":
            install(manifest, args.tools_dir)
        elif args.action in ("check", "exec"):
            env = check(manifest, args.tools_dir)
            if args.action == "exec":
                os.execvpe(command[1], command[1:], env)
    except (ProvisionError, ServiceError) as error:
        print(f"Run provisioning: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("Run provisioning: operation failed; verify paths and permissions.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
