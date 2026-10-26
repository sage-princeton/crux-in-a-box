import asyncio
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from crux_scaffold import external_resources
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.external_resources import AwsCompute, AwsComputeOptions, Runpod, RunpodOptions
from crux_scaffold.resources import ResourceMonitor, ResourceReadError

from scripted import ScriptedModel, call, say

NOW = datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
START = datetime(2026, 10, 14, 12, 15, tzinfo=UTC)
ENV = {"AWS_PROFILE": "test-profile", "RUN_SLUG": "run-one", "RUNPOD_API_KEY": "private-test-value"}


def context(state_dir=None):
    return SimpleNamespace(env=ENV, now=lambda: NOW, usage=SimpleNamespace(started_at=START), state_dir=state_dir)


def aws_group(cost="2.50", quantity="3.0", usage_type="USE1-BoxUsage:t3.medium", unit="Hrs"):
    return {"Keys": ["Name$run-one", usage_type], "Metrics": {
        "UnblendedCost": {"Amount": cost, "Unit": "USD"},
        "UsageQuantity": {"Amount": quantity, "Unit": unit},
    }}


def aws_page(groups=None, token=None):
    result = {"ResultsByTime": [{"TimePeriod": {"Start": "2026-10-14", "End": "2026-10-15"},
                                 "Estimated": True, "Groups": groups if groups is not None else [aws_group()]}]}
    if token:
        result["NextPageToken"] = token
    return result


def mock_aws(monkeypatch, pages, *, auth_failure=False, live_failure=False):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[:3] == ["aws", "--profile", "test-profile"]
        assert "--region" in command and "--no-paginate" in command
        assert kwargs["timeout"] > 0 and kwargs["timeout"] <= 30
        assert kwargs["env"]["AWS_PROFILE"] == "test-profile"
        if "get-caller-identity" in command:
            if auth_failure:
                return subprocess.CompletedProcess(command, 1, "", "private-test-value")
            result = {"Account": "123456789012"}
        elif "get-cost-and-usage" in command:
            specification = json.loads(command[command.index("--cli-input-json") + 1])
            filters = specification["Filter"]["And"]
            assert {"Tags": {"Key": "Name", "Values": ["run-one"],
                             "MatchOptions": ["EQUALS", "CASE_SENSITIVE"]}} in filters
            assert {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": ["123456789012"]}} in filters
            assert {"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}} in filters
            assert specification["TimePeriod"] == {"Start": "2026-10-14", "End": "2026-10-16"}
            assert specification["Granularity"] == "DAILY"
            result = pages.pop(0)
        elif "describe-instances" in command:
            if live_failure:
                return subprocess.CompletedProcess(command, 1, "", "private-test-value")
            specification = json.loads(command[command.index("--cli-input-json") + 1])
            assert {"Name": "tag:Name", "Values": ["run-one"]} in specification["Filters"]
            result = {"Reservations": [{"Instances": [{"InstanceId": "i-one", "State": {"Name": "running"},
                       "Tags": [{"Key": "Name", "Value": "run-one"}]}]}]}
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    monkeypatch.setattr(external_resources.subprocess, "run", run)
    return calls


def billing(pod, *, amount=2.5, records=True):
    rows = [{"podId": pod, "startTime": "2026-10-14T12:00:00Z", "endTime": "2026-10-14T13:00:00Z",
             "totalAmount": amount, "gpuAmount": amount - 0.5, "cpuAmount": 0, "diskAmount": 0.5}] if records else []
    return {"records": rows, "metadata": {"recordCount": len(rows), "query": {
        "podId": pod, "startTime": "2026-10-14T12:00:00Z", "endTime": "2026-10-15T16:00:00Z",
        "bucketSize": "hour"}}}


def pod(pod_id, *, status="RUNNING", count=2, run_slug="run-one"):
    return {"id": pod_id, "status": status, "gpu": {"count": count}, "cost": 1.25 if status == "RUNNING" else 0,
            "env": {"CRUX_RUN_SLUG": run_slug, "SECRET": "private-pod-secret"}, "runtime": {"uptime": 3600}}


def pod_page(*pods, cursor=None):
    return {"pods": list(pods), "pagination": {"nextCursor": cursor, "hasNextPage": cursor is not None}}


def mock_runpod(monkeypatch, responses):
    requests = []

    def open_request(request, *, timeout):
        assert 0 < timeout <= 30
        assert request.get_header("Authorization") == "Bearer private-test-value"
        url = urlsplit(request.full_url)
        assert url.scheme == "https" and url.hostname == "api.runpod.io"
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(external_resources._HTTP, "open", open_request)
    return requests


def test_runpod_discovers_only_owned_pods_created_after_initial_refresh(monkeypatch, tmp_path):
    requests = mock_runpod(monkeypatch, [pod_page(pod("foreign", run_slug="run-two")),
                                        pod_page(pod("foreign", run_slug="run-two"), pod("new-pod")),
                                        billing("new-pod")])
    provider = Runpod("gpu", RunpodOptions())
    assert asyncio.run(provider.read(context(tmp_path))).metrics["cost_usd"].value == 0
    result = asyncio.run(provider.read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 2.5
    assert result.metrics["running_gpus"].value == 2
    queried = [parse_qs(urlsplit(r.full_url).query)["podId"] for r in requests
               if urlsplit(r.full_url).path == "/v2/billing/pods"]
    assert queried == [["new-pod"]]


def test_runpod_retains_discovered_ids_after_termination_and_process_restart(monkeypatch, tmp_path):
    mock_runpod(monkeypatch, [pod_page(pod("short-lived")), billing("short-lived"),
                             pod_page(), billing("short-lived", amount=4)])
    first = Runpod("gpu", RunpodOptions())
    asyncio.run(first.read(context(tmp_path)))
    restored = Runpod("gpu", RunpodOptions())
    result = asyncio.run(restored.read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 4
    assert result.metrics["running_pods"].value == result.metrics["running_gpus"].value == 0
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    saved = files[0].read_text()
    assert json.loads(saved)["pod_ids"] == ["short-lived"]
    assert "private-pod-secret" not in saved and "private-test-value" not in saved


def test_runpod_persists_discovery_before_a_billing_failure(monkeypatch, tmp_path):
    mock_runpod(monkeypatch, [pod_page(pod("brief-pod")), TimeoutError(), pod_page(), billing("brief-pod")])
    with pytest.raises(ValueError):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 2.5


def test_runpod_discovers_across_pages_with_exact_marker_not_name_or_prefix(monkeypatch, tmp_path):
    untagged = {**pod("untagged"), "name": "run-one-training", "env": {}}
    requests = mock_runpod(monkeypatch, [pod_page(untagged, pod("foreign", run_slug="run-one-extra"), cursor="next"),
                                        pod_page(pod("owned")), billing("owned")])
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 2.5
    assert parse_qs(urlsplit(requests[0].full_url).query) == {"includeClusterPods": ["true"], "limit": ["100"]}
    assert parse_qs(urlsplit(requests[1].full_url).query)["cursor"] == ["next"]
    assert json.loads(next(tmp_path.rglob("*.json")).read_text())["pod_ids"] == ["owned"]


@pytest.mark.parametrize("failure", [pod_page(cursor="same"),
                                   pod_page(pod("owned")), {"pods": [], "pagination": {"hasNextPage": True}},
                                   TimeoutError()])
def test_runpod_keeps_discovered_ids_when_a_later_discovery_page_fails(monkeypatch, tmp_path, failure):
    mock_runpod(monkeypatch, [pod_page(pod("owned"), cursor="same"), failure,
                             pod_page(), billing("owned")])
    with pytest.raises((ValueError, KeyError)):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 2.5


@pytest.mark.parametrize("change", ["slug", "start", "corrupt"])
def test_runpod_never_reuses_inventory_for_another_run_or_silently_discards_corruption(monkeypatch, tmp_path, change):
    requests = mock_runpod(monkeypatch, [pod_page(pod("owned")), billing("owned")])
    asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    ctx = context(tmp_path)
    if change == "slug":
        ctx.env = {**ENV, "RUN_SLUG": "another-run"}
    elif change == "start":
        ctx.usage.started_at = NOW
    else:
        next(tmp_path.rglob("*.json")).write_text("not-json")
    with pytest.raises((ResourceReadError, ValidationError)):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(ctx))
    assert len(requests) == 2


def test_runpod_does_not_bill_a_known_pod_reassigned_to_another_run(monkeypatch, tmp_path):
    mock_runpod(monkeypatch, [pod_page(pod("owned")), billing("owned"),
                             pod_page(pod("owned", run_slug="another-run"))])
    asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    with pytest.raises(ResourceReadError, match="changed ownership"):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))


def test_runpod_retains_discovery_during_pending_billing_and_recovers_after_restart(monkeypatch, tmp_path):
    mock_runpod(monkeypatch, [pod_page(), pod_page(pod("new")), billing("new", records=False),
                             pod_page(), billing("new", amount=4)])
    monitor = ResourceMonitor({"gpu": Runpod("gpu", RunpodOptions())})
    ctx = context(tmp_path)
    asyncio.run(monitor.refresh(ctx, force=True))
    asyncio.run(monitor.refresh(ctx, force=True))
    assert monitor.status("gpu") == "fresh"
    assert monitor.snapshot("gpu").metrics["running_pods"].value == 1
    assert monitor.snapshot("gpu").metrics["cost_usd"].stale
    restored = ResourceMonitor({"gpu": Runpod("gpu", RunpodOptions())})
    asyncio.run(restored.refresh(ctx, force=True))
    assert restored.status("gpu") == "fresh"
    assert restored.snapshot("gpu").metrics["cost_usd"].value == 4


def test_runpod_failed_inventory_replace_preserves_saved_ids_and_skips_billing(monkeypatch, tmp_path):
    requests = mock_runpod(monkeypatch, [pod_page(pod("first")), billing("first"),
                                        pod_page(pod("second"))])
    asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    path = next(tmp_path.rglob("*.json"))
    before = path.read_bytes()

    def failed_replace(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(type(path), "replace", failed_replace)
    with pytest.raises(OSError):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert path.read_bytes() == before and list(path.parent.iterdir()) == [path]
    assert len(requests) == 3


def test_aws_uses_run_tag_and_account_across_pages_without_mixing_usage_units(monkeypatch):
    calls = mock_aws(monkeypatch, [aws_page(token="page-two"), aws_page([aws_group("1", "4", "OtherUsage", "GB")])])
    result = asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))
    assert result.as_of == NOW
    assert result.metrics["cost_usd"].value == 3.5
    assert result.metrics["usage_USE1-BoxUsage:t3.medium"].unit == "Hrs"
    assert result.metrics["usage_OtherUsage"].unit == "GB"
    assert result.metrics["running_instances"].value == 1
    assert "24 hours" in result.note and "2026-10-14" in result.note
    cost_calls = [call for call in calls if "get-cost-and-usage" in call]
    second = json.loads(cost_calls[1][cost_calls[1].index("--cli-input-json") + 1])
    assert second["NextPageToken"] == "page-two"


def test_aws_authentication_failure_stops_before_cost_requests_and_hides_errors(monkeypatch):
    calls = mock_aws(monkeypatch, [], auth_failure=True)
    with pytest.raises(ResourceReadError, match="aws configure sso --use-device-code") as error:
        asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))
    assert len(calls) == 1
    assert "private-test-value" not in str(error.value)


@pytest.mark.parametrize("groups", [[aws_group(cost="NaN")], [aws_group(cost="-1")],
                                    [{**aws_group(), "Keys": ["Name$another-run", "BoxUsage"]}]])
def test_aws_rejects_invalid_cost_or_wrong_run(monkeypatch, groups):
    mock_aws(monkeypatch, [aws_page(groups)])
    with pytest.raises(ValueError):
        asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))


def test_aws_repeated_pagination_cursor_is_incomplete_not_a_total(monkeypatch):
    mock_aws(monkeypatch, [aws_page(token="same"), aws_page(token="same")])
    with pytest.raises(ValueError, match="pagination"):
        asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))


def test_aws_pending_billing_keeps_live_count_without_claiming_zero_cost(monkeypatch):
    mock_aws(monkeypatch, [aws_page([])])
    result = asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))
    assert "cost_usd" not in result.metrics
    assert result.metrics["running_instances"].value == 1
    assert "pending" in result.note


def test_aws_retains_billing_when_live_instances_are_unavailable(monkeypatch):
    mock_aws(monkeypatch, [aws_page()], live_failure=True)
    result = asyncio.run(AwsCompute("aws", AwsComputeOptions()).read(context()))
    assert result.metrics["cost_usd"].value == 2.5
    assert "running_instances" not in result.metrics
    assert "live instance count unavailable" in result.note
    assert "private-test-value" not in result.note


def test_aws_executes_a_real_cli_process_with_explicit_profile_and_context_environment(tmp_path):
    executable = tmp_path / "aws"
    log = tmp_path / "calls.jsonl"
    executable.write_text(f'''#!{sys.executable}
import json, os, sys
arguments = sys.argv[1:]
assert arguments[:2] == ["--profile", "test-profile"]
with open(os.environ["CLI_LOG"], "a") as stream:
    stream.write(json.dumps(arguments) + "\\n")
if "get-caller-identity" in arguments:
    result = {{"Account": "123456789012"}}
elif "get-cost-and-usage" in arguments:
    result = {aws_page()!r}
elif "describe-instances" in arguments:
    result = {{"Reservations": []}}
else:
    raise AssertionError(arguments)
print(json.dumps(result))
''')
    executable.chmod(0o755)
    ctx = context()
    ctx.env = {**ENV, "PATH": str(tmp_path), "CLI_LOG": str(log)}
    provider = AwsCompute("aws", AwsComputeOptions())
    provider.validate(ctx.env)
    result = asyncio.run(provider.read(ctx))
    assert result.metrics["cost_usd"].value == 2.5
    assert result.metrics["running_instances"].value == 0
    assert len(log.read_text().splitlines()) == 3
    assert "private-test-value" not in log.read_text()


def test_runpod_scopes_each_pod_and_shows_current_resources_not_invented_gpu_hours(monkeypatch, tmp_path):
    requests = mock_runpod(monkeypatch, [pod_page(pod("pod-one"), pod("pod-two", status="EXITED")),
                                        billing("pod-one"), billing("pod-two", amount=4)])
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 6.5
    assert result.metrics["gpu_cost_usd"].value == 5.5
    assert result.metrics["running_gpus"].value == 2
    assert result.metrics["current_hourly_usd"].value == 1.25
    assert "gpu_hours" not in result.metrics
    assert "hour" in result.note and "2026-10-14" in result.note
    assert "private-pod-secret" not in result.model_dump_json()
    for request, identifier in zip(requests[1:], ["pod-one", "pod-two"], strict=True):
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["podId"] == [identifier]
        assert query["bucketSize"] == ["hour"]
        assert query["startTime"] == ["2026-10-14T12:00:00Z"]
        assert query["endTime"] == ["2026-10-15T16:00:00Z"]


def test_runpod_accepts_equivalent_time_formats_and_additional_query_metadata(tmp_path, monkeypatch):
    body = billing("pod-one")
    body["metadata"]["query"].update(startTime="2026-10-14T12:00:00.000Z",
                                       endTime="2026-10-15T16:00:00+00:00", lastN=None)
    mock_runpod(monkeypatch, [pod_page(pod("pod-one")), body])
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert result.metrics["cost_usd"].value == 2.5


@pytest.mark.parametrize("response", ["x" * external_resources.MAX_BYTES, TimeoutError("private-test-value")])
def test_runpod_bounds_requests_and_does_not_expose_response_data(tmp_path, monkeypatch, response):
    mock_runpod(monkeypatch, [response])
    with pytest.raises(ValueError) as error:
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert "private-test-value" not in str(error.value)


def test_runpod_missing_billing_is_pending_not_free_compute(tmp_path, monkeypatch):
    mock_runpod(monkeypatch, [pod_page(pod("pod-one")), billing("pod-one", records=False)])
    result = asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert "cost_usd" not in result.metrics
    assert result.metrics["running_gpus"].value == 2
    assert "pending" in result.note


def test_runpod_monitor_tracks_new_pods_while_billing_is_pending_then_recovers(tmp_path, monkeypatch):
    mock_runpod(monkeypatch, [pod_page(), pod_page(pod("new-pod")), billing("new-pod", records=False),
                             pod_page(pod("new-pod")), billing("new-pod", records=False),
                             pod_page(pod("new-pod")), billing("new-pod")])
    monitor = ResourceMonitor({"gpu": Runpod("gpu", RunpodOptions(limits={"cost_usd": 2}))})
    ctx = context(tmp_path)

    async def run():
        await monitor.refresh(ctx, force=True)
        assert monitor.snapshot("gpu").metrics["running_pods"].value == 0
        assert monitor.snapshot("gpu").metrics["cost_usd"].value == 0
        for _ in range(2):
            await monitor.refresh(ctx, force=True)
            pending = monitor.snapshot("gpu")
            assert monitor.status("gpu") == "fresh"
            assert pending.metrics["running_pods"].value == 1
            assert pending.metrics["running_gpus"].value == 2
            assert pending.metrics["current_hourly_usd"].value == 1.25
            assert pending.metrics["cost_usd"].stale
            assert "Tracking 1 persisted pod IDs" in pending.note
            report = monitor.describe(NOW)
            assert "cost_usd: unknown; limit 2; remaining unknown" in report
            assert "Billing pending" in report
            assert not monitor.exhausted()
        await monitor.refresh(ctx, force=True)

    asyncio.run(run())
    billed = monitor.snapshot("gpu")
    assert billed.metrics["running_pods"].value == 1
    assert billed.metrics["cost_usd"].value == 2.5
    assert not billed.metrics["cost_usd"].stale
    assert monitor.exhausted()
    assert "cost_usd: 2.5 USD" in monitor.describe(NOW)
    inventory = next((tmp_path / "resources").glob("runpod-*.json"))
    assert json.loads(inventory.read_text())["pod_ids"] == ["new-pod"]


@pytest.mark.parametrize("change", ["scope", "count", "negative", "time"])
def test_runpod_rejects_misattributed_or_incomplete_billing(tmp_path, monkeypatch, change):
    body = billing("pod-one")
    if change == "scope":
        body["records"][0]["podId"] = "someone-else"
    elif change == "count":
        body["metadata"]["recordCount"] = 2
    elif change == "negative":
        body["records"][0]["totalAmount"] = -1
    else:
        body["records"][0]["startTime"] = "2025-01-01T00:00:00Z"
    mock_runpod(monkeypatch, [pod_page(pod("pod-one")), body])
    with pytest.raises(ValueError):
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))


def test_runpod_does_not_turn_forbidden_or_redirects_into_zero(tmp_path, monkeypatch):
    mock_runpod(monkeypatch, [HTTPError("", 403, "private-test-value", {}, None)])
    with pytest.raises(ValueError) as error:
        asyncio.run(Runpod("gpu", RunpodOptions()).read(context(tmp_path)))
    assert "private-test-value" not in str(error.value)
    assert external_resources._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere") is None


def test_provider_validation_fails_before_calls_without_credentials_or_scope(monkeypatch):
    monkeypatch.setattr(external_resources.shutil, "which", lambda *args, **kwargs: "/test/aws")
    AwsCompute("aws", AwsComputeOptions()).validate(ENV)
    Runpod("gpu", RunpodOptions()).validate(ENV)
    for environment in [{}, {"AWS_PROFILE": "test"}]:
        with pytest.raises(InvalidDropInError):
            AwsCompute("aws", AwsComputeOptions()).validate(environment)
    with pytest.raises(InvalidDropInError):
        Runpod("gpu", RunpodOptions()).validate({})
    monkeypatch.setattr(external_resources.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(InvalidDropInError, match="AWS CLI"):
        AwsCompute("aws", AwsComputeOptions()).validate(ENV)
    for environment in [{"RUNPOD_API_KEY": "test"}, {**ENV, "RUN_SLUG": ""}, {**ENV, "RUN_SLUG": "bad\nslug"}]:
        with pytest.raises(InvalidDropInError):
            Runpod("gpu", RunpodOptions()).validate(environment)
    with pytest.raises(ValidationError):
        RunpodOptions(pod_ids=["pod-one"])


def test_declared_compute_providers_reach_every_model_call_and_reuse_cached_billing(
        monkeypatch, drop_in_dir, assemble):
    with (drop_in_dir / "scaffold.toml").open("a") as configuration:
        configuration.write('''
[resources.aws]
type = "aws-compute"

[resources.gpu]
type = "runpod"
''')
    aws_calls = mock_aws(monkeypatch, [aws_page()])
    pod_requests = mock_runpod(monkeypatch, [pod_page(pod("pod-one")), billing("pod-one", amount=4)])
    monkeypatch.setattr(external_resources.shutil, "which", lambda *args, **kwargs: "/test/aws")
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "read-standing-context"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env={**ENV, "CRUX_MODEL": "test"})
    scaffold.context.now = lambda: NOW
    scaffold.context.usage.started_at = START
    asyncio.run(scaffold.run())
    assert len(pm.instructions) == 2
    for instructions in pm.instructions:
        assert "  aws:" in instructions and "  gpu:" in instructions
        assert "cost_usd: 2.5 USD" in instructions
        assert "cost_usd: 4 USD" in instructions
        assert "running_instances: 1 instances" in instructions
        assert "running_gpus: 2 GPUs" in instructions
        assert "current_hourly_usd: 1.25 USD/hour" in instructions
    assert len([command for command in aws_calls if "get-cost-and-usage" in command]) == 1
    assert len(pod_requests) == 2
