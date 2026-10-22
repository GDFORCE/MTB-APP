"""Rebuild a patient's reminders from the latest COMMITTED projection (P10-04).

Reminders are derived only from ``compute_patient_projection`` (committed evaluation rows), diffed
against the stored ACTIVE reminders with the existing ``reconcile`` rule (a moved date is a cancel
plus an add, so the patient is never reminded of both), and applied in ONE transaction together with
their outbox messages. Without a GRANTED, complete preference record nothing is scheduled and any
existing reminder is withdrawn; there are no default zones, times or lead days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from .dashboard import compute_patient_projection
from .ports import UnitOfWork
from .records import AuditRecord, OutboxRecord, utc_now
from .reminder_records import NotificationPreferenceRecord, ReminderNoticeRecord
from .schedule_projection import reconcile

AGGREGATE = "REMINDER"


@dataclass(frozen=True)
class SyncOutcome:
    created: tuple[str, ...]
    withdrawn: tuple[str, ...]
    unchanged: int
    reason: str | None = None


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _in_quiet(local: datetime, prefs: NotificationPreferenceRecord) -> bool:
    if prefs.quiet_start is None:
        return False
    now, start, end = local.hour * 60 + local.minute, _minutes(prefs.quiet_start), _minutes(prefs.quiet_end)
    return start <= now < end if start < end else (now >= start or now < end)      # window may cross midnight


def scheduled_instant(nominal: date, prefs: NotificationPreferenceRecord, *, now: datetime) -> datetime:
    """The UTC instant a reminder for ``nominal`` should arrive: lead days before, at the patient's local
    delivery time, never inside quiet hours, never in the past (then the next allowed moment)."""
    zone = ZoneInfo(prefs.timezone)
    hours, minutes = (int(part) for part in prefs.delivery_time.split(":"))
    target = datetime.combine(nominal - timedelta(days=prefs.lead_days), time(hours, minutes), tzinfo=zone)
    local = max(target, now.astimezone(zone))
    for _ in range(2):                                              # at most one quiet window to cross
        if not _in_quiet(local, prefs):
            break
        end_hours, end_minutes = (int(part) for part in prefs.quiet_end.split(":"))
        end = local.replace(hour=end_hours, minute=end_minutes, second=0, microsecond=0)
        local = end if end > local else end + timedelta(days=1)
    return local.astimezone(timezone.utc)


class ReminderSyncService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    # -- preferences ------------------------------------------------------------------------
    def record_preferences(self, organization_id: UUID, patient_id: UUID, *, actor_id: UUID, consent: str,
                           timezone_name: str, delivery_time: str, lead_days: int,
                           quiet_start: str | None = None, quiet_end: str | None = None) -> NotificationPreferenceRecord:
        def work(tx):
            tx.patients.get(organization_id, patient_id)                 # must exist in this organization
            current = tx.reminders.get_preferences(organization_id, patient_id)
            record = NotificationPreferenceRecord(
                organization_id=organization_id, patient_id=patient_id, consent=consent, timezone=timezone_name,
                delivery_time=delivery_time, lead_days=lead_days, quiet_start=quiet_start, quiet_end=quiet_end,
                recorded_by=actor_id)
            saved = tx.reminders.save_preferences(record, expected_revision=current.revision if current else None)
            delivery_fields = ("timezone", "delivery_time", "lead_days", "quiet_start", "quiet_end")
            changed_delivery = current is not None and any(
                getattr(current, field) != getattr(record, field) for field in delivery_fields)
            if record.consent != "GRANTED" or changed_delivery:
                # The worker scans granted preferences. A withdrawn patient
                # would otherwise never be visited again, leaving old notices
                # active. Retire them in this same preference transaction.
                reason = "CONSENT_NOT_GRANTED" if record.consent != "GRANTED" else "PREFERENCES_CHANGED"
                at = utc_now()
                for notice in tx.reminders.list_active(organization_id, patient_id):
                    tx.reminders.withdraw(organization_id, patient_id, notice.reminder_key, at=at, reason=reason)
                    self._enqueue(tx, organization_id, patient_id, notice.id, "REMINDER_WITHDRAWN",
                                  {"reminder_key": notice.reminder_key, "reason": reason}, at)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id, action="NOTIFICATION_PREFERENCES_RECORDED",
                entity_type="PATIENT", entity_id=patient_id,
                before={"consent": current.consent} if current else None, after={"consent": consent}))
            return saved
        return self._uow.run(work)

    # -- sync -------------------------------------------------------------------------------
    def sync(self, organization_id: UUID, patient_id: UUID, *, now: datetime, today: date) -> SyncOutcome:
        patient = self._uow.read(lambda tx: tx.patients.get(organization_id, patient_id))
        prefs = self._uow.read(lambda tx: tx.reminders.get_preferences(organization_id, patient_id))
        if prefs is None or prefs.consent != "GRANTED":
            desired, reason = [], "CONSENT_NOT_GRANTED"
        else:
            projection = compute_patient_projection(self._uow, patient, today=today)
            desired = [item for item in projection.reminders if item.audience == "PATIENT"]
            reason = None
        evaluation_id = self._uow.read(lambda tx: self._current_evaluation(tx, patient))

        def work(tx):
            active = {row.reminder_key: row for row in tx.reminders.list_active(organization_id, patient_id)}
            plan = reconcile(list(active), desired)
            by_key = {item.key: item for item in desired}
            created, withdrawn = [], []
            for key in plan.cancelled:
                tx.reminders.withdraw(organization_id, patient_id, key, at=now,
                                      reason=reason or "SCHEDULE_CHANGED")
                self._enqueue(tx, organization_id, patient_id, active[key].id, "REMINDER_WITHDRAWN",
                              {"reminder_key": key, "reason": reason or "SCHEDULE_CHANGED"}, now)
                withdrawn.append(key)
            for key in (*plan.added, *plan.updated):
                candidate = by_key[key]
                when = scheduled_instant(candidate.nominal_date, prefs, now=now)
                notice = ReminderNoticeRecord(
                    organization_id=organization_id, patient_id=patient_id, reminder_key=key,
                    logical_key=candidate.logical_key, event_code=candidate.event_code, scheduled_for=when,
                    nominal_date=candidate.nominal_date, policy_state=candidate.policy_state,
                    requires_attendance=candidate.requires_attendance, message=candidate.message,
                    evaluation_id=evaluation_id)
                # A concurrent sync that wins the unique (active) key aborts this transaction; the
                # caller retries and finds the reminder already scheduled, so nothing is duplicated.
                tx.reminders.add_notice(notice)
                self._enqueue(tx, organization_id, patient_id, notice.id, "REMINDER_SCHEDULED",
                              {"reminder_key": key, "scheduled_for": when.isoformat()}, now)
                created.append(key)
            return SyncOutcome(tuple(created), tuple(withdrawn), len(plan.unchanged), reason)
        return self._uow.run(work)

    @staticmethod
    def _current_evaluation(tx, patient):
        fresh = tx.patients.get(patient.organization_id, patient.id)
        if fresh.current_schedule_version_id is None:
            return None
        pointer = tx.evaluations.get_pointer(fresh.id, fresh.current_schedule_version_id)
        return pointer.current_evaluation_id if pointer else None

    @staticmethod
    def _enqueue(tx, organization_id, patient_id, notice_id, event_type, payload, now):
        """One outbox message per notice and transition; the notice id makes the key unique and stable."""
        tx.outbox.add(OutboxRecord(
            organization_id=organization_id, aggregate_type=AGGREGATE, aggregate_id=patient_id,
            event_type=event_type, payload={**payload, "notice_id": str(notice_id)}, created_at=now,
            dedupe_key=f"reminder:{notice_id}:{event_type}"))
