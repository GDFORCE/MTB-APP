"""Change 9 - site time zones; timed activities in elapsed time (cases K2, K3).

Visits stay dates. Doses and samples are date-times in the site's zone, and
"4 hours after the dose" means four real hours, even across a clock change.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, by_code, context, evaluate, visit

from app.domain.schedule.evaluator import activity_actual_key, add_amount
from app.domain.schedule.models import Activity, ClaimEvidence, PatientContext
from app.domain.schedule.timing import (
    ActivityReference, AnchorReference, OffsetTiming, TemporalAmount, TimeUnit,
)

LONDON = ZoneInfo("Europe/London")
KOLKATA = ZoneInfo("Asia/Kolkata")


def hours(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.HOUR)


def test_k3_spring_forward_four_real_hours_is_05_30_bst():
    dose = datetime(2027, 3, 28, 0, 30, tzinfo=LONDON)
    sample = add_amount(dose, hours(4))
    assert (sample.hour, sample.minute) == (5, 30)
    assert sample.tzname() == "BST"
    assert sample.astimezone(timezone.utc) - dose.astimezone(timezone.utc) == timedelta(hours=4)


def test_fall_back_four_real_hours_is_03_30_gmt():
    """31-Oct-2027: London clocks go back at 02:00 BST, so naive arithmetic
    (04:30) would be five real hours after the dose."""
    dose = datetime(2027, 10, 31, 0, 30, tzinfo=LONDON)
    sample = add_amount(dose, hours(4))
    assert (sample.hour, sample.minute) == (3, 30)
    assert sample.tzname() == "GMT"
    assert sample.astimezone(timezone.utc) - dose.astimezone(timezone.utc) == timedelta(hours=4)


def test_kolkata_has_no_clock_change():
    dose = datetime(2027, 3, 28, 0, 30, tzinfo=KOLKATA)
    sample = add_amount(dose, TemporalAmount(value=90, unit=TimeUnit.MINUTE))
    assert sample == datetime(2027, 3, 28, 2, 0, tzinfo=KOLKATA)


def test_calendar_units_keep_local_wall_clock_across_the_change():
    dose = datetime(2027, 3, 27, 9, 0, tzinfo=LONDON)
    next_day = add_amount(dose, TemporalAmount(value=1, unit=TimeUnit.DAY))
    assert (next_day.hour, next_day.tzname()) == (9, "BST")
    next_month = add_amount(dose, TemporalAmount(value=1, unit=TimeUnit.MONTH))
    assert next_month == datetime(2027, 4, 27, 9, 0, tzinfo=LONDON)


def test_calendar_arithmetic_landing_in_the_gap_moves_to_a_real_time():
    value = add_amount(datetime(2027, 3, 27, 1, 30, tzinfo=LONDON), TemporalAmount(value=1, unit=TimeUnit.DAY))
    assert (value.hour, value.minute, value.tzname()) == (2, 30, "BST")


def test_naive_datetimes_and_dates_keep_previous_arithmetic():
    assert add_amount(datetime(2027, 3, 28, 0, 30), hours(4)) == datetime(2027, 3, 28, 4, 30)
    assert add_amount(date(2027, 3, 28), TemporalAmount(value=2, unit=TimeUnit.DAY)) == date(2027, 3, 30)


def test_unknown_time_zone_is_refused():
    with pytest.raises(ValueError):
        PatientContext(patient_id="00000000-0000-0000-0000-000000000001",
                       schedule_version_id="00000000-0000-0000-0000-000000000002",
                       time_zone="Mars/Olympus")


def _pk_schedule():
    schedule = approved([visit("DOSING", OffsetTiming(
        reference=AnchorReference(code="DAY_1"), offset=TemporalAmount(value=0, unit=TimeUnit.DAY)))],
        anchors=[anchor("DAY_1")])
    evidence_id = schedule.evidence[0].id
    dose = Activity(code="DOSE", protocol_label="Dose", display_name="Dose",
                    activity_type="TREATMENT", evidence_refs=[evidence_id])
    pk = Activity(code="PK", protocol_label="PK 4h", display_name="PK 4h", activity_type="LABORATORY",
                  timing=OffsetTiming(reference=ActivityReference(activity_code="DOSE"), offset=hours(4)),
                  evidence_refs=[evidence_id])
    schedule.events[0].activities = [dose, pk]
    for item, claims in ((dose, ["ACTIVITY"]), (pk, ["ACTIVITY", "ACTIVITY_TIMING"])):
        schedule.claim_evidence.extend(ClaimEvidence(
            evidence_id=evidence_id, claim_type=claim, claim_entity_type="ACTIVITY",
            claim_entity_id=item.id, confidence=0.9) for claim in claims)
    return schedule


def _pk_time(time_zone: str | None, dose_time: datetime) -> datetime:
    schedule = _pk_schedule()
    result = evaluate(schedule, context(
        schedule, anchors={"DAY_1": date(2027, 3, 28)}, time_zone=time_zone,
        activity_actuals={activity_actual_key("DOSING", 0, "DOSE"): dose_time}))
    visit_result = by_code(result)["DOSING"][0]
    assert visit_result.timing.nominal_start == date(2027, 3, 28), "the visit stays a date"
    pk = next(item for item in visit_result.activities if item.activity_code == "PK")
    return pk.timing.nominal_start


def test_k3_end_to_end_naive_dose_time_is_read_in_the_site_zone():
    sample = _pk_time("Europe/London", datetime(2027, 3, 28, 0, 30))
    assert sample == datetime(2027, 3, 28, 5, 30, tzinfo=LONDON)
    assert sample.tzname() == "BST"


def test_k2_same_schedule_in_kolkata():
    sample = _pk_time("Asia/Kolkata", datetime(2027, 3, 28, 0, 30))
    assert sample == datetime(2027, 3, 28, 4, 30, tzinfo=KOLKATA)


def test_without_a_site_zone_the_old_naive_behaviour_is_kept():
    assert _pk_time(None, datetime(2027, 3, 28, 0, 30)) == datetime(2027, 3, 28, 4, 30)


def test_property_elapsed_hours_are_exact_across_every_2027_london_hour():
    start = datetime(2027, 1, 1, tzinfo=timezone.utc)
    for step in range(0, 365 * 24, 7):
        dose = (start + timedelta(hours=step)).astimezone(LONDON)
        for amount in (1, 4, 12, 25):
            first = add_amount(dose, hours(amount))
            assert first.astimezone(timezone.utc) - dose.astimezone(timezone.utc) == timedelta(hours=amount)
            assert first == add_amount(dose, hours(amount))
