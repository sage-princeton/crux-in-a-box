"""Materialize the Carbon demo with the selected source's unchanged TabPFN gates."""

import argparse
import hashlib
import importlib.util
import shutil
import urllib.request
from pathlib import Path

BASE_FILES = (
    "tabpfn_extensions.py", "provision.json", "prompts/review.md",
    "prompts/research-continue.md", "prompts/finalize-continue.md",
    "prompts/codex-worker.md", "workspace/paper/paper.tex",
    "workspace/paper/abstract.txt",
)


def materialize(output: Path, *, template: bytes | None = None) -> None:
    """Copy reviewed inputs to a fresh external staging directory; never copy secrets."""
    root = Path(__file__).resolve().parent
    base = root.parent / "tabpfn"
    if output.exists():
        raise ValueError("Choose a fresh output directory")
    if root in output.resolve().parents or base in output.resolve().parents:
        raise ValueError("Stage outside the source drop-ins")
    spec = importlib.util.spec_from_file_location("tabpfn_prepare", base / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if template is None:
        with urllib.request.urlopen(module.TEMPLATE_URL, timeout=30) as response:
            template = response.read(2_000_000)
    if hashlib.sha256(template).hexdigest() != module.TEMPLATE_SHA256:
        raise ValueError("Venue template checksum mismatch")
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        style = archive.read("Styles/neurips_2025.sty")
    for name in BASE_FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / name, target)
    for name in ("scaffold.toml", "PROMPT.md", "workspace/AGENTS.md", "workspace/RESEARCH.md",
                 "personas/pi.md", "personas/ra.md", "prompts/finalize.md"):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    worker = output / "prompts/codex-worker.md"
    worker.write_text(worker.read_text().replace("GPU spend and running pods", "no external compute allowance"))
    paper = output / "workspace/paper/paper.tex"
    paper.write_text(paper.read_text().replace("Detecting harmful deployment shift in prior-fitted networks",
                                             "Confidence and feature shift under CPU-scale corruption"))
    (output / "workspace/paper/neurips_2025.sty").write_bytes(style)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args()
    materialize(args.output, template=args.template.read_bytes() if args.template else None)
    print(f"Prepared {args.output}; run scaffold check before inference.")
