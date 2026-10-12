"""Sync PyMongo client for UCTSM.

Sync on purpose: the service layer and the evaluator are synchronous and the
routes are ``def`` handlers run in FastAPI's threadpool. A separate client from
the legacy Motor one in ``server.py``; UUIDs use the standard (binary subtype 4)
representation and timestamps come back timezone-aware (UTC).
"""

from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from .settings import MongoSettings


def build_client(settings: MongoSettings, *, server_selection_timeout_ms: int = 30000) -> MongoClient:
    return MongoClient(
        settings.url,
        serverSelectionTimeoutMS=server_selection_timeout_ms,
        uuidRepresentation="standard",
        tz_aware=True,
        retryWrites=True,
        appname="mtb-uctsm",
    )


def get_database(client: MongoClient, settings: MongoSettings) -> Database:
    return client[settings.database]
