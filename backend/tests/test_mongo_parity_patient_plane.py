"""Behavioural parity: the SQL patient service vs the Mongo-backed one.

Each scenario is a sequence of operations run through BOTH stacks - the existing
SQLAlchemy ``PatientScheduleService`` and the repository-port
``app.application.patient_schedule.PatientScheduleService`` (on the in-memory fake
and on a real replica set). After every step the persisted result is normalised
and compared. A difference is a behaviour change in the port, never a test to
adjust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.patients import PatientAnchorRecord, PatientRecord
from app.application.patient_schedule import PatientScheduleService as MongoPatientService
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence, OccurrenceFactKind,
    RecurrenceBasis, RecurrenceRule, RecurrenceTermination, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount,
    TemporalBasis,
)
from app.services.schedule_service import PatientScheduleService as SqlPatientService
from app.services.schedule_service import ScheduleReviewService

pytest_plugins = ["tests.contract.conftest"]

HORIZON = date(2027, 12, 31)
BASELINE = date(2026, 10, 1)


# ----------------------------------------------------------------- schedules
def days(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit="DAY")


def from_baseline(code: str, offset: int, *, sequence: int, **extra) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        sequence_number=sequence,
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=days(offset)), **extra)


def from_visit(code: str, source: str, offset: int, *, sequence: int, actual: bool) -> Event:
    mode = DependencyMode.ACTUAL_PREVIOUS_EVENT if actual else DependencyMode.NOMINAL
    basis = TemporalBasis.ACTUAL_VISIT if actual else TemporalBasis.CURRENT_PLANNED
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        sequence_number=sequence,
        timing=OffsetTiming(
            reference=EventReference(event_code=source, temporal_basis=basis), offset=days(offset)),
        dependencies=[Dependency(source_event_code=source, dependency_type="TEMPORAL")],
        dependency_mode=mode)


def schedule_of(*events: Event) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=3, source_text="schedule of assessments")
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        paths = [("EVENT_NAME", "display_name"), ("TIMING", "timing")]
        if event.dependencies:
            paths += [("DEPENDENCY", "dependencies"), ("DEPENDENCY_MODE", "dependency_mode")]
        if event.recurrence:
            paths.append(("RECURRENCE", "recurrence"))
        for claim_type, path in paths:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path, confidence=0.9))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.DRAFT),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=list(events), evidence=[evidence], claim_evidence=claims)


# -------------------------------------------------------------------- stacks
@dataclass
class Snapshot:
    rows: list[tuple]
    evaluations: int
    reconciliation: dict | None

    def __eq__(self, other: object) -> bool:  # readable pytest diffs
        return isinstance(other, Snapshot) and (self.rows, self.evaluations, self.reconciliation) \
            == (other.rows, other.evaluations, other.reconciliation)


def normalise(events) -> list[tuple]:
    return sorted(
        ((e.event_code if hasattr(e, "event_code") else None) or e.logical_key.rsplit(":", 1)[0],
         e.occurrence_index, e.status, e.nominal_start_date, e.earliest_date, e.latest_date,
         bool(e.protected_history))
        for e in events)


class SqlStack:
    organization_id = uuid4()

    def __init__(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        ids = {name: uuid4() for name in ("trial", "protocol", "version", "definition")}
        self.ids = ids
        self.session.add_all([
            db.Trial(id=ids["trial"], organization_id=self.organization_id),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="ABC-1"),
            db.ProtocolVersion(
                id=ids["version"], protocol_id=ids["protocol"], version_label="1",
                document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["version"], name="Primary"),
        ])
        self.session.flush()
        self.service = SqlPatientService(self.session)
        self.reviewer = uuid4()
        self.tick = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

    def publish(self, schedule: UniversalSchedule) -> UUID:
        schedule.schedule_metadata.protocol_version_id = self.ids["version"]
        ScheduleRepository(self.session).persist_draft(
            schedule, schedule_definition_id=self.ids["definition"])
        self.session.flush()
        ScheduleReviewService(self.session).approve_reviewed_table(
            schedule.schedule_version_id, reviewer_id=self.reviewer, reason="reviewed")
        self.session.commit()
        self.version_id = schedule.schedule_version_id
        return schedule.schedule_version_id

    def enrol(self, code: str, anchors: dict[str, date]) -> UUID:
        patient = db.Patient(
            organization_id=self.organization_id, trial_id=self.ids["trial"], patient_code=code,
            current_schedule_version_id=self.version_id, dimension_values={})
        self.session.add(patient)
        self.session.flush()
        for anchor_code, value in anchors.items():
            self.anchor(patient.id, anchor_code, value)
        self.session.commit()
        return patient.id

    def anchor(self, patient_id: UUID, code: str, value: date) -> None:
        anchor_id = self.session.scalar(select(db.Anchor.id).where(
            db.Anchor.schedule_version_id == self.version_id, db.Anchor.code == code))
        self.tick += timedelta(minutes=1)
        self.session.add(db.PatientAnchor(
            patient_id=patient_id, anchor_definition_id=anchor_id, value_date=value,
            status="CONFIRMED", source_type="OPERATIONAL_ENROLMENT", recorded_at=self.tick))
        self.session.commit()

    def evaluate(self, patient_id: UUID, key: str | None = None):
        evaluation, _ = self.service.evaluate(
            patient_id, organization_id=self.organization_id, horizon=HORIZON,
            schedule_version_id=self.version_id, idempotency_key=key)
        self.session.commit()
        return evaluation.output_summary.get("reconciliation")

    def fact(self, patient_id: UUID, logical_key: str, kind, value=None, supersedes=None):
        record = self.service.record_occurrence_fact(
            patient_id, organization_id=self.organization_id, logical_key=logical_key, kind=kind,
            value=value, supersedes_occurrence_id=supersedes)
        self.session.commit()
        return record.id

    def snapshot(self, patient_id: UUID, reconciliation) -> Snapshot:
        events = self.service.current_events(patient_id, self.version_id)
        evaluations = self.session.query(db.ScheduleEvaluation).join(
            db.PatientSchedule, db.PatientSchedule.id == db.ScheduleEvaluation.patient_schedule_id,
        ).filter(db.PatientSchedule.patient_id == patient_id).count()
        rows = sorted(
            (self.session.get(db.Event, e.event_definition_id).code, e.occurrence_index, e.status,
             e.nominal_start_date, e.earliest_date, e.latest_date, bool(e.protected_history))
            for e in events)
        return Snapshot(rows, evaluations, reconciliation)


class RepoStack:
    organization_id = uuid4()

    def __init__(self, uow):
        self.uow = uow
        org = self.organization_id
        trial = TrialRecord(organization_id=org, external_trial_id=f"x-{uuid4().hex[:6]}")
        protocol = ProtocolRecord(organization_id=org, trial_id=trial.id, protocol_number="ABC-1")
        version = ProtocolVersionRecord(
            organization_id=org, trial_id=trial.id, protocol_id=protocol.id, version_label="1",
            document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64)
        self.definition = ScheduleDefinitionRecord(
            organization_id=org, trial_id=trial.id, protocol_id=protocol.id,
            protocol_version_id=version.id, name="Primary")

        def seed(tx):
            tx.authoring.add_trial(trial)
            tx.authoring.add_protocol(protocol)
            tx.authoring.add_protocol_version(version)
            tx.authoring.add_definition(self.definition)

        uow.run(seed)
        self.trial_id = trial.id
        self.service = MongoPatientService(uow)
        self.reviewer = uuid4()
        self.tick = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

    def publish(self, schedule: UniversalSchedule) -> UUID:
        schedule.schedule_metadata.protocol_version_id = self.definition.protocol_version_id
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED

        def work(tx):
            tx.schedules.persist_draft(schedule, schedule_definition_id=self.definition.id)
            tx.schedules.transition(schedule.schedule_version_id, to_status="IN_REVIEW")
            tx.schedules.mark_approved(
                schedule.schedule_version_id, reviewer_id=self.reviewer,
                approved_at=datetime.now(timezone.utc), effective_from=None)

        self.uow.run(work)
        self.version_id = schedule.schedule_version_id
        self.anchor_ids = {a.code: a.id for a in schedule.anchors}
        return schedule.schedule_version_id

    def enrol(self, code: str, anchors: dict[str, date]) -> UUID:
        patient = PatientRecord(
            organization_id=self.organization_id, trial_id=self.trial_id, patient_code=code,
            current_schedule_version_id=self.version_id,
            # A natively enrolled patient is explicitly authoritative in this plane; an
            # unclassified one is (deliberately) refused clinical writes.
            authority_plane="MONGO")
        self.uow.run(lambda tx: tx.patients.add(patient))
        for anchor_code, value in anchors.items():
            self.anchor(patient.id, anchor_code, value)
        return patient.id

    def anchor(self, patient_id: UUID, code: str, value: date) -> None:
        self.tick += timedelta(minutes=1)
        record = PatientAnchorRecord(
            organization_id=self.organization_id, patient_id=patient_id,
            anchor_definition_id=self.anchor_ids[code], anchor_code=code, value_date=value,
            status="CONFIRMED", source_type="OPERATIONAL_ENROLMENT", recorded_at=self.tick)
        self.uow.run(lambda tx: tx.facts.add_anchor(record))

    def evaluate(self, patient_id: UUID, key: str | None = None):
        evaluation, _ = self.service.evaluate(
            patient_id, organization_id=self.organization_id, horizon=HORIZON,
            schedule_version_id=self.version_id, idempotency_key=key)
        return evaluation.output_summary.get("reconciliation")

    def fact(self, patient_id: UUID, logical_key: str, kind, value=None, supersedes=None):
        return self.service.record_occurrence_fact(
            patient_id, organization_id=self.organization_id, logical_key=logical_key, kind=kind,
            value=value, supersedes_occurrence_id=supersedes).id

    def snapshot(self, patient_id: UUID, reconciliation) -> Snapshot:
        events = self.uow.read(
            lambda tx: self.service.current_events(tx, patient_id, self.version_id))
        evaluations = self.uow.read(
            lambda tx: tx.evaluations.count_evaluations(patient_id, self.version_id))
        rows = sorted(
            (e.event_code, e.occurrence_index, e.status, e.nominal_start_date, e.earliest_date,
             e.latest_date, bool(e.protected_history)) for e in events)
        return Snapshot(rows, evaluations, reconciliation)


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


# ----------------------------------------------------------------- scenarios
def run_scenario(stack, builder, script):
    """Run ``script(stack, patient_id)`` collecting a snapshot per step."""
    stack.publish(builder())
    patient_id = stack.enrol("P-001", {"BASELINE": BASELINE})
    snapshots: list[Snapshot] = []

    def step(reconciliation=None):
        snapshots.append(stack.snapshot(patient_id, reconciliation))

    script(stack, patient_id, step)
    return snapshots


def assert_parity(sql_stack, repo_stack, builder, script):
    expected = run_scenario(sql_stack, builder, script)
    actual = run_scenario(repo_stack, builder, script)
    assert len(expected) == len(actual) > 0
    for index, (want, got) in enumerate(zip(expected, actual)):
        assert got == want, f"step {index}: Mongo-backed result differs from SQL"


def key(code: str, index: int = 0) -> str:
    return f"{code}:{index}"


def test_baseline_relative_schedule_evaluates_and_is_stable_on_re_evaluation(sql_stack, repo_stack):
    def build():
        return schedule_of(from_baseline("V1", 7, sequence=1), from_baseline("V2", 14, sequence=2))

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_actual_relative_visit_waits_then_follows_the_recorded_actual(sql_stack, repo_stack):
    def build():
        return schedule_of(
            from_baseline("V2", 9, sequence=1),
            from_visit("V3", "V2", 14, sequence=2, actual=True))

    def script(stack, patient, step):
        step(stack.evaluate(patient))                       # V3 waits for V2's actual
        stack.fact(patient, key("V2"), OccurrenceFactKind.PERFORMED, date(2026, 10, 13))
        step(stack.evaluate(patient))                       # V3 = 13 Oct + 14 = 27 Oct
        step()

    assert_parity(sql_stack, repo_stack, build, script)


def test_a_corrected_actual_moves_its_dependents_and_keeps_the_visit_completed(sql_stack, repo_stack):
    def build():
        return schedule_of(
            from_baseline("V2", 9, sequence=1),
            from_visit("V3", "V2", 14, sequence=2, actual=True))

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        first = stack.fact(patient, key("V2"), OccurrenceFactKind.PERFORMED, date(2026, 10, 13))
        step(stack.evaluate(patient))
        stack.fact(patient, key("V2"), OccurrenceFactKind.PERFORMED, date(2026, 10, 15),
                   supersedes=first)
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_a_planned_relative_visit_ignores_a_late_actual(sql_stack, repo_stack):
    def build():
        return schedule_of(
            from_baseline("V2", 9, sequence=1),
            from_visit("V3", "V2", 14, sequence=2, actual=False))

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        stack.fact(patient, key("V2"), OccurrenceFactKind.PERFORMED, date(2026, 10, 20))
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_a_missed_visit_stays_missed_across_re_evaluations(sql_stack, repo_stack):
    def build():
        return schedule_of(from_baseline("V1", 7, sequence=1), from_baseline("V2", 14, sequence=2))

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        stack.fact(patient, key("V1"), OccurrenceFactKind.MISSED)
        step(stack.evaluate(patient))
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_completion_survives_a_baseline_change_that_moves_everything_else(sql_stack, repo_stack):
    def build():
        return schedule_of(from_baseline("V1", 7, sequence=1), from_baseline("V2", 14, sequence=2))

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        stack.fact(patient, key("V1"), OccurrenceFactKind.PERFORMED, date(2026, 10, 9))
        step(stack.evaluate(patient))
        stack.anchor(patient, "BASELINE", date(2026, 10, 5))   # baseline moves by 4 days
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_a_repeating_visit_protects_only_the_occurrence_that_happened(sql_stack, repo_stack):
    def build():
        # The first occurrence is the baseline itself and the series counts from the baseline:
        # state that basis explicitly (an unstated one now blocks approval) instead of relying
        # on a legacy start that disagrees with the event's own +7 day timing.
        weekly = from_baseline("WEEKLY", 0, sequence=1)
        weekly.recurrence = RecurrenceRule(
            interval=PositiveTemporalAmount(value=7, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="COUNT", count=4),
            basis=RecurrenceBasis.FIXED_ANCHOR_SERIES, basis_status="STATED")
        return schedule_of(weekly)

    def script(stack, patient, step):
        step(stack.evaluate(patient))
        stack.fact(patient, key("WEEKLY", 1), OccurrenceFactKind.PERFORMED, date(2026, 10, 16))
        step(stack.evaluate(patient))
        stack.anchor(patient, "BASELINE", date(2026, 10, 3))
        step(stack.evaluate(patient))

    assert_parity(sql_stack, repo_stack, build, script)


def test_the_same_idempotency_key_creates_one_evaluation(sql_stack, repo_stack):
    def build():
        return schedule_of(from_baseline("V1", 7, sequence=1))

    def script(stack, patient, step):
        step(stack.evaluate(patient, key="run-1"))
        step(stack.evaluate(patient, key="run-1"))
        step(stack.evaluate(patient, key="run-2"))

    assert_parity(sql_stack, repo_stack, build, script)


def test_headline_case_visit_three_follows_visit_two_actual_and_baseline_work_stays_put(repo_stack):
    """Visit 2 planned 10 Oct, actually 13 Oct; Visit 3 is 14 days after the
    ACTUAL Visit 2 -> 27 Oct. A baseline-relative imaging visit does not move."""
    def build():
        return schedule_of(
            from_baseline("V2", 9, sequence=1),
            from_baseline("IMAGING", 30, sequence=2),
            from_visit("V3", "V2", 14, sequence=3, actual=True))

    stack = repo_stack
    stack.publish(build())
    patient = stack.enrol("P-HEADLINE", {"BASELINE": BASELINE})
    stack.evaluate(patient)
    before = {row[0]: row for row in stack.snapshot(patient, None).rows}
    assert before["V2"][3] == date(2026, 10, 10) and before["V3"][2] == "BLOCKED"

    stack.fact(patient, key("V2"), OccurrenceFactKind.PERFORMED, date(2026, 10, 13))
    stack.evaluate(patient)
    after = {row[0]: row for row in stack.snapshot(patient, None).rows}
    assert after["V2"][2] == "COMPLETED" and after["V2"][3] == date(2026, 10, 10)  # planned kept
    assert after["V3"][3] == date(2026, 10, 27)
    assert after["IMAGING"][3] == before["IMAGING"][3] == date(2026, 10, 31)
    assert stack.snapshot(patient, None).evaluations == 2
