"""Prepare the staged SDK example's workspace from the pinned CRTR archive."""

import argparse
import hashlib
import json
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_SHA256 = "9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0"


def extract_source(archive: Path, destination: Path) -> str:
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = PurePosixPath(member.filename)
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                    or not path.parts or path.parts[0] != "CRTR-main"
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("Unsafe CRTR archive member")
        source.extractall(destination)
        return source.comment.decode("ascii").strip()


def prepare(root: Path, archive: Path) -> None:
    workspace = root.resolve() / "workspace"
    output = workspace / "crtr"
    if output.exists() or output.is_symlink():
        raise ValueError("CRTR checkout already exists; preserve the current workspace")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError("CRTR archive checksum does not match the supplied research input")
    with tempfile.TemporaryDirectory(prefix=".contrastive-prepare-", dir=workspace) as temporary:
        revision = extract_source(archive, Path(temporary) / "source")
        source = Path(temporary) / "source/CRTR-main"
        if not (source / "runner.py").is_file():
            raise ValueError("CRTR archive has no runner.py")
        provenance = {"archive_sha256": digest, "revision": revision, "source": "crtr/"}
        source.rename(output)
        (workspace / "SOURCE.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--archive", type=Path, default=root / "workspace/source.zip")
    args = parser.parse_args()
    try:
        prepare(root, args.archive)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Preparation failed: {error}\n")
    print("Pilot bundle prepared. Run scaffold check before starting research.")


if __name__ == "__main__":
    main()
