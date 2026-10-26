import time

import pytest
from lxml import html

from status_view import coverage_rows

from .conftest import ORIGIN, authorize


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("error_code", ["", "langfuse_recency"])
def test_failed_status_keeps_last_success_and_history_is_private(
    client, store, target, monkeypatch, legacy, error_code
):
    key = target["key"]
    for end in (900, 1800):
        store.save_collection(key, end, {"target": target, "sources": []})
    report = {
        "activity": "alive",
        "alert": "",
        "recommendation": "Continue",
        "progress": "Two evaluations completed",
        "quality": "Unknown",
        "milestones": "Baseline complete",
        "source_ids": [],
        "budget": "Unknown",
    }
    if not legacy:
        report.update(
            quality=["Content checks failed for two groups.", "Search remains unverified."],
            milestones=["The pilot is deployed.", "Final acceptance is unfinished."],
            details=["URL coverage passed.", "<script>alert('evidence')</script>"],
            reported_budget="Workspace ledger reports $200.49 spent against a $200 limit.",
        )
    store.publish(
        key,
        900,
        "status",
        {
            "outcome": "completed",
            "window_end": 900,
            "checked_at": 910,
            "evidence_at": 899,
            "report": report,
            "coverage_gaps": [],
        },
    )
    store.publish(
        key,
        1800,
        "status",
        {
            "outcome": "failed",
            "window_end": 1800,
            "checked_at": 1810,
            "error": "Unavailable",
            "error_code": error_code,
        },
    )
    row = store.fleet()[0]
    assert row["latest"]["checked_at"] == 910 and row["attempt"] == "failed"
    with monkeypatch.context() as clock:
        clock.setattr("status_view.time.time", lambda: 2700)
        view = coverage_rows([row])[0]
    assert view["stale"] and view["attention"]
    path = f"/workloads/{key}/status"
    assert client.get(path, base_url=ORIGIN).status_code == 302
    authorize(client, store)
    page = client.get(path, base_url=ORIGIN)
    assert page.status_code == 200 and b"Two evaluations" in page.data and b"Unavailable" in page.data
    page_html = html.fromstring(page.data)
    assert page_html.xpath("//details[summary='Check details']//p[contains(., 'Unavailable')]")
    assert "Last reported status: Active." in page_html.text_content()
    overview = client.get("/", base_url=ORIGIN)
    overview_html = html.fromstring(overview.data)
    assert overview.status_code == 200
    failure_message = (
        "Langfuse logs are stale or have not been updated"
        if error_code == "langfuse_recency"
        else "Could not refresh status"
    )
    assert failure_message in overview_html.text_content()
    assert failure_message in page_html.text_content()
    assert "Showing an earlier report." in overview_html.text_content()
    assert "Last reported status: Active." in overview_html.text_content()
    for document in (page_html, overview_html):
        checks = document.xpath("//div[@class='status-checks']/ul/li")
        assert [item.text_content() for item in checks] == (["Unknown"] if legacy else report["quality"])
        assert document.xpath("//div[contains(@class, 'status-report')]/details/summary[text()='Details']")
        assert not document.xpath("//script[contains(text(), 'evidence')]")
        if not legacy:
            assert "Spend & time." in document.text_content()
            assert report["reported_budget"] in document.text_content()
            details = document.xpath("//div[contains(@class, 'status-report')]/details")[0]
            assert "URL coverage passed." in details.text_content()
            assert "The pilot is deployed." in details.text_content()
    history, cursor = store.history(key, None)
    assert [r["window_end"] for r in history] == [1800, 900] and cursor is None


@pytest.mark.parametrize(
    "resources",
    [
        (
            "As of October 11, the ledger reports LLM $200.49/$200, API $13/$100, "
            "and estimated AWS $19.43/$100. The task deadline is November 18, 2026, 23:36 UTC."
        ),
        "The task deadline is November 18, 2026, 23:36 UTC. Spend is not recorded.",
        "",
    ],
)
def test_current_report_keeps_warnings_visible_and_supporting_facts_collapsed(client, store, target, resources):
    now = int(time.time())
    key = target["key"]
    report = {
        "activity": "stalled",
        "alert": "The run stopped with release checks unfinished.",
        "recommendation": "The operator should decide whether to fund another run.",
        "progress": "The completion report marks the work unfinished.",
        "quality": ["Content checks failed for two groups.", "Twenty pages need visual review."],
        "milestones": ["The pilot is deployed."],
        "details": ["Access control passed."],
        "reported_budget": resources,
        "source_ids": [],
        "budget": "Configured project spend and deadline are not supplied.",
    }
    store.save_collection(key, now, {"target": target, "sources": []})
    store.publish(
        key,
        now,
        "status",
        {
            "outcome": "completed",
            "window_end": now,
            "checked_at": now,
            "evidence_at": now,
            "report": report,
            "coverage_gaps": ["Some workspace evidence was omitted."],
        },
    )
    authorize(client, store)
    response = client.get("/", base_url=ORIGIN)
    assert response.status_code == 200
    document = html.fromstring(response.data)
    visible = document.xpath("//div[contains(@class, 'status-report')]")[0]
    assert "Project status: Stalled." in visible.text_content()
    assert report["alert"] in visible.text_content()
    assert report["recommendation"] in visible.text_content()
    assert [item.text_content() for item in visible.xpath("./div[@class='status-checks']/ul/li")] == report["quality"]
    assert not visible.xpath("./div[@class='status-checks']/ancestor::details")
    details = visible.xpath("./details")[0]
    assert details.get("open") is None
    assert "Access control passed." in details.text_content()
    assert (resources or "No spend or deadline found in the available evidence.") in visible.text_content()
    if resources:
        assert "No spend or deadline found" not in visible.text_content()
    assert "Configured figures" not in document.text_content()
    assert report["budget"] not in document.text_content()
    assert "Some workspace evidence was omitted." in document.text_content()
    assert "Could not refresh status" not in document.text_content()
