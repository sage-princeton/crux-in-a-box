"""Seed optional service credentials without replacing refreshed authentication."""

import base64
import binascii
import gzip
import io
import json
import os
import re
import secrets
import stat
import tarfile
from pathlib import Path, PurePosixPath

MAX_COMPRESSED = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_MEMBERS = 2048
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
KEY = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
RESERVED = {"HOME", "PATH", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD", "SERVICE_AUTH",
            "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "SHELLOPTS"}


class ServiceError(ValueError):
    pass


def require(condition, message="Invalid service declaration."):
    if not condition:
        raise ServiceError(message)


def fields(value, allowed, required=()):
    require(isinstance(value, dict) and not value.keys() - set(allowed)
            and set(required) <= value.keys())


def literal(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    try:
        value.encode("utf-8")
        return True
    except UnicodeError:
        return False


def validate_services(services):
    require(isinstance(services, list))
    names, keys = set(), set()
    for service in services:
        fields(service, ("name", "environment", "required_env", "check"), ("name", "check"))
        name = service["name"]
        require(isinstance(name, str) and NAME.fullmatch(name) and name not in names)
        names.add(name)
        environment, needed = service.get("environment", {}), service.get("required_env", [])
        require(isinstance(environment, dict) and isinstance(needed, list))
        for key, value in environment.items():
            require(isinstance(key, str) and KEY.fullmatch(key) and key not in RESERVED
                    and key not in keys and literal(value))
            keys.add(key)
        require(all(isinstance(key, str) and KEY.fullmatch(key) for key in needed))
        require(len(needed) == len(set(needed)))
        check = service["check"]
        fields(check, ("argv", "timeout_seconds", "hint"), ("argv",))
        argv, timeout = check["argv"], check.get("timeout_seconds", 30)
        require(isinstance(argv, list) and argv and all(literal(arg) for arg in argv) and argv[0])
        require(type(timeout) is int and 1 <= timeout <= 300)
        require(literal(check.get("hint", "")))
    return services


def service_checks(services, env):
    checks = []
    for service in validate_services(services):
        hint = service["check"].get("hint", "Provide service credentials and re-run configure-run.sh.")
        for key in service.get("required_env", []):
            require(env.get(key), f"Service {service['name']} requires {key}. {hint}")
        checks.append({"name": service["name"], **service["check"]})
    return checks


def unpack_bundle(value):
    message = "Invalid service authentication bundle."
    require(isinstance(value, str) and len(value) <= (MAX_COMPRESSED + 2) // 3 * 4, message)
    try:
        compressed = base64.b64decode(value, validate=True)
        require(len(compressed) <= MAX_COMPRESSED, message)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            data = stream.read(MAX_EXPANDED + 1)
        require(len(data) <= MAX_EXPANDED, message)
        entries = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                require(len(entries) < MAX_MEMBERS and not member.name.startswith("/")
                        and "\\" not in member.name and "\0" not in member.name, message)
                parts = PurePosixPath(member.name).parts
                require(".." not in parts and parts not in entries and
                        (member.isdir() or member.isreg() and member.sparse is None)
                        and 0 <= member.size <= MAX_EXPANDED, message)
                require(parts or member.isdir(), message)
                entries[parts] = None if member.isdir() else archive.extractfile(member).read()
        for parts in entries:
            require(all(entries.get(parts[:i]) is None for i in range(1, len(parts))), message)
        return entries
    except (binascii.Error, OSError, EOFError, tarfile.TarError, UnicodeError, ValueError):
        raise ServiceError(message) from None


def open_directory(parent, name, uid, gid, create=True, private=True):
    created = False
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if create and (created or private):
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_home(home):
    home = Path(home)
    require(home.is_absolute() and ".." not in home.parts, "Invalid service home directory.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in home.parts[1:]:
            child = open_directory(descriptor, part, 0, 0, create=False)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def repair_file(parent, name, uid, gid):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe existing service authentication path.")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe existing service authentication path.")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def seed_file(parent, name, data, uid, gid):
    try:
        repair_file(parent, name, uid, gid)
        return
    except FileNotFoundError:
        pass
    temporary = ".seed-" + secrets.token_hex(12)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), 0o600)
            os.fsync(output.fileno())
        try:
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError:
            repair_file(parent, name, uid, gid)
    finally:
        os.unlink(temporary, dir_fd=parent)


def seed(service_fd, entries, uid, gid):
    for parts, data in sorted(entries.items(), key=lambda item: len(item[0])):
        if not parts:
            continue
        parent = os.dup(service_fd)
        try:
            for part in parts if data is None else parts[:-1]:
                child = open_directory(parent, part, uid, gid)
                os.close(parent)
                parent = child
            if data is not None:
                seed_file(parent, parts[-1], data, uid, gid)
        finally:
            os.close(parent)


def validate_auth(services, secrets):
    validate_services(services)
    require(isinstance(secrets, dict), "Invalid service authentication configuration.")
    bundles = secrets.get("SERVICE_AUTH", {})
    require(isinstance(bundles, dict) and not bundles.keys() - {s["name"] for s in services},
            "Authentication bundle supplied for an undeclared service.")
    return {name: unpack_bundle(value) for name, value in bundles.items()}


def configure_services(services, secrets, home, uid, gid, env_file):
    entries = validate_auth(services, secrets)
    if not services:
        return
    descriptor = os.open(env_file, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as output:
        require(stat.S_ISREG(os.fstat(output.fileno()).st_mode) and os.fstat(output.fileno()).st_nlink == 1,
                "Unsafe run environment file.")
        original = output.read()
        existing = set(re.findall(r"^\s*([A-Z_][A-Z0-9_]*)=", original, re.MULTILINE))
        additions = []
        for service in services:
            directory = Path(home) / ".config" / "crux-services" / service["name"]
            for key, value in service.get("environment", {}).items():
                require(key not in existing, f"Service environment conflicts with {key}.")
                additions.append(f"{key}={json.dumps(value.replace('{service_dir}', str(directory)), ensure_ascii=False)}\n")
        descriptor = open_home(home)
        try:
            for part in (".config", "crux-services"):
                child = open_directory(descriptor, part, uid, gid, private=part != ".config")
                os.close(descriptor)
                descriptor = child
            for service in services:
                child = open_directory(descriptor, service["name"], uid, gid)
                try:
                    seed(child, entries.get(service["name"], {}), uid, gid)
                finally:
                    os.close(child)
        finally:
            os.close(descriptor)
        if additions:
            output.write(("\n" if original and not original.endswith("\n") else "") + "".join(additions))
