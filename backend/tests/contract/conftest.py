"""Fixtures shared by the repository contract suites.

Every contract test runs against the in-memory fake AND a real MongoDB replica
set. The Mongo URL comes from ``UCTSM_TEST_MONGO_URL`` and must be local - tests
never touch a shared cluster. If it is unreachable the Mongo variants FAIL when
``UCTSM_REQUIRE_MONGO=1`` (CI) and are skipped with a loud reason otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.persistence.memory import InMemoryUnitOfWork  # noqa: E402
from app.persistence.mongodb.client import build_client  # noqa: E402
from app.persistence.mongodb.migrate import run_migrations  # noqa: E402
from app.persistence.mongodb.settings import MongoSettings, require_local  # noqa: E402
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork  # noqa: E402

DEFAULT_URL = "mongodb://localhost:27017/?replicaSet=rs0"


def _mongo_client_or_none():
    url = os.getenv("UCTSM_TEST_MONGO_URL", DEFAULT_URL)
    settings = require_local(MongoSettings(url=url, database="unused"))
    client = build_client(settings, server_selection_timeout_ms=2000)
    try:
        client.admin.command("ping")
        topology = client.admin.command("hello")
        if not topology.get("setName"):
            raise RuntimeError("MongoDB is not a replica set; transactions need one")
    except Exception as error:  # noqa: BLE001
        client.close()
        return None, str(error)
    return client, None


@pytest.fixture(scope="session")
def mongo_client():
    client, error = _mongo_client_or_none()
    if client is None:
        if os.getenv("UCTSM_REQUIRE_MONGO") == "1":
            pytest.fail(f"MongoDB replica set required but unavailable: {error}")
        pytest.skip(f"local MongoDB replica set unavailable ({error}); Mongo contract tests NOT run")
    yield client
    client.close()


@pytest.fixture(scope="session")
def _mongo_database(mongo_client):
    """One migrated database per test session; tests clean it between runs."""
    name = f"uctsm_test_{uuid4().hex[:12]}"
    database = mongo_client[name]
    run_migrations(database)
    yield database
    mongo_client.drop_database(name)


@pytest.fixture
def mongo_uow(mongo_client, _mongo_database):
    from app.persistence.mongodb.migrate import LEDGER

    def wipe():
        for name in _mongo_database.list_collection_names():
            if name != LEDGER and not name.startswith("system."):
                _mongo_database[name].delete_many({})

    wipe()
    yield MongoUnitOfWork(mongo_client, _mongo_database)
    wipe()


@pytest.fixture(params=["memory", "mongo"])
def uow(request):
    if request.param == "memory":
        return InMemoryUnitOfWork()
    return request.getfixturevalue("mongo_uow")
