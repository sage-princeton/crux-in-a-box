import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import (
    RESOURCE_PROVIDERS,
    ResourceMetric,
    ResourceMonitor,
    ResourceOptions,
    ResourceProvider,
    ResourceReadError,
    ResourceSnapshot,
)

START = datetime(2026, 10, 15, 12, tzinfo=UTC)
NOW = START + timedelta(hours=1)
ENV = {"COST_TRACKER_URL": "https://tracker.example/cost", "COST_TRACKER_TOKEN": "secret-test",
       "RUN_SLUG": "CRUX-Test"}
PAYLOAD = {"source": "langfuse", "run_slug": "CRUX-Test", "environment": "crux-test",
           "from_timestamp": START.isoformat(), "to_timestamp": NOW.isoformat(),
           "retrieved_at": NOW.isoformat(), "has_data": True, "observations": 4,
           "total_spend": 1.25, "total_tokens": 150, "input_tokens": 120, "output_tokens": 30}


def context(env=None, clock=None):
    return SimpleNamespace(env=ENV if env is None else env, usage=SimpleNamespace(started_at=START),
                           now=clock or (lambda: NOW))


def snapshot(value=1.25):
    return ResourceSnapshot(as_of=NOW, metrics={"cost_usd": ResourceMetric(value=value, unit="USD")})


class ScriptedResource(ResourceProvider):
    type_name = "test-resource"

    def __init__(self, *results, **options):
        super().__init__("models", ResourceOptions(**options))
        self.results, self.calls = list(results), 0

    async def read(self, ctx):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_metric_values_and_resource_limits_are_finite_and_nonnegative(value):
    with pytest.raises(ValidationError):
        ResourceMetric(value=value, unit="USD")
    with pytest.raises(ValidationError):
        ResourceOptions(limits={"credits": value})


def test_resource_options_are_strict_and_snapshots_require_timezone():
    for options in [{"refresh_seconds": -1}, {"timeout_seconds": 0}, {"typo": 1}]:
        with pytest.raises(ValidationError):
            ResourceOptions(**options)
    with pytest.raises(ValidationError):
        ResourceSnapshot(as_of=NOW.replace(tzinfo=None), metrics={})


def test_monitor_refreshes_caches_and_force_refreshes_without_resetting_limits():
    provider = ScriptedResource(snapshot(), snapshot(2), refresh_seconds=60, limits={"cost_usd": 2})
    monitor = ResourceMonitor({"models": provider})
    ctx = context()

    async def run():
        await monitor.refresh(ctx)
        assert monitor.status("models") == "fresh"
        assert not monitor.exhausted()
        await monitor.refresh(ctx)
        assert monitor.status("models") == "cached"
        assert provider.calls == 1
        await monitor.refresh(ctx, force=True)

    asyncio.run(run())
    assert monitor.exhausted()
    assert monitor.snapshot("models").metrics["cost_usd"].value == 2
    report = monitor.describe(NOW + timedelta(seconds=10))
    assert "cost_usd: 2 USD" in report and "remaining 0 USD" in report
    assert "age 10s" in report and "2026-10-15T13:00:00Z" in report


def test_refresh_failures_preserve_snapshot_and_never_report_a_fake_zero_or_exception():
    provider = ScriptedResource(snapshot(2), RuntimeError("secret-test"), limits={"cost_usd": 2})
    monitor = ResourceMonitor({"models": provider, "credits": ScriptedResource(RuntimeError("secret-test"))})

    async def run():
        await monitor.refresh(context())
        await monitor.refresh(context())

    asyncio.run(run())
    assert monitor.status("models") == "stale"
    assert monitor.status("credits") == "unavailable"
    assert monitor.snapshot("credits") is None
    assert monitor.exhausted()
    report = monitor.describe(NOW)
    assert "secret-test" not in report
    assert "cost_usd: 2 USD" in report and "unavailable" in report and "stale" in report


def test_monitor_times_out_providers_and_refreshes_independently():
    class SlowResource(ScriptedResource):
        async def read(self, ctx):
            await asyncio.Event().wait()

    monitor = ResourceMonitor({"slow": SlowResource(timeout_seconds=0.01), "models": ScriptedResource(snapshot())})
    asyncio.run(monitor.refresh(context()))
    assert monitor.status("slow") == "unavailable"
    assert monitor.status("models") == "fresh"


def test_empty_refresh_retains_populated_snapshot_and_its_exhausted_limit():
    empty = ResourceSnapshot(as_of=NOW, metrics={}, note="no observations yet")
    provider = ScriptedResource(empty, snapshot(2), empty, limits={"cost_usd": 2})
    monitor = ResourceMonitor({"models": provider})

    async def run():
        await monitor.refresh(context())
        assert monitor.status("models") == "fresh"
        assert "no observations yet" in monitor.describe(NOW)
        await monitor.refresh(context())
        assert monitor.exhausted()
        await monitor.refresh(context())

    asyncio.run(run())
    assert monitor.exhausted()
    assert monitor.status("models") == "stale"
    assert monitor.snapshot("models").metrics["cost_usd"].value == 2
    assert "retaining last successful measurements" in monitor.describe(NOW)


def test_sanitized_resource_errors_are_visible_and_clear_after_success():
    action = "AWS authentication expired; run aws configure sso --use-device-code"
    provider = ScriptedResource(ResourceReadError(action), snapshot())
    monitor = ResourceMonitor({"aws": provider})

    async def run():
        await monitor.refresh(context())
        assert monitor.status("aws") == "unavailable"
        assert action in monitor.describe(NOW)
        await monitor.refresh(context())

    asyncio.run(run())
    assert monitor.status("aws") == "fresh"
    assert action not in monitor.describe(NOW)


def test_partial_refresh_retains_previous_cost_and_timestamp_without_merging_live_counts():
    previous = ResourceSnapshot(as_of=NOW, metrics={"cost_usd": ResourceMetric(value=2, unit="USD"),
                                                   "running_pods": ResourceMetric(value=1, unit="pods")})
    partial = ResourceSnapshot(as_of=NOW + timedelta(minutes=1), metrics={
        "running_pods": ResourceMetric(value=0, unit="pods")}, note="billing pending")
    monitor = ResourceMonitor({"compute": ScriptedResource(previous, partial, limits={"cost_usd": 2})})

    async def run():
        await monitor.refresh(context())
        await monitor.refresh(context())

    asyncio.run(run())
    assert monitor.exhausted()
    assert monitor.status("compute") == "stale"
    assert monitor.snapshot("compute") == previous
    report = monitor.describe(NOW + timedelta(minutes=1))
    assert "incomplete measurements" in report
    assert "age 60s" in report
    assert "running_pods: 1 pods" in report


def test_cache_expires_and_unknown_limited_metric_is_reported():
    clock = [NOW]
    provider = ScriptedResource(ResourceSnapshot(as_of=NOW, metrics={}), snapshot(), refresh_seconds=60,
                                limits={"cost_usd": 5})
    monitor = ResourceMonitor({"models": provider})
    ctx = context(clock=lambda: clock[0])

    async def run():
        await monitor.refresh(ctx)
        assert "cost_usd: unknown" in monitor.describe(clock[0])
        assert not monitor.exhausted()
        clock[0] += timedelta(seconds=60)
        await monitor.refresh(ctx)

    asyncio.run(run())
    assert provider.calls == 2


@pytest.mark.parametrize("env", [{}, {**ENV, "COST_TRACKER_URL": "http://tracker.example"},
                                  {**ENV, "COST_TRACKER_URL": "https://user:pass@tracker.example"},
                                  {**ENV, "RUN_SLUG": ""}])
def test_langfuse_validates_configuration_before_running(env):
    with pytest.raises(InvalidDropInError):
        RESOURCE_PROVIDERS.create("models", {"type": "langfuse"}).validate(env)


def http_response(monkeypatch, payload):
    opener = MagicMock()
    opener.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    monkeypatch.setattr("crux_scaffold.resources.urllib.request.urlopen", opener)
    return opener


def test_langfuse_fetches_exact_run_window_and_reports_remote_usage_without_local_addition(monkeypatch):
    opener = http_response(monkeypatch, PAYLOAD)
    provider = RESOURCE_PROVIDERS.create("models", {"type": "langfuse"})
    provider.validate(ENV)
    result = asyncio.run(provider.read(context()))
    request = opener.call_args.args[0]
    assert json.loads(request.data) == {"run_slug": "CRUX-Test", "from_timestamp": START.isoformat()}
    assert request.get_header("Authorization") == "Bearer secret-test"
    assert opener.call_args.kwargs["timeout"] == 10
    assert result.as_of == NOW
    assert result.metrics["cost_usd"] == ResourceMetric(value=1.25, unit="USD")
    assert result.metrics["total_tokens"].value == 150


def test_langfuse_no_observations_is_unknown_not_zero(monkeypatch):
    http_response(monkeypatch, {**PAYLOAD, "has_data": False, "observations": 0, "total_spend": 0,
                               "total_tokens": 0, "input_tokens": 0, "output_tokens": 0})
    result = asyncio.run(RESOURCE_PROVIDERS.create("models", {"type": "langfuse"}).read(context()))
    assert result.metrics == {}
    assert result.note == "no observations yet"


@pytest.mark.parametrize("change", [{"environment": "other-run"}, {"run_slug": "other-run"},
                                    {"from_timestamp": (START - timedelta(hours=1)).isoformat()},
                                    {"to_timestamp": (START - timedelta(seconds=1)).isoformat()},
                                    {"total_spend": float("nan")}, {"total_tokens": -1},
                                    {"has_data": "false"}, {"observations": 0},
                                    {"retrieved_at": NOW.replace(tzinfo=None).isoformat()}])
def test_langfuse_rejects_incorrect_scope_or_malformed_measurements(monkeypatch, change):
    http_response(monkeypatch, {**PAYLOAD, **change})
    with pytest.raises(ValueError):
        asyncio.run(RESOURCE_PROVIDERS.create("models", {"type": "langfuse"}).read(context()))
