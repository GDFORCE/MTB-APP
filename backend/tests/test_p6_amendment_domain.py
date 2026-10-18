"""P6-01/02/04 domain rules: content identity, event mapping, adoption points."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from _engine_cases import anchor, approved, visit

from app.domain.schedule.amendment import (
    AdoptionMode, AdoptionPoint, EventMapping, MappingReviewStatus, MappingType, Translation,
    mapping_coverage_problems, mapping_revision, propose_event_mappings, schedule_content_hash,
    translate_occurrence,
)
from app.domain.schedule.models import ScheduleStatus
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming


def build(days: dict[str, int], *, names: dict[str, str] | None = None, number: int = 1):
    events = []
    for code, day in days.items():
        values = {}
        if names and code in names:
            values["display_name"] = names[code]
        events.append(visit(
            code, ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=day),
            **values))
    schedule = approved(events, anchors=[anchor("REGISTRATION")])
    schedule.schedule_metadata.version_number = number
    return schedule


def confirm(item, **extra):
    """A validated copy (``model_copy(update=)`` would skip validation)."""
    data = item.model_dump()
    data.update({"review_status": MappingReviewStatus.CONFIRMED, "reviewed_by": uuid4(), **extra})
    return EventMapping.model_validate(data)


def by_kind(mappings, kind):
    return [item for item in mappings if item.mapping_type == kind]


# ------------------------------------------------------------------- content hash
def test_content_hash_is_stable_and_ignores_lifecycle_fields():
    schedule = build({"V1": 1, "V2": 15})
    first = schedule_content_hash(schedule)
    assert first == schedule_content_hash(schedule.model_copy(deep=True))
    changed = schedule.model_copy(deep=True)
    changed.schedule_metadata.status = ScheduleStatus.SUPERSEDED
    changed.schedule_metadata.effective_from = date(2026, 1, 1)
    assert schedule_content_hash(changed) == first


def test_content_hash_changes_when_anything_date_affecting_changes():
    base = build({"V1": 1, "V2": 15})
    baseline = schedule_content_hash(base)
    moved = build({"V1": 1, "V2": 16})
    moved.schedule_version_id = base.schedule_version_id
    moved.events = [event.model_copy(update={"id": base.events[i].id})
                    for i, event in enumerate(moved.events)]
    moved.evidence = base.evidence
    moved.claim_evidence = base.claim_evidence
    assert schedule_content_hash(moved) != baseline
    # evidence text is part of the content too
    edited = base.model_copy(deep=True)
    edited.evidence[0].source_text = "a different rule"
    assert schedule_content_hash(edited) != baseline


# -------------------------------------------------------------------- mapping
def test_identical_code_name_and_type_is_trusted_without_review():
    mappings = propose_event_mappings(build({"V1": 1, "V2": 15}), build({"V1": 1, "V2": 18}))
    assert {item.mapping_type for item in mappings} == {MappingType.EQUIVALENT}
    assert all(item.review_status == MappingReviewStatus.AUTO_CONFIRMED for item in mappings)


def test_a_reused_code_with_a_different_name_needs_review_and_carries_nothing():
    v1 = build({"V4": 57}, names={"V4": "Week 8 Follow-up"})
    v2 = build({"V4": 99}, names={"V4": "Safety Call"})
    [mapping] = propose_event_mappings(v1, v2)
    assert mapping.mapping_type == MappingType.EQUIVALENT
    assert mapping.review_status == MappingReviewStatus.PROPOSED
    assert mapping.provenance == "AUTO_CODE_ONLY" and mapping.confidence < 0.5
    kind, target, reason = translate_occurrence([mapping], "V4", 0)
    assert kind == Translation.UNRESOLVED and target is None and "not been reviewed" in reason


def test_rename_by_name_is_a_proposal_not_a_fact():
    v1 = build({"WEEK_4": 29}, names={"WEEK_4": "Week 4 Visit"})
    v2 = build({"VISIT_04": 29}, names={"VISIT_04": "Week 4 Visit"})
    mappings = propose_event_mappings(v1, v2)
    [rename] = by_kind(mappings, MappingType.RENAMED)
    assert (rename.source_codes, rename.target_codes) == (["WEEK_4"], ["VISIT_04"])
    assert rename.review_status == MappingReviewStatus.PROPOSED
    assert translate_occurrence(mappings, "WEEK_4", 0)[0] == Translation.UNRESOLVED
    confirmed = confirm(rename)
    assert translate_occurrence([confirmed], "WEEK_4", 3) == (
        Translation.TARGET, ("VISIT_04", 3), None)


def test_split_and_merge_are_proposed_and_need_explicit_occurrence_rules():
    v1 = build({"WEEK_4": 29}, names={"WEEK_4": "Week 4"})
    v2 = build({"WEEK_4A": 29, "WEEK_4B": 31},
               names={"WEEK_4A": "Week 4 clinic", "WEEK_4B": "Week 4 lab"})
    mappings = propose_event_mappings(v1, v2)
    [split] = by_kind(mappings, MappingType.SPLIT)
    assert split.source_codes == ["WEEK_4"] and split.target_codes == ["WEEK_4A", "WEEK_4B"]
    confirmed = confirm(split, occurrence_rules=[{
        "source_code": "WEEK_4", "source_index": 0, "target_code": "WEEK_4A", "target_index": 0}])
    assert translate_occurrence([confirmed], "WEEK_4", 0) == (
        Translation.TARGET, ("WEEK_4A", 0), None)
    # an occurrence with no rule is never guessed
    kind, target, reason = translate_occurrence([confirmed], "WEEK_4", 1)
    assert kind == Translation.UNRESOLVED and target is None and "no reviewed split/merge rule" in reason

    merged = propose_event_mappings(v2, v1)
    [merge] = by_kind(merged, MappingType.MERGED)
    assert merge.source_codes == ["WEEK_4A", "WEEK_4B"] and merge.target_codes == ["WEEK_4"]


def test_a_removed_visit_stays_in_history_and_an_added_visit_needs_nothing():
    mappings = propose_event_mappings(
        build({"V1": 1, "OLD": 20}, names={"OLD": "Obsolete Assessment"}),
        build({"V1": 1, "NEW": 40}, names={"NEW": "Brand New Procedure"}))
    [removed] = by_kind(mappings, MappingType.REMOVED)
    [added] = by_kind(mappings, MappingType.ADDED)
    assert removed.review_status == added.review_status == MappingReviewStatus.AUTO_CONFIRMED
    assert translate_occurrence(mappings, "OLD", 0)[0] == Translation.NONE


def test_mapping_shapes_are_validated():
    with pytest.raises(ValidationError):
        EventMapping(mapping_type=MappingType.SPLIT, source_codes=["A"], target_codes=["B"])
    with pytest.raises(ValidationError):
        EventMapping(mapping_type=MappingType.EQUIVALENT, source_codes=["A"], target_codes=["A"],
                     occurrence_rules=[{"source_code": "A", "target_code": "A"}])
    with pytest.raises(ValidationError):
        EventMapping(mapping_type=MappingType.EQUIVALENT, source_codes=["A"], target_codes=["A"],
                     review_status=MappingReviewStatus.CONFIRMED)


def test_revision_changes_with_review_state_and_coverage_is_checked():
    v1 = build({"V1": 1, "V2": 15}, names={"V2": "Week 2"})
    v2 = build({"V1": 1, "V2": 15}, names={"V2": "Fortnight"})
    mappings = propose_event_mappings(v1, v2)
    assert mapping_coverage_problems(v1, v2, mappings) == []
    before = mapping_revision(mappings)
    reviewed = [confirm(item) if item.review_status == MappingReviewStatus.PROPOSED else item
                for item in mappings]
    assert mapping_revision(reviewed) != before
    # dropping a mapping leaves both versions' events uncovered
    problems = mapping_coverage_problems(v1, v2, mappings[:1])
    assert {item["code"] for item in problems} >= {"MAPPING_SOURCE_COVERAGE", "MAPPING_TARGET_COVERAGE"}
    # an unknown code is reported against its mapping
    bogus = EventMapping(mapping_type=MappingType.REMOVED, source_codes=["NOPE"],
                         review_status=MappingReviewStatus.AUTO_CONFIRMED)
    assert any(item["code"] == "MAPPING_SOURCE_UNKNOWN"
               for item in mapping_coverage_problems(v1, v2, [*mappings, bogus]))


# ---------------------------------------------------------------- adoption point
@pytest.mark.parametrize("fields", [
    {"mode": "REMAIN_ON_V1"},
    {"mode": "ADOPT_IMMEDIATELY"},
    {"mode": "ADOPT_AT_EVENT", "event_code": "V3", "occurrence_index": 0},
    {"mode": "ADOPT_AT_CYCLE", "repeat_block_code": "TX", "cycle_number": 3},
    {"mode": "ADOPT_ON_DATE", "on_date": date(2027, 1, 5)},
])
def test_valid_adoption_points(fields):
    assert AdoptionPoint(**fields).mode == AdoptionMode(fields["mode"])


@pytest.mark.parametrize("fields", [
    {"mode": "ADOPT_AT_EVENT", "event_code": "V3"},
    {"mode": "ADOPT_AT_EVENT", "event_code": "V3", "occurrence_index": 0, "on_date": date(2027, 1, 5)},
    {"mode": "ADOPT_AT_CYCLE", "repeat_block_code": "TX"},
    {"mode": "ADOPT_AT_CYCLE", "repeat_block_code": "TX", "cycle_number": 0},
    {"mode": "ADOPT_ON_DATE"},
    {"mode": "ADOPT_IMMEDIATELY", "on_date": date(2027, 1, 5)},
    {"mode": "REMAIN_ON_V1", "event_code": "V3", "occurrence_index": 0},
    {"mode": "WHENEVER"},
])
def test_invalid_adoption_points_are_rejected(fields):
    with pytest.raises(ValidationError):
        AdoptionPoint(**fields)
