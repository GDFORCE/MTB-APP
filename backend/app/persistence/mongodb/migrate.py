"""Versioned, resumable validator / index migrations.

Each migration module under ``migrations/`` defines ``VERSION`` (int),
``NAME`` and ``apply(database)``. ``apply`` must be idempotent (create-if-missing
collections, ``collMod`` for validators, named indexes), so a run that died half
way is simply re-run: the ledger row stays RUNNING until ``apply`` returns.

    python -m app.persistence.mongodb.migrate          # apply pending
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pkgutil
from datetime import datetime, timezone
from types import ModuleType

from pymongo.database import Database
from pymongo.errors import CollectionInvalid

from . import migrations

LEDGER = "_schema_migrations"


def discover() -> list[ModuleType]:
    modules = [
        importlib.import_module(f"{migrations.__name__}.{info.name}")
        for info in pkgutil.iter_modules(migrations.__path__)
        if info.name[:4].isdigit()
    ]
    modules.sort(key=lambda module: module.VERSION)
    versions = [module.VERSION for module in modules]
    if len(versions) != len(set(versions)):
        raise RuntimeError("duplicate migration versions")
    return modules


def _checksum(module: ModuleType) -> str:
    return hashlib.sha256(inspect.getsource(module).encode("utf-8")).hexdigest()


def ensure_collection(database: Database, name: str, validator: dict | None = None) -> None:
    """Create the collection if missing, then (re)apply its validator."""
    try:
        database.create_collection(name)
    except CollectionInvalid:
        pass
    if validator is not None:
        database.command(
            "collMod", name, validator=validator,
            validationLevel="strict", validationAction="error")


def run_migrations(database: Database) -> list[int]:
    """Apply every pending migration in order; return the versions applied."""
    ensure_collection(database, LEDGER)
    ledger = database[LEDGER]
    ledger.create_index("version", unique=True, name="uq_version")
    applied: list[int] = []
    for module in discover():
        checksum = _checksum(module)
        row = ledger.find_one({"version": module.VERSION})
        if row and row["status"] == "COMPLETED":
            if row["checksum"] != checksum:
                raise RuntimeError(
                    f"migration {module.VERSION} ({module.NAME}) was edited after it was applied; "
                    "add a new migration instead")
            continue
        ledger.update_one(
            {"version": module.VERSION},
            {"$set": {"name": module.NAME, "checksum": checksum, "status": "RUNNING",
                      "started_at": datetime.now(timezone.utc)}},
            upsert=True)
        module.apply(database)
        ledger.update_one(
            {"version": module.VERSION},
            {"$set": {"status": "COMPLETED", "completed_at": datetime.now(timezone.utc)}})
        applied.append(module.VERSION)
    return applied


def main() -> None:  # pragma: no cover - CLI
    from .client import build_client, get_database
    from .settings import MongoSettings

    settings = MongoSettings.from_env()
    client = build_client(settings)
    done = run_migrations(get_database(client, settings))
    print(f"applied migrations: {done or 'none (up to date)'}")


if __name__ == "__main__":  # pragma: no cover
    main()
