"""Record auxiliary account settings in an operator config or on the provisioned run box."""

import os
import re
import sys
import tempfile
from pathlib import Path


def role_settings(account):
    """Return the two non-secret run variables for a verified AWS account ID."""
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Invalid AWS account ID.")
    return {
        "AUX_RESOURCE_ACCOUNT_ID": account,
        "AUX_RESOURCE_ROLE_ARN": f"arn:aws:iam::{account}:role/crux-agent-devops",
    }


def write_settings(path, values):
    """Atomically replace managed assignments, preserving unrelated file contents.

    :param path: Existing operator config or root-owned systemd environment file.
    :param values: Names and literal non-secret AWS identity values.
    :returns: Whether the file changed.
    """
    original = path.read_text()
    current = dict(line.split("=", 1) for line in original.splitlines() if "=" in line)
    if all(current.get(k) == v for k, v in values.items()):
        return False
    lines = [line for line in original.splitlines() if line.partition("=")[0].strip() not in values]
    lines.extend(f"{key}={value}" for key, value in values.items())
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    write_settings(Path("/etc/crux-run.env"), role_settings(sys.argv[1]))
