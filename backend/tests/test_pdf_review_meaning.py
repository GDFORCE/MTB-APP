"""The review table must state the same temporal basis/selector as the rule."""
from uuid import uuid4

import pytest

from app.application.pilot import build_pilot_schedule
from app.application.review_projection import build_review_view
from app.domain.schedule.models import Dependency
from app.domain.schedule.timing import EventReference, OffsetTiming, TemporalAmount


@pytest.mark.parametrize("basis,label", [
    ("ORIGINAL_PLANNED", "original planned date"), ("CURRENT_PLANNED", "current planned date"),
    ("ACTUAL_VISIT", "actual visit date"), ("ACTUAL_TREATMENT", "actual treatment date"),
    ("MANUAL_ANCHOR", "manually entered date"),
])
def test_reference_summary_keeps_explicit_date_basis(basis, label):
    schedule = build_pilot_schedule(uuid4(), "Synthetic meaning review")
    event = next(row for row in schedule.events if row.code == "LAB_AFTER_TX")
    event.timing = OffsetTiming(reference=EventReference(event_code="C1D1", temporal_basis=basis),
                               offset=TemporalAmount(value=7, unit="DAY"))
    view = build_review_view(schedule)
    row = next(row for row in view["rows"] if row["code"] == event.code)
    assert f"7 days after {label} of Cycle 1 Day 1" in row["timing_summary"]


def test_dependency_summary_names_its_own_occurrence_and_tracks_are_not_unknown_rules():
    schedule = build_pilot_schedule(uuid4(), "Synthetic selector review")
    event = next(row for row in schedule.events if row.code == "LAB_AFTER_TX")
    event.dependencies = [Dependency(source_event_code="C1D1", dependency_type="TEMPORAL",
                                     source_occurrence="NUMBER", source_occurrence_number=2)]
    row = next(row for row in build_review_view(schedule)["rows"] if row["code"] == event.code)
    summaries = [item["summary"] for item in row["details"]["constraints"]]
    assert any("occurrence 3 of Cycle 1 Day 1" in text for text in summaries)
    assert any("Laboratory track (LABS)" in text for text in summaries)
    assert not any("Additional track" in text for text in summaries)


def test_actual_or_planned_reference_names_its_explicit_fallback():
    schedule = build_pilot_schedule(uuid4(), "Synthetic fallback review")
    event = next(row for row in schedule.events if row.code == "LAB_AFTER_TX")
    event.timing.reference = EventReference(event_code="C1D1", temporal_basis="ACTUAL_OR_PLANNED",
                                           fallback_basis="ORIGINAL_PLANNED")
    row = next(row for row in build_review_view(schedule)["rows"] if row["code"] == event.code)
    assert "actual date of Cycle 1 Day 1, otherwise its original planned date" in row["timing_summary"]
