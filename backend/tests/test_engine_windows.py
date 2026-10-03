"""Change 3 - windows as their own rule (cases B2, B3, B4).

A window is not a kind of timing. It sits around whatever target date the
timing produced - including every occurrence of a repeat - and may be one-sided.
A window the protocol does not state stays empty.
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, by_code, context, evaluate, only, visit

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.deviation import DeviationType, assess_visit
from app.domain.schedule.diff import ChangeType, Significance, compare_schedule_versions
from app.domain.schedule.models import (
    PatientEventStatus, RecurrenceRule, RecurrenceTermination, ScheduleStatus,
)
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount, OffsetTiming,
    PositiveTemporalAmount, ProtocolDayTiming, RangeTiming, TemporalAmount, TimeUnit,
    Window,
)
from app.domain.schedule.validator import ScheduleValidator

FIRST_DOSE = date(2026, 10, 5)
END_OF_THERAPY = date(2026, 12, 7)


def days(before: int, after: int) -> Window:
    return Window(
        before=NonNegativeTemporalAmount(value=before, unit=TimeUnit.DAY),
        after=NonNegativeTemporalAmount(value=after, unit=TimeUnit.DAY),
    )


def day_15(**changes):
    return visit("DAY_15", ProtocolDayTiming(
        reference=AnchorReference(code="FIRST_DOSE"), day=15), **changes)


def quarterly(**changes):
    return visit("QUARTERLY_FU", OffsetTiming(
        reference=AnchorReference(code="END_OF_THERAPY"),
        offset=TemporalAmount(value=3, unit=TimeUnit.MONTH),
    ), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=3, unit=TimeUnit.MONTH),
        start_reference=AnchorReference(code="END_OF_THERAPY"),
        start_offset=TemporalAmount(value=3, unit=TimeUnit.MONTH),
        termination=RecurrenceTermination(type="COUNT", count=3),
    ), **changes)


def test_b3_window_only_after_minus_0_plus_3():
    schedule = approved([day_15(window=days(0, 3))], anchors=[anchor("FIRST_DOSE")])
    item = only(evaluate(schedule, context(schedule, anchors={"FIRST_DOSE": FIRST_DOSE})), "DAY_15")

    assert item.timing.nominal_start == date(2026, 10, 19)
    assert (item.timing.earliest, item.timing.latest) == (date(2026, 10, 19), date(2026, 10, 22))


def test_b3_a_visit_after_a_one_sided_window_is_late_by_the_window_edge():
    schedule = approved([day_15(window=days(0, 3))], anchors=[anchor("FIRST_DOSE")])
    item = only(evaluate(schedule, context(schedule, anchors={"FIRST_DOSE": FIRST_DOSE})), "DAY_15")
    deviation = assess_visit(
        logical_key="DAY_15:0", event_code="DAY_15", display_name="Day 15",
        status=item.status.value, planned_date=item.timing.nominal_start,
        earliest_date=item.timing.earliest, latest_date=item.timing.latest,
        actual_date=date(2026, 10, 23), today=date(2026, 10, 30),
    )
    assert deviation.deviation_type == DeviationType.LATE
    assert deviation.outside_window_days == 1
    early = assess_visit(
        logical_key="DAY_15:0", event_code="DAY_15", display_name="Day 15",
        status=item.status.value, planned_date=item.timing.nominal_start,
        earliest_date=item.timing.earliest, latest_date=item.timing.latest,
        actual_date=date(2026, 10, 18), today=date(2026, 10, 30),
    )
    assert early.deviation_type == DeviationType.EARLY


def test_b2_window_is_reapplied_to_every_quarterly_occurrence():
    schedule = approved([quarterly(window=days(14, 14))], anchors=[anchor("END_OF_THERAPY")])
    result = evaluate(schedule, context(schedule, anchors={"END_OF_THERAPY": END_OF_THERAPY}))
    occurrences = [
        (item.timing.nominal_start, item.timing.earliest, item.timing.latest)
        for item in by_code(result)["QUARTERLY_FU"]
    ]
    assert occurrences == [
        (date(2027, 3, 7), date(2027, 2, 21), date(2027, 3, 21)),
        (date(2027, 6, 7), date(2027, 5, 24), date(2027, 6, 21)),
        (date(2027, 9, 7), date(2027, 8, 24), date(2027, 9, 21)),
    ]


def test_b4_no_window_stated_stays_empty():
    schedule = approved([day_15(), quarterly()], anchors=[
        anchor("FIRST_DOSE"), anchor("END_OF_THERAPY")])
    result = evaluate(schedule, context(schedule, anchors={
        "FIRST_DOSE": FIRST_DOSE, "END_OF_THERAPY": END_OF_THERAPY}))
    for item in result.events:
        assert item.status == PatientEventStatus.RESOLVED
        assert (item.timing.earliest, item.timing.latest) == (None, None)


def test_a6_nominal_with_window_timing_still_works_on_single_and_repeating_visits():
    nominal = OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"),
        offset=TemporalAmount(value=4, unit=TimeUnit.WEEK))
    single = visit("VISIT_3", NominalWindowTiming(nominal=nominal, window=days(3, 3)))
    schedule = approved([single], anchors=[anchor("FIRST_DOSE")])
    item = only(evaluate(schedule, context(schedule, anchors={"FIRST_DOSE": FIRST_DOSE})), "VISIT_3")
    assert (item.timing.nominal_start, item.timing.earliest, item.timing.latest) == (
        date(2026, 11, 2), date(2026, 10, 30), date(2026, 11, 5))

    # Stating the same window on the event as well is not a conflict.
    both = visit("VISIT_3", NominalWindowTiming(nominal=nominal, window=days(3, 3)),
                 window=days(3, 3))
    schedule = approved([both], anchors=[anchor("FIRST_DOSE")])
    assert not ScheduleValidator.blocking(ScheduleValidator().validate(schedule))


def test_validator_blocks_a_window_on_a_timing_with_no_single_target():
    ranged = visit("PET", RangeTiming(
        reference=AnchorReference(code="END_OF_THERAPY"),
        start=TemporalAmount(value=12, unit=TimeUnit.WEEK),
        end=TemporalAmount(value=16, unit=TimeUnit.WEEK),
    ), window=days(7, 7))
    schedule = approved([ranged], anchors=[anchor("END_OF_THERAPY")], status=ScheduleStatus.DRAFT)
    issues = [item for item in ScheduleValidator().validate(schedule)
              if item.issue_code == "AMBIGUOUS_WINDOW"]
    assert len(issues) == 1 and issues[0].blocking


def test_validator_blocks_two_different_windows_on_one_visit():
    nominal = ProtocolDayTiming(reference=AnchorReference(code="FIRST_DOSE"), day=15)
    event = visit("DAY_15", NominalWindowTiming(nominal=nominal, window=days(3, 3)),
                  window=days(0, 3))
    schedule = approved([event], anchors=[anchor("FIRST_DOSE")], status=ScheduleStatus.DRAFT)
    conflicts = [item for item in ScheduleValidator().validate(schedule)
                 if item.issue_code == "CONFLICTING_SOURCE"]
    assert conflicts and conflicts[0].blocking


def test_validator_requires_evidence_for_a_window():
    schedule = approved([day_15(window=days(0, 3))], anchors=[anchor("FIRST_DOSE")])
    schedule.claim_evidence = [
        item for item in schedule.claim_evidence if item.claim_type != "WINDOW"]
    missing = [item for item in ScheduleValidator().validate(schedule)
               if item.issue_code == "MISSING_EVIDENCE"
               and item.details.get("claim_type") == "WINDOW"]
    assert missing and missing[0].blocking


def test_a_window_change_is_classified_for_amendment_review():
    before = approved([day_15(window=days(0, 3))], anchors=[anchor("FIRST_DOSE")])
    after = before.model_copy(deep=True)
    after.events[0].window = days(1, 5)
    widened = [item for item in compare_schedule_versions(before, after).typed_changes
               if item.change_type == ChangeType.WINDOW_WIDENED]
    assert widened and widened[0].significance == Significance.ADMINISTRATIVE

    removed = before.model_copy(deep=True)
    removed.events[0].window = None
    changed = compare_schedule_versions(before, removed).typed_changes
    assert [item.change_type for item in changed] == [ChangeType.TIMING_CHANGED]


def test_event_window_survives_persist_and_load():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            ids = {name: uuid4() for name in ("trial", "protocol", "protocol_version", "definition")}
            session.add_all([
                db.Trial(id=ids["trial"], organization_id=uuid4()),
                db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
                db.ProtocolVersion(
                    id=ids["protocol_version"], protocol_id=ids["protocol"],
                    version_label="1", document_name="protocol.pdf",
                    document_uri="private://protocol.pdf", document_hash="a" * 64),
                db.ScheduleDefinition(
                    id=ids["definition"], protocol_version_id=ids["protocol_version"],
                    name="Primary"),
            ])
            session.flush()
            schedule = approved(
                [day_15(window=days(0, 3)), quarterly()],
                anchors=[anchor("FIRST_DOSE"), anchor("END_OF_THERAPY")],
                protocol_version_id=ids["protocol_version"])
            repository = ScheduleRepository(session)
            repository.persist_draft(schedule, schedule_definition_id=ids["definition"])
            session.commit()

            loaded = {item.code: item for item in repository.get(schedule.schedule_version_id).events}
            assert loaded["DAY_15"].window == days(0, 3)
            assert loaded["QUARTERLY_FU"].window is None

            edited = loaded["QUARTERLY_FU"].model_copy(update={"window": days(14, 14)})
            repository.replace_event(schedule.schedule_version_id, edited)
            session.commit()
            reloaded = {item.code: item for item in repository.get(schedule.schedule_version_id).events}
            assert reloaded["QUARTERLY_FU"].window == days(14, 14)
    finally:
        engine.dispose()
