"""Provision a pinned Linux CLI and seed the anonymous TabPFN paper workspace."""
import argparse
import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "1.5.13"
RELEASE = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.13-5999302598852608"
ARCHIVES = {
    "aarch64": ("linux-arm/cli_linux_arm64.tar.gz",
                "dccea2eb455099f5175f0c87cc47b38fcefe4cb0cb44dea3927469bb4bbf68ac3c0b30fc04affb24c17e90959386a93c3785e8e4b8b1a3338ca4e4d57b80057b"),
    "x86_64": ("linux-x64/cli_linux_x64.tar.gz",
               "014b37387703acab1f47cd532c8bb60167a6a3537b0f9891de03f1b1ffa9614307bc83c4b2f7551273b0877d722716b0cd9e26782f6b2a50705db4f05e18c68f"),
}


def install_cli(target: Path) -> None:
    """Verify Google's release archive before atomically installing its binary.

    :param target: Destination executable on the Linux run host.
    """
    name, digest = ARCHIVES[platform.machine()]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        archive = Path(directory) / "cli.tar.gz"
        with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        with archive.open("rb") as source:
            if hashlib.file_digest(source, "sha512").hexdigest() != digest:
                raise ValueError("Antigravity archive checksum mismatch")
        staged = Path(directory) / "agy"
        with tarfile.open(archive) as tar, staged.open("wb") as out:
            member = tar.getmember("antigravity")
            if not member.isfile():
                raise ValueError("Missing CLI executable in release")
            with tar.extractfile(member) as binary:
                shutil.copyfileobj(binary, out)
        staged.chmod(0o755)
        os.replace(staged, target)


class ResearchInputs:
    """Publish only the original scientific inputs and adapted operating instructions.

    :ivar marker: Sentinel identifying an initialized experiment workspace.
    """

    original = HERE.parents[1] / "scaffolds/sdk/run-harness/tabpfn-full/workspace"
    marker = ".tabpfn-antigravity"

    def seed(self, workspace: Path) -> None:
        """Create a complete project once, preserving research on subsequent launches.

        :param workspace: Dedicated destination directory outside the source checkout.
        """
        if (workspace / self.marker).is_file():
            current = workspace / "AGENTS.md"
            previous = {'afa940e9c31aed37f5a26dce03f0b48ad07249c1ac9ff393338e4689d56fe4e7'}
            if current.is_file() and not current.is_symlink() and hashlib.sha256(current.read_bytes()).hexdigest() in previous:
                shutil.copyfile(HERE / "workspace/AGENTS.md", current)
            return
        if workspace.exists():
            raise FileExistsError(f"Unrecognized existing workspace: {workspace}")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace.parent) as tmp:
            staged = Path(tmp) / "project"
            (staged / "paper").mkdir(parents=True)
            for name in ("LOG.md", ".gitignore", "paper/paper.tex", "paper/abstract.txt",
                         "paper/checklist.tex", "paper/neurips_2026.sty"):
                shutil.copyfile(self.original / name, staged / name)
            for name in ("AGENTS.md", "RESEARCH.md", "PLAN.md", "BRIEF.md"):
                shutil.copyfile(HERE / "workspace" / name, staged / name)
            (staged / self.marker).touch()
            staged.rename(workspace)


def main() -> None:
    """Install the CLI or seed research inputs independently for deployment and validation.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install-cli", type=Path, metavar="EXECUTABLE")
    action.add_argument("--seed", type=Path, metavar="WORKSPACE")
    args = parser.parse_args()
    if args.seed:
        ResearchInputs().seed(args.seed)
    else:
        install_cli(args.install_cli)


if __name__ == "__main__":
    main()
