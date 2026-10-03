"""Service-level harness for the end-to-end schedule scenarios.

The ``test_engine_*`` unit tests call the evaluator directly with a hand-built
``PatientContext``. The scenarios built on this harness go through the same
path a real trial does (docs/schedule-engine-case-handling.md, Part 2):

    persist a draft -> review/approve -> enrol a patient -> evaluate ->
    record anchors / actuals / conditions / assignments through
    PatientScheduleImpactService preview -> confirm -> re-evaluate

and then read back what was PERSISTED (``PatientEvent`` rows), what the
reminder worker would send (``compute_patient_projection``) and what the
deviation report says. ``check_invariants`` holds the cross-cutting safety
rules every scenario must keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    ConditionState, PatientConditionStatus, PatientEventStatus, ScheduleStatus,
    UniversalSchedule,
)
from app.services.deviation_service import DeviationService
from app.services.impact_service import PatientScheduleImpactService
from app.services.reminder_delivery import compute_patient_projection
from app.services.schedule_service import (
    PatientScheduleService, ScheduleReviewService, resolve_current_schedule_version,
)

HORIZON = date(2027, 12, 31)

#: Statuses that must never reach a patient as a reminder (doc Part 3).
NEVER_REMINDED = frozenset({
    "NOT_APPLICABLE", "WAITING_FOR_ANCHOR", "WAITING_FOR_CONDITION",
    "WAITING_FOR_ASSIGNMENT", "BLOCKED", "PAUSED", "CANCELLED", "UNRESOLVED",
    "AVAILABLE_ON_DEMAND",
})


@dataclass
class Trial:
    """One sqlite-backed trial with a protocol, a version and a schedule definition."""

    session: Session
    organization_id: UUID
    trial_id: UUID
    protocol_version_id: UUID
    definition_id: UUID
    actor_id: UUID
    reviewer_id: UUID
    _clock: datetime = field(
        default_factory=lambda: datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc))

    # --- time ---------------------------------------------------------------
    def tick(self) -> datetime:
        """A strictly increasing recording time.

        ``PatientAnchor.recorded_at`` defaults to the database ``now()``, which
        has one-second resolution on sqlite (and is the transaction start on
        Postgres); two anchors recorded together would tie and the "latest
        value wins" rule would depend on row order. Scenarios record in order.
        """
        self._clock += timedelta(minutes=1)
        return self._clock

    # --- schedule lifecycle ------------------------------------------------
    def publish(self, schedule: UniversalSchedule) -> UniversalSchedule:
        """Persist as a draft, then approve through the review service."""
        schedule.schedule_metadata.status = ScheduleStatus.DRAFT
        schedule.schedule_metadata.protocol_version_id = self.protocol_version_id
        ScheduleRepository(self.session).persist_draft(
            schedule, schedule_definition_id=self.definition_id)
        self.session.flush()
        ScheduleReviewService(self.session).approve_reviewed_table(
            schedule.schedule_version_id, reviewer_id=self.reviewer_id,
            reason="Schedule table reviewed against the protocol")
        self.session.commit()
        stored = ScheduleRepository(self.session).get(schedule.schedule_version_id)
        assert stored.schedule_metadata.status == ScheduleStatus.APPROVED
        return stored

    def current_version_id(self) -> UUID:
        return resolve_current_schedule_version(
            self.session, self.trial_id, self.definition_id).id

    # --- enrolment -----------------------------------------------------------
    def enrol(
        self,
        code: str,
        *,
        anchors: dict[str, date] | None = None,
        arm: str | None = None,
        dimensions: dict[str, list[str]] | None = None,
        horizon: date = HORIZON,
    ) -> db.Patient:
        """Add Patient: pinned to the version in force, enrolment anchors confirmed."""
        version_id = self.current_version_id()
        arm_id = None
        if arm is not None:
            arm_id = self.session.scalar(select(db.Arm.id).where(
                db.Arm.schedule_version_id == version_id, db.Arm.code == arm))
            assert arm_id is not None, arm
        patient = db.Patient(
            organization_id=self.organization_id, trial_id=self.trial_id,
            patient_code=code, current_schedule_version_id=version_id,
            arm_id=arm_id, dimension_values=dimensions or {},
        )
        self.session.add(patient)
        self.session.flush()
        self.session.add(db.PatientScheduleAssignment(
            patient_id=patient.id, schedule_version_id=version_id,
            assignment_type="ENROLMENT", assigned_by=self.actor_id,
        ))
        for anchor_code, value in (anchors or {}).items():
            self.session.add(db.PatientAnchor(
                patient_id=patient.id, anchor_definition_id=self.anchor_id(patient, anchor_code),
                value_date=value, status="CONFIRMED", source_type="OPERATIONAL_ENROLMENT",
                recorded_by=self.actor_id, recorded_at=self.tick(),
            ))
        self.session.commit()
        self.evaluate(patient, horizon=horizon)
        return patient

    def anchor_id(self, patient: db.Patient, code: str) -> UUID:
        anchor_id = self.session.scalar(select(db.Anchor.id).where(
            db.Anchor.schedule_version_id == patient.current_schedule_version_id,
            db.Anchor.code == code,
        ))
        assert anchor_id is not None, f"anchor {code} not in the patient's schedule"
        return anchor_id

    # --- evaluation ----------------------------------------------------------
    def service(self) -> PatientScheduleService:
        return PatientScheduleService(self.session)

    def evaluate(self, patient: db.Patient, *, horizon: date = HORIZON, rolling=None):
        _, rows = self.service().evaluate(
            patient.id, organization_id=self.organization_id, horizon=horizon,
            rolling=rolling, actor_id=self.actor_id, reason="scenario re-evaluation")
        self.session.commit()
        return rows

    def result(self, patient: db.Patient, *, horizon: date = HORIZON, rolling=None):
        return self.service().evaluate_result(
            patient, patient.current_schedule_version_id, horizon=horizon, rolling=rolling)

    def rows(self, patient: db.Patient) -> dict[str, db.PatientEvent]:
        """The patient's CURRENT persisted plan, by logical key."""
        self.session.refresh(patient)
        return {
            row.logical_key: row for row in self.service().current_events(
                patient.id, patient.current_schedule_version_id)
        }

    # --- recording through preview -> confirm ---------------------------------
    def impact(self) -> PatientScheduleImpactService:
        return PatientScheduleImpactService(self.session)

    def preview(self, patient: db.Patient, *, target=None, horizon: date = HORIZON,
                reason: str = "scenario change", **changes) -> db.ScheduleImpactProposal:
        proposal = self.impact().preview(
            patient.id, organization_id=self.organization_id,
            target_schedule_version_id=target or patient.current_schedule_version_id,
            horizon=horizon, actor_id=self.actor_id, reason=reason, **changes)
        self.session.commit()
        return proposal

    def confirm(self, proposal: db.ScheduleImpactProposal) -> dict[str, db.PatientEvent]:
        self.impact().confirm(
            proposal.id, organization_id=self.organization_id, actor_id=self.actor_id)
        self.session.commit()
        return self.rows(proposal_patient(self.session, proposal))

    def anchor_candidate(self, patient: db.Patient, code: str, value: date,
                         source_type: str) -> db.PatientAnchor:
        """What POST /patients/{id}/anchors writes: a pending candidate."""
        row = db.PatientAnchor(
            patient_id=patient.id, anchor_definition_id=self.anchor_id(patient, code),
            value_date=value, status="PENDING_CONFIRMATION", source_type=source_type,
            recorded_by=self.actor_id, recorded_at=self.tick(),
        )
        self.session.add(row)
        self.session.commit()
        return row

    def record_anchor(self, patient: db.Patient, code: str, value: date, source_type: str,
                      *, horizon: date = HORIZON):
        """Candidate -> impact preview -> confirm. Returns (proposal, rows)."""
        candidate = self.anchor_candidate(patient, code, value, source_type)
        proposal = self.preview(patient, horizon=horizon, anchor_candidate=candidate,
                                reason=f"{code} recorded")
        return proposal, self.confirm(proposal)

    def condition(self, patient: db.Patient, code: str, state: ConditionState,
                  occurred: date | None, *, resolved: date | None = None,
                  horizon: date = HORIZON, index: int = 0):
        """A PI confirms a condition transition through preview -> confirm."""
        proposal = self.preview(patient, horizon=horizon, reason=f"{code} {state.value}",
                                condition_change=PatientConditionStatus(
                                    condition_code=code, state=state,
                                    occurrence_date=occurred, resolution_date=resolved,
                                    occurrence_index=index))
        return proposal, self.confirm(proposal)

    def record_actual(self, patient: db.Patient, logical_key: str, actual: date,
                      *, completed: bool = True) -> db.PatientEventOccurrence:
        """What POST /patient-events/{id}/occurrences writes for a done visit."""
        row = self.rows(patient)[logical_key]
        occurrence = db.PatientEventOccurrence(
            patient_event_id=row.id, occurrence_type="VISIT",
            scheduled_date=row.nominal_start_date, actual_date=actual,
            status="COMPLETED" if completed else "RECORDED",
            recorded_by=self.actor_id, recorded_at=self.tick(),
        )
        self.session.add(occurrence)
        if completed:
            row.status = "COMPLETED"
        self.session.commit()
        return occurrence

    # --- what leaves the system ---------------------------------------------------
    def projection(self, patient: db.Patient, today: date):
        self.session.refresh(patient)
        return compute_patient_projection(self.session, patient, today=today)

    def deviations(self, patient: db.Patient, today: date):
        report = DeviationService(self.session).report(
            patient.id, organization_id=self.organization_id, today=today)
        return {item.logical_key: item for item in report.visits}

    def audit(self, action: str) -> list[db.AuditEvent]:
        return list(self.session.scalars(select(db.AuditEvent).where(
            db.AuditEvent.action == action).order_by(db.AuditEvent.created_at)))


def proposal_patient(session: Session, proposal: db.ScheduleImpactProposal) -> db.Patient:
    return session.get(db.Patient, proposal.patient_id)


def new_trial() -> tuple[Trial, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    ids = {name: uuid4() for name in (
        "organization", "trial", "protocol", "protocol_version", "definition")}
    session.add_all([
        db.Trial(id=ids["trial"], organization_id=ids["organization"]),
        db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="MTB-SCN-1"),
        db.ProtocolVersion(
            id=ids["protocol_version"], protocol_id=ids["protocol"], version_label="1",
            document_name="protocol.pdf", document_uri="private://protocol.pdf",
            document_hash="a" * 64,
        ),
        db.ScheduleDefinition(
            id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary"),
    ])
    session.commit()
    trial = Trial(
        session=session, organization_id=ids["organization"], trial_id=ids["trial"],
        protocol_version_id=ids["protocol_version"], definition_id=ids["definition"],
        actor_id=uuid4(), reviewer_id=uuid4(),
    )
    return trial, engine


# --- invariants -----------------------------------------------------------------

def check_invariants(
    trial: Trial,
    patient: db.Patient,
    *,
    today: date,
    completed_before: dict[str, tuple] | None = None,
    horizon: date = HORIZON,
) -> dict[str, tuple]:
    """The cross-cutting safety rules (doc Part 1 and Part 3), checked on the
    persisted plan, the live evaluation, and the reminder projection.

    Returns a snapshot of every completed/missed occurrence, to pass back in as
    ``completed_before`` after the next change: history never moves.
    """
    rows = trial.rows(patient)

    # 1. No RESOLVED occurrence without timing. (A PAUSED or CANCELLED
    # occurrence may keep the date it WOULD have had - that is what makes it
    # legible as "held" - but it is never due, as checked below.)
    for key, row in rows.items():
        dated = any(value is not None for value in (
            row.nominal_start_date, row.earliest_date, row.latest_date))
        if row.status == "RESOLVED":
            assert dated, f"{key} is RESOLVED with no timing"
    live = trial.result(patient, horizon=horizon)
    for item in live.events:
        if item.status == PatientEventStatus.RESOLVED:
            assert item.timing is not None, f"{item.event_code}:{item.occurrence_index} RESOLVED without timing"

    # 2. Nothing that is not actionable is ever reminded about or put in a calendar.
    projection = trial.projection(patient, today)
    for reminder in projection.reminders:
        row = rows[reminder.logical_key]
        assert row.status not in NEVER_REMINDED, (
            f"reminder for {reminder.logical_key} in status {row.status}")
        assert row.status == "RESOLVED", reminder.logical_key
    event_entries = {item.reference for item in projection.calendar if item.kind == "EVENT"}
    for key in event_entries:
        assert rows[key].status not in NEVER_REMINDED, key

    # 3. Completed history never changes.
    snapshot = {
        key: (row.status, row.nominal_start_date, row.earliest_date, row.latest_date,
              row.logical_occurrence_id)
        for key, row in rows.items() if row.status in {"COMPLETED", "MISSED"}
    }
    for key, before in (completed_before or {}).items():
        assert key in snapshot, f"completed {key} disappeared"
        assert snapshot[key] == before, f"completed {key} changed: {before} -> {snapshot[key]}"

    # 4. Re-evaluation is deterministic.
    again = trial.result(patient, horizon=horizon)
    assert _fingerprint(live) == _fingerprint(again)
    return snapshot


def _fingerprint(result) -> list[dict]:
    return [
        item.model_dump(mode="json", exclude={"event_definition_id"})
        for item in result.events
    ]


def statuses(rows: dict[str, db.PatientEvent], *keys: str) -> list[str]:
    return [rows[key].status for key in keys]


def reminder_keys(projection) -> set[str]:
    return {item.logical_key for item in projection.reminders}
