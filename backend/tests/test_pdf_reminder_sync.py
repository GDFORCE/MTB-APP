"""P10-04: reminders are rebuilt from the committed projection, idempotently, with consent, zone and quiet hours."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.reminder_records import NotificationPreferenceRecord
from app.application.reminder_sync import ReminderSyncService, scheduled_instant
from app.domain.schedule.models import OccurrenceFactKind
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]
NOW = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)
SYNC_TODAY = date(2026, 1, 1)


def prefs(**extra):
    base = dict(organization_id=uuid4(), patient_id=uuid4(), consent="GRANTED", timezone="America/New_York",
                delivery_time="09:00", lead_days=2, recorded_by=uuid4())
    base.update(extra)
    return NotificationPreferenceRecord(**base)


# ---------------------------------------------------------------- pure scheduling rules
def test_a_reminder_arrives_lead_days_before_at_the_patients_local_time_in_utc():
    winter = scheduled_instant(date(2026, 1, 20), prefs(), now=NOW)
    assert winter == datetime(2026, 1, 18, 14, 0, tzinfo=timezone.utc)        # 09:00 EST = 14:00 UTC
    summer = scheduled_instant(date(2026, 7, 12), prefs(), now=NOW)
    assert summer == datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)        # 09:00 EDT = 13:00 UTC
    assert scheduled_instant(date(2026, 3, 12), prefs(), now=NOW) == datetime(2026, 3, 10, 13, 0, tzinfo=timezone.utc)  # DST began 8 March


def test_quiet_hours_move_a_reminder_to_their_end_including_a_window_that_crosses_midnight():
    quiet = prefs(quiet_start="08:00", quiet_end="10:00")
    assert scheduled_instant(date(2026, 3, 12), quiet, now=NOW) == datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)   # 10:00 EDT
    overnight = prefs(delivery_time="23:30", quiet_start="22:00", quiet_end="07:00")
    assert scheduled_instant(date(2026, 3, 12), overnight, now=NOW) == datetime(2026, 3, 11, 11, 0, tzinfo=timezone.utc)  # 07:00 EDT next day


def test_a_reminder_that_would_be_in_the_past_is_due_at_the_next_allowed_moment():
    late = datetime(2026, 3, 11, 20, 3, tzinfo=timezone.utc)
    assert scheduled_instant(date(2026, 3, 12), prefs(), now=late) == late
    quiet = prefs(quiet_start="15:00", quiet_end="18:00")                      # 20:03 UTC = 16:03 EDT, inside quiet hours
    assert scheduled_instant(date(2026, 3, 12), quiet, now=late) == datetime(2026, 3, 11, 22, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [dict(timezone="Mars/Olympus"), dict(delivery_time="9am"), dict(lead_days=31),
                                 dict(consent="MAYBE"), dict(quiet_start="08:00"), dict(quiet_start="08:00", quiet_end="08:00")])
def test_incomplete_or_invalid_preferences_are_refused_never_defaulted(bad):
    with pytest.raises(ValueError):
        prefs(**bad)


# ---------------------------------------------------------------- the service on both stores
def setup(uow):
    world = World(uow)
    patient = world.patient_ids[0]
    return world, patient, ReminderSyncService(uow)


def pending(world, patient):
    return [row for row in world.uow.read(lambda tx: tx.outbox.list_pending(limit=1000))
            if row.aggregate_type == "REMINDER" and row.aggregate_id == patient]


def keys(world, patient):
    return {row.reminder_key for row in world.uow.read(lambda tx: tx.reminders.list_active(world.org, patient))}


def grant(world, patient, service, **extra):
    args = dict(consent="GRANTED", timezone_name="Europe/London", delivery_time="09:00", lead_days=1)
    args.update(extra)
    return service.record_preferences(world.org, patient, actor_id=world.pi, **args)


def test_without_consent_nothing_is_scheduled_and_the_reason_is_explicit(uow):
    world, patient, service = setup(uow)
    outcome = service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    assert outcome.created == () and outcome.reason == "CONSENT_NOT_GRANTED" and pending(world, patient) == []
    grant(world, patient, service, consent="DECLINED")
    assert service.sync(world.org, patient, now=NOW, today=SYNC_TODAY).created == ()


def test_granted_consent_schedules_each_projected_reminder_once_and_a_rerun_changes_nothing(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    first = service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    assert first.created and first.withdrawn == ()
    assert keys(world, patient) == set(first.created)
    messages = pending(world, patient)
    assert len(messages) == len(first.created) and {m.event_type for m in messages} == {"REMINDER_SCHEDULED"}
    assert len({m.dedupe_key for m in messages}) == len(messages)
    again = service.sync(world.org, patient, now=NOW + timedelta(minutes=5), today=SYNC_TODAY)
    assert again.created == () and again.withdrawn == () and again.unchanged == len(first.created)
    assert len(pending(world, patient)) == len(messages)                     # no duplicate outbox messages


def test_a_moved_date_cancels_the_old_reminder_and_adds_the_new_one_never_both(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    first = service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    before = keys(world, patient)
    world.patients.record_actual_occurrence(
        patient, organization_id=world.org, logical_key="C1D1:0", kind=OccurrenceFactKind.PERFORMED,
        visit_date=date(2026, 1, 8), treatment_date=date(2026, 1, 15), actor_id=world.pi, reason="late dose",
        horizon=HORIZON, today=TODAY)
    moved = service.sync(world.org, patient, now=NOW + timedelta(hours=1), today=SYNC_TODAY)
    after = keys(world, patient)
    assert moved.created or moved.withdrawn                                    # something moved
    assert not (set(moved.withdrawn) & after)                                  # nothing withdrawn is still active
    assert after == (before - set(moved.withdrawn)) | set(moved.created)
    logical = lambda ks: [k.rsplit(":", 1)[0] for k in ks]
    assert len(logical(after)) == len(set(logical(after)))                     # one reminder per visit, never two dates
    assert first.created


def test_withdrawing_consent_withdraws_every_active_reminder(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    assert keys(world, patient)
    grant(world, patient, service, consent="WITHDRAWN")
    assert keys(world, patient) == set()  # withdrawal takes effect in the preference write itself
    outcome = service.sync(world.org, patient, now=NOW + timedelta(hours=2), today=SYNC_TODAY)
    assert outcome.created == () and outcome.withdrawn == () and outcome.reason == "CONSENT_NOT_GRANTED"
    assert keys(world, patient) == set()
    assert any(m.event_type == "REMINDER_WITHDRAWN" for m in pending(world, patient))


def test_preferences_are_tenant_scoped_revision_guarded_and_audited(uow):
    world, patient, service = setup(uow)
    with pytest.raises(Exception):
        service.record_preferences(uuid4(), patient, actor_id=world.pi, consent="GRANTED",
                                   timezone_name="Europe/London", delivery_time="09:00", lead_days=1)
    first = grant(world, patient, service)
    second = grant(world, patient, service, lead_days=3)
    assert (first.revision, second.revision) == (1, 2) and second.lead_days == 3
    stored = world.uow.read(lambda tx: tx.reminders.get_preferences(world.org, patient))
    assert stored.lead_days == 3 and world.uow.read(lambda tx: tx.reminders.get_preferences(uuid4(), patient)) is None
    actions = [a.action for a in world.uow.read(lambda tx: tx.audit.list_for_entity(world.org, "PATIENT", patient))]
    assert actions.count("NOTIFICATION_PREFERENCES_RECORDED") == 2


def test_a_reminder_that_returns_to_an_earlier_date_is_scheduled_again(uow):
    world, patient, service = setup(uow)
    grant(world, patient, service)
    first = service.sync(world.org, patient, now=NOW, today=SYNC_TODAY)
    key = first.created[0]
    uow.run(lambda tx: tx.reminders.withdraw(world.org, patient, key, reason="SCHEDULE_CHANGED", at=NOW))
    assert key not in keys(world, patient)
    back = service.sync(world.org, patient, now=NOW + timedelta(hours=1), today=SYNC_TODAY)
    assert key in back.created and key in keys(world, patient)
    assert len({m.dedupe_key for m in pending(world, patient)}) == len(pending(world, patient))
