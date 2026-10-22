"""Consent changes must take effect even when the worker scans only granted patients."""
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.application.operations_worker import run_cycle
from app.application.reminder_sync import ReminderSyncService
from test_pdf_reminder_sync import NOW, SYNC_TODAY, grant, keys, pending, setup

pytest_plugins = ["tests.contract.conftest"]


@pytest.mark.parametrize("consent", ["WITHDRAWN", "DECLINED"])
def test_revocation_immediately_retires_notices_without_a_second_sync(uow, consent):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    created = service.sync(world.org, patient, now=NOW, today=SYNC_TODAY).created
    assert created
    grant(world, patient, service, consent=consent)
    assert keys(world, patient) == set()
    withdrawn = [row for row in pending(world, patient) if row.event_type == "REMINDER_WITHDRAWN"]
    assert len(withdrawn) == len(created)
    assert {row.payload["reason"] for row in withdrawn} == {"CONSENT_NOT_GRANTED"}
    assert run_cycle(uow, now=NOW, today=SYNC_TODAY)["created"] == 0


def test_changed_delivery_preferences_replace_the_old_notice_set(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    before = uow.read(lambda tx: tx.reminders.list_active(world.org, patient))
    grant(world, patient, service, delivery_time="11:00")
    assert keys(world, patient) == set()
    service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    after = uow.read(lambda tx: tx.reminders.list_active(world.org, patient))
    assert {row.reminder_key for row in before} == {row.reminder_key for row in after}
    assert not ({row.id for row in before} & {row.id for row in after})
    original = {row.reminder_key: row for row in before}
    assert all(row.scheduled_for == original[row.reminder_key].scheduled_for + timedelta(hours=2) for row in after)


def test_identical_preferences_do_not_cancel_or_duplicate_active_notices(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    before = uow.read(lambda tx: tx.reminders.list_active(world.org, patient))
    messages = len(pending(world, patient))
    grant(world, patient, service)
    assert {row.id for row in uow.read(lambda tx: tx.reminders.list_active(world.org, patient))} == {row.id for row in before}
    assert len(pending(world, patient)) == messages


def test_preference_and_notice_changes_roll_back_together_on_outbox_failure(uow):
    world, patient, service = setup(uow)
    old = grant(world, patient, service)
    service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    before = keys(world, patient)
    messages = len(pending(world, patient))
    with patch.object(ReminderSyncService, "_enqueue", side_effect=RuntimeError("injected test failure")):
        with pytest.raises(RuntimeError, match="injected test failure"):
            grant(world, patient, service, consent="WITHDRAWN")
    stored = uow.read(lambda tx: tx.reminders.get_preferences(world.org, patient))
    assert stored.revision == old.revision and stored.consent == "GRANTED"
    assert keys(world, patient) == before and len(pending(world, patient)) == messages
