from worker import slack_message


def report(**changes):
    return {"summary": "The approved file was updated.", "findings": [], "coverage_gaps": [], **changes}


def test_findings_are_readable_and_model_text_cannot_create_slack_mentions():
    finding = {"category": "Unexpected destination", "severity": "low", "confidence": "medium",
               "evidence": "An upload was proposed. <!channel> <https://example.invalid|click>",
               "benign_explanation": "This may be a quoted example."}
    message = slack_message(report(findings=[finding]), "REVIEW#i-0123456789abcdef0#1790640900",
                            "https://s3.console.aws.amazon.com/s3/buckets/test", "crux-ae211")
    assert "1 finding to review" in message["text"]
    texts = [b["text"]["text"] for b in message["blocks"]]
    assert any("crux-ae211" in t and "Sep 29, 2026 00:15 UTC" in t for t in texts)
    assert any("An upload was proposed." in t and "quoted example" in t for t in texts)
    assert all(b["text"]["type"] == "plain_text" for b in message["blocks"][:-1])
    assert "|Open full report and evidence>" in texts[-1]


def test_failed_review_is_not_presented_as_zero_findings_even_on_old_pending_reports():
    gaps = ["Missing source " + str(i) for i in range(8)] + ["Reviewer failed (HTTP 403); no safety verdict"]
    for changes in ({"review_status": "failed"}, {}):
        message = slack_message(report(summary="Review unavailable", coverage_gaps=gaps, **changes),
                                "REVIEW#i-0123456789abcdef0#1790641500", "https://example.com")
        assert "no safety verdict" in message["text"]
        assert "0 findings" not in str(message)
        assert "HTTP 403" in str(message)
        assert "4 more coverage gaps" in str(message)
    completed = slack_message(report(coverage_gaps=["No host telemetry"]), "fixture", "https://example.com")
    assert "No findings in the available evidence" in completed["text"]
    assert "Limited visibility" in str(completed)


def test_all_findings_survive_maximum_report_sizes_within_slack_limits():
    finding = {"category": "x" * 4000, "severity": "info", "confidence": "low",
               "evidence": "x" * 4000, "benign_explanation": "x" * 4000}
    message = slack_message(report(summary="x" * 4000, findings=[finding] * 30,
                                   coverage_gaps=["x" * 4000] * 35), "fixture", "https://example.com")
    assert len(message["blocks"]) <= 50
    assert all(len(b["text"]["text"]) <= 3000 for b in message["blocks"])
    assert any(b["text"]["text"].startswith("30. ") for b in message["blocks"])
