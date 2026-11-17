"""Experiment spending is shared and independent of scaffold inference."""

import asyncio
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from crux_scaffold import experiment_api as api
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMonitor
from crux_scaffold.usage import UsageLedger


def environment(tmp_path):
    return {"OPENROUTER_API_KEY": "fixture-agent", "OPENROUTER_EXPERIMENT_API_KEY": "fixture-experiment",
            "OPENROUTER_EXPERIMENT_BUDGET_USD": "4", "RUN_SLUG": "vf-smoke",
            "OPENROUTER_EXPERIMENT_STATE_DIR": str(tmp_path / "credits")}


def key(usage=0.25, remaining=3.75, **changes):
    return {"data": {"usage": usage, "limit": 4, "limit_remaining": remaining,
                     "limit_reset": None, "byok_usage": 0, **changes}}


def response(identifier="gen-one", cost=0.1, **usage):
    return {"id": identifier, "model": "openai/model", "choices": [{"message": {"content": "B"}}],
            "usage": {"cost": cost, "is_byok": False, "prompt_tokens": 12, "completion_tokens": 3, **usage}}


def transport(monkeypatch, replies):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        assert request.get_header("Authorization") == "Bearer fixture-experiment"
        assert request.full_url.startswith("https://openrouter.ai/api/v1/")
        assert timeout > 0
        result = replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(api._HTTP, "open", open_request)
    return requests


def test_separate_credential_and_shared_provider_total(monkeypatch, tmp_path):
    env = environment(tmp_path)
    requests = transport(monkeypatch, [key(), response(), key(usage=0.35, remaining=3.65)])
    result = api.complete(env, "openai/model", "Which option?", provider="openai", max_tokens=64,
                          agent="worker-a", experiment="pilot")
    assert result["answer"] == "B"
    assert json.loads(requests[1].data)["provider"] == {"only": ["openai"], "allow_fallbacks": False}
    status = api.status(env)
    assert status["spent_usd"] == 0.35
    assert status["remaining_usd"] == 3.65
    assert status["receipt_cost_usd"] == 0.1
    assert status["account_balance_usd"] is None
    assert "fixture-" not in json.dumps(status)


@pytest.mark.parametrize("change", [{"OPENROUTER_EXPERIMENT_API_KEY": "fixture-agent"},
                                    {"OPENROUTER_EXPERIMENT_API_KEY": ""},
                                    {"OPENROUTER_EXPERIMENT_BUDGET_USD": "nan"}])
def test_invalid_credentials_fail_before_network(tmp_path, change):
    env = {**environment(tmp_path), **change}
    with pytest.raises(InvalidDropInError):
        api.validate(env)


@pytest.mark.parametrize("changes", [{"limit": None}, {"limit": 5}, {"limit_reset": "daily"},
                                      {"usage": -1}, {"byok_usage": 0.01}])
def test_unbounded_resetting_malformed_or_byok_keys_are_refused(monkeypatch, tmp_path, changes):
    transport(monkeypatch, [key(**changes)])
    with pytest.raises(api.ExperimentError):
        api.status(environment(tmp_path))


def test_zero_and_tighter_workspace_budgets_are_respected(monkeypatch, tmp_path):
    transport(monkeypatch, [key(budgets=[{"remaining_usd": 0}])])
    assert api.status(environment(tmp_path))["remaining_usd"] == 0


def test_smoke_allowance_can_be_lower_than_an_explicit_provider_backstop(monkeypatch, tmp_path):
    transport(monkeypatch, [key(limit=50, remaining=50, usage=0)])
    result = api.status({**environment(tmp_path), "OPENROUTER_EXPERIMENT_KEY_CAP_USD": "50"})
    assert result["remaining_usd"] == 4
    assert result["provider_limit_usd"] == 50


def test_explicit_byok_mode_counts_upstream_spend_and_requires_provider_cap(monkeypatch, tmp_path):
    env = {**environment(tmp_path), "OPENROUTER_EXPERIMENT_BILLING_MODE": "byok"}
    transport(monkeypatch, [key(usage=.1, byok_usage=.5, include_byok_in_limit=True),
                           response(cost=.001, is_byok=True, cost_details={"upstream_inference_cost": .02}),
                           key(usage=.101, byok_usage=.52, include_byok_in_limit=True)])
    result = api.complete(env, "openai/model", "Q", provider="openai", max_tokens=64, agent="a", experiment="e")
    assert result["receipt"]["cost_usd"] == pytest.approx(.021)
    assert result["receipt"]["openrouter_cost_usd"] == .001
    assert result["receipt"]["upstream_cost_usd"] == .02
    assert api.status(env)["spent_usd"] == pytest.approx(.621)


def test_byok_without_an_inclusive_key_cap_is_refused(monkeypatch, tmp_path):
    env = {**environment(tmp_path), "OPENROUTER_EXPERIMENT_BILLING_MODE": "byok"}
    transport(monkeypatch, [key(byok_usage=0, include_byok_in_limit=False)])
    with pytest.raises(api.ExperimentError, match="include BYOK"):
        api.status(env)


def test_transient_in_flight_hold_retries_without_changing_credentials(monkeypatch, tmp_path):
    body = {"error": {"metadata": {"limit_source": "openrouter_in_flight_budget"}}}
    error = HTTPError("https://openrouter.ai", 402, "held", {"Retry-After": "1"},
                      io.BytesIO(json.dumps(body).encode()))
    requests = transport(monkeypatch, [key(), error, response()])
    waits = []
    monkeypatch.setattr(api.time, "sleep", waits.append)
    api.complete(environment(tmp_path), "openai/model", "Q", provider="openai", max_tokens=64,
                 agent="a", experiment="e")
    assert waits == [1]
    assert len(requests) == 3


def test_exhausted_experiments_do_not_exhaust_run_budget(monkeypatch, tmp_path):
    env = environment(tmp_path)
    provider = api.OpenRouterExperiments("experiments", api.ExperimentOptions())
    provider.validate(env)
    ctx = SimpleNamespace(env=env, now=lambda: datetime.now(UTC), usage=UsageLedger(),
                          workspace=SimpleNamespace(root=tmp_path))
    monitor = ResourceMonitor({"experiments": provider})
    transport(monkeypatch, [key(usage=4, remaining=0), key(usage=4, remaining=0)])
    asyncio.run(monitor.refresh(ctx))
    assert monitor.snapshot("experiments").metrics["remaining_usd"].value == 0
    assert not monitor.exhausted()
    with pytest.raises(api.ExperimentError, match="exhausted"):
        api.complete(env, "openai/model", "Q", provider="openai", max_tokens=64, agent="a", experiment="x")
    assert ctx.usage.total_tokens == 0


def test_resume_keeps_identity_and_detects_replaced_key(monkeypatch, tmp_path):
    env = environment(tmp_path)
    transport(monkeypatch, [key(), key(usage=0.5, remaining=3.5), key()])
    api.status(env)
    assert api.status(env)["spent_usd"] == 0.5
    with pytest.raises(api.ExperimentError, match="identity"):
        api.status({**env, "OPENROUTER_EXPERIMENT_API_KEY": "replacement"})


def test_duplicate_receipts_are_atomic_and_do_not_double_count(monkeypatch, tmp_path):
    env = environment(tmp_path)
    transport(monkeypatch, [key(usage=0, remaining=4), key(usage=0, remaining=4)])
    api.status(env)
    receipt = {"generation_id": "gen-one", "cost_usd": 0.1, "agent": "a", "experiment": "e"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: api.record(env, receipt), range(8)))
    result = api.status(env)
    assert result["receipt_cost_usd"] == 0.1
    assert result["spent_usd"] == 0.1  # provider settlement has not caught up


def test_outage_retains_stale_status_without_leaking_provider_body(monkeypatch, tmp_path):
    env = environment(tmp_path)
    monitor = ResourceMonitor({"experiments": api.OpenRouterExperiments("experiments", api.ExperimentOptions())})
    ctx = SimpleNamespace(env=env, now=lambda: datetime.now(UTC), workspace=SimpleNamespace(root=tmp_path))
    error = HTTPError("https://openrouter.ai", 401, "fixture-experiment", {}, io.BytesIO(b"fixture-experiment"))
    transport(monkeypatch, [key(), error])
    asyncio.run(monitor.refresh(ctx))
    asyncio.run(monitor.refresh(ctx, force=True))
    assert monitor.status("experiments") == "stale"
    assert "fixture-experiment" not in monitor.describe(ctx.now())
    assert "retaining" in monitor.describe(ctx.now())


def test_missing_usage_is_unknown_not_a_free_request(monkeypatch, tmp_path):
    result = response()
    del result["usage"]["cost"]
    transport(monkeypatch, [key(), result])
    with pytest.raises(api.ExperimentError, match="usage"):
        api.complete(environment(tmp_path), "openai/model", "Q", provider="openai", max_tokens=64,
                     agent="a", experiment="e")


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                       ("WEBP", "image/webp"), ("GIF", "image/gif")])
def test_images_use_the_same_isolated_call_and_receipt(monkeypatch, tmp_path, format, mime):
    path = tmp_path / "image.bin"
    Image.new("RGB", (2, 2), "red").save(path, format=format)
    requests = transport(monkeypatch, [key(), response()])
    result = api.complete(environment(tmp_path), "openai/model", "Describe this image.", provider="openai",
                          max_tokens=64, agent="a", experiment="vision", image_files=[path])
    payload = json.loads(requests[1].data)
    assert payload["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64," +
                                          base64.b64encode(path.read_bytes()).decode("ascii")}},
    ]}]
    assert payload["provider"] == {"only": ["openai"], "allow_fallbacks": False}
    assert result["receipt"]["cost_usd"] == 0.1
    assert result["receipt"]["billing_mode"] == "credits"
    assert result["receipt"]["image_count"] == 1
    assert "image.bin" not in json.dumps(result)
    with api._database(environment(tmp_path)) as db:
        assert db.execute("SELECT SUM(cost) FROM receipts").fetchone()[0] == 0.1


def test_cli_accepts_multiple_images_in_order(monkeypatch, tmp_path, capsys):
    images = [tmp_path / f"{color}.png" for color in ("red", "blue")]
    for path, color in zip(images, ("red", "blue"), strict=True):
        Image.new("RGB", (2, 2), color).save(path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Compare the images.")
    requests = transport(monkeypatch, [key(), response()])
    assert api.main(["call", "--model", "openai/model", "--provider", "openai", "--agent", "a",
                     "--experiment", "vision", "--max-tokens", "64", "--prompt-file", str(prompt),
                     "--image-file", str(images[0]), "--image-file", str(images[1])],
                    env=environment(tmp_path)) == 0
    content = json.loads(requests[1].data)["messages"][0]["content"]
    assert [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in content[1:]] == [
        path.read_bytes() for path in images]
    assert json.loads(capsys.readouterr().out)["receipt"]["image_count"] == 2


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid", "unsupported", "oversized"])
def test_invalid_images_fail_without_network_or_content_leaks(monkeypatch, tmp_path, kind):
    path = tmp_path / "private-input"
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid":
        path.write_bytes(b"private image payload")
    elif kind == "unsupported":
        Image.new("RGB", (2, 2)).save(path, format="BMP")
    elif kind == "oversized":
        monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 128)
        path.write_bytes(b"x" * 129)
    requests = transport(monkeypatch, [])
    with pytest.raises(api.ExperimentError) as error:
        api.complete(environment(tmp_path), "openai/model", "private prompt", provider="openai",
                     max_tokens=64, agent="a", experiment="vision", image_files=[path])
    assert "private" not in str(error.value)
    assert not requests


def test_encoded_request_size_is_bounded_before_submission(monkeypatch, tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(path)
    monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 512)
    requests = transport(monkeypatch, [key()])
    with pytest.raises(api.ExperimentError, match="request exceeds"):
        api.complete(environment(tmp_path), "openai/model", "Q" * 512, provider="openai",
                     max_tokens=64, agent="a", experiment="vision", image_files=[path])
    assert all(request.data is None for request in requests)


def test_images_cannot_bypass_exhaustion_or_billing_mode(monkeypatch, tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(path)
    requests = transport(monkeypatch, [key(usage=4, remaining=0)])
    with pytest.raises(api.ExperimentError, match="exhausted"):
        api.complete(environment(tmp_path), "openai/model", "Q", provider="openai",
                     max_tokens=64, agent="a", experiment="vision", image_files=[path])
    assert len(requests) == 1
    transport(monkeypatch, [key(), response(is_byok=True)])
    with pytest.raises(api.ExperimentError, match="billing mode"):
        api.complete(environment(tmp_path), "openai/model", "Q", provider="openai",
                     max_tokens=64, agent="a", experiment="vision", image_files=[path])
