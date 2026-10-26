import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.loop import RunState, StateFile
from crux_scaffold.resources import (
    RESOURCE_PROVIDERS,
    ResourceMetric,
    ResourceOptions,
    ResourceProvider,
    ResourceSnapshot,
)
from crux_scaffold.tools import TOOLS, Arguments
from crux_scaffold.usage import Budget, UsageLedger

from scripted import ScriptedModel, call, say

START = datetime(2026, 10, 15, tzinfo=UTC)


@RESOURCE_PROVIDERS.register
class ScriptedResource(ResourceProvider):
    type_name = "scripted-resource"
    Options = ResourceOptions

    async def read(self, ctx):
        return ResourceSnapshot(as_of=ctx.now(), metrics={
            "cost_usd": ResourceMetric(value=ctx.usage.total_tokens / 100, unit="USD"),
            "total_tokens": ResourceMetric(value=ctx.usage.total_tokens, unit="tokens")})


def configure(root, text):
    with (root / "scaffold.toml").open("a") as config:
        config.write("\n" + text + "\n")


def test_every_call_gets_current_budget_and_coding_usage_without_history_accumulation(drop_in_dir, assemble):
    configure(drop_in_dir, '[resources.models]\ntype = "scripted-resource"')
    pm = ScriptedModel(call("engineer", {"brief": "code"}, "p1"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    scaffold.context.now = lambda: START
    asyncio.run(scaffold.run())
    assert len(pm.instructions) == 2
    assert all(instructions.count("<resource_status>") == 1 for instructions in pm.instructions)
    assert "total tokens: 0" in pm.instructions[0]
    assert "total tokens: 165" in pm.instructions[1]
    assert "model spend: 1.650000 USD" in pm.instructions[1]
    assert "elapsed time: 0s" in pm.instructions[0]
    assert all("<resource_status>" not in str(items) for items in pm.inputs)


def test_context_trimming_cannot_remove_status_and_delegates_receive_it(drop_in_dir, assemble):
    reviewer = ScriptedModel(say("fine"))
    pm = ScriptedModel(call("reviewer", {"input": "review"}, "p1"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer})
    scaffold.strategy.select = lambda items: items[-1:]
    asyncio.run(scaffold.run())
    assert reviewer.instructions[0].startswith("You are the reviewer.")
    assert "<resource_status>" in reviewer.instructions[0]
    assert "total tokens: 15" in reviewer.instructions[0]


def test_on_demand_tool_refreshes_the_same_resource_report(drop_in_dir, assemble):
    configure(drop_in_dir, '[resources.models]\ntype = "scripted-resource"')
    scaffold = assemble(drop_in_dir)
    scaffold.context.usage.started_at = START
    scaffold.context.now = lambda: START + timedelta(seconds=15)
    scaffold.context.usage.add("engineer", 100, 50)
    report = asyncio.run(TOOLS.create("budget_status", {}).invoke(scaffold.context, Arguments()))
    assert "elapsed time: 15s" in report
    assert "model spend: 1.500000 USD" in report
    assert "models" in report


@pytest.mark.parametrize("setting", ["max_wall_seconds = 10", "max_cost_usd = 1"])
def test_limits_stop_before_next_iteration_and_restart_keeps_start(drop_in_dir, assemble, tmp_path, setting):
    configure(drop_in_dir, '[resources.models]\ntype = "scripted-resource"\n[budget]\n' + setting)
    state = RunState(usage=UsageLedger(started_at=START))
    state.usage.add("engineer", 100, 50)
    StateFile(tmp_path / "state").save(state)
    pm = ScriptedModel()
    scaffold = assemble(drop_in_dir, {"pm": pm})
    scaffold.context.now = lambda: START + timedelta(seconds=20)
    assert asyncio.run(scaffold.run()).status == "budget_exhausted"
    assert pm.instructions == []
    assert StateFile(tmp_path / "state").load().usage.started_at == START


def test_start_and_usage_are_saved_even_when_first_turn_is_interrupted(drop_in_dir, assemble, tmp_path):
    scaffold = assemble(drop_in_dir)
    scaffold.context.now = lambda: START

    async def interrupted(*args, **kwargs):
        assert StateFile(tmp_path / "state").load().usage.started_at == START
        scaffold.context.usage.add("pm", 10, 5)
        raise asyncio.CancelledError

    scaffold.runtime.run = interrupted
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scaffold.run())
    saved = StateFile(tmp_path / "state").load().usage
    assert saved.started_at == START and saved.total_tokens == 15


def test_resource_limits_stop_the_loop(drop_in_dir, assemble):
    configure(drop_in_dir, '[resources.api]\ntype = "scripted-resource"\nlimits = { cost_usd = 0 }')
    scaffold = assemble(drop_in_dir)
    assert asyncio.run(scaffold.run()).status == "budget_exhausted"


def test_model_budget_requires_a_declared_provider(drop_in_dir, assemble):
    configure(drop_in_dir, '[budget]\nmax_cost_usd = 1')
    with pytest.raises(InvalidDropInError, match="model_resource"):
        assemble(drop_in_dir)


@pytest.mark.parametrize("field,value", [("max_cost_usd", -1), ("max_cost_usd", float("nan")),
                                        ("max_wall_seconds", float("inf")), ("max_total_tokens", -1)])
def test_invalid_limits_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Budget(**{field: value})


def test_remote_and_local_tokens_are_not_added():
    ledger = UsageLedger(started_at=START)
    ledger.add("pm", 100, 50)
    budget = Budget(max_total_tokens=200)
    assert not budget.exhausted(ledger, reported_tokens=150)
    assert budget.exhausted(ledger, reported_tokens=200)
