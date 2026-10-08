import os
import secrets
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from database import engine
from review import digest
from store import Store
from web import create_app
from web_auth import SESSION_COOKIE

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://incidents.example.test"


@pytest.fixture(scope="session")
def db():
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or not url.database.endswith("_test"):
        raise ValueError("Tests require a disposable PostgreSQL database ending in _test")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield engine()
    engine().dispose()


@pytest.fixture
def store(db):
    with db.begin() as connection:
        connection.execute(
            text("TRUNCATE workloads, notices, budgets, sessions, login_requests CASCADE")
        )
    return Store(db)


@pytest.fixture
def target(store):
    target = {
        "instance_id": "i-test",
        "name": "experiment",
        "slug": "experiment",
        "state": "running",
        "authorization": "Inspect this test run.",
        "subject_families": ["openai"],
    }
    store.sync([target], 900, 1800)
    return target


@pytest.fixture
def client(store):
    app = create_app(store, {"origin": ORIGIN, "bucket": "private-evidence"})
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


def report(anchor="observation:123"):
    return {
        "review_status": "completed",
        "summary": "Review complete",
        "coverage_gaps": [],
        "findings": [
            {
                "detector_id": "unexpected_upload",
                "anchor_id": anchor,
                "evidence": "Private evidence",
                "source_ids": [anchor],
                "severity": "low",
                "confidence": "medium",
            }
        ],
    }


def ingest(store, target, end=900):
    key = target["key"]
    store.save_collection(key, end, {"target": target, "sources": []})
    result = {
        "outcome": "completed",
        "window_end": end,
        "artifact_prefix": "private/review",
        "report": report(),
        "model": {"reported_model": "model-1"},
    }
    store.publish(key, end, "incident", result)
    store.ingest(key, end, result, {"observation:123": "observation:123"})
    return store.instance_page(target["instance_id"], None, None, 50)[0]
