"""Behavioural parity of the amendment / impact workflow: SQL service vs port."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.impact import PatientScheduleImpactService as RepoImpact
from app.application.patients import PatientAnchorRecord
from app.db import models as db
from app.domain.schedule.models import (
    ConditionState, OccurrenceFactKind, PatientConditionStatus, ScheduleStatus,
)
from app.services.impact_service import PatientScheduleImpactService as SqlImpact
from test_mongo_parity_patient_plane import (
    BASELINE, HORIZON, RepoStack as BaseRepoStack, SqlStack as BaseSqlStack, from_baseline,
    schedule_of,
)

pytest_plugins = ["tests.contract.conftest"]

ACTOR = UUID("77777777-7777-4777-8777-777777777777")


class SqlStack(BaseSqlStack):
    def __init__(self):
        super().__init__()
        self.impact = SqlImpact(self.session)
        self.numbers = 0
        self.version_ids: list[UUID] = []

    def publish(self, schedule):
        self.numbers += 1
        schedule.schedule_metadata.version_number = self.numbers
        version = super().publish(schedule)
        self.version_ids.append(version)
        return version

    def pin_first(self) -> UUID:
        self.version_id = self.version_ids[0]
        return self.version_id

    def preview(self, patient_id, target, **kwargs):
        proposal = self.impact.preview(
            patient_id, organization_id=self.organization_id,
            target_schedule_version_id=target, horizon=HORIZON, actor_id=ACTOR,
            reason="Protocol amendment", **kwargs)
        self.session.commit()
        return proposal.id

    def confirm(self, proposal_id):
        try:
            _, evaluation, events = self.impact.confirm(
                proposal_id, organization_id=self.organization_id, actor_id=ACTOR)
        except ValueError as error:
            self.session.commit()                 # the HTTP layer commits stale / expired
            return ("error", str(error))
        self.session.commit()
        return ("ok", len(events))

    def cancel(self, proposal_id):
        try:
            self.impact.cancel(
                proposal_id, organization_id=self.organization_id, actor_id=ACTOR, reason="no")
        except ValueError as error:
            self.session.rollback()
            return ("error", str(error))
        self.session.commit()
        return ("ok",)

    def proposal(self, proposal_id):
        row = self.session.get(db.ScheduleImpactProposal, proposal_id)
        return row.status, row.impact["summary"]

    def candidate(self, patient_id, code, value):
        anchor_id = self.session.scalar(select(db.Anchor.id).where(
            db.Anchor.schedule_version_id == self.version_id, db.Anchor.code == code))
        row = db.PatientAnchor(
            patient_id=patient_id, anchor_definition_id=anchor_id, value_date=value,
            status="PENDING_CONFIRMATION", source_type="PATIENT_EVENT_OCCURRENCE")
        self.session.add(row)
        self.session.commit()
        return row

    def pinned_version(self, patient_id) -> int:
        patient = self.session.get(db.Patient, patient_id)
        self.session.refresh(patient)
        return self.version_ids.index(patient.current_schedule_version_id) + 1

    def assignments(self, patient_id):
        return [(a.assignment_type, self.version_ids.index(a.schedule_version_id) + 1)
                for a in self.session.scalars(select(db.PatientScheduleAssignment).where(
                    db.PatientScheduleAssignment.patient_id == patient_id))]

    def audit_actions(self):
        return sorted(a.action for a in self.session.scalars(select(db.AuditEvent)))

    def current(self, patient_id):
        version = self.session.get(db.Patient, patient_id).current_schedule_version_id
        self.version_id = version
        return super().snapshot(patient_id, None).rows

    def poke_state(self, patient_id):
        self.session.add(db.PatientState(
            patient_id=patient_id, state_code="new_fact", state_value=True,
            effective_at=datetime(2026, 9, 30, tzinfo=timezone.utc)))
        self.session.commit()

    def set_expiry(self, proposal_id, minutes_ago: int):
        row = self.session.get(db.ScheduleImpactProposal, proposal_id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        self.session.commit()


class RepoStack(BaseRepoStack):
    def __init__(self, uow):
        super().__init__(uow)
        self.impact = RepoImpact(uow)
        self.numbers = 0
        self.version_ids: list[UUID] = []
        self.anchors_by_version: dict[UUID, dict[str, UUID]] = {}

    def publish(self, schedule):
        """Through the ported review service, so approval audits match SQL's."""
        from app.application.schedule_review import ScheduleReviewService

        self.numbers += 1
        schedule.schedule_metadata.version_number = self.numbers
        schedule.schedule_metadata.protocol_version_id = self.definition.protocol_version_id
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
        self.uow.run(lambda tx: tx.schedules.persist_draft(
            schedule, schedule_definition_id=self.definition.id))
        ScheduleReviewService(self.uow).approve_reviewed_table(
            schedule.schedule_version_id, reviewer_id=self.reviewer, reason="reviewed")
        self.version_id = schedule.schedule_version_id
        self.anchor_ids = {a.code: a.id for a in schedule.anchors}
        self.anchors_by_version[schedule.schedule_version_id] = self.anchor_ids
        self.version_ids.append(schedule.schedule_version_id)
        return schedule.schedule_version_id

    def pin_first(self) -> UUID:
        self.version_id = self.version_ids[0]
        self.anchor_ids = self.anchors_by_version[self.version_id]
        return self.version_id

    def preview(self, patient_id, target, **kwargs):
        return self.impact.preview(
            patient_id, organization_id=self.organization_id,
            target_schedule_version_id=target, horizon=HORIZON, actor_id=ACTOR,
            reason="Protocol amendment", **kwargs).id

    def confirm(self, proposal_id):
        try:
            _, _, events = self.impact.confirm(
                proposal_id, organization_id=self.organization_id, actor_id=ACTOR)
        except ValueError as error:
            return ("error", str(error))
        return ("ok", len(events))

    def cancel(self, proposal_id):
        try:
            self.impact.cancel(
                proposal_id, organization_id=self.organization_id, actor_id=ACTOR, reason="no")
        except ValueError as error:
            return ("error", str(error))
        return ("ok",)

    def proposal(self, proposal_id):
        row = self.uow.read(lambda tx: tx.impacts.get(proposal_id))
        return row.status, row.impact["summary"]

    def candidate(self, patient_id, code, value):
        record = PatientAnchorRecord(
            organization_id=self.organization_id, patient_id=patient_id,
            anchor_definition_id=self.anchor_ids[code], anchor_code=code, value_date=value,
            status="PENDING_CONFIRMATION", source_type="PATIENT_EVENT_OCCURRENCE")
        self.uow.run(lambda tx: tx.facts.add_anchor(record))
        return record

    def pinned_version(self, patient_id) -> int:
        patient = self.uow.read(lambda tx: tx.patients.get(self.organization_id, patient_id))
        return self.version_ids.index(patient.current_schedule_version_id) + 1

    def assignments(self, patient_id):
        rows = self.uow.read(lambda tx: tx.impacts.list_assignments(patient_id))
        return [(a.assignment_type, self.version_ids.index(a.schedule_version_id) + 1) for a in rows]

    def audit_actions(self):
        return sorted(a.action for a in self.uow.read(
            lambda tx: tx.audit.list_for_organization(self.organization_id, limit=100000)))

    def current(self, patient_id):
        patient = self.uow.read(lambda tx: tx.patients.get(self.organization_id, patient_id))
        self.version_id = patient.current_schedule_version_id
        return super().snapshot(patient_id, None).rows

    def poke_state(self, patient_id):
        from app.application.patients import PatientStateRecord

        record = PatientStateRecord(
            organization_id=self.organization_id, patient_id=patient_id, state_code="new_fact",
            state_value=True, effective_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.uow.run(lambda tx: tx.facts.append_state(record))

    def set_expiry(self, proposal_id, minutes_ago: int):
        def work(tx):
            row = tx.impacts.get(proposal_id)
            tx.impacts.save(row.model_copy(update={
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)}))

        self.uow.run(work)


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


def two_versions(stack):
    """V1 with one visit; V2 (an amendment) moves it and adds one."""
    stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
    stack.publish(schedule_of(from_baseline("VISIT_1", 10, sequence=1), from_baseline("VISIT_2", 17, sequence=2)))
    stack.pin_first()
    patient = stack.enrol("P-001", {"BASELINE": BASELINE})
    stack.evaluate(patient)
    return patient


def both(sql_stack, repo_stack, scenario):
    expected = scenario(sql_stack)
    actual = scenario(repo_stack)
    assert actual == expected
    return actual


def test_confirmed_amendment_assigns_the_version_and_keeps_completed_history(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        stack.fact(patient, "VISIT_1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 9))
        proposal = stack.preview(patient, stack.version_ids[1])
        status, summary = stack.proposal(proposal)
        outcome = stack.confirm(proposal)
        return (summary, status, outcome, stack.proposal(proposal)[0], stack.pinned_version(patient),
                stack.current(patient), stack.assignments(patient), stack.audit_actions())

    result = both(sql_stack, repo_stack, scenario)
    assert result[0]["PROTECTED"] == 1 and result[0]["ADDED"] == 1
    assert result[4] == 2 and result[3] == "CONFIRMED"
    visit_1 = next(row for row in result[5] if row[0] == "VISIT_1")
    assert visit_1[2] == "COMPLETED"                          # history kept across versions


def test_a_changed_input_between_preview_and_confirm_makes_the_proposal_stale(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        proposal = stack.preview(patient, stack.version_ids[1])
        stack.poke_state(patient)
        outcome = stack.confirm(proposal)
        return outcome, stack.proposal(proposal)[0], stack.pinned_version(patient)

    outcome, status, pinned = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "error" and "new impact preview" in outcome[1]
    assert status == "STALE" and pinned == 1


def test_a_fact_recorded_between_preview_and_confirm_also_invalidates_it(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        proposal = stack.preview(patient, stack.version_ids[1])
        stack.fact(patient, "VISIT_1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 9))
        outcome = stack.confirm(proposal)
        return outcome[0], stack.proposal(proposal)[0], stack.pinned_version(patient)

    assert both(sql_stack, repo_stack, scenario) == ("error", "STALE", 1)


def test_an_expired_proposal_cannot_be_confirmed(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        proposal = stack.preview(patient, stack.version_ids[1])
        stack.set_expiry(proposal, 5)
        outcome = stack.confirm(proposal)
        return outcome, stack.proposal(proposal)[0], stack.pinned_version(patient)

    outcome, status, pinned = both(sql_stack, repo_stack, scenario)
    assert outcome == ("error", "impact proposal has expired") and status == "EXPIRED" and pinned == 1


def test_a_cancelled_proposal_cannot_be_confirmed_or_cancelled_again(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        proposal = stack.preview(patient, stack.version_ids[1])
        first = stack.cancel(proposal)
        again = stack.cancel(proposal)
        confirm = stack.confirm(proposal)
        return first, again, confirm, stack.proposal(proposal)[0], stack.pinned_version(patient)

    first, again, confirm, status, pinned = both(sql_stack, repo_stack, scenario)
    assert first == ("ok",) and again[0] == "error" and confirm[0] == "error"
    assert status == "CANCELLED" and pinned == 1


def test_a_target_outside_the_patients_trial_or_not_approved_is_refused(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        try:
            stack.preview(patient, uuid4())
        except ValueError as error:
            return str(error)
        return "accepted"

    assert both(sql_stack, repo_stack, scenario) \
        == "target must be an approved schedule version for the patient's trial"


def test_a_confirmed_anchor_candidate_moves_the_dates_and_is_audited_as_a_change(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        candidate = stack.candidate(patient, "BASELINE", date(2026, 10, 3))
        before = stack.current(patient)
        proposal = stack.preview(patient, stack.version_ids[0], anchor_candidate=candidate)
        summary = stack.proposal(proposal)[1]
        outcome = stack.confirm(proposal)
        return before, summary, outcome, stack.current(patient), stack.pinned_version(patient), \
            stack.assignments(patient), stack.audit_actions()

    result = both(sql_stack, repo_stack, scenario)
    assert result[1]["MOVED"] == 1
    assert "PATIENT_ANCHOR_CHANGED" in result[6]
    assert result[5][-1][0] == "INPUT_CHANGE"


def test_a_condition_change_is_applied_through_a_confirmed_proposal(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        change = PatientConditionStatus(
            condition_code="PREGNANT", state=ConditionState.ACTIVE, occurrence_date=date(2026, 10, 8))
        proposal = stack.preview(patient, stack.version_ids[0], condition_change=change)
        outcome = stack.confirm(proposal)
        return outcome, stack.proposal(proposal), stack.audit_actions()

    outcome, proposal, audit = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "ok" and proposal[0] == "CONFIRMED"
    assert "PATIENT_CONDITION_STATE_CHANGED" in audit
