import importlib.util
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from crux_scaffold import experiment_api
from crux_scaffold.cli import main
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

ROOT = Path(__file__).resolve().parents[1] / "run-harness" / "vlm-pa"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "fixture-agent",
       "OPENROUTER_EXPERIMENT_API_KEY": "fixture-experiment", "OPENROUTER_EXPERIMENT_BUDGET_USD": "100",
       "OPENROUTER_EXPERIMENT_KEY_CAP_USD": "200", "OPENROUTER_EXPERIMENT_BILLING_MODE": "credits",
       "SLACK_BOT_TOKEN": "fixture", "RUN_SLUG": "fixture", "RUNPOD_API_KEY": "fixture",
       "COST_TRACKER_URL": "https://cost.example/cost", "COST_TRACKER_TOKEN": "fixture"}


@pytest.fixture
def full(tmp_path):
    """A staged copy of the drop-in with its Slack placeholders resolved, as prepare.py does before launch."""
    root = shutil.copytree(ROOT, tmp_path / "vlm-pa",
                           ignore=shutil.ignore_patterns("placeholders.txt", "secrets.json", "__pycache__"))
    spec = importlib.util.spec_from_file_location("vlm_pa_prepare", root / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(root, "CTEST", "1.000000")
    return root


@pytest.fixture
def scaffold(tmp_path, full):
    return Scaffold(DropInDirectory.load(full), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def snapshot(scaffold, resource, **metrics):
    scaffold.context.resources._snapshots[resource] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={name: ResourceMetric(value=value, unit="USD")
                                               for name, value in metrics.items()})


def test_requires_preparation():
    with pytest.raises(InvalidDropInError, match="unresolved placeholders"):
        DropInDirectory.load(ROOT)


def test_assembles_without_network(tmp_path, capsys, full):
    assert main(["check", "--drop-in", str(full), "--state-dir", str(tmp_path / "state")], env=ENV) == 0
    out = capsys.readouterr().out
    assert "openrouter-experiments" in out and "runpod" in out


def test_uses_the_seven_day_budgets_shared_loop_team_and_review_panel(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 604800
    assert config.budget.max_cost_usd == 15000
    assert config.resources["gpu"]["type"] == "runpod"
    assert config.resources["gpu"]["limits"] == {"cost_usd": 3000}
    assert config.resources["experiments"]["type"] == "openrouter-experiments"
    assert not scaffold.context.resources.providers["experiments"].options.limits
    assert scaffold.loop.type_name == "tabpfn_research"
    assert config.loop["reserve_wall_seconds"] == 60480
    assert config.context["trigger_tokens"] == 400000
    assert config.tools["rest"]["total_seconds"] == 604800
    assert "restart_context" in config.agents["pi"].tools
    assert set(config.agents["pi"].delegates) == {f"worker_{letter}" for letter in "abcdefgh"} | {
        "research_assistant", "research_assistant_b", "research_assistant_c"}
    panel = ["google/gemini-4-carbon@preset/ai-studio-priority", "anthropic/claude-opus-5.5", "openai/gpt-6-astra"]
    assert config.gates["research_review"]["models"] == config.gates["final_review"]["models"] == panel
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    scaffold.runtime.describe()


@pytest.mark.parametrize("resource, below, at, reason", [("models", 13499, 13500, "model spend reserve"),
                                                         ("gpu", 2699, 2700, "gpu reserve")])
def test_enters_finalization_at_its_spend_reserves(scaffold, resource, below, at, reason):
    scaffold.context.usage.started_at = scaffold.context.now()
    snapshot(scaffold, resource, cost_usd=below)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    snapshot(scaffold, resource, cost_usd=at)
    assert scaffold.loop.reserve_reason(scaffold.context) == reason


def test_reserves_the_final_tenth_of_the_week(scaffold):
    now = scaffold.context.now()
    scaffold.context.now = lambda: now
    scaffold.context.usage.started_at = now - timedelta(seconds=544319)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    scaffold.context.usage.started_at -= timedelta(seconds=1)
    assert scaffold.loop.reserve_reason(scaffold.context) == "time reserve"


def test_exhausted_experiments_leave_inference_and_finalization_available(scaffold):
    scaffold.context.usage.started_at = scaffold.context.now()
    snapshot(scaffold, "experiments", spent_usd=100, remaining_usd=0)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    assert not scaffold.context.budget_exhausted()


def test_example_lists_gpu_hugging_face_and_experiment_credentials():
    env = json.loads((ROOT / "secrets.json.example").read_text())
    assert {"RUNPOD_API_KEY", "HF_TOKEN", "OPENROUTER_EXPERIMENT_API_KEY", "SLACK_BOT_TOKEN"} <= env.keys()
    assert not {"SERPAPI_KEY", "TABPFN_TOKEN"} & env.keys()
    assert env["OPENROUTER_EXPERIMENT_BUDGET_USD"] == "100"
    assert env["OPENROUTER_EXPERIMENT_KEY_CAP_USD"] == "200"
    assert env["OPENROUTER_EXPERIMENT_BILLING_MODE"] == "credits"
    assert env["OPENROUTER_EXPERIMENT_STATE_DIR"] == "/srv/crux-run/state/experiments"


@pytest.mark.parametrize("spent, expected_remaining", [(0, 100), (40, 60), (100, 0)])
def test_experiments_use_the_run_allowance_under_a_larger_provider_cap(monkeypatch, tmp_path, spent,
                                                                    expected_remaining):
    env = {**ENV, "OPENROUTER_EXPERIMENT_STATE_DIR": str(tmp_path / "experiments")}
    monkeypatch.setattr(experiment_api, "_request", lambda *args, **kwargs: {"data": {
        "usage": spent, "limit": 200, "limit_remaining": 200 - spent, "limit_reset": None, "byok_usage": 0}})
    status = experiment_api.status(env)
    assert status["allocation_usd"] == 100
    assert status["provider_limit_usd"] == 200
    assert status["remaining_usd"] == expected_remaining
    if not expected_remaining:
        with pytest.raises(experiment_api.ExperimentError, match="exhausted"):
            experiment_api.complete(env, "openai/model", "Q", provider="openai", max_tokens=64,
                                    agent="worker_a", experiment="fixture")


def test_bundles_only_the_question_and_scaffold_files():
    workspace = {path.relative_to(ROOT / "workspace").as_posix()
                 for path in (ROOT / "workspace").rglob("*") if path.is_file() and "__pycache__" not in path.parts}
    assert workspace == {".gitignore", "AGENTS.md", "LOG.md", "PLAN.md", "RESEARCH.md", "paper/abstract.txt",
                         "paper/checklist.tex", "paper/neurips_2026.sty", "paper/paper.tex"}
