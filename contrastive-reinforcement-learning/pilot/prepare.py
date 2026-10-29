"""Create a fresh run bundle with verified CRTR sources."""

import argparse
import hashlib
import json
import shutil
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


def prepare(root: Path, output: Path, archive: Path) -> None:
    if output.is_symlink():
        raise ValueError("Output already exists as a symlink")
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Output already exists or is inside the template; use a fresh sibling directory")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError("CRTR archive checksum does not match the supplied research input")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contrastive-prepare-", dir=output.parent) as temporary:
        staged = Path(temporary) / "run"
        shutil.copytree(root, staged, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        revision = extract_source(archive, Path(temporary) / "source")
        shutil.move(str(Path(temporary) / "source/CRTR-main"), staged / "workspace/crtr")
        if not (staged / "workspace/crtr/runner.py").is_file():
            raise ValueError("CRTR archive has no runner.py")
        provenance = {"archive_sha256": digest, "revision": revision, "source": "crtr/"}
        (staged / "workspace/SOURCE.json").write_text(json.dumps(provenance, indent=2) + "\n")
        staged.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(Path(__file__).resolve().parent, args.output, args.archive)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Preparation failed: {error}\n")
    print("Pilot bundle prepared. Run scaffold check before starting research.")


if __name__ == "__main__":
    main()
