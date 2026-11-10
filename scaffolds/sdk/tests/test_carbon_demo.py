"""The demo must retain the real research gates and its explicit resource envelope."""

import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "run-harness"


def test_carbon_demo_uses_tabpfn_gates_with_bounded_cpu_budget():
    demo = tomllib.loads((ROOT / "google-carbon-demo/scaffold.toml").read_text())
    base = tomllib.loads((ROOT / "tabpfn/scaffold.toml").read_text())
    assert demo["budget"] == {"max_wall_seconds": 2700, "max_cost_usd": 75}
    assert demo["loop"]["reserve_wall_seconds"] == 900
    assert demo["loop"]["phases"] == base["loop"]["phases"]
    assert "gpu" not in demo["resources"]
    assert demo["context"]["context_window_tokens"] == 1048576
    assert demo["context"]["image_tokens"] == 1120
    for name, gate in demo["gates"].items():
        expected = dict(base["gates"][name])
        expected["models"] = ["google/gemini-4-carbon", *expected["models"][1:]]
        assert gate == expected
    assert demo["agents"].keys() == base["agents"].keys()
    assert demo["coding_agents"].keys() == base["coding_agents"].keys()


def test_materialization_copies_only_declared_inputs(tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile

    path = ROOT / "google-carbon-demo/prepare.py"
    spec = importlib.util.spec_from_file_location("carbon_prepare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("Styles/neurips_2025.sty", "test style")
    content = archive.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    real_spec = module.importlib.util.spec_from_file_location
    def patched_spec(name, location):
        result = real_spec(name, location)
        real_exec = result.loader.exec_module
        def execute(target):
            real_exec(target)
            target.TEMPLATE_SHA256 = digest
        result.loader.exec_module = execute
        return result
    monkeypatch.setattr(module.importlib.util, "spec_from_file_location", patched_spec)
    output = tmp_path / "inputs"
    module.materialize(output, template=content)
    for name in ("tabpfn_extensions.py", "prompts/review.md"):
        assert (output / name).read_bytes() == (ROOT / "tabpfn" / name).read_bytes()
    assert not (output / "secrets.json").exists()
    assert not (output / "placeholders.txt").exists()
    assert "45 minutes" in (output / "workspace/AGENTS.md").read_text()
