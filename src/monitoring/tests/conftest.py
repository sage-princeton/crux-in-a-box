import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from database import engine
from store import Store

ROOT = Path(__file__).resolve().parents[1]


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
