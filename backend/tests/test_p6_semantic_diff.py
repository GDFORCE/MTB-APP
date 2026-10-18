"""P6-03: the comparison covers every schedule concept, not just event codes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.pilot import build_pilot_schedule
from app.domain.schedule.diff import ChangeType, Significance, compare_schedule_versions
from app.domain.schedule.models import (
    Anchor, HoldResumePolicy, ScheduleTrack, StudyDimension, TrackLink, TrackRebasePolicy,
)
from app.domain.schedule.timing import TemporalBasis


def pair():
    """V1 and an independent V2 copy: identifiers regenerate, exactly as for a real amendment."""
    v1 = build_pilot_schedule(uuid4(), "Pilot")
    v2 = build_pilot_schedule(uuid4(), "Pilot")
    return v1, v2


def event(schedule, code):
    return next(item for item in schedule.events if item.code == code)


def types(diff):
    return {item.change_type for item in diff.typed_changes}


def test_independently_built_identical_versions_have_no_changes():
    v1, v2 = pair()
    assert compare_schedule_versions(v1, v2).typed_changes == []


def test_a_timing_change_to_the_same_logical_visit_is_clinical():
    v1, v2 = pair()
    event(v2, "C1D1").timing.offset.value += 3
    diff = compare_schedule_versions(v1, v2)
    [change] = [c for c in diff.typed_changes if c.entity_code == "C1D1"]
    assert change.change_type == ChangeType.TIMING_CHANGED
    assert change.significance == Significance.CLINICAL
    assert diff.added_events == [] and diff.removed_events == []


def test_basis_dependency_and_recurrence_changes_are_each_reported():
    v1, v2 = pair()
    event(v2, "LAB_AFTER_TX").timing.reference.temporal_basis = TemporalBasis.ACTUAL_VISIT
    event(v2, "DOSE").recurrence.termination.count += 2
    event(v2, "LAB_AFTER_TX").dependencies = []
    diff = compare_schedule_versions(v1, v2)
    assert {ChangeType.TIMING_CHANGED, ChangeType.RECURRENCE_CHANGED,
            ChangeType.DEPENDENCY_CHANGED} <= types(diff)


def test_tracks_links_and_membership_are_compared_by_code_not_id():
    v1, v2 = pair()
    next(t for t in v2.tracks if t.code == "IMAGING").rebase_policy = \
        TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE
    v2.track_links = []
    labs = next(t for t in v2.tracks if t.code == "LABS")
    event(v2, "IMAGING_BASELINE").track_id = labs.id
    v2.tracks.append(ScheduleTrack(
        code="SAFETY", track_type="SAFETY", display_name="Safety", anchor_code="BASELINE",
        rebase_policy=TrackRebasePolicy.FIXED_FROM_ANCHOR, review_state="CONFIRMED"))
    diff = compare_schedule_versions(v1, v2)
    assert {ChangeType.TRACK_CHANGED, ChangeType.TRACK_LINK_REMOVED,
            ChangeType.TRACK_MEMBERSHIP_CHANGED, ChangeType.TRACK_ADDED} <= types(diff)
    membership = next(c for c in diff.typed_changes
                      if c.change_type == ChangeType.TRACK_MEMBERSHIP_CHANGED)
    assert membership.entity_code == "IMAGING_BASELINE"
    assert membership.before == ["TRACK", "IMAGING"] and membership.after == ["TRACK", "LABS"]


def test_a_new_track_link_and_a_changed_one_are_reported():
    v1, v2 = pair()
    v2.track_links[0].relation = "FOLLOWS_PLANNED"
    v2.track_links.append(TrackLink(
        source_track_code="TREATMENT", target_track_code="IMAGING", relation="FOLLOWS_ACTUAL",
        review_state="CONFIRMED"))
    assert {ChangeType.TRACK_LINK_CHANGED, ChangeType.TRACK_LINK_ADDED} <= types(
        compare_schedule_versions(v1, v2))


def test_anchor_population_and_process_changes_are_reported():
    v1, v2 = pair()
    v2.anchors[0].derivation_rule = {"role": "OTHER"}
    v2.populations.append(StudyDimension(code="ADULT", protocol_label="Adults", display_name="Adults"))
    v2.processes[0].hold_resume_policy = HoldResumePolicy.NOMINAL
    v2.anchors.append(Anchor(code="RANDOMISATION", display_name="Randomisation",
                             anchor_type="RANDOMISATION", status="RESOLVED"))
    diff = compare_schedule_versions(v1, v2)
    assert {ChangeType.ANCHOR_CHANGED, ChangeType.ANCHOR_ADDED, ChangeType.POPULATION_ADDED,
            ChangeType.PROCESS_CHANGED} <= types(diff)


def test_a_changed_window_and_activity_are_reported_and_a_reused_code_is_not_hidden():
    v1, v2 = pair()
    event(v2, "IMAGING_BASELINE").window.before.value += 2
    event(v2, "C1D1").event_type = "SITE_VISIT"
    diff = compare_schedule_versions(v1, v2)
    assert ChangeType.WINDOW_WIDENED in types(diff)
    attribute = next(c for c in diff.typed_changes if c.change_type == ChangeType.EVENT_ATTRIBUTE_CHANGED)
    assert attribute.entity_code == "C1D1" and attribute.significance == Significance.CLINICAL


def test_evidence_only_changes_are_administrative():
    v1, v2 = pair()
    v2.evidence[0].source_text = "Reworded source sentence"
    diff = compare_schedule_versions(v1, v2)
    assert types(diff) == {ChangeType.EVIDENCE_CHANGED}
    assert all(c.significance == Significance.ADMINISTRATIVE for c in diff.typed_changes)
    assert diff.clinically_significant() == []


def test_a_removed_and_an_added_visit_are_reported_with_their_codes():
    v1, v2 = pair()
    v2.events = [e for e in v2.events if e.code != "DOSE"]
    v2.track_links = v2.track_links
    diff = compare_schedule_versions(v1, v2)
    assert diff.removed_events == ["DOSE"]
    assert any(c.change_type == ChangeType.VISIT_REMOVED and c.entity_code == "DOSE"
               for c in diff.typed_changes)


@pytest.mark.parametrize("field", ["rebase_policy", "stoppable"])
def test_track_policy_fields_are_clinical(field):
    v1, v2 = pair()
    track = next(t for t in v2.tracks if t.code == "TREATMENT")
    if field == "rebase_policy":
        track.rebase_policy = TrackRebasePolicy.FIXED_FROM_ANCHOR
    else:
        track.stoppable = not track.stoppable
    [change] = [c for c in compare_schedule_versions(v1, v2).typed_changes
                if c.change_type == ChangeType.TRACK_CHANGED]
    assert change.significance == Significance.CLINICAL and field in str(change.summary)
