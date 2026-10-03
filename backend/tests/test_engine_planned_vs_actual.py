"""Change 4 - planned vs actual anchors (cases C6 and D1-D4).

A planned surgery date may place the pre-op visits. It must never date the
post-op visits as if surgery had happened. These tests walk the document's
neoadjuvant -> surgery -> adjuvant pathway with its exact dates.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, context, evaluate, only, visit

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.anchor_status import (
    AnchorRecord, AnchorStatus, record_is_actual, resolve_anchor_status,
)
from app.domain.schedule.models import (
    PatientEventStatus, RecurrenceRule, RecurrenceTermination,
)
from app.domain.schedule.timing import (
    AnchorAcceptance, AnchorReference, OffsetTiming, PositiveTemporalAmount,
    RangeTiming, TemporalAmount, TimeUnit, WithinTiming,
)
from app.domain.schedule.validator import ScheduleValidator
from app.services.schedule_service import PatientScheduleService

LAST_NEOADJ_DOSE = date(2026, 11, 16)
PLANNED_SURGERY = date(2026, 12, 14)
ACTUAL_SURGERY = date(2026, 12, 17)
DELAYED_SURGERY = date(2027, 1, 4)


def weeks(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.WEEK)


def surgery_pathway(*, post_op_accepts: AnchorAcceptance = AnchorAcceptance.ACTUAL, protocol_version_id=None):
    """The table under section D of the case document."""
    surgery_ref = AnchorReference(code="SURGERY")
    events = [
        visit("SURGERY_PROCEDURE", RangeTiming(
            reference=AnchorReference(code="LAST_NEOADJ_DOSE"),
            start=weeks(3), end=weeks(6),
        ), event_type="PROCEDURE"),
        visit("PRE_OP", WithinTiming(
            reference=AnchorReference(
                code="SURGERY", accepts=AnchorAcceptance.PLANNED_OR_ACTUAL),
            duration=PositiveTemporalAmount(value=14, unit=TimeUnit.DAY),
            direction="BEFORE",
        )),
        visit("POST_OP", OffsetTiming(
            reference=AnchorReference(code="SURGERY", accepts=post_op_accepts),
            offset=TemporalAmount(value=30, unit=TimeUnit.DAY),
        )),
        visit("ADJUVANT_START", RangeTiming(
            reference=surgery_ref, start=weeks(4), end=weeks(8),
        ), event_type="TREATMENT"),
    ]
    return approved(events, anchors=[
        anchor("LAST_NEOADJ_DOSE"),
        anchor("SURGERY", source_event_code="SURGERY_PROCEDURE"),
    ], protocol_version_id=protocol_version_id)


def pathway_context(schedule, **changes):
    values = {"anchors": {"LAST_NEOADJ_DOSE": LAST_NEOADJ_DOSE}}
    for key, value in changes.items():
        if key == "anchors":
            values["anchors"] = {**values["anchors"], **value}
        else:
            values[key] = value
    return context(schedule, **values)


def test_d1_surgery_not_yet_scheduled_shows_only_the_surgery_window():
    schedule = surgery_pathway()
    result = evaluate(schedule, pathway_context(schedule))

    surgery = only(result, "SURGERY_PROCEDURE")
    assert surgery.status == PatientEventStatus.RESOLVED
    assert (surgery.timing.earliest, surgery.timing.latest) == (
        date(2026, 12, 7), date(2026, 12, 28))
    # The window's opening is not a planned surgery date: nothing counts from it.
    for code in ("PRE_OP", "POST_OP", "ADJUVANT_START"):
        item = only(result, code)
        assert item.status == PatientEventStatus.WAITING_FOR_ANCHOR, code
        assert item.timing is None


def test_d2_planned_surgery_dates_pre_op_but_not_post_op_or_adjuvant():
    schedule = surgery_pathway()
    result = evaluate(schedule, pathway_context(
        schedule, planned_anchors={"SURGERY": PLANNED_SURGERY}))

    pre_op = only(result, "PRE_OP")
    assert pre_op.status == PatientEventStatus.RESOLVED
    assert (pre_op.timing.earliest, pre_op.timing.latest) == (
        date(2026, 11, 30), date(2026, 12, 14))
    assert pre_op.explanation["anchor_basis"] == {"SURGERY": "PLANNED"}

    for code in ("POST_OP", "ADJUVANT_START"):
        item = only(result, code)
        assert item.status == PatientEventStatus.WAITING_FOR_ANCHOR, code
        assert item.timing is None
        assert item.explanation["waiting_for_actual_anchor"] == ["SURGERY"]
        assert "actual SURGERY date" in item.explanation["reason"]


def test_d3_actual_surgery_dates_post_op_and_adjuvant():
    schedule = surgery_pathway()
    result = evaluate(schedule, pathway_context(
        schedule, anchors={"SURGERY": ACTUAL_SURGERY},
        planned_anchors={"SURGERY": PLANNED_SURGERY}))

    post_op = only(result, "POST_OP")
    assert post_op.status == PatientEventStatus.RESOLVED
    assert post_op.timing.nominal_start == date(2027, 1, 16)
    assert post_op.explanation["anchor_basis"] == {"SURGERY": "ACTUAL"}
    adjuvant = only(result, "ADJUVANT_START")
    assert (adjuvant.timing.earliest, adjuvant.timing.latest) == (
        date(2027, 1, 14), date(2027, 2, 11))
    # Once surgery is real, the actual date wins over the old plan everywhere.
    assert only(result, "PRE_OP").timing.latest == ACTUAL_SURGERY


def test_d4_surgery_delayed_moves_post_op_and_adjuvant():
    schedule = surgery_pathway()
    result = evaluate(schedule, pathway_context(
        schedule, anchors={"SURGERY": DELAYED_SURGERY}))

    assert only(result, "POST_OP").timing.nominal_start == date(2027, 2, 3)
    adjuvant = only(result, "ADJUVANT_START")
    assert (adjuvant.timing.earliest, adjuvant.timing.latest) == (
        date(2027, 2, 1), date(2027, 3, 1))


def test_recorded_surgery_event_is_an_actual_anchor():
    """The source event's recorded date is what actually happened (C2)."""
    schedule = surgery_pathway()
    result = evaluate(schedule, pathway_context(
        schedule, actual_event_values={"SURGERY_PROCEDURE": [ACTUAL_SURGERY]}))

    assert only(result, "POST_OP").timing.nominal_start == date(2027, 1, 16)


def test_a_calculated_dose_date_is_a_plan_not_an_actual_last_dose():
    """C2: 'follow-up 30 days after the last dose' waits for the dose to happen,
    unless the rule explicitly accepts the projected (planned) value."""
    dose = visit("DOSE", OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"),
        offset=TemporalAmount(value=63, unit=TimeUnit.DAY),
    ), event_type="TREATMENT")

    def schedule_with(accepts):
        follow_up = visit("SAFETY_FOLLOW_UP", OffsetTiming(
            reference=AnchorReference(code="LAST_DOSE", accepts=accepts),
            offset=TemporalAmount(value=30, unit=TimeUnit.DAY),
        ))
        return approved([dose.model_copy(deep=True), follow_up], anchors=[
            anchor("FIRST_DOSE"),
            anchor("LAST_DOSE", source_event_code="DOSE", derivation_rule={"selection": "LAST"}),
        ])

    strict = schedule_with(AnchorAcceptance.ACTUAL)
    first_dose = {"FIRST_DOSE": date(2026, 10, 5)}
    waiting = only(evaluate(strict, context(strict, anchors=first_dose)), "SAFETY_FOLLOW_UP")
    assert waiting.status == PatientEventStatus.WAITING_FOR_ANCHOR

    done = evaluate(strict, context(
        strict, anchors=first_dose, actual_event_values={"DOSE": [date(2026, 12, 7)]}))
    assert only(done, "SAFETY_FOLLOW_UP").timing.nominal_start == date(2027, 1, 6)

    projected = schedule_with(AnchorAcceptance.PLANNED_OR_ACTUAL)
    result = evaluate(projected, context(projected, anchors=first_dose))
    follow_up = only(result, "SAFETY_FOLLOW_UP")
    assert follow_up.timing.nominal_start == date(2027, 1, 6)
    assert follow_up.explanation["anchor_basis"] == {"LAST_DOSE": "PLANNED"}


def test_latest_dose_given_is_not_the_last_dose_while_doses_remain():
    """The second of several doses is the latest given, not the LAST dose."""
    dose = visit("DOSE", OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"),
        offset=TemporalAmount(value=0, unit=TimeUnit.DAY),
    ), event_type="TREATMENT", recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="FIRST_DOSE"),
        termination=RecurrenceTermination(type="COUNT", count=4),
    ))
    follow_up = visit("SAFETY_FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="LAST_DOSE"),
        offset=TemporalAmount(value=30, unit=TimeUnit.DAY),
    ))
    schedule = approved([dose, follow_up], anchors=[
        anchor("FIRST_DOSE"),
        anchor("LAST_DOSE", source_event_code="DOSE", derivation_rule={"selection": "LAST"}),
    ])
    given = [date(2026, 10, 5), date(2026, 10, 26)]
    result = evaluate(schedule, context(
        schedule, anchors={"FIRST_DOSE": date(2026, 10, 5)},
        actual_event_values={"DOSE": given}))
    assert only(result, "SAFETY_FOLLOW_UP").status == PatientEventStatus.WAITING_FOR_ANCHOR

    all_given = [*given, date(2026, 11, 16), date(2026, 12, 7)]
    result = evaluate(schedule, context(
        schedule, anchors={"FIRST_DOSE": date(2026, 10, 5)},
        actual_event_values={"DOSE": all_given}))
    assert only(result, "SAFETY_FOLLOW_UP").timing.nominal_start == date(2027, 1, 6)


def test_planned_start_of_a_repeat_is_ignored_unless_accepted():
    follow_up = visit("FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="SURGERY"),
        offset=TemporalAmount(value=0, unit=TimeUnit.DAY),
    ), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=1, unit=TimeUnit.MONTH),
        start_reference=AnchorReference(code="SURGERY"),
        termination=RecurrenceTermination(type="COUNT", count=2),
    ))
    schedule = approved([follow_up], anchors=[anchor("SURGERY")])
    result = evaluate(schedule, context(schedule, planned_anchors={"SURGERY": PLANNED_SURGERY}))
    item = only(result, "FOLLOW_UP")
    assert item.status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert item.explanation["waiting_for_actual_anchor"] == ["SURGERY"]


def test_existing_schedules_default_to_actual_and_round_trip():
    reference = AnchorReference.model_validate({"kind": "ANCHOR", "code": "BASELINE"})
    assert reference.accepts == AnchorAcceptance.ACTUAL
    dumped = AnchorReference(
        code="SURGERY", accepts=AnchorAcceptance.PLANNED_OR_ACTUAL).model_dump(mode="json")
    assert dumped["accepts"] == "PLANNED_OR_ACTUAL"
    assert AnchorReference.model_validate(dumped).accepts == AnchorAcceptance.PLANNED_OR_ACTUAL


def test_validator_flags_a_post_event_visit_that_accepts_a_planned_date():
    unsafe = surgery_pathway(post_op_accepts=AnchorAcceptance.PLANNED_OR_ACTUAL)
    issues = ScheduleValidator().validate(unsafe)
    flagged = [item for item in issues if item.issue_code == "PLANNED_ANCHOR_AFTER_EVENT"]
    assert len(flagged) == 1
    assert flagged[0].details["event_code"] == "POST_OP"
    assert flagged[0].blocking is False

    safe = ScheduleValidator().validate(surgery_pathway())
    assert not [item for item in safe if item.issue_code == "PLANNED_ANCHOR_AFTER_EVENT"]


@pytest.mark.parametrize("status,source_type,expected", [
    ("CONFIRMED", None, True),
    ("CONFIRMED", "OPERATIONAL_ENROLMENT", True),
    ("CONFIRMED", "SOURCE_DOCUMENT", True),
    ("CONFIRMED", "PLANNED_PROCEDURE", False),
    ("CONFIRMED", "planned", False),
    ("PROVISIONAL", "SOURCE_DOCUMENT", True),
    ("PROVISIONAL", None, False),
    ("PROVISIONAL", "SOMETHING_NEW", False),
])
def test_record_classification(status, source_type, expected):
    assert record_is_actual(status, source_type) is expected


def test_a_confirmed_planned_date_still_reads_as_planned():
    view = resolve_anchor_status(
        anchor_code="SURGERY", display_name="Surgery",
        records=[AnchorRecord(
            value=PLANNED_SURGERY, record_status="CONFIRMED",
            source_type="PLANNED_PROCEDURE", recorded_at=datetime.now(timezone.utc),
        )],
    )
    assert view.status == AnchorStatus.PLANNED
    assert view.confirmed is True


# --- the stored path: schedule_service._context (the line-632 gap) -------------

@pytest.fixture()
def stored():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = {name: uuid4() for name in (
            "organization", "trial", "protocol", "protocol_version", "definition")}
        session.add_all([
            db.Trial(id=ids["trial"], organization_id=ids["organization"]),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
            db.ProtocolVersion(
                id=ids["protocol_version"], protocol_id=ids["protocol"],
                version_label="1", document_name="protocol.pdf",
                document_uri="private://protocol.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["protocol_version"],
                name="Primary"),
        ])
        session.flush()
        schedule = surgery_pathway(protocol_version_id=ids["protocol_version"])
        ScheduleRepository(session).persist_draft(
            schedule, schedule_definition_id=ids["definition"])
        patient = db.Patient(
            organization_id=ids["organization"], trial_id=ids["trial"],
            patient_code="P-001", current_schedule_version_id=schedule.schedule_version_id,
        )
        session.add(patient)
        session.flush()
        anchors = {item.code: item for item in schedule.anchors}
        session.add(db.PatientAnchor(
            patient_id=patient.id, anchor_definition_id=anchors["LAST_NEOADJ_DOSE"].id,
            value_date=LAST_NEOADJ_DOSE, status="CONFIRMED",
            source_type="OPERATIONAL_ENROLMENT",
        ))
        session.commit()
        yield session, schedule, patient, anchors
    engine.dispose()


def _add_surgery(session, patient, anchors, value, source_type):
    session.add(db.PatientAnchor(
        patient_id=patient.id, anchor_definition_id=anchors["SURGERY"].id,
        value_date=value, status="CONFIRMED", source_type=source_type,
        recorded_at=datetime.now(timezone.utc),
    ))
    session.commit()


def test_stored_planned_surgery_never_dates_post_op(stored):
    session, schedule, patient, anchors = stored
    _add_surgery(session, patient, anchors, PLANNED_SURGERY, "PLANNED_PROCEDURE")
    service = PatientScheduleService(session)

    context_ = service._context(patient, schedule.schedule_version_id)
    assert context_.planned_anchors == {"SURGERY": PLANNED_SURGERY}
    assert "SURGERY" not in context_.anchors
    assert context_.anchors["LAST_NEOADJ_DOSE"] == LAST_NEOADJ_DOSE

    result = service.evaluate_result(
        patient, schedule.schedule_version_id, horizon=date(2027, 12, 31))
    assert only(result, "PRE_OP").timing.earliest == date(2026, 11, 30)
    assert only(result, "POST_OP").status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert only(result, "ADJUVANT_START").status == PatientEventStatus.WAITING_FOR_ANCHOR


def test_stored_actual_surgery_dates_post_op(stored):
    session, schedule, patient, anchors = stored
    _add_surgery(session, patient, anchors, PLANNED_SURGERY, "PLANNED_PROCEDURE")
    _add_surgery(session, patient, anchors, ACTUAL_SURGERY, "PROCEDURE_RECORD")
    result = PatientScheduleService(session).evaluate_result(
        patient, schedule.schedule_version_id, horizon=date(2027, 12, 31))
    assert only(result, "POST_OP").timing.nominal_start == date(2027, 1, 16)


def test_stored_schedule_round_trips_anchor_acceptance(stored):
    session, schedule, _, _ = stored
    loaded = ScheduleRepository(session).get(schedule.schedule_version_id)
    events = {item.code: item for item in loaded.events}
    assert events["PRE_OP"].timing.reference.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL
    assert events["POST_OP"].timing.reference.accepts == AnchorAcceptance.ACTUAL
