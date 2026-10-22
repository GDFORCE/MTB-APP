"""Acceptance repair: a calendar date cannot supply a missing intraday clock.

These are rule-level expectations, not dates obtained from the evaluator.
"""
from datetime import date, datetime, timezone

import pytest

from _engine_cases import anchor, approved, context, evaluate, visit
from app.domain.schedule.calendar_math import add_amount
from app.domain.schedule.models import Activity, ClaimEvidence, RecurrenceRule, RecurrenceTermination
from app.domain.schedule.timing import (
    AnchorReference, NonNegativeTemporalAmount, OffsetTiming, PositiveTemporalAmount,
    TemporalAmount, TimeUnit, Window,
)


def offset(value=36, unit=TimeUnit.HOUR):
    return OffsetTiming(reference=AnchorReference(code="START"),
                        offset=TemporalAmount(value=value, unit=unit))


@pytest.mark.parametrize("value,unit", [(36, TimeUnit.HOUR), (-90, TimeUnit.MINUTE), (0, TimeUnit.HOUR)])
def test_intraday_arithmetic_refuses_a_calendar_date(value, unit):
    with pytest.raises(ValueError, match="time of day"):
        add_amount(date(2027, 1, 1), TemporalAmount(value=value, unit=unit))


def test_missing_clock_waits_without_moving_an_unrelated_calendar_rule():
    schedule = approved([visit("SAMPLE", offset()), visit("IMAGING", offset(14, TimeUnit.DAY))],
                        anchors=[anchor("START")])
    rows = {row.event_code: row for row in evaluate(
        schedule, context(schedule, anchors={"START": date(2027, 1, 1)})).events}
    assert rows["SAMPLE"].timing is None
    assert rows["SAMPLE"].status.value == "WAITING_FOR_ANCHOR"
    assert rows["SAMPLE"].explanation["reason_code"] == "TIME_OF_DAY_REQUIRED"
    assert rows["IMAGING"].timing.nominal_start == date(2027, 1, 15)


def test_supplying_the_clock_resolves_the_same_rule_without_an_assumed_timezone():
    schedule = approved([visit("SAMPLE", offset())], anchors=[anchor("START")])
    row = evaluate(schedule, context(schedule, anchors={
        "START": datetime(2027, 1, 1, 10, 30, tzinfo=timezone.utc)})).events[0]
    assert row.status.value == "RESOLVED"
    assert row.timing.nominal_start == datetime(2027, 1, 2, 22, 30, tzinfo=timezone.utc)


def test_an_hourly_window_cannot_be_projected_around_a_date_only_target():
    schedule = approved([visit("SAMPLE", offset(1, TimeUnit.DAY), window=Window(
        before=NonNegativeTemporalAmount(value=2, unit=TimeUnit.HOUR),
        after=NonNegativeTemporalAmount(value=2, unit=TimeUnit.HOUR)))], anchors=[anchor("START")])
    row = evaluate(schedule, context(schedule, anchors={"START": date(2027, 1, 1)})).events[0]
    assert row.status.value == "WAITING_FOR_ANCHOR" and row.timing is None
    assert row.explanation["reason_code"] == "TIME_OF_DAY_REQUIRED"


def test_hourly_recurrence_does_not_duplicate_the_same_date_when_the_clock_is_missing():
    event = visit("SAMPLE", offset(0, TimeUnit.DAY), recurrence=RecurrenceRule(
        basis="FIRST_RESOLVED_OCCURRENCE", basis_status="STATED",
        start_reference=AnchorReference(code="START"),
        interval=PositiveTemporalAmount(value=6, unit=TimeUnit.HOUR),
        termination=RecurrenceTermination(type="COUNT", count=3)))
    schedule = approved([event], anchors=[anchor("START")])
    rows = evaluate(schedule, context(schedule, anchors={"START": date(2027, 1, 1)})).events
    assert len(rows) == 1 and rows[0].timing is None
    assert rows[0].status.value == "WAITING_FOR_ANCHOR"


def test_missing_activity_clock_does_not_remove_a_recorded_actual_or_the_parent_visit():
    event = visit("V", offset(0, TimeUnit.DAY), activities=[Activity(
        code="SAMPLE", protocol_label="Sample", display_name="Sample", activity_type="SAMPLE",
        timing=offset(2))])
    schedule = approved([event], anchors=[anchor("START")])
    actual = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    activity = event.activities[0]
    activity.evidence_refs = [schedule.evidence[0].id]
    schedule.claim_evidence.extend(ClaimEvidence(
        evidence_id=schedule.evidence[0].id, claim_type=claim, claim_entity_type="ACTIVITY",
        claim_entity_id=activity.id, confidence=0.9) for claim in ("ACTIVITY", "ACTIVITY_TIMING"))
    ctx = context(schedule, anchors={"START": date(2027, 1, 1)})
    waiting = evaluate(schedule, ctx).events[0]
    assert waiting.status.value == "RESOLVED"
    assert waiting.activities[0].status.value == "WAITING_FOR_ANCHOR"
    assert waiting.activities[0].timing is None
    from app.domain.schedule.evaluator import activity_actual_key
    ctx.activity_actuals[activity_actual_key("V", 0, "SAMPLE")] = actual
    completed = evaluate(schedule, ctx).events[0].activities[0]
    assert completed.status.value == "COMPLETED" and completed.actual_time == actual
    assert completed.timing is None
