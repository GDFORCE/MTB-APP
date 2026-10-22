from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.errors import StaleRevisionError
from app.application.outbox_delivery import OutboxDelivery, PermanentDeliveryError
from app.application.records import OutboxRecord

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def enqueue(uow, **overrides):
    row = OutboxRecord(organization_id=uuid4(), aggregate_type="PATIENT", aggregate_id=uuid4(),
                       event_type="SCHEDULE_CHANGED", dedupe_key=uuid4().hex,
                       created_at=NOW, **overrides)
    uow.run(lambda tx: tx.outbox.add(row))
    return row


def claim(uow, token="first", now=NOW):
    return uow.run(lambda tx: tx.outbox.claim(now=now, lease_seconds=10, token=token))


def test_claim_is_exclusive_and_redelivery_keeps_the_idempotency_key(uow):
    row = enqueue(uow)
    first = claim(uow)
    assert first.id == row.id and first.attempts == 1
    assert claim(uow, token="competitor") is None
    second = claim(uow, token="replacement", now=NOW + timedelta(seconds=11))
    assert second.attempts == 2 and second.dedupe_key == first.dedupe_key
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.outbox.settle(
            row.organization_id, row.id, "first", now=NOW + timedelta(seconds=12), status="SENT"))


def test_delivery_is_outside_transaction_and_success_is_not_reoffered(uow):
    row = enqueue(uow); observed = []
    def deliver(record):
        # A new transaction here proves no nested transaction wraps the handler.
        observed.append(uow.read(lambda tx: tx.outbox.list_pending()))
        assert record.dedupe_key == row.dedupe_key
    service = OutboxDelivery(uow, clock=lambda: NOW)
    assert service.run_once(deliver)["status"] == "SENT"
    assert service.run_once(deliver)["status"] == "IDLE"
    assert observed == [[]]


def test_retry_waits_for_backoff_and_reaches_dead_letter_without_leaking_text(uow):
    enqueue(uow, max_attempts=2); current = [NOW]
    service = OutboxDelivery(uow, clock=lambda: current[0])
    def failure(record):
        raise RuntimeError("secret patient text must not appear in the result")
    first = service.run_once(failure)
    assert first["status"] == "PENDING" and "secret" not in str(first)
    assert service.run_once(failure)["status"] == "IDLE"
    current[0] += timedelta(seconds=3)
    assert service.run_once(failure)["status"] == "DEAD"
    assert service.run_once(failure)["status"] == "IDLE"


def test_permanent_failure_is_not_retried(uow):
    enqueue(uow)
    def failure(record):
        raise PermanentDeliveryError()
    service = OutboxDelivery(uow, clock=lambda: NOW)
    assert service.run_once(failure)["status"] == "DEAD"
    assert service.run_once(failure)["status"] == "IDLE"


def test_repeated_process_crashes_also_have_a_bounded_attempt_limit(uow):
    enqueue(uow, max_attempts=1)
    assert claim(uow) is not None
    assert claim(uow, now=NOW + timedelta(seconds=11)) is None
    assert claim(uow, now=NOW + timedelta(days=1)) is None


def test_foreign_tenant_cannot_acknowledge_another_delivery(uow):
    row = enqueue(uow); claim(uow)
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.outbox.settle(uuid4(), row.id, "first", now=NOW, status="SENT"))
    uow.run(lambda tx: tx.outbox.settle(row.organization_id, row.id, "first", now=NOW, status="SENT"))


# ---- P13-03: inbox + authorized, audited dead-letter replay ------------------------------------
from app.application.errors import RecordNotFoundError
from app.application.outbox_delivery import DeadLetterService, ReplayDeniedError
from app.authorization.policy import Grant, Principal


def operator(org, *, grant=True, role="pi", org_admin=False):
    return Principal(user_id=str(uuid4()), organization_id=str(org), role=role, org_admin=org_admin,
                     grants=(Grant("outbox.replay"),) if grant else ())


def make_dead(uow):
    row = enqueue(uow, max_attempts=1)
    service = OutboxDelivery(uow, clock=lambda: NOW)
    def boom(_):
        raise RuntimeError("synthetic patient payload must not leak")
    assert service.run_once(boom)["status"] == "DEAD"
    return row


def test_replay_requires_the_grant_the_reason_and_the_same_organization_and_is_audited(uow):
    row = make_dead(uow)
    replay = DeadLetterService(uow, clock=lambda: NOW)
    for denied in (operator(row.organization_id, grant=False), operator(row.organization_id, grant=False, org_admin=True),
                   operator(uuid4()), operator(row.organization_id, role="patient")):
        with pytest.raises(ReplayDeniedError):
            replay.replay(denied, row.organization_id, row.id, reason="Retry after provider fix")
    assert uow.read(lambda tx: tx.outbox.get(row.organization_id, row.id)).status == "DEAD"
    who = operator(row.organization_id)
    with pytest.raises(ValueError):
        replay.replay(who, row.organization_id, row.id, reason="  ")
    assert [item["id"] for item in replay.list_dead(who, row.organization_id)] == [str(row.id)]
    result = replay.replay(who, row.organization_id, row.id, reason="Retry after provider fix")
    assert result["status"] == "PENDING" and result["dedupe_key"] == row.dedupe_key
    audit = uow.read(lambda tx: tx.audit.list_for_entity(row.organization_id, "OUTBOX_MESSAGE", row.id))
    assert [a.action for a in audit] == ["OUTBOX_DEAD_LETTER_REPLAYED"]
    assert audit[0].reason == "Retry after provider fix" and audit[0].before["status"] == "DEAD"
    with pytest.raises(RecordNotFoundError):                       # a pending message is not replayable
        replay.replay(who, row.organization_id, row.id, reason="again")
    with pytest.raises(RecordNotFoundError):                       # another tenant cannot see it
        other = operator(uuid4())
        replay.replay(other, UUID_OF(other), row.id, reason="cross-tenant probe")


def UUID_OF(principal):
    from uuid import UUID
    return UUID(principal.organization_id)


def test_replayed_message_is_applied_once_when_the_consumer_already_processed_it(uow):
    row = make_dead(uow)
    effects = []
    # The consumer actually processed this message earlier (late success after the dead-letter).
    from app.application.records import InboxRecord
    uow.run(lambda tx: tx.inbox.add(InboxRecord(
        organization_id=row.organization_id, consumer="reminders", dedupe_key=row.dedupe_key, outbox_id=row.id)))
    DeadLetterService(uow, clock=lambda: NOW).replay(
        operator(row.organization_id), row.organization_id, row.id, reason="Verify and close")
    service = OutboxDelivery(uow, clock=lambda: NOW)
    assert service.run_once(effects.append, consumer="reminders")["status"] == "DUPLICATE_SUPPRESSED"
    assert effects == [] and service.run_once(effects.append, consumer="reminders")["status"] == "IDLE"


def test_one_business_result_per_consumer_and_a_second_consumer_is_independent(uow):
    enqueue(uow); calls = []
    service = OutboxDelivery(uow, clock=lambda: NOW)
    assert service.run_once(calls.append, consumer="reminders")["status"] == "SENT"
    assert len(calls) == 1
    row = uow.read(lambda tx: tx.inbox.find("reminders", calls[0].dedupe_key))
    assert row is not None and row.outbox_id == calls[0].id
    assert uow.read(lambda tx: tx.inbox.find("calendar", calls[0].dedupe_key)) is None
