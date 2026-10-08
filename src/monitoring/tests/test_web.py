import secrets
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from lxml import html
from test_lifecycle import ingest, report, store

from review import digest
from web import create_app
from web_auth import LOGIN_COOKIE, SESSION_COOKIE

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
    store.put_once(
        {
            "pk": "SESSION#" + digest(token),
            "sk": "STATE",
            "actor": {"id": "operator@example.test", "issuer": "test"},
            "csrf": csrf,
            "expires_at": expires or int(time.time()) + 300,
        }
    )
    client.set_cookie(SESSION_COOKIE, token, domain="incidents.example.test")
    return csrf


@pytest.mark.parametrize(
    ("elapsed", "label"),
    [
        (0, "just now"),
        (1, "1 second ago"),
        (59, "59 seconds ago"),
        (60, "1 minute ago"),
        (3599, "59 minutes ago"),
        (3600, "1 hour ago"),
        (86400, "1 day ago"),
        (30 * 86400, "1 month ago"),
        (365 * 86400, "1 year ago"),
        (-120, "in 2 minutes"),
    ],
)
def test_timestamp_keeps_absolute_date_and_adds_relative_label(client, monkeypatch, elapsed, label):
    now = 1791475200
    monkeypatch.setattr(time, "time", lambda: now)
    value = Decimal(now - elapsed)  # DynamoDB returns numbers as Decimal.
    with client.application.test_request_context():
        component = client.application.jinja_env.get_template("components/timestamp.html")
        element = html.fromstring(component.module.timestamp(value))
    assert datetime.fromisoformat(element.get("datetime")) == datetime.fromtimestamp(
        int(value), UTC
    )
    assert "ET" in element.text_content()
    assert element.text_content().endswith(f"({label})")


def test_filtered_pages_are_full_and_running_instances_precede_history(client, store):
    authorize(client, store)
    # Closed records sort first within the index; they must not consume the page.
    prototype = ingest(store)
    store.table.delete_item(Key={"pk": "INCIDENTS", "sk": prototype["id"]})
    for instance, slug, state in [
        ("i-test", "z-running", "running"),
        ("i-old", "a-history", "stopped"),
    ]:
        store.sync_fleet([{"instance_id": instance, "slug": slug, "state": state}])
        for n in range(110):
            iid = f"{instance}-{n:04d}"
            store.table.put_item(
                Item={
                    **prototype,
                    "pk": "INCIDENTS",
                    "sk": iid,
                    "id": iid,
                    "instance_id": instance,
                    "workload_label": slug,
                    "status": "closed" if n < 55 else "open",
                }
            )
    path, pages = "/", []
    while path:
        response = client.get(path, base_url=ORIGIN)
        assert response.status_code == 200
        document = html.fromstring(response.data)
        links = document.xpath('//td[contains(@class,"incident-title")]/a/@href')
        assert links
        pages.append(links)
        next_links = document.xpath('//div[contains(@class,"pagination")]/a/@href')
        path = next_links[0] if next_links else None
    assert [len(page) for page in pages] == [50, 50, 10]
    links = sum(pages, [])
    assert len(set(links)) == 110
    assert all("/i-test-" in link for link in links[:55])
    assert all("/i-old-" in link for link in links[55:])
    assert client.get("/?cursor=invalid", base_url=ORIGIN).status_code == 400


def test_anonymous_pages_redirect_without_disclosing_incidents(client, store):
    item = ingest(store, text="PRIVATE_TRANSCRIPT_MARKER<script>bad()</script>")
    store.transition(item["id"], item["version"], "closed", {"id": "PRIVATE_ACTOR"}, "PRIVATE_NOTE")
    for path in [
        "/",
        "/?status=closed",
        "/?status=all",
        "/incidents/" + item["id"],
        "/incidents/not-an-id",
    ]:
        response = client.get(path, base_url=ORIGIN)
        assert response.status_code == 302
        assert response.headers["Location"] == "/auth/login"
        body = response.get_data(as_text=True)
        assert "Unexpected upload destination" not in body
        assert (
            "PRIVATE_" not in body and "reviews/test" not in body and "observation:123" not in body
        )
        assert 'name="version"' not in body
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "same-origin"
    assert "Unexpected upload destination" not in client.get("/", base_url=ORIGIN).get_data(
        as_text=True
    )
    authorize(client, store)
    body = client.get("/incidents/" + item["id"], base_url=ORIGIN).get_data(as_text=True)
    assert "Unexpected upload destination" in body
    assert (
        "PRIVATE_TRANSCRIPT_MARKER" in body and "PRIVATE_NOTE" in body and "PRIVATE_ACTOR" in body
    )
    assert "<script>bad()</script>" not in body


def test_readiness_discloses_no_incident_data_and_expired_session_cannot_read(client, store):
    ingest(store, text="PRIVATE_TRANSCRIPT_MARKER")
    authorize(client, store, expires=int(time.time()) - 1)
    assert client.get("/", base_url=ORIGIN).status_code == 302
    response = client.get("/healthz", base_url=ORIGIN)
    assert response.status_code == 200
    assert response.json == {"status": "ok", "revision": "local"}
    assert client.get("/static/app.css", base_url=ORIGIN).status_code == 200


def test_auth_csrf_origin_stale_write_close_reopen_and_logout(client, store):
    item = ingest(store)
    path = "/incidents/" + item["id"] + "/status"
    data = {"version": item["version"], "status": "closed"}
    assert client.post(path, data=data, base_url=ORIGIN).status_code == 401
    csrf = authorize(client, store)
    assert (
        client.post(path, data=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code == 403
    )
    data["csrf"] = csrf
    for origin in ("null", ""):
        assert (
            client.post(path, data=data, base_url=ORIGIN, headers={"Origin": origin}).status_code
            == 403
        )
    assert (
        client.post(
            path, data=data, base_url=ORIGIN, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        client.post(path, data=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code == 303
    )
    assert store.incident(item["id"])["status"] == "closed"
    assert (
        client.post(path, data=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code == 409
    )
    data.update(status="open", version=store.incident(item["id"])["version"])
    assert (
        client.post(path, data=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code == 303
    )
    assert store.incident(item["id"])["status"] == "open"
    response = client.get("/incidents/" + item["id"], base_url=ORIGIN)
    assert "Private evidence" in response.get_data(as_text=True)
    assert (
        client.post(
            "/auth/logout", base_url=ORIGIN, data={"csrf": csrf}, headers={"Origin": ORIGIN}
        ).status_code
        == 302
    )
    assert (
        client.post(path, data=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code == 401
    )


def test_expired_session_cannot_mutate_and_bad_saml_cannot_authenticate(client, store):
    item = ingest(store)
    csrf = authorize(client, store, expires=int(time.time()) - 1)
    assert (
        client.post(
            "/incidents/" + item["id"] + "/status",
            base_url=ORIGIN,
            data={"version": item["version"], "status": "closed", "csrf": csrf},
            headers={"Origin": ORIGIN},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/callback", base_url=ORIGIN, data={"SAMLResponse": "fake", "RelayState": "fake"}
        ).status_code
        == 403
    )
    assert client.get("/", base_url="https://evil.test").status_code == 400


def test_malformed_security_tokens_are_rejected_without_server_errors(client, store):
    item = ingest(store)
    authorize(client, store)
    response = client.post(
        "/incidents/" + item["id"] + "/status",
        base_url=ORIGIN,
        data={"version": item["version"], "status": "closed", "csrf": "invalid-\u2603"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403
    assert store.incident(item["id"])["status"] == "open"
    client.set_cookie(LOGIN_COOKIE, "ascii-login-nonce", domain="incidents.example.test")
    assert (
        client.post(
            "/auth/callback",
            base_url=ORIGIN,
            data={"RelayState": "invalid-\u2603", "SAMLResponse": "invalid"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("historical_state", ["terminated", "no longer present"])
def test_incident_tables_keep_distinct_instances_with_the_same_label_separate(
    client, store, historical_state
):
    authorize(client, store)
    first = ingest(store)
    store.sync_fleet(
        [{"instance_id": "i-test", "slug": "test-workload", "state": "running", "review_count": 1}]
    )
    store.ingest(
        report(),
        "REVIEW#i-second#300",
        "reviews/second",
        {"slug": "test-workload"},
        {},
        {"observation:123": "observation:123"},
    )
    store.sync_fleet(
        [
            {
                "instance_id": "i-second",
                "slug": "test-workload",
                "state": historical_state,
                "review_count": 1,
            }
        ]
    )
    page = html.fromstring(client.get("/?status=all", base_url=ORIGIN).data)
    groups = page.xpath('//section[@class="instance-group"]')
    assert len(groups) == 2
    assert "i-test" in groups[0].xpath(".//h3")[0].text_content()
    assert groups[0].xpath(".//tbody//a/@href") == ["/incidents/" + first["id"]]
    assert "i-second" in groups[1].xpath(".//h3")[0].text_content()
    assert groups[1].xpath("ancestor::details[not(@open)]")
