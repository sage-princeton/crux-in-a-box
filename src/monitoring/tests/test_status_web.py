import time

import pytest
from botocore.exceptions import ClientError
from lxml import html
from test_lifecycle import ingest, store
from test_status import completed, status_store
from test_web import ORIGIN, SETTINGS, authorize

from status_store import workload_key
from web import create_app


@pytest.fixture
def status_client(store, status_store):
    app = create_app(store, SETTINGS, status_store)
    app.config.update(TESTING=True)
    client = app.test_client()
    authorize(client, store)
    return client


def seed(
    status_store,
    iid="i-status",
    run="run-1",
    state="running",
    end=None,
    progress="Two evaluations completed.",
):
    end = end or int(time.time()) // 900 * 900
    key = workload_key(iid, run)
    status_store.sync(
        key,
        {"instance_id": iid, "workload_id": run, "slug": "example-project", "state": state},
        int(time.time()),
        1800,
    )
    status_store.publish(key, end, completed(end, progress))
    return key


def test_latest_status_is_before_incidents_without_creating_an_incident(
    status_client, status_store, store
):
    key = seed(status_store)
    before = list(store.all("INCIDENTS"))
    response = status_client.get("/", base_url=ORIGIN)
    page = html.fromstring(response.data)
    assert page.xpath('//*[@id="coverage"]//ol[@class]/li')
    assert len(page.xpath('//ol[contains(@class,"status-report")]/li')) == 5
    body = response.get_data(as_text=True)
    assert body.index('id="coverage"') < body.index('id="incident-table"')
    assert "Two evaluations completed." in body
    assert list(store.all("INCIDENTS")) == before == []
    assert status_client.get(f"/workloads/{key}/status", base_url=ORIGIN).status_code == 200


def test_failure_staleness_and_history_keep_original_report_time(status_client, status_store):
    end = int(time.time()) - 7200
    key = seed(status_store, end=end)
    status_store.publish(
        key,
        end + 3600,
        {"outcome": "failed", "checked_at": end + 3610, "error": "Budget exhausted"},
    )
    page = status_client.get("/", base_url=ORIGIN).get_data(as_text=True)
    assert "Stale evidence" in page and "Latest check failed" in page
    assert "Two evaluations completed." in page and "last successful report" in page
    history = status_client.get(f"/workloads/{key}/status", base_url=ORIGIN).get_data(as_text=True)
    assert "Budget exhausted" in history and "Two evaluations completed." in history


def test_independent_data_read_failures_do_not_hide_the_other_section(
    status_client, status_store, store, monkeypatch
):
    seed(status_store)
    ingest(store)
    original = status_store.fleet

    def unavailable(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "PRIVATE_DETAILS"}}, "Query"
        )

    monkeypatch.setattr(status_store, "fleet", unavailable)
    page = status_client.get("/", base_url=ORIGIN).get_data(as_text=True)
    assert "Project status is temporarily unavailable" in page
    assert "Unexpected upload destination" in page
    assert "PRIVATE_DETAILS" not in page
    monkeypatch.setattr(status_store, "fleet", original)
    monkeypatch.setattr(store, "all", unavailable)
    page = status_client.get("/", base_url=ORIGIN).get_data(as_text=True)
    assert "Incident data is temporarily unavailable" in page
    assert "Two evaluations completed." in page


def test_status_is_private_escaped_and_scoped_by_run(status_client, status_store, store):
    first = seed(status_store, run="first", progress="PRIVATE_STATUS<script>bad()</script>")
    seed(status_store, run="second", progress="Different run")
    body = status_client.get(f"/?workload={first}", base_url=ORIGIN).get_data(as_text=True)
    assert "PRIVATE_STATUS&lt;script&gt;" in body and "<script>" not in body
    assert "Different run" not in body
    anonymous = create_app(store, SETTINGS, status_store).test_client()
    for path in ("/", f"/workloads/{first}/status"):
        response = anonymous.get(path, base_url=ORIGIN)
        assert response.status_code == 302
        assert "PRIVATE_STATUS" not in response.get_data(as_text=True)
    assert (
        status_client.get(f"/workloads/{first}/status?before=invalid", base_url=ORIGIN).status_code
        == 400
    )


def test_coverage_paginates_and_keeps_retired_history_separate(status_client, status_store):
    for n in range(7):
        seed(status_store, iid=f"i-{n}")
    retired = seed(status_store, iid="i-retired", state="stopped")
    page = html.fromstring(status_client.get("/", base_url=ORIGIN).data)
    assert len(page.xpath('//article[contains(@class,"workload-status")]')) == 5
    assert page.xpath('//nav[@aria-label="Coverage pages"]')
    second = html.fromstring(status_client.get("/?coverage_page=2", base_url=ORIGIN).data)
    assert len(second.xpath('//article[contains(@class,"workload-status")]')) == 2
    assert status_client.get(f"/?workload={retired}", base_url=ORIGIN).status_code == 200
    assert status_client.get("/?coverage_page=-1", base_url=ORIGIN).status_code == 400
