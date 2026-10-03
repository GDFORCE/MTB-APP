"""PT-01 at enrolment: a date the site marks PLANNED is never treated as actual."""

from pathlib import Path
import sys
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.anchor_status import record_is_actual
from app.services.enrolment_preview import split_planned_anchors


def test_planned_dates_are_split_from_actual_ones():
    dates = {"BASELINE": date(2026, 10, 1), "SURGERY": date(2026, 11, 2)}
    actual, planned = split_planned_anchors(dates, {"SURGERY": "PLANNED", "BASELINE": "ACTUAL"})
    assert actual == {"BASELINE": date(2026, 10, 1)}
    assert planned == {"SURGERY": date(2026, 11, 2)}


def test_no_sources_keeps_the_previous_meaning():
    dates = {"SURGERY": date(2026, 11, 2)}
    assert split_planned_anchors(dates, None) == (dates, {})


def test_the_stored_planned_record_cannot_satisfy_an_actual_only_rule():
    # operational_bridge stores a PLANNED entry as CONFIRMED + source_type PLANNED.
    assert record_is_actual("CONFIRMED", "PLANNED") is False
    assert record_is_actual("CONFIRMED", "OPERATIONAL_ENROLMENT") is True
