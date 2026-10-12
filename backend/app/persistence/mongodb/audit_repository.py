"""Insert-only audit repository (``audit_events``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pymongo import ASCENDING, DESCENDING
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.application.errors import DuplicateRecordError
from app.application.records import AuditRecord

from .codec import aware, encode

COLLECTION = "audit_events"
SCHEMA_VERSION = 1


def _to_document(record: AuditRecord) -> dict[str, Any]:
    return {
        "_id": record.id,
        "schema_version": SCHEMA_VERSION,
        "organization_id": record.organization_id,
        "actor_id": record.actor_id,
        "action": record.action,
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "before": encode(record.before),
        "after": encode(record.after),
        "reason": record.reason,
        "metadata": encode(record.metadata),
        "created_at": encode(record.created_at),
    }


def _from_document(doc: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        id=doc["_id"], organization_id=doc["organization_id"], actor_id=doc.get("actor_id"),
        action=doc["action"], entity_type=doc["entity_type"], entity_id=doc["entity_id"],
        before=doc.get("before"), after=doc.get("after"), reason=doc.get("reason"),
        metadata=doc.get("metadata") or {}, created_at=aware(doc["created_at"]),
    )


class MongoAuditRepository:
    """No update and no delete exists on purpose: the audit trail is append-only."""

    def __init__(self, database: Database, session: ClientSession | None = None):
        self._collection = database[COLLECTION]
        self._session = session

    def append(self, record: AuditRecord) -> None:
        try:
            self._collection.insert_one(_to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(
                f"audit event {record.id} already exists", key=str(record.id)) from error

    def list_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID, *, limit: int = 500,
    ) -> list[AuditRecord]:
        cursor = self._collection.find(
            {"organization_id": organization_id, "entity_type": entity_type, "entity_id": entity_id},
            session=self._session,
        ).sort([("created_at", ASCENDING), ("_id", ASCENDING)]).limit(limit)
        return [_from_document(doc) for doc in cursor]

    def list_for_organization(
        self, organization_id: UUID, *, action: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[AuditRecord]:
        query: dict[str, Any] = {"organization_id": organization_id}
        if action is not None:
            query["action"] = action
        if since is not None:
            query["created_at"] = {"$gte": encode(since)}
        cursor = self._collection.find(query, session=self._session).sort(
            [("created_at", DESCENDING), ("_id", DESCENDING)]).limit(limit)
        return [_from_document(doc) for doc in cursor]
