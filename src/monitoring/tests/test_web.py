import base64
import json
import time

import pytest
from conftest import ORIGIN, authorize, ingest
from lxml import html

from lifecycle import Conflict


def test_private_pages_require_signin_after_logout(client, store, target):
    item = ingest(store, target)
    path = f"/incidents/{item['id']}"
    assert client.get(path, base_url=ORIGIN).status_code == 302
    csrf = authorize(client, store)
    assert client.get(path, base_url=ORIGIN).status_code == 200
    response = client.post(
        "/auth/logout", base_url=ORIGIN, data={"csrf": csrf}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 302
    assert client.get(path, base_url=ORIGIN).status_code == 302


@pytest.mark.parametrize(
    "origin,csrf_override", [(None, None), ("https://untrusted.example", None), (ORIGIN, "bad")]
)
def test_untrusted_operator_requests_leave_incident_open(
    client, store, target, origin, csrf_override
):
    item = ingest(store, target)
    csrf = authorize(client, store)
    response = client.post(
        f"/incidents/{item['id']}/status",
        base_url=ORIGIN,
        data={
            "version": item["version"],
            "status": "closed",
            "csrf": csrf_override or csrf,
        },
        headers={"Origin": origin} if origin else {},
    )
    assert response.status_code == 403
    unchanged = store.incident(item["id"])
    assert (unchanged["status"], unchanged["version"]) == ("open", item["version"])


def test_operator_closes_incident_and_stale_changes_are_rejected(client, store, target):
    item = ingest(store, target)
    path = f"/incidents/{item['id']}/status"
    data = {"version": item["version"], "status": "closed", "csrf": authorize(client, store)}
    assert (
        client.post(path, base_url=ORIGIN, data=data, headers={"Origin": ORIGIN}).status_code == 303
    )
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
    store.transition(
        item["id"], item["version"], "closed", {"id": "operator"}, "<script>alert(1)</script>"
    )
    authorize(client, store)
    page = client.get(f"/incidents/{item['id']}", base_url=ORIGIN)
    assert b"&lt;script&gt;" in page.data and b"<script>" not in page.data
    listing = client.get("/?status=all", base_url=ORIGIN)
    assert b"Private evidence" not in listing.data
    assert b"not registered for this workload" not in listing.data


def test_invalid_database_cursors_return_bad_request(client, store, target):
    item = ingest(store, target)
    authorize(client, store)
    cursor = base64.urlsafe_b64encode(
        json.dumps([target["instance_id"], "invalid-uuid"]).encode()
    ).decode()
    assert client.get("/", query_string={"cursor": cursor}, base_url=ORIGIN).status_code == 400
    for name in ("events", "observations"):
        assert (
            client.get(
                f"/incidents/{item['id']}", query_string={name: "invalid-number"}, base_url=ORIGIN
            ).status_code
            == 400
        )
