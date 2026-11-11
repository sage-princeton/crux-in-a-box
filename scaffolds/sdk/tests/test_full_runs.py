"""The full-run drop-ins write against the NeurIPS 2026 style committed in their paper folder."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "run-harness"
FULL_RUNS = ["tabpfn-full", "contrastive-rl-full"]


@pytest.mark.parametrize("name", FULL_RUNS)
def test_full_runs_use_the_committed_neurips_2026_style(name):
    paper = HARNESS / name / "workspace/paper"
    assert "\\usepackage{neurips_2026}" in (paper / "paper.tex").read_text()
    assert (paper / "neurips_2026.sty").is_file() and (paper / "checklist.tex").is_file()
    # The style prints \texttt in its author block and adds line numbers; with T1 fonts that needs cm-super.
    assert "cm-super" in json.loads((HARNESS / name / "provision.json").read_text())["apt_packages"]


@pytest.mark.parametrize("name", FULL_RUNS)
def test_full_run_preparation_resolves_slack_settings_without_a_download(tmp_path, name):
    root = shutil.copytree(HARNESS / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location(f"{name.replace('-', '_')}_prepare", root / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(root, "C123", "U123", "1.000000")
    config = (root / "scaffold.toml").read_text()
    assert 'channel_id = "C123"' in config and "{{SLACK_" not in config
