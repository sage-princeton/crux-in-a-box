"""Stream the whole run workspace to private S3, including dotfiles and binaries."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOTS = (Path("/srv/crux-run"), Path("/home/ubuntu/crux-in-a-box-harness/workspace"))


def write_archive(root, stream):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Workspace root must be a real directory")
    # No path allowlist or exclusions. Link entries are saved without reading outside the root.
    with tarfile.open(fileobj=stream, mode="w|gz", dereference=False) as archive:
        archive.add(root, arcname="workspace")


class HashingWriter:
    def __init__(self, stream):
        self.stream, self.hash, self.bytes = stream, hashlib.sha256(), 0

    def write(self, block):
        self.hash.update(block)
        self.bytes += len(block)
        return self.stream.write(block)


def archive_workspace(root, destination):
    with open(destination, "wb") as output:
        writer = HashingWriter(output)
        write_archive(root, writer)
    return {"sha256": writer.hash.hexdigest(), "bytes": writer.bytes}


def expected_size(root):
    # Upper estimate lets AWS CLI choose multipart sizes for large streams.
    size = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            stat = (Path(directory) / name).lstat()
            size += stat.st_size + 16384
    return max(size * 2, 1024 * 1024)


def main():
    import re

    key = os.environ["SSM_UploadKey"]
    if not re.fullmatch(r"workspaces/i-[a-f0-9]+/[0-9]+/workspace.tar.gz", key):
        raise ValueError("Invalid workspace upload key")
    root = next((root for root in ROOTS if root.is_dir()), None)
    if root is None:
        raise FileNotFoundError("Run workspace not found")
    credentials = json.loads(os.environ["SSM_Credentials"])
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": credentials["SessionToken"],
        "AWS_DEFAULT_REGION": os.environ["CRUX_ARCHIVE_REGION"],
    }
    # SSM parameter values are never left in the child process's environment.
    env.pop("SSM_Credentials")
    bucket = os.environ["CRUX_ARCHIVE_BUCKET"]
    command = [
        shutil.which("aws") or "/usr/local/bin/aws",
        "s3",
        "cp",
        "-",
        f"s3://{bucket}/{key}",
        "--sse",
        "AES256",
        "--content-type",
        "application/gzip",
        "--expected-size",
        str(expected_size(root)),
        "--only-show-errors",
    ]
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    ) as upload:
        writer = HashingWriter(upload.stdin)
        try:
            write_archive(root, writer)
            upload.stdin.close()
            if upload.wait(timeout=3600) != 0:
                raise RuntimeError("Workspace upload failed")
        except BaseException:
            upload.kill()
            raise
    print(json.dumps({"sha256": writer.hash.hexdigest(), "bytes": writer.bytes, "root": str(root)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SSM output must never contain upload credentials or workspace contents.
        print(type(error).__name__)
        raise SystemExit(1) from None
