import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from lxml import html

from lifecycle import Conflict

from .conftest import ORIGIN, authorize, ingest, report


def test_private_pages_require_signin_after_logout(client, store, target):
    item = ingest(store, target)
    path = f"/incidents/{item['id']}"
    assert client.get(path, base_url=ORIGIN).status_code == 302
    csrf = authorize(client, store)
    assert client.get(path, base_url=ORIGIN).status_code == 200
    response = client.post("/auth/logout", base_url=ORIGIN, data={"csrf": csrf}, headers={"Origin": ORIGIN})
    assert response.status_code == 302
    assert client.get(path, base_url=ORIGIN).status_code == 302


@pytest.mark.parametrize("origin,csrf_override", [(None, None), ("https://untrusted.example", None), (ORIGIN, "bad")])
@pytest.mark.parametrize("bulk", [False, True])
def test_untrusted_operator_requests_leave_incident_open(client, store, target, origin, csrf_override, bulk):
    item = ingest(store, target)
    csrf = authorize(client, store)
    response = client.post(
        "/incidents/status" if bulk else f"/incidents/{item['id']}/status",
        base_url=ORIGIN,
        data={
            "version": item["version"],
            "status": "closed",
            "csrf": csrf_override or csrf,
            "selection": [f"{item['id']}:{item['version']}"],
        },
        headers={"Origin": origin} if origin else {},
    )
    assert response.status_code == 403
    unchanged = store.incident(item["id"])
    assert (unchanged["status"], unchanged["version"]) == ("open", item["version"])


def test_bulk_selection_closes_and_reopens_only_selected_incidents_with_audit_history(client, store, target):
    items = [ingest(store, target, anchor=f"observation:{index}") for index in range(3)]
    store.acknowledge_incidents(target["key"], items[-1]["id"])
    csrf = authorize(client, store)
    page = client.get("/?status=all", base_url=ORIGIN)
    tree = html.fromstring(page.data)
    values = tree.xpath('//input[@name="selection"]/@value')
    assert values == [f"{item['id']}:{item['version']}" for item in items]
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in page.headers["Content-Security-Policy"]
    data = {
        "csrf": csrf,
        "selection": values[:2],
        "status": "closed",
        "view_status": "all",
        "note": "Reviewed together",
    }
    result = client.post("/incidents/status", base_url=ORIGIN, data=data, headers={"Origin": ORIGIN})
    assert result.status_code == 303
    assert b"Closed 2 incidents." in client.get(result.location, base_url=ORIGIN).data
    assert [store.incident(item["id"])["status"] for item in items] == ["closed", "closed", "open"]
    for item in items[:2]:
        events, _ = store.incident_history(item["id"], "events", None)
        assert len(events) == 1 and events[0]["note"] == "Reviewed together"
        assert events[0]["actor"]["id"] == "operator"
    # A mixed selection leaves incidents already in the requested state unchanged.
    current = [store.incident(item["id"]) for item in items]
    data.update(selection=[f"{item['id']}:{item['version']}" for item in current], status="open")
    result = client.post("/incidents/status", base_url=ORIGIN, data=data, headers={"Origin": ORIGIN})
    assert result.status_code == 303
    assert b"Reopened 2 incidents." in client.get(result.location, base_url=ORIGIN).data
    assert [store.incident(item["id"])["status"] for item in items] == ["open"] * 3
    assert store.incident(items[2]["id"])["version"] == items[2]["version"]
    assert store.incident_history(items[2]["id"], "events", None)[0] == []
    assert store.new_incidents() == []
    data.update(selection=[f"{item['id']}:{store.incident(item['id'])['version']}" for item in items])
    result = client.post("/incidents/status", base_url=ORIGIN, data=data, headers={"Origin": ORIGIN})
    assert result.status_code == 303
    assert b"No changes needed" in client.get(result.location, base_url=ORIGIN).data


def test_bulk_stale_selection_rolls_back_every_incident(client, store, target):
    first = ingest(store, target, anchor="observation:first")
    second = ingest(store, target, anchor="observation:second")
    csrf = authorize(client, store)
    ingest(store, target, 1800, anchor="observation:second")
    response = client.post(
        "/incidents/status",
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        data={
            "csrf": csrf,
            "status": "closed",
            "selection": [f"{item['id']}:{item['version']}" for item in (first, second)],
        },
    )
    assert response.status_code == 409
    assert b"One or more incidents changed" in response.data
    assert store.incident(first["id"])["version"] == first["version"]
    for item in (first, second):
        assert store.incident(item["id"])["status"] == "open"
        assert store.incident_history(item["id"], "events", None)[0] == []


def test_bulk_selection_is_limited_to_the_current_incident_page(client, store, target):
    items = [ingest(store, target, anchor=f"observation:{index}") for index in range(50)]
    anchor = "observation:next-page"
    store.ingest(
        target["key"],
        900,
        {"report": report(anchor), "artifact_prefix": "private"},
        {anchor: anchor},
    )
    authorize(client, store)
    page = html.fromstring(client.get("/", base_url=ORIGIN).data)
    values = page.xpath('//input[@name="selection"]/@value')
    assert len(values) == 50
    assert values == [f"{item['id']}:{item['version']}" for item in items[:50]]
    assert "Select all 50 on this page" in page.text_content()
    assert page.xpath('//a[contains(text(), "Next page")]')


@pytest.mark.parametrize("invalid", ["empty", "duplicate", "unknown", "uuid", "version", "missing_version", "limit"])
def test_invalid_bulk_selections_do_not_change_incidents(client, store, target, invalid):
    item = ingest(store, target)
    selection = f"{item['id']}:{item['version']}"
    values = {
        "empty": [],
        "duplicate": [selection, selection],
        "unknown": [selection, "00000000-0000-0000-0000-000000000000:1"],
        "uuid": ["invalid:1"],
        "version": [f"{item['id']}:0"],
        "missing_version": [item["id"]],
        "limit": [selection] * 51,
    }[invalid]
    response = client.post(
        "/incidents/status",
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        data={
            "csrf": authorize(client, store),
            "status": "closed",
            "selection": values,
        },
    )
    assert response.status_code == 400
    assert store.incident(item["id"])["status"] == "open"
    assert store.incident_history(item["id"], "events", None)[0] == []


def test_signed_out_bulk_requests_are_denied(client, store, target):
    item = ingest(store, target)
    response = client.post(
        "/incidents/status",
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        data={
            "selection": [f"{item['id']}:{item['version']}"],
            "status": "closed",
        },
    )
    assert response.status_code == 401 and store.incident(item["id"])["status"] == "open"


def test_operator_closes_incident_and_stale_changes_are_rejected(client, store, target):
    item = ingest(store, target)
    path = f"/incidents/{item['id']}/status"
    data = {"version": item["version"], "status": "closed", "csrf": authorize(client, store)}
    assert client.post(path, base_url=ORIGIN, data=data, headers={"Origin": ORIGIN}).status_code == 303
    assert store.incident(item["id"])["status"] == "closed"
    assert (
        client.post(
            path,
            base_url=ORIGIN,
            data={**data, "status": "open"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 409
    )
    assert store.incident(item["id"])["status"] == "closed"


def test_expired_and_replayed_login_requests_cannot_create_sessions(store):
    now = int(time.time())
    store.login("nonce", "request", now + 300)
    store.finish_login("nonce", "session", {"id": "operator"}, "csrf", now + 300, now)
    with pytest.raises(Conflict):
        store.finish_login("nonce", "replay", {"id": "operator"}, "csrf", now + 300, now)
    assert store.session("replay") is None
    store.login("expired", "request", now - 1)
    with pytest.raises(Conflict):
        store.finish_login("expired", "session2", {"id": "operator"}, "csrf", now + 300, now)
    assert store.session("session2") is None


def test_readiness_is_public_and_content_free(client):
    response = client.get("/healthz", base_url=ORIGIN)
    assert response.json == {"status": "ok", "revision": "local"}


def test_expired_sessions_cannot_read_incidents(client, store, target):
    ingest(store, target)
    authorize(client, store, expires=int(time.time()) - 1)
    assert client.get("/", base_url=ORIGIN).status_code == 302
    assert b"Private evidence" not in client.get("/", base_url=ORIGIN).data


@pytest.mark.parametrize(
    "elapsed,label",
    [
        (0, "just now"),
        (1, "1 second ago"),
        (60, "1 minute ago"),
        (3600, "1 hour ago"),
        (86400, "1 day ago"),
        (-120, "in 2 minutes"),
    ],
)
def test_timestamp_component_keeps_absolute_and_relative_dates(client, monkeypatch, elapsed, label):
    now = 1791475200
    monkeypatch.setattr(time, "time", lambda: now)
    with client.application.test_request_context():
        component = client.application.jinja_env.get_template("components/timestamp.html")
        element = html.fromstring(component.module.timestamp(now - elapsed))
    assert "ET" in element.text_content() and element.text_content().endswith(f"({label})")


def test_private_evidence_and_operator_text_are_escaped(client, store, target):
    item = ingest(store, target)
    store.transition(item["id"], item["version"], "closed", {"id": "operator"}, "<script>alert(1)</script>")
    authorize(client, store)
    page = client.get(f"/incidents/{item['id']}", base_url=ORIGIN)
    assert b"&lt;script&gt;" in page.data and b"<script>" not in page.data
    assert all(label in page.data for label in [b"What happened", b"Why did it happen?", b"What needs to be done now?"])
    assert b"Private evidence" in page.data
    listing = client.get("/?status=all", base_url=ORIGIN)
    assert b"Private evidence" not in listing.data
    assert b"not registered for this workload" not in listing.data


def test_invalid_database_cursors_return_bad_request(client, store, target):
    item = ingest(store, target)
    authorize(client, store)
    cursor = base64.urlsafe_b64encode(json.dumps([target["instance_id"], "invalid-uuid"]).encode()).decode()
    assert client.get("/", query_string={"cursor": cursor}, base_url=ORIGIN).status_code == 400
    for name in ("events", "observations"):
        assert (
            client.get(f"/incidents/{item['id']}", query_string={name: "invalid-number"}, base_url=ORIGIN).status_code
            == 400
        )


@pytest.mark.parametrize("status", ["all", "open", "closed"])
def test_workload_selection_scopes_status_and_incidents_and_survives_tabs(client, store, target, status):
    selected = {**target, "name": "pr49-test", "slug": "pr49-test", "state": "no longer enrolled"}
    other = {**target, "instance_id": "i-other", "name": "other", "slug": "other"}
    store.sync([selected, other], 1800, 1800)
    opened = ingest(store, selected)
    closed = ingest(store, selected, anchor="observation:closed")
    closed = store.transition(closed["id"], closed["version"], "closed", {"id": "operator"}, "Resolved")
    unrelated = ingest(store, other)
    authorize(client, store)
    page = client.get("/", query_string={"workload": selected["key"], "status": status}, base_url=ORIGIN)
    assert page.status_code == 200
    tree = html.fromstring(page.data)
    articles = tree.xpath('//article[contains(@class, "workload-status")]/@aria-label')
    assert articles == ["pr49-test project status"]
    incident_links = tree.xpath('//section[@id="incident-table"]//tbody//a/@href')
    expected = {f"/incidents/{item['id']}" for item in (opened, closed) if status in ("all", item["status"])}
    assert set(incident_links) == expected
    assert f"/incidents/{unrelated['id']}" not in incident_links
    assert tree.xpath('//section[@id="incident-table"]//details[@open]')
    assert set(tree.xpath('//select[@name="workload"]/option/@value')) == {
        "",
        selected["key"],
        other["key"],
    }
    for link in tree.xpath('//nav[@aria-label="Incident status"]/a/@href'):
        assert parse_qs(urlsplit(link).query)["workload"] == [selected["key"]]
        next_page = client.get(link, base_url=ORIGIN)
        assert next_page.status_code == 200
        assert f"/incidents/{unrelated['id']}".encode() not in next_page.data
    detail = html.fromstring(client.get(f"/incidents/{opened['id']}", base_url=ORIGIN).data)
    back = detail.xpath("//main/a/@href")[0]
    assert parse_qs(urlsplit(back).query)["workload"] == [selected["key"]]


def test_selected_workload_pagination_stays_scoped_and_rejects_another_workloads_cursor(client, store, target):
    other = {**target, "instance_id": "i-other", "name": "aaa-other", "slug": "aaa-other"}
    store.sync([target, other], 1800, 1800)
    unrelated = ingest(store, other)
    store.save_collection(target["key"], 900, {"target": target})
    for index in range(52):
        anchor = f"observation:{index}"
        store.ingest(
            target["key"],
            900,
            {"report": report(anchor), "artifact_prefix": "private"},
            {anchor: anchor},
        )
    selected_ids = {item["id"] for item in store.instance_page(target["instance_id"], None, None, 100)}
    authorize(client, store)
    page = client.get("/", query_string={"workload": target["key"], "status": "all"}, base_url=ORIGIN)
    tree = html.fromstring(page.data)
    links = tree.xpath('//section[@id="incident-table"]//tbody//a/@href')
    assert len(links) == 50 and f"/incidents/{unrelated['id']}" not in links
    next_page = tree.xpath('//div[contains(@class, "pagination")]/a/@href')[0]
    assert parse_qs(urlsplit(next_page).query)["workload"] == [target["key"]]
    remaining = html.fromstring(client.get(next_page, base_url=ORIGIN).data)
    links += remaining.xpath('//section[@id="incident-table"]//tbody//a/@href')
    assert len(links) == 52 and set(links) == {f"/incidents/{key}" for key in selected_ids}
    cursor = base64.urlsafe_b64encode(json.dumps([other["instance_id"], None]).encode()).decode()
    assert (
        client.get("/", query_string={"workload": target["key"], "cursor": cursor}, base_url=ORIGIN).status_code == 400
    )


def test_selected_workload_with_no_incidents_does_not_show_other_workloads(client, store, target):
    other = {**target, "instance_id": "i-other", "name": "other", "slug": "other"}
    store.sync([target, other], 1800, 1800)
    unrelated = ingest(store, other)
    authorize(client, store)
    page = client.get("/", query_string={"workload": target["key"]}, base_url=ORIGIN)
    assert page.status_code == 200 and b"No open incidents on this page" in page.data
    assert f"/incidents/{unrelated['id']}".encode() not in page.data
