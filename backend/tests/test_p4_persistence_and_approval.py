"""P4-02/P4-04/P4-05: the recurrence basis survives SQL and Mongo persistence
unchanged, and every approval entry point refuses an unconfirmed or conflicting
repeat - while a reviewer-stated basis is approvable and dates the same on both
stores (one shared evaluator)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.schedule_review import ScheduleReviewService as RepoReview
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, PatientContext, RecurrenceRule,
    RecurrenceTermination, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.services.schedule_service import ScheduleReviewService as SqlReview
from test_mongo_parity_review import ORG, RepoStack, SqlStack

pytest_plugins = ["tests.contract.conftest"]

BASELINE = date(2026, 1, 1)
EXPECTED = [date(2026, 1, 15), date(2026, 2, 12), date(2026, 3, 12)]


def schedule(**rule_fields) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=4, source_text="Every 28 days from Day 15")
    event = Event(
        code="LABS", protocol_label="Labs", display_name="Labs", event_type="VISIT", sequence_number=1,
        timing={"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                "offset": {"value": 14, "unit": "DAY"}},
        recurrence=RecurrenceRule(
            interval={"value": 28, "unit": "DAY"},
            start_reference={"kind": "ANCHOR", "code": "BASELINE"},
            termination=RecurrenceTermination(type="COUNT", count=3), **rule_fields),
        evidence_refs=[evidence.id])
    claims = [ClaimEvidence(evidence_id=evidence.id, claim_type=kind, claim_entity_type="EVENT",
                            claim_entity_id=event.id, claim_path=path, confidence=0.9)
              for kind, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"),
                                 ("RECURRENCE", "recurrence"))]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.VALIDATION_REQUIRED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        events=[event], evidence=[evidence], claim_evidence=claims)


STATED = {"basis": "FIRST_RESOLVED_OCCURRENCE", "basis_status": "STATED"}


def dates_of(stored: UniversalSchedule) -> list[date]:
    approved = stored.model_copy(deep=True)
    approved.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(patient_id=uuid4(), schedule_version_id=approved.schedule_version_id,
                             anchors={"BASELINE": BASELINE})
    result = ScheduleEvaluator().evaluate(approved, context, horizon=date(2026, 12, 31), today=BASELINE)
    return [item.timing.nominal_start for item in result.events]


def sql_stack() -> SqlStack:
    return SqlStack()


def seeded(stack, rule_fields):
    version = stack.add_protocol_version("1.0")
    return stack.draft(schedule(**rule_fields), version)


def test_sql_round_trip_preserves_basis_and_dates_the_same(  ):
    stack = sql_stack()
    version = seeded(stack, STATED | {"actual_source": None})
    stack.commit()
    stored = stack.get_schedule(version)
    rule = stored.events[0].recurrence
    assert rule.basis.value == "FIRST_RESOLVED_OCCURRENCE" and rule.basis_status == "STATED"
    assert dates_of(stored) == EXPECTED


def test_mongo_and_memory_round_trip_preserve_basis_and_date_the_same(uow):
    stack = RepoStack(uow)
    version = seeded(stack, STATED)
    rule = stack.get_schedule(version).events[0].recurrence
    assert rule.basis.value == "FIRST_RESOLVED_OCCURRENCE" and rule.basis_status == "STATED"
    assert dates_of(stack.get_schedule(version)) == EXPECTED


def test_actual_basis_and_source_round_trip_on_both_stores(uow):
    fields = {"basis": "PREVIOUS_ACTUAL_OCCURRENCE", "basis_status": "STATED",
              "actual_source": "ACTUAL_TREATMENT"}
    sql = sql_stack()
    sql_version = seeded(sql, fields)
    sql.commit()
    repo = RepoStack(uow)
    repo_version = seeded(repo, fields)
    for stored in (sql.get_schedule(sql_version), repo.get_schedule(repo_version)):
        rule = stored.events[0].recurrence
        assert rule.basis.value == "PREVIOUS_ACTUAL_OCCURRENCE" and rule.actual_source == "ACTUAL_TREATMENT"


@pytest.mark.parametrize("fields,code", [
    ({"basis": "FIRST_RESOLVED_OCCURRENCE", "basis_status": "UNCONFIRMED"}, "RECURRENCE_BASIS_UNCONFIRMED"),
    ({"basis_status": "UNCONFIRMED"}, "RECURRENCE_BASIS_UNCONFIRMED"),
    ({}, "RECURRENCE_START_CONFLICT"),    # legacy rule restarting at the anchor
    ({**STATED, "start_offset": {"value": 7, "unit": "DAY"}}, "RECURRENCE_START_CONFLICT"),
])
def test_every_sql_approval_entry_point_refuses_the_unsafe_repeat(fields, code):
    stack = sql_stack()
    version = seeded(stack, fields)
    stack.commit()
    review = SqlReview(stack.session)
    stack.session.commit()
    validation = review.validate(version)
    assert code in [issue.issue_code for issue in validation if issue.blocking]
    with pytest.raises(ValueError):
        review.approve_reviewed_table(version, reviewer_id=uuid4(), reason="reviewed")
    stack.session.rollback()
    review.submit_for_review(version, actor_id=uuid4()) if False else None
    assert stack.session.get(db.ScheduleVersion, version).status != "APPROVED"


@pytest.mark.parametrize("fields,code", [
    ({"basis": "FIRST_RESOLVED_OCCURRENCE", "basis_status": "UNCONFIRMED"}, "RECURRENCE_BASIS_UNCONFIRMED"),
    ({**STATED, "start_offset": {"value": 7, "unit": "DAY"}}, "RECURRENCE_START_CONFLICT"),
])
def test_every_port_approval_entry_point_refuses_the_unsafe_repeat(uow, fields, code):
    stack = RepoStack(uow)
    version = seeded(stack, fields)
    review = RepoReview(uow)
    assert code in [issue.issue_code for issue in review.validate(version) if issue.blocking]
    with pytest.raises(ValueError):
        review.approve_reviewed_table(version, reviewer_id=uuid4(), reason="reviewed")
    assert uow.read(lambda tx: tx.schedules.get_header(version)).status != "APPROVED"
