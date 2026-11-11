"""The full-run drop-ins ship the NeurIPS 2026 template and install it without a download."""

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "run-harness"


def load_prepare(root, name):
    spec = importlib.util.spec_from_file_location(f"{name.replace('-', '_')}_prepare", root / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["tabpfn-full", "contrastive-rl-full"])
def test_full_runs_install_the_bundled_neurips_2026_template(tmp_path, name):
    root = tmp_path / name
    shutil.copytree(HARNESS / name, root, ignore=shutil.ignore_patterns("__pycache__"))
    module = load_prepare(root, name)
    archive = module.TEMPLATE.read_bytes()
    assert module.TEMPLATE.parent == root and hashlib.sha256(archive).hexdigest() == module.TEMPLATE_SHA256
    module.prepare(root, "C123", "U123", "1.000000", archive)
    with zipfile.ZipFile(root / "workspace/templates/paper_template.zip") as template:
        assert {"neurips_2026.sty", "neurips_2026.tex", "checklist.tex"} <= set(template.namelist())
        assert template.read("neurips_2026.sty") == (root / "workspace/paper/neurips_2026.sty").read_bytes()
    paper = (root / "workspace/paper/paper.tex").read_text()
    assert "\\usepackage{neurips_2026}" in paper and "neurips_2025" not in paper
    assert not (root / "workspace/paper/neurips_2025.sty").exists()


@pytest.mark.parametrize("name", ["tabpfn-full", "contrastive-rl-full"])
def test_a_changed_template_archive_is_refused(tmp_path, name):
    root = tmp_path / name
    shutil.copytree(HARNESS / name, root, ignore=shutil.ignore_patterns("__pycache__"))
    module = load_prepare(root, name)
    with pytest.raises(ValueError, match="checksum"):
        module.prepare(root, "C123", "U123", "1.000000", module.TEMPLATE.read_bytes() + b"\n")
    assert "{{SLACK_CHANNEL_ID}}" in (root / "scaffold.toml").read_text()


@pytest.mark.parametrize("name", ["tabpfn-full", "contrastive-rl-full"])
def test_full_run_boxes_install_the_fonts_the_2026_style_needs(name):
    # neurips_2026.sty sets \texttt{email} in the author block and adds line numbers; with the template's T1 encoding
    # that needs cm-super's typewriter fonts, which texlive-fonts-recommended lacks (pdflatex: ectt1000 not found).
    packages = json.loads((HARNESS / name / "provision.json").read_text())["apt_packages"]
    assert "cm-super" in packages
