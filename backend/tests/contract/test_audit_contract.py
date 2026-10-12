"""Contract for the audit repository and unit-of-work atomicity.

Runs against the in-memory fake and MongoDB (see conftest).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.errors import DuplicateRecordError
from app.application.records import AuditRecord

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
ORG = uuid4()


def record(entity_id, *, minutes=0, action="PATIENT_EVALUATED", org=ORG, **extra) -> AuditRecord:
    return AuditRecord(
        organization_id=org, action=action, entity_type="PATIENT", entity_id=entity_id,
        created_at=T0 + timedelta(minutes=minutes), **extra)


def test_append_then_read_back_losslessly(uow):
    entity = uuid4()
    original = record(
        entity, actor_id=uuid4(), reason="occurrence recorded",
        before={"actual_date": None}, after={"actual_date": "2026-10-13", "n": 2},
        metadata={"source": "SITE_ENTRY"})
    uow.run(lambda tx: tx.audit.append(original))
    [loaded] = uow.read(lambda tx: tx.audit.list_for_entity(ORG, "PATIENT", entity))
    assert loaded == original


def test_entity_history_is_oldest_first_and_scoped_to_the_organization(uow):
    entity, other_org = uuid4(), uuid4()

    def write(tx):
        tx.audit.append(record(entity, minutes=5, action="B"))
        tx.audit.append(record(entity, minutes=1, action="A"))
        tx.audit.append(record(entity, minutes=3, action="X", org=other_org))

    uow.run(write)
    rows = uow.read(lambda tx: tx.audit.list_for_entity(ORG, "PATIENT", entity))
    assert [item.action for item in rows] == ["A", "B"]
    assert uow.read(lambda tx: tx.audit.list_for_entity(other_org, "PATIENT", entity))[0].action == "X"


def test_organization_feed_is_newest_first_and_filterable(uow):
    first, second = uuid4(), uuid4()

    def write(tx):
        tx.audit.append(record(first, minutes=1, action="EVAL"))
        tx.audit.append(record(second, minutes=9, action="EVAL"))
        tx.audit.append(record(second, minutes=5, action="OTHER"))

    uow.run(write)
    feed = uow.read(lambda tx: tx.audit.list_for_organization(ORG, action="EVAL"))
    assert [item.entity_id for item in feed] == [second, first]
    recent = uow.read(lambda tx: tx.audit.list_for_organization(ORG, since=T0 + timedelta(minutes=4)))
    assert {item.action for item in recent} == {"EVAL", "OTHER"} and len(recent) == 2


def test_a_repeated_id_is_a_duplicate_not_an_overwrite(uow):
    entity = uuid4()
    original = record(entity, reason="first")
    uow.run(lambda tx: tx.audit.append(original))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.audit.append(original.model_copy(update={"reason": "second"})))
    [loaded] = uow.read(lambda tx: tx.audit.list_for_entity(ORG, "PATIENT", entity))
    assert loaded.reason == "first"


def test_a_failed_operation_commits_nothing(uow):
    entity = uuid4()

    def write(tx):
        tx.audit.append(record(entity, minutes=1))
        tx.audit.append(record(entity, minutes=2))
        raise RuntimeError("boom after two writes")

    with pytest.raises(RuntimeError):
        uow.run(write)
    assert uow.read(lambda tx: tx.audit.list_for_entity(ORG, "PATIENT", entity)) == []


def test_the_repository_exposes_no_way_to_rewrite_history(uow):
    audit = uow.read(lambda tx: tx.audit)
    assert not any(hasattr(audit, name) for name in ("update", "delete", "replace", "remove"))
