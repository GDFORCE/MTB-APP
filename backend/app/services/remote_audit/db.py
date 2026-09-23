"""Mongo bootstrap for the remote-audit worker process.

`backend/server.py` builds the whole FastAPI application (every route, every
dependency) as a side effect of import — reusing that from a standalone
worker process would mean importing all of it just to reach `db`. This module
instead opens its own Motor client from the same environment variables
(`MONGO_URL`, `DB_NAME`) that `server.py` already requires, pointing at the
exact same logical database, so `remote_audit_*` collections live alongside
`organizations`/`org_sites`/`notifications` as the plan intends — without the
worker process depending on the API app's module graph.

Deliberately NOT a cached/singleton client: Motor pins a client to whichever
asyncio event loop is running when it's first used, so a module-level cache
that outlives one event loop (e.g. across pytest-asyncio's per-test loops)
breaks on the second use with "Event loop is closed". A real worker process
calls `get_db()` once at startup and holds the reference itself for its
process lifetime — that's already a singleton in practice, without a
decorator that actively causes harm in tests.

Route handlers (`remote_audit_routes.py`, imported at the bottom of
server.py exactly like `org_routes.py`) should keep using `db` imported
directly from `server` instead — this module is for code that must run
outside that import graph.
"""
from __future__ import annotations

import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


def get_db() -> AsyncIOMotorDatabase:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "mtb_app")
    return AsyncIOMotorClient(mongo_url, tz_aware=True)[db_name]
