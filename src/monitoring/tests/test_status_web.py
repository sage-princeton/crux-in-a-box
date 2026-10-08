from conftest import ORIGIN, authorize

from status_view import coverage_rows


def test_failed_status_keeps_last_success_and_history_is_private(
    client, store, target, monkeypatch
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
        {"outcome": "failed", "window_end": 1800, "checked_at": 1810, "error": "Unavailable"},
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
    assert (
        page.status_code == 200 and b"Two evaluations" in page.data and b"Unavailable" in page.data
    )
    history, cursor = store.history(key, None)
    assert [r["window_end"] for r in history] == [1800, 900] and cursor is None
