import asyncio
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import Agent
from agents.run_config import CallModelData, ModelInputData
from test_blind_review import submission_pdf

from crux_scaffold.agent_runtimes.base import TurnOutcome, Verdict
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GateContext, GateResult
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

from scripted import ScriptedModel, call, say

EXAMPLE = Path(__file__).resolve().parents[1] / "run-harness/tabpfn"
ENV = {"CRUX_MODEL": "google/gemini-4-argon@preset/ai-studio-only", "CRUX_REASONING_EFFORT": "high", "OPENROUTER_API_KEY": "test",
       "RUN_SLUG": "tabpfn-test", "SLACK_BOT_TOKEN": "test",
       "COST_TRACKER_URL": "https://cost.test/cost", "COST_TRACKER_TOKEN": "test", "RUNPOD_API_KEY": "test"}


@pytest.fixture
def scaffold(tmp_path):
    root = tmp_path / "tabpfn"
    shutil.copytree(EXAMPLE, root)
    path = root / "scaffold.toml"
    path.write_text(path.read_text().replace("{{SLACK_CHANNEL_ID}}", "CTEST")
                    .replace("{{SLACK_OPERATOR_ID}}", "UTEST").replace("{{SLACK_AFTER_TS}}", "0"))
    return Scaffold(DropInDirectory.load(root), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def submit(scaffold):
    workspace = scaffold.context.workspace
    workspace.write_file("paper/abstract.txt", "A test manuscript.")
    workspace.write_file("paper/paper.aux", r"\newlabel{crux:main-end}{{1}{1}}")
    path = workspace.root / "paper/paper.pdf"
    path.write_bytes(submission_pdf())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    workspace.write_file("REVIEW_REQUEST.json", json.dumps({"paper_sha256": digest}))
    return digest


def gate_context(scaffold, phase="research"):
    return GateContext(scaffold.context, scaffold.drop_in, scaffold.runtime, phase, 1, "PRIVATE author output")


def test_only_requested_resource_caps(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 21600
    assert config.budget.max_cost_usd == 200
    assert config.budget.max_total_tokens is None
    assert config.delegation.max_concurrent is config.delegation.max_depth is None
    assert config.runtime["max_turns"] == "unlimited"
    assert all(p.max_iterations == "unlimited" for p in scaffold.loop.options.phases)
    assert all(g.options.max_turns == "unlimited" for g in scaffold.loop.gates.values())
    assert config.resources["gpu"]["limits"] == {"cost_usd": 100}
    assert "run_shell" in config.agents["pi"].tools


def test_argon_provider_and_context_build_without_openai_credentials(scaffold):
    assert "OPENAI_API_KEY" not in scaffold.context.env
    description = scaffold.runtime.describe()
    assert description[0] == "model provider openai-compatible (responses)"
    assert scaffold.strategy.type_name == "compaction"
    assert scaffold.runtime.provider is not None
    assert set(scaffold.coding_agents) == {"worker_a", "worker_b", "worker_c"}
    assert scaffold.context.model == ENV["CRUX_MODEL"]
    assert scaffold.runtime.options.model_provider.extra_body == {
        "provider": {"only": ["google-ai-studio"], "allow_fallbacks": False}}
    assert scaffold.context.reasoning_effort == "high"


@pytest.mark.parametrize("model", ["google/gemini-4-argon", ENV["CRUX_MODEL"]])
def test_argon_provisioning_requires_preset_for_codex_routing(model):
    script = EXAMPLE.parents[3] / "devops/ec2-workspaces/sdk_config.py"
    result = subprocess.run([sys.executable, str(script), "--model-provider", "openrouter",
                             "--model", model],
                            input=(EXAMPLE / "scaffold.toml").read_text(), text=True, capture_output=True, check=False)
    if "@preset/" not in model:
        assert result.returncode != 0
        assert "preset" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "api_key_env": "OPENROUTER_API_KEY", "coding_agent": "codex",
        "codex_base_url": "https://openrouter.ai/api/v1", "codex_api_key_env": "OPENROUTER_API_KEY",
    }


def test_unrequested_or_invalid_paper_never_calls_reviewer(scaffold):
    async def judge(*args, **kwargs):
        pytest.fail("Reviewer must not run")
    scaffold.runtime.judge = judge
    gate = scaffold.loop.gates["research_review"]
    assert not asyncio.run(gate.check(gate_context(scaffold))).passed
    submit(scaffold)
    scaffold.context.workspace.write_file("paper/abstract.txt", "word " * 201)
    assert "abstract" in asyncio.run(gate.check(gate_context(scaffold))).feedback.lower()


def test_review_is_blind_archived_cached_and_bound_to_pdf(scaffold):
    digest = submit(scaffold)
    verdict = Verdict(passed=True, feedback="Overall (1-6): 5\nRecommendation: Accept", next_prompt=None)
    judge = ScriptedModel(call("read_pdf", {"path": "submission.pdf", "page": 1}, "pdf"),
                          say(verdict.model_dump_json()))
    scaffold.runtime.models["judge"] = judge
    gate = scaffold.loop.gates["research_review"]
    result = asyncio.run(gate.check(gate_context(scaffold)))
    assert result.passed
    assert asyncio.run(gate.check(gate_context(scaffold))).passed
    assert len(judge.inputs) == 2
    assert "PRIVATE" not in str(judge.inputs)
    assert "data:image/png;base64," in str(judge.inputs)
    archived = list((scaffold.context.state_dir / "tabpfn/reviews").glob("*.json"))
    assert len(archived) == 1
    record = json.loads(archived[0].read_text())
    assert record["paper_sha256"] == digest
    assert record["verdict"] == verdict.model_dump()
    assert (scaffold.context.workspace.root / "reviews" / archived[0].name).read_text() == archived[0].read_text()
    scaffold.context.workspace.write_file("REVIEW_REQUEST.json", json.dumps({"paper_sha256": "old"}))
    assert not asyncio.run(gate.check(gate_context(scaffold))).passed


@pytest.mark.parametrize("harness", ["tabpfn", "contrastive-rl"])
@pytest.mark.parametrize("formatting", ["inline", "multiline", "table", "bold_values"])
def test_latest_review_survives_compaction(tmp_path, harness, formatting):
    root = tmp_path / harness
    shutil.copytree(EXAMPLE.parent / harness, root)
    config = root / "scaffold.toml"
    config.write_text(config.read_text().replace("{{SLACK_CHANNEL_ID}}", "CTEST")
                      .replace("{{SLACK_OPERATOR_ID}}", "UTEST").replace("{{SLACK_AFTER_TS}}", "0"))
    scaffold = Scaffold(DropInDirectory.load(root), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())
    submit(scaffold)
    scores = {"Quality": "2", "Clarity": "2", "Significance": "2", "Originality": "3",
              "Overall": "2", "Confidence": "5", "Recommendation": "Reject"}
    separator = "  " if formatting == "inline" else "\n\n"
    ratings = separator.join(f"**{name}:** {value}" for name, value in scores.items())
    if formatting == "table":
        ratings = "| Criterion | Rating |\n| --- | --- |\n" + "\n".join(
            f"| {name} | {value} |" for name, value in scores.items())
    elif formatting == "bold_values":
        ratings = separator.join(f"**{name}:** **{value}**" for name, value in scores.items())
    feedback = separator.join([
        "**Summary:** " + "Full reviewer discussion. " * 1000,
        "**Strengths:** (1) *Useful problem:* Context for the review.",
        "**Weaknesses:**",
        "1. *Confounded baselines:* Detailed reviewer evidence.",
        "2. **Missing ablation:** More detailed evidence.",
        "(3) *Binomial noise:* More detailed evidence.",
        ratings,
    ])
    verdict = Verdict(passed=False, feedback=feedback, next_prompt=None)

    async def judge(*args, **kwargs):
        return verdict

    scaffold.runtime.judge = judge
    gate = scaffold.loop.gates["research_review"]
    assert not asyncio.run(gate.check(gate_context(scaffold))).passed
    workspace = scaffold.context.workspace
    status = json.loads(workspace.read_file("REVIEW_STATUS.json"))
    assert status["verdict"] == verdict.model_dump()
    assert json.loads(workspace.read_file(status["review_path"]))["verdict"] == verdict.model_dump()
    workspace.write_file("PLAN.md", "Old plan. " * 10000)
    research = scaffold.assembly.research_context("pi")
    assert json.dumps(status, indent=2) in research
    assert "truncated" in research

    assert not asyncio.run(gate.check(gate_context(scaffold))).passed
    assert json.loads(workspace.read_file("REVIEW_STATUS.json")) == status
    (root / "prompts/review.md").write_text("Updated review rubric")
    verdict = Verdict(passed=True, feedback="Overall (1-6): 5\nRecommendation: Accept\n### Remaining limitation",
                      next_prompt=None)
    assert asyncio.run(gate.check(gate_context(scaffold))).passed
    latest = json.loads(workspace.read_file("REVIEW_STATUS.json"))
    assert latest["review_path"] != status["review_path"]
    assert latest["verdict"] == verdict.model_dump()

    scaffold.context.resources = scaffold.context.steering = None
    scaffold.strategy.options = scaffold.strategy.options.model_copy(update={"trigger_tokens": 50000,
                                                                            "keep_recent_items": 1})
    summary = ScriptedModel(say("The review was lost from this handoff."))
    history = [{"role": "user", "content": feedback * 10}, {"role": "assistant", "content": "Old review received."},
               {"role": "user", "content": "Finalize"}]
    selected = asyncio.run(scaffold.runtime._select_input(CallModelData(
        model_data=ModelInputData(input=history, instructions=""), agent=Agent(name="pi", model=summary), context=None)))
    assert "compaction:pi" in scaffold.context.usage.by_source
    tail = selected.input[-1]["content"]
    assert json.dumps(latest, indent=2) in tail
    assert "Confounded baselines" not in str(selected.input)
    assert "<research_context>" not in str(history)


def test_finalization_reserve_does_not_claim_acceptance(scaffold):
    ctx = scaffold.context
    ctx.usage.started_at = ctx.now() - timedelta(hours=5, minutes=1)
    assert scaffold.loop.reserve_reason(ctx)
    assert not ctx.budget.exhausted(ctx.usage, now=ctx.now())


def test_reserve_interrupts_research_and_persists_final_phase(scaffold, monkeypatch):
    monkeypatch.setattr(sys.modules["crux_drop_in.tabpfn_extensions"], "cleanup_pods", lambda env: {})
    calls = []

    async def run(prompt, *, workflow):
        calls.append(workflow)
        if workflow == "research":
            await asyncio.Event().wait()
        return TurnOutcome(final_output="Packaged", completed=True)

    async def check(ctx):
        return GateResult(gate="final_review", passed=True, feedback="Packaged")

    ctx = scaffold.context
    ctx.resources = None
    ctx.budget = ctx.budget.model_copy(update={"max_wall_seconds": 2})
    ctx.usage.started_at = ctx.now()
    scaffold.loop.options = scaffold.loop.options.model_copy(update={"reserve_wall_seconds": 1.95, "poll_seconds": 0.01})
    scaffold.loop.gates["final_review"] = SimpleNamespace(check=check)
    outcome = asyncio.run(scaffold.loop.run(SimpleNamespace(run=run), scaffold.drop_in, ctx,
                                            scaffold.state, scaffold.store))
    assert outcome.status == "completed"
    assert calls == ["research", "finalize"]
    assert scaffold.store.load().phase_index == 2
    assert json.loads(ctx.workspace.read_file("FINALIZATION_REASON.json")) == {"reason": "time reserve"}


@pytest.mark.parametrize("resource,amount,expected", [("models", 180, "model spend reserve"),
                                                      ("gpu", 90, "gpu reserve")])
def test_reported_spend_enters_reserve(scaffold, resource, amount, expected):
    from crux_scaffold.resources import ResourceMetric, ResourceSnapshot

    ctx = scaffold.context
    ctx.usage.started_at = ctx.now()
    ctx.resources._snapshots[resource] = ResourceSnapshot(
        as_of=ctx.now(), metrics={"cost_usd": ResourceMetric(value=amount, unit="USD")})
    assert scaffold.loop.reserve_reason(ctx) == expected


def test_deadline_cancels_an_unlimited_inflight_turn_and_saves_state(scaffold, monkeypatch):
    cancelled = []
    monkeypatch.setattr(sys.modules["crux_drop_in.tabpfn_extensions"], "cleanup_pods", lambda env: {})

    async def run(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    ctx = scaffold.context
    ctx.resources = None
    ctx.budget = ctx.budget.model_copy(update={"max_wall_seconds": 0.05})
    ctx.usage.started_at = ctx.now()
    scaffold.state.phase_index = 1
    runtime = SimpleNamespace(run=run)
    outcome = asyncio.run(scaffold.loop.run(runtime, scaffold.drop_in, ctx, scaffold.state, scaffold.store))
    assert outcome.status == "budget_exhausted"
    assert cancelled == [True]
    assert scaffold.store.load().iteration == 0


def test_completed_run_stays_completed_after_deadline(scaffold, monkeypatch):
    monkeypatch.setattr(sys.modules["crux_drop_in.tabpfn_extensions"], "cleanup_pods", lambda env: {})
    scaffold.context.usage.started_at = scaffold.context.now() - timedelta(hours=7)
    scaffold.state.phase_index = 2
    outcome = asyncio.run(scaffold.loop.run(SimpleNamespace(), scaffold.drop_in, scaffold.context,
                                            scaffold.state, scaffold.store))
    assert outcome.status == "completed"


def test_cleanup_deletes_only_exact_run_owned_pods_and_checks_pagination(scaffold):
    extension = sys.modules["crux_drop_in.tabpfn_extensions"]
    calls = []

    def request(method, path):
        calls.append((method, path))
        if method == "DELETE":
            return {}
        if "cursor=next" in path:
            return {"pods": [{"id": "ours", "env": {"CRUX_RUN_SLUG": "tabpfn-test"}}],
                    "pagination": {"hasNextPage": False, "nextCursor": None}}
        return {"pods": [{"id": "other", "env": {"CRUX_RUN_SLUG": "other-run"}}],
                "pagination": {"hasNextPage": True, "nextCursor": "next"}}

    outcome = extension.cleanup_pods(ENV, request=request)
    assert outcome == {"terminated": ["ours"], "failed": [], "discovery_complete": True}
    assert [path for method, path in calls if method == "DELETE"] == ["pods/ours"]


def test_final_gate_preserves_reject_and_requires_current_evidence(scaffold):
    digest = submit(scaffold)
    verdict = Verdict(passed=False, feedback="Overall (1-6): 2\nRecommendation: Reject", next_prompt=None)

    async def judge(*args, **kwargs):
        return verdict

    scaffold.runtime.judge = judge
    gate = scaffold.loop.gates["final_review"]
    ctx = gate_context(scaffold, "finalize")
    assert not asyncio.run(gate.check(ctx)).passed
    workspace = scaffold.context.workspace
    status = json.loads(workspace.read_file("REVIEW_STATUS.json"))
    for path, text in {"README.md": "Run bash reproduce.sh", "reproduce.sh": "echo evidence",
                       "results/index.html": "<html lang='en'><title>Results</title><h1>Results</h1></html>",
                       "COMPLETION_REPORT.md": f"{digest}\n{status['review_path']}\nReject",
                       "evidence/result.txt": "evidence"}.items():
        workspace.write_file(path, text)
    workspace.write_file("evidence/claims.json", json.dumps([
        {"claim": "Test only", "artifact": "evidence/result.txt", "command": "cat evidence/result.txt",
         "sha256": hashlib.sha256(b"evidence").hexdigest()}]))
    assert not asyncio.run(gate.check(ctx)).passed
    (scaffold.context.state_dir / "tabpfn/reserve.json").write_text('{"reason":"time reserve"}')
    workspace.write_file("reproduce.sh", "exit 1")
    assert not asyncio.run(gate.check(ctx)).passed
    workspace.write_file("reproduce.sh", "echo evidence")
    assert asyncio.run(gate.check(ctx)).passed
    assert json.loads(workspace.read_file("RUN_OUTCOME.json"))["accepted"] is False
    workspace.write_file("evidence/result.txt", "changed")
    assert not asyncio.run(gate.check(ctx)).passed


def test_render_prompts_still_renders_the_drop_in():
    spec = importlib.util.spec_from_file_location("tabpfn_render_prompts", EXAMPLE / "render_prompts.py")
    render_prompts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_prompts)
    rendered = render_prompts.render()
    assert rendered.startswith("# TabPFN run: prompts")
    # A fenced template inside a prompt keeps its line breaks, and the file's block uses a longer fence around it.
    assert "\nResources\nTime <elapsed> h of <total> h" in rendered
    assert "````text\n" in rendered
    assert render_prompts.unwrap("a\nb\n```text\nc\nd\n```\ne\nf") == "a b\n```text\nc\nd\n```\ne f"
