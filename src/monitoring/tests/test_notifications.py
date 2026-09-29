from datetime import datetime

from worker import slack_message


def report(**changes):
    return {"summary": "The approved file was updated.", "findings": [], "coverage_gaps": [], **changes}


def test_findings_are_readable_and_model_text_cannot_create_slack_mentions():
    finding = {"category": "Unexpected destination", "severity": "low", "confidence": "medium",
               "evidence": "An upload was proposed. <!channel> <https://example.invalid|click>",
               "benign_explanation": "This may be a quoted example."}
    message = slack_message(report(findings=[finding]), "REVIEW#i-0123456789abcdef0#1790640900",
                            "https://s3.console.aws.amazon.com/s3/buckets/test")
    texts = [b["text"]["text"] for b in message["blocks"]]
    assert texts[0] == ("1. Unexpected destination: An upload was proposed. <!channel> "
                        "<https://example.invalid|click> (Low severity, medium confidence), 2026-09-28 20:15 ET")
    assert all(b["type"] == "section" for b in message["blocks"])
    assert "quoted example" not in str(message)
    assert all(b["text"]["type"] == "plain_text" for b in message["blocks"][:-1])
    assert texts[-1].startswith("2. <") and "|Full report and evidence>" in texts[-1]


def test_failed_review_is_not_presented_as_zero_findings_even_on_old_pending_reports():
    gaps = ["Missing source " + str(i) for i in range(8)] + ["Reviewer failed (HTTP 403); no safety verdict"]
    for changes in ({"review_status": "failed"}, {}):
        message = slack_message(report(summary="Review unavailable", coverage_gaps=gaps, **changes),
                                "REVIEW#i-0123456789abcdef0#1790641500", "https://example.com")
        assert "no safety verdict" in message["text"]
        assert "0 findings" not in str(message)
        assert "HTTP 403" in str(message)
        text = "\n".join(b["text"]["text"] for b in message["blocks"])
        assert text.startswith("1. Review unavailable: No safety verdict could be produced,")
        assert "10. Coverage gap: Reviewer failed (HTTP 403); no safety verdict," in text
        assert "11. <" in text
    completed = slack_message(report(coverage_gaps=["No host telemetry"]), "fixture", "https://example.com")
    assert "No findings in the available evidence" in completed["text"]
    assert "2. Coverage gap: No host telemetry" in str(completed)


def test_all_findings_survive_maximum_report_sizes_within_slack_limits():
    finding = {"category": "x" * 4000, "severity": "info", "confidence": "low",
               "evidence": "x" * 4000, "benign_explanation": "x" * 4000}
    message = slack_message(report(summary="x" * 4000, findings=[finding] * 30,
                                   coverage_gaps=["x" * 4000] * 35), "fixture", "https://example.com")
    assert len(message["blocks"]) <= 50
    assert all(len(b["text"]["text"]) <= 3000 for b in message["blocks"])
    lines = [line for block in message["blocks"] for line in block["text"]["text"].splitlines()]
    assert len(lines) == 66
    assert all(line.startswith(f"{i}. ") for i, line in enumerate(lines, 1))


def test_review_timestamp_uses_eastern_daylight_and_standard_time():
    for utc, expected in [("2026-07-01T12:00:00+00:00", "2026-07-01 08:00 ET"),
                          ("2026-01-01T12:00:00+00:00", "2026-01-01 07:00 ET")]:
        end = int(datetime.fromisoformat(utc).timestamp())
        message = slack_message(report(), f"REVIEW#i-0123456789abcdef0#{end}", "https://example.com")
        assert all(block["text"]["text"].endswith(expected) for block in message["blocks"])
