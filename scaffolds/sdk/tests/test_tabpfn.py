import asyncio
import hashlib
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_blind_review import submission_pdf

from crux_scaffold.agent_runtimes.base import TurnOutcome, Verdict
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.gates import GateContext, GateResult
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

from scripted import ScriptedModel, call, say

EXAMPLE = Path(__file__).resolve().parents[1] / "run-harness/tabpfn"
ENV = {"CRUX_MODEL": "gpt-test", "RUN_SLUG": "tabpfn-test", "SLACK_BOT_TOKEN": "test",
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
