import json

import httpx
import pytest

from review import (
    MAX_EVIDENCE_BYTES,
    CoverageError,
    collect_langfuse,
    collect_sftp,
    evaluate,
    scrub,
    select_reviewer,
)

SECRETS = {
    "MONITORING_LANGFUSE_BASE_URL": "https://langfuse.example",
    "MONITORING_LANGFUSE_PUBLIC_KEY": "public",
    "MONITORING_LANGFUSE_SECRET_KEY": "private",
}


def test_langfuse_cursor_preserves_session_window_and_required_fields():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.params["sessionId"] == "trusted-session"
        assert request.url.params["fromStartTime"] == "1970-01-01T00:00:00+00:00"
        assert request.url.params["toStartTime"] == "1970-01-01T00:05:00+00:00"
        assert "io" in request.url.params["fields"]
        assert "filter" not in request.url.params
        return httpx.Response(
            200,
            json={
                "data": [{"id": str(len(requests)), "sessionId": "trusted-session"}],
                "meta": {"cursor": "next" if len(requests) == 1 else None},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sources = collect_langfuse(client, {"session_id": "trusted-session"}, SECRETS, 0, 300)
    assert [s["id"] for s in sources] == ["observation:1", "observation:2"]
    assert requests[1].url.params["cursor"] == "next"


@pytest.mark.parametrize(
    "body",
    [
        {"data": [{"id": "wrong", "sessionId": "another-session"}], "meta": {}},
        {"data": [], "meta": {"cursor": "repeated"}},
        {"data": "x" * MAX_EVIDENCE_BYTES},
    ],
)
def test_incomplete_or_misattributed_langfuse_is_not_accepted(body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        with pytest.raises(CoverageError):
            collect_langfuse(client, {"session_id": "trusted-session"}, SECRETS, 0, 300)


def test_model_family_separation_uses_observed_and_declared_models():
    sources = [{"kind": "langfuse", "data": {"model": "claude-example"}}]
    assert select_reviewer(["openai/gpt-example"], sources, []) == "openai/gpt-example"
    with pytest.raises(CoverageError, match="different model family"):
        select_reviewer(["anthropic/claude-example", "openai/gpt-example"], sources, ["openai"])
    with pytest.raises(CoverageError, match="unknown"):
        select_reviewer(
            ["openai/gpt-example"], [{"kind": "langfuse", "data": {"model": "unknown"}}], []
        )


@pytest.mark.parametrize(
    "model",
    [
        "google/gemini-example",
        "deepseek/deepseek-example",
        "other/claude-example",
        "claude-example",
    ],
)
def test_disallowed_reviewers_fail_before_network_access(model):
    with pytest.raises(CoverageError, match="explicit"):
        select_reviewer(["anthropic/claude-example", model], [], ["openai"])
    with pytest.raises(CoverageError, match="explicit"):
        evaluate(None, model, "fixture", {})


def test_large_reviewer_input_fails_before_network_access():
    with pytest.raises(CoverageError, match="128 KiB"):
        evaluate(None, "anthropic/claude-example", "fixture", {"data": "x" * (128 * 1024)})


def test_unverified_or_escaping_sftp_sources_fail_before_connecting():
    with pytest.raises(CoverageError):
        collect_sftp({"boundary_verified": False}, "127.0.0.1", "")
    for path in ["/etc/passwd", "/exports/../private", "/exports/.env"]:
        with pytest.raises(ValueError):
            collect_sftp({"boundary_verified": True, "paths": [path]}, "127.0.0.1", "")


def test_reviewer_cannot_invent_citations_or_acquire_tools():
    report = {
        "summary": "suspicious",
        "workload_profile": "research",
        "next_source_ids": [],
        "coverage_gaps": [],
        "findings": [
            {
                "category": "exfiltration",
                "detector_id": "unexpected_upload",
                "anchor_id": "invented",
                "severity": "low",
                "confidence": "low",
                "evidence": "unsupported",
                "source_ids": ["invented"],
                "benign_explanation": "unknown",
            }
        ],
    }

    def handler(request):
        body = json.loads(request.content)
        assert "tools" not in body
        assert body["provider"]["require_parameters"] is True
        return httpx.Response(
            200,
            json={
                "model": "anthropic/claude-example",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(report)}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CoverageError, match="cited"):
            evaluate(
                client,
                "anthropic/claude-example",
                "test-credential",
                {"sources": [{"id": "known"}]},
            )


def test_exact_non_vendor_secret_is_scrubbed_without_breaking_json():
    secret = 'arbitrary-credential-"with-quotes'
    result = scrub({"text": "before " + secret + " after"}, [secret])
    assert result == {"text": "before [REDACTED] after"}
