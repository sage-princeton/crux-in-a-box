import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from crux_scaffold import experiment_api
from crux_scaffold.cli import main
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

ROOT = Path(__file__).resolve().parents[1] / "run-harness" / "vftts"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "fixture-agent",
       "OPENROUTER_EXPERIMENT_API_KEY": "fixture-experiment", "OPENROUTER_EXPERIMENT_BUDGET_USD": "3000",
       "OPENROUTER_EXPERIMENT_KEY_CAP_USD": "6000",
       "OPENROUTER_EXPERIMENT_BILLING_MODE": "credits", "SLACK_BOT_TOKEN": "fixture", "RUN_SLUG": "fixture",
       "COST_TRACKER_URL": "https://cost.example/cost", "COST_TRACKER_TOKEN": "fixture"}


@pytest.fixture
def full(tmp_path):
    """A staged copy of the full profile with its Slack placeholders resolved, as prepare.py does before launch."""
    root = shutil.copytree(ROOT, tmp_path / "vftts",
                           ignore=shutil.ignore_patterns("placeholders.txt", "secrets.json", "__pycache__"))
    spec = importlib.util.spec_from_file_location("vftts_prepare", root / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(root, "CTEST", "1.000000")
    return root


@pytest.fixture
def scaffold(tmp_path, full):
    return Scaffold(DropInDirectory.load(full), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def test_full_profile_requires_preparation():
    with pytest.raises(InvalidDropInError, match="unresolved placeholders"):
        DropInDirectory.load(ROOT)


def test_full_profile_assembles_without_network(tmp_path, capsys, full):
    assert main(["check", "--drop-in", str(full), "--state-dir", str(tmp_path / "state")], env=ENV) == 0
    assert "openrouter-experiments" in capsys.readouterr().out


def test_full_profile_has_seven_day_budgets_and_an_unlimited_experiment_resource():
    import tomllib
    full = tomllib.loads((ROOT / "scaffold.toml").read_text())
    assert full["budget"]["max_cost_usd"] == 15000
    assert full["budget"]["max_wall_seconds"] == 604800
    assert "limits" not in full["resources"]["experiments"]


def test_full_profile_uses_the_shared_research_loop_team_and_review_panel(scaffold):
    config = scaffold.drop_in.config
    assert scaffold.loop.type_name == "tabpfn_research"
    assert "gpu" not in config.resources
    assert config.resources["experiments"]["type"] == "openrouter-experiments"
    assert not scaffold.context.resources.providers["experiments"].options.limits
    assert set(config.agents["pi"].delegates) == {f"worker_{letter}" for letter in "abcdefgh"} | {
        "research_assistant", "research_assistant_b", "research_assistant_c"}
    assert "restart_context" in config.agents["pi"].tools
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    scaffold.runtime.describe()


def test_full_profile_enters_finalization_at_its_seven_day_reserves(scaffold):
    scaffold.context.usage.started_at = scaffold.context.now()
    assert scaffold.loop.options.reserve_wall_seconds == 60480
    scaffold.context.resources._snapshots["models"] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"cost_usd": ResourceMetric(value=13499, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    scaffold.context.resources._snapshots["models"] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"cost_usd": ResourceMetric(value=13500, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) == "model spend reserve"


def test_exhausted_experiments_leave_inference_and_finalization_available(scaffold):
    scaffold.context.usage.started_at = scaffold.context.now()
    scaffold.context.resources._snapshots["experiments"] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"spent_usd": ResourceMetric(value=3000, unit="USD"),
                                               "remaining_usd": ResourceMetric(value=0, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    assert not scaffold.context.budget_exhausted()


def test_full_example_needs_no_gpu_or_search_credentials():
    env = json.loads((ROOT / "secrets.json.example").read_text())
    assert not {"RUNPOD_API_KEY", "SERPAPI_KEY"} & env.keys()
    assert {"HF_TOKEN", "OPENROUTER_EXPERIMENT_API_KEY", "SLACK_BOT_TOKEN"} <= env.keys()
    assert env["OPENROUTER_EXPERIMENT_BUDGET_USD"] == "3000"
    assert env["OPENROUTER_EXPERIMENT_KEY_CAP_USD"] == "6000"
    assert env["OPENROUTER_EXPERIMENT_BILLING_MODE"] == "credits"
    assert env["OPENROUTER_EXPERIMENT_STATE_DIR"] == "/srv/crux-run/state/experiments"


@pytest.mark.parametrize("spent, expected_remaining", [(0, 3000), (1000, 2000), (3000, 0)])
def test_experiments_use_the_run_allowance_under_a_larger_provider_cap(monkeypatch, tmp_path, spent,
                                                                    expected_remaining):
    env = {**ENV, "OPENROUTER_EXPERIMENT_STATE_DIR": str(tmp_path / "experiments")}
    monkeypatch.setattr(experiment_api, "_request", lambda *args, **kwargs: {"data": {
        "usage": spent, "limit": 6000, "limit_remaining": 6000 - spent,
        "limit_reset": None, "byok_usage": 0,
    }})
    status = experiment_api.status(env)
    assert status["allocation_usd"] == 3000
    assert status["provider_limit_usd"] == 6000
    assert status["remaining_usd"] == expected_remaining
    if not expected_remaining:
        with pytest.raises(experiment_api.ExperimentError, match="exhausted"):
            experiment_api.complete(env, "openai/model", "Q", provider="openai", max_tokens=64,
                                    agent="worker_a", experiment="fixture")


def test_failed_review_feedback_reaches_every_phase_continuation(full):
    import tomllib

    from crux_scaffold.config import PhaseConfig
    from crux_scaffold.gates import GateResult
    from crux_scaffold.loop import PhasedLoop, PhasedOptions

    feedback = "Fixture correction: report the actual experimental receipt cost."
    drop_in = DropInDirectory.load(full)
    for raw in tomllib.loads((full / "scaffold.toml").read_text())["loop"]["phases"]:
        phase = PhaseConfig.model_validate({**raw, "gates": []})
        loop = PhasedLoop("fixture", PhasedOptions(phases=[phase]), gates={})
        prompt = loop.continuation(drop_in, phase, 1, [GateResult(gate="review", passed=False, feedback=feedback)])
        assert feedback in prompt


def test_full_profile_bundles_only_the_question_and_scaffold_files():
    workspace = {path.relative_to(ROOT / "workspace").as_posix()
                 for path in (ROOT / "workspace").rglob("*") if path.is_file() and "__pycache__" not in path.parts}
    assert workspace == {".gitignore", "AGENTS.md", "LOG.md", "PLAN.md", "RESEARCH.md", "paper/abstract.txt",
                         "paper/checklist.tex", "paper/neurips_2026.sty", "paper/paper.tex"}
