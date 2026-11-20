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

ROOT = Path(__file__).resolve().parents[1] / "run-harness" / "inductive-forecasting"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "fixture-agent",
       "OPENROUTER_EXPERIMENT_API_KEY": "fixture-experiment", "OPENROUTER_EXPERIMENT_BUDGET_USD": "2000",
       "OPENROUTER_EXPERIMENT_KEY_CAP_USD": "4000", "OPENROUTER_EXPERIMENT_BILLING_MODE": "credits",
       "SLACK_BOT_TOKEN": "fixture", "RUN_SLUG": "fixture", "RUNPOD_API_KEY": "fixture",
       "COST_TRACKER_URL": "https://cost.example/cost", "COST_TRACKER_TOKEN": "fixture"}
ASSISTANTS = ["research_assistant"] + [f"research_assistant_{letter}" for letter in "bcdef"]
WORKERS = [f"worker_{letter}" for letter in "abcdefghijklmnopqrst"]


@pytest.fixture
def full(tmp_path):
    """A staged copy of the drop-in with its Slack placeholders resolved, as prepare.py does before launch."""
    root = shutil.copytree(ROOT, tmp_path / "inductive-forecasting",
                           ignore=shutil.ignore_patterns("placeholders.txt", "secrets.json", "__pycache__"))
    spec = importlib.util.spec_from_file_location("inductive_forecasting_prepare", root / "prepare.py")
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


def test_uses_the_five_day_budgets_shared_loop_team_and_review_panel(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 432000
    assert config.budget.max_cost_usd == 30000
    assert config.resources["gpu"]["type"] == "runpod"
    assert config.resources["gpu"]["limits"] == {"cost_usd": 1500}
    assert config.resources["experiments"]["type"] == "openrouter-experiments"
    assert not scaffold.context.resources.providers["experiments"].options.limits
    assert scaffold.loop.type_name == "tabpfn_research"
    assert config.loop["reserve_wall_seconds"] == 43200
    assert config.context["trigger_tokens"] == 400000
    assert config.tools["rest"]["total_seconds"] == 432000
    assert "restart_context" in config.agents["pi"].tools
    assert set(config.agents["pi"].delegates) == set(WORKERS) | set(ASSISTANTS)
    assert set(config.coding_agents) == set(WORKERS) and set(ASSISTANTS) <= set(config.agents)
    panel = ["google/gemini-4-carbon@preset/ai-studio-priority", "anthropic/claude-opus-5.5", "openai/gpt-6-astra"]
    assert config.gates["research_review"]["models"] == config.gates["final_review"]["models"] == panel
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    scaffold.runtime.describe()


@pytest.mark.parametrize("resource, below, at, reason", [("models", 26999, 27000, "model spend reserve"),
                                                         ("gpu", 1349, 1350, "gpu reserve")])
def test_enters_finalization_at_its_spend_reserves(scaffold, resource, below, at, reason):
    scaffold.context.usage.started_at = scaffold.context.now()
    snapshot(scaffold, resource, cost_usd=below)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    snapshot(scaffold, resource, cost_usd=at)
    assert scaffold.loop.reserve_reason(scaffold.context) == reason


def test_reserves_the_final_tenth_of_the_run(scaffold):
    now = scaffold.context.now()
    scaffold.context.now = lambda: now
    scaffold.context.usage.started_at = now - timedelta(seconds=388799)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    scaffold.context.usage.started_at -= timedelta(seconds=1)
    assert scaffold.loop.reserve_reason(scaffold.context) == "time reserve"


def test_exhausted_experiments_leave_inference_and_finalization_available(scaffold):
    scaffold.context.usage.started_at = scaffold.context.now()
    snapshot(scaffold, "experiments", spent_usd=2000, remaining_usd=0)
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    assert not scaffold.context.budget_exhausted()


def test_example_lists_gpu_hugging_face_and_experiment_credentials():
    env = json.loads((ROOT / "secrets.json.example").read_text())
    assert {"RUNPOD_API_KEY", "HF_TOKEN", "OPENROUTER_EXPERIMENT_API_KEY", "SLACK_BOT_TOKEN"} <= env.keys()
    assert not {"SERPAPI_KEY", "TABPFN_TOKEN"} & env.keys()
    assert env["OPENROUTER_EXPERIMENT_BUDGET_USD"] == "2000"
    assert env["OPENROUTER_EXPERIMENT_KEY_CAP_USD"] == "4000"
    assert env["OPENROUTER_EXPERIMENT_BILLING_MODE"] == "credits"
    assert env["OPENROUTER_EXPERIMENT_STATE_DIR"] == "/srv/crux-run/state/experiments"


@pytest.mark.parametrize("spent, expected_remaining", [(0, 2000), (400, 1600), (2000, 0)])
def test_experiments_use_the_run_allowance_under_a_larger_provider_cap(monkeypatch, tmp_path, spent,
                                                                    expected_remaining):
    env = {**ENV, "OPENROUTER_EXPERIMENT_STATE_DIR": str(tmp_path / "experiments")}
    monkeypatch.setattr(experiment_api, "_request", lambda *args, **kwargs: {"data": {
        "usage": spent, "limit": 4000, "limit_remaining": 4000 - spent, "limit_reset": None, "byok_usage": 0}})
    status = experiment_api.status(env)
    assert status["allocation_usd"] == 2000
    assert status["provider_limit_usd"] == 4000
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


def test_pi_sees_the_review_history_and_reviewers_report_scores(scaffold):
    assert scaffold.drop_in.config.agents["pi"].workspace_context == ["REVIEW_HISTORY.md", "REVIEW_STATUS.json",
                                                                      "PLAN.md"]
    rubric = (ROOT / "prompts/review.md").read_text()
    assert "**Path forward:** exactly one of Revise or Rethink" in rubric and "`SCORES quality=" in rubric
    # The drop-in loads the shared review gate, which renders REVIEW_HISTORY.md after every review.
    assert all(hasattr(scaffold.loop.gates[name], "record_history") for name in ("research_review", "final_review"))


def test_the_pi_persona_names_the_whole_team_and_its_pace():
    # The persona's team paragraph, Slack layout and pace follow scaffold.toml's delegates and budget.
    persona = (ROOT / "personas/pi.md").read_text()
    assert (len(ASSISTANTS), len(WORKERS)) == (6, 20)
    assert "six SDK research assistants" in persona and "twenty Codex workers" in persona
    assert f"`{ASSISTANTS[-1]}`" in persona and f"`{WORKERS[-1]}`" in persona
    assert "of 26 busy" in persona
    assert "$30,000 over five days" in persona and "$27,000 finalization" in persona
    assert "Experiments <spent> of 2000 USD" in persona


def test_every_agent_is_told_to_stop_at_and_report_withheld_work(scaffold):
    # The URL blacklist cannot stop search snippets, mirrors or pods; the standing context covers the rest.
    rule = " ".join((ROOT / "workspace/AGENTS.md").read_text().split())
    assert "Recent work that addresses this run's research question directly is withheld" in rule
    assert '"Blocked by run network policy."' in rule and "stop at the title or snippet" in rule
    # The proxy also blocks search results that show the work; those blocks expose nothing and are not escalated.
    assert "a search whose results would show withheld work is blocked too" in rule
    assert "A blocked request shows you nothing, so it needs no report" in rule
    assert "reports it to the PI at once with `report_progress`" in rule
    assert "notifies the operator with `escalate`" in rule
    config = scaffold.drop_in.config
    assert all("workspace/AGENTS.md" in config.agents[name].context for name in ["pi", *ASSISTANTS])
    assert all("workspace/AGENTS.md" in config.coding_agents[name]["context"] for name in WORKERS)
    assert "escalate" in config.agents["pi"].tools
    persona = " ".join((ROOT / "personas/pi.md").read_text().split())
    assert "a possible encounter with withheld work (see AGENTS.md)" in persona
