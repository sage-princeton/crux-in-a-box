import base64
import json
import secrets
import time

import pytest
from lxml import html
from test_lifecycle import ingest

from lifecycle import Conflict
from review import digest
from web import create_app
from web_auth import SESSION_COOKIE

ORIGIN = "https://incidents.example.test"
SETTINGS = {
    "origin": ORIGIN,
    "bucket": "private-evidence",
    "idp": {
        "entityId": "https://idp.example.test",
        "singleSignOnService": {"url": "https://idp.example.test/login"},
        "x509cert": "configured-in-signed-saml-tests",
    },
}


@pytest.fixture
def client(store):
    app = create_app(store, SETTINGS)
    app.config.update(TESTING=True)
    return app.test_client()


def authorize(client, store, expires=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    nonce = digest(secrets.token_urlsafe(32))
    now = int(time.time())
    store.login(nonce, "fixture-request", now + 300)
    store.finish_login(
        nonce, digest(token), {"id": "operator", "issuer": "test"}, csrf, expires or now + 300, now
    )
    client.set_cookie(SESSION_COOKIE, token, domain="incidents.example.test")
    return csrf


def test_authentication_csrf_conflict_and_logout(client, store, target):
    item = ingest(store, target)
    path = f"/incidents/{item['id']}"
    assert client.get(path, base_url=ORIGIN).status_code == 302
    csrf = authorize(client, store)
    assert client.get(path, base_url=ORIGIN).status_code == 200
    data = {"version": item["version"], "status": "closed", "csrf": csrf}
    assert client.post(path + "/status", base_url=ORIGIN, data=data).status_code == 403
    assert (
        client.post(
            path + "/status",
            base_url=ORIGIN,
            data={**data, "csrf": "bad"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/status", base_url=ORIGIN, data=data, headers={"Origin": ORIGIN}
        ).status_code
        == 303
    )
    assert (
        client.post(
            path + "/status",
            base_url=ORIGIN,
            data={**data, "status": "open"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/auth/logout", base_url=ORIGIN, data={"csrf": csrf}, headers={"Origin": ORIGIN}
        ).status_code
        == 302
    )
    assert client.get(path, base_url=ORIGIN).status_code == 302


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


def test_readiness_is_content_free_and_expired_sessions_cannot_read(client, store, target):
    ingest(store, target)
    response = client.get("/healthz", base_url=ORIGIN)
    assert response.json == {"status": "ok", "revision": "local"}
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
