"""P6 repair: mapping determinism, unique fact transfer, readiness gate, reversal."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from _engine_cases import anchor, approved, visit

from app.domain.schedule.amendment import (
    EventMapping, MappingReviewStatus, MappingType, Translation, mapping_coverage_problems,
    mapping_problems, mapping_readiness_problems, mapping_revision, propose_event_mappings,
    reverse_translate_occurrence, translate_occurrence,
)
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming


def build(days, names=None):
    events = []
    for code, day in days.items():
        extra = {"display_name": names[code]} if names and code in names else {}
        events.append(visit(
            code, ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=day), **extra))
    return approved(events, anchors=[anchor("REGISTRATION")])


def confirm(item, **extra):
    data = item.model_dump()
    data.update({"review_status": MappingReviewStatus.CONFIRMED, "reviewed_by": uuid4(), **extra})
    return EventMapping.model_validate(data)


def rule(source, si, target, ti):
    return {"source_code": source, "source_index": si, "target_code": target, "target_index": ti}


def split_mapping(rules):
    return EventMapping(
        mapping_type=MappingType.SPLIT, source_codes=["A"], target_codes=["B", "C"],
        provenance="REVIEWER", review_status=MappingReviewStatus.CONFIRMED, reviewed_by=uuid4(),
        occurrence_rules=rules)


def merge_mapping(rules):
    return EventMapping(
        mapping_type=MappingType.MERGED, source_codes=["A", "B"], target_codes=["C"],
        provenance="REVIEWER", review_status=MappingReviewStatus.CONFIRMED, reviewed_by=uuid4(),
        occurrence_rules=rules)


def test_a_source_occurrence_cannot_be_sent_to_two_visits():
    with pytest.raises(ValidationError, match="more than one visit"):
        split_mapping([rule("A", 0, "B", 0), rule("A", 0, "C", 0)])


def test_exact_duplicate_rules_collapse_and_rule_order_changes_nothing():
    rules = [rule("A", 0, "B", 0), rule("A", 1, "C", 0)]
    one = split_mapping(rules)
    other = split_mapping([*reversed(rules), rules[0]])
    assert [r.model_dump() for r in one.occurrence_rules] == [r.model_dump() for r in other.occurrence_rules]
    assert mapping_revision([one]) == mapping_revision([other.model_copy(update={"id": one.id})])
    for mapping in (one, other):
        assert translate_occurrence([mapping], "A", 0)[1] == ("B", 0)
        assert translate_occurrence([mapping], "A", 1)[1] == ("C", 0)


def test_merge_inverse_is_ambiguous_not_first_row():
    forward = [rule("A", 0, "C", 0), rule("B", 0, "C", 0)]
    for rules in (forward, list(reversed(forward))):
        mapping = merge_mapping(rules)
        assert translate_occurrence([mapping], "A", 0)[1] == ("C", 0)
        assert translate_occurrence([mapping], "B", 0)[1] == ("C", 0)
        kind, target, reason = reverse_translate_occurrence([mapping], "C", 0)
        assert (kind, target) == (Translation.UNRESOLVED, None)
        assert "reviewed transfer policy" in reason


def test_unambiguous_reverse_round_trips():
    split = split_mapping([rule("A", 0, "B", 0), rule("A", 1, "C", 0)])
    for code, index in (("A", 0), ("A", 1)):
        _, target, _ = translate_occurrence([split], code, index)
        assert reverse_translate_occurrence([split], *target)[1] == (code, index)
    merge = merge_mapping([rule("A", 0, "C", 0), rule("B", 0, "C", 1)])
    for code in ("A", "B"):
        _, target, _ = translate_occurrence([merge], code, 0)
        assert reverse_translate_occurrence([merge], *target)[1] == (code, 0)


def test_reverse_covers_every_mapping_type_and_status():
    equivalent = EventMapping(mapping_type=MappingType.EQUIVALENT, source_codes=["A"], target_codes=["A"],
                              review_status=MappingReviewStatus.AUTO_CONFIRMED)
    assert reverse_translate_occurrence([equivalent], "A", 4) == (Translation.TARGET, ("A", 4), None)
    renamed = EventMapping(mapping_type=MappingType.RENAMED, source_codes=["A"], target_codes=["Z"],
                           provenance="AUTO_NAME_MATCH", review_status=MappingReviewStatus.PROPOSED)
    assert reverse_translate_occurrence([renamed], "Z", 0)[0] == Translation.UNRESOLVED
    assert reverse_translate_occurrence([confirm(renamed)], "Z", 2)[1] == ("A", 2)
    rejected = confirm(renamed, review_status=MappingReviewStatus.REJECTED)
    assert "not covered" in reverse_translate_occurrence([rejected], "Z", 0)[2]
    assert "not covered" in translate_occurrence([rejected], "A", 0)[2]
    added = EventMapping(mapping_type=MappingType.ADDED, target_codes=["N"], provenance="AUTO_NO_COUNTERPART",
                         review_status=MappingReviewStatus.AUTO_CONFIRMED)
    assert "only in the later version" in reverse_translate_occurrence([added], "N", 0)[2]
    removed = EventMapping(mapping_type=MappingType.REMOVED, source_codes=["O"], provenance="AUTO_NO_COUNTERPART",
                           review_status=MappingReviewStatus.AUTO_CONFIRMED)
    assert translate_occurrence([removed], "O", 0)[0] == Translation.NONE
    assert reverse_translate_occurrence([removed], "O", 0)[0] == Translation.UNRESOLVED
    assert reverse_translate_occurrence([], "Q", 0)[0] == Translation.UNRESOLVED


def test_overlapping_rows_are_ambiguous_not_first_row():
    one = confirm(EventMapping(mapping_type=MappingType.RENAMED, source_codes=["A"], target_codes=["X"],
                               provenance="AUTO_NAME_MATCH"))
    two = confirm(EventMapping(mapping_type=MappingType.RENAMED, source_codes=["A"], target_codes=["Y"],
                               provenance="AUTO_NAME_MATCH"))
    for rows in ([one, two], [two, one]):
        kind, target, reason = translate_occurrence(rows, "A", 0)
        assert (kind, target) == (Translation.UNRESOLVED, None) and "ambiguous" in reason
    twin = one.model_copy(update={"id": uuid4()})
    assert reverse_translate_occurrence([one, twin], "X", 0)[0] == Translation.UNRESOLVED


def test_automatic_confirmation_cannot_stand_in_for_review():
    with pytest.raises(ValidationError, match="split or merge"):
        EventMapping(mapping_type=MappingType.SPLIT, source_codes=["A"], target_codes=["B", "C"],
                     review_status=MappingReviewStatus.AUTO_CONFIRMED, provenance="AUTO_EXACT")
    with pytest.raises(ValidationError, match="exact or no-counterpart"):
        EventMapping(mapping_type=MappingType.RENAMED, source_codes=["A"], target_codes=["B"],
                     review_status=MappingReviewStatus.AUTO_CONFIRMED, provenance="AUTO_SIMILAR_NAME")
    with pytest.raises(ValidationError, match="who reviewed"):
        EventMapping(mapping_type=MappingType.RENAMED, source_codes=["A"], target_codes=["B"],
                     review_status=MappingReviewStatus.REJECTED)


def test_readiness_is_separate_from_structural_coverage_and_combined_gate_reports_both():
    v1 = build({"V1": 1, "V2": 15}, {"V2": "Week 2"})
    v2 = build({"V1": 1, "V2": 15}, {"V2": "Fortnight"})
    mappings = propose_event_mappings(v1, v2)
    assert mapping_coverage_problems(v1, v2, mappings) == []
    assert [p["code"] for p in mapping_readiness_problems(mappings)] == ["MAPPING_UNREVIEWED"]
    assert [p["code"] for p in mapping_problems(v1, v2, mappings)] == ["MAPPING_UNREVIEWED"]
    reviewed = [confirm(m) if m.review_status == MappingReviewStatus.PROPOSED else m for m in mappings]
    assert mapping_problems(v1, v2, reviewed) == []
    assert {p["code"] for p in mapping_problems(v1, v2, mappings[:1])} >= {
        "MAPPING_TARGET_COVERAGE", "MAPPING_SOURCE_COVERAGE"}


def test_split_merge_readiness_needs_rules_for_every_visit():
    thin = split_mapping([rule("A", 0, "B", 0)])
    assert [p["code"] for p in mapping_readiness_problems([thin])] == ["MAPPING_RULES_MISSING"]
    full = split_mapping([rule("A", 0, "B", 0), rule("A", 1, "C", 0)])
    assert mapping_readiness_problems([full]) == []
    assert [p["code"] for p in mapping_readiness_problems([split_mapping([])])] == ["MAPPING_RULES_MISSING"]


def test_a_rejected_proposal_is_kept_but_its_replacement_supplies_coverage():
    v1 = build({"V4": 57}, {"V4": "Week 8 Follow-up"})
    v2 = build({"V4": 99}, {"V4": "Safety Call"})
    [proposal] = propose_event_mappings(v1, v2)
    rejected = confirm(proposal, review_status=MappingReviewStatus.REJECTED, review_reason="different visit")
    replacements = [
        EventMapping(mapping_type=MappingType.REMOVED, source_codes=["V4"], provenance="REVIEWER",
                     review_status=MappingReviewStatus.CONFIRMED, reviewed_by=uuid4()),
        EventMapping(mapping_type=MappingType.ADDED, target_codes=["V4"], provenance="REVIEWER",
                     review_status=MappingReviewStatus.CONFIRMED, reviewed_by=uuid4())]
    assert mapping_problems(v1, v2, [rejected, *replacements]) == []
    assert mapping_revision([rejected, *replacements]) == mapping_revision(replacements)
    assert {p["code"] for p in mapping_problems(v1, v2, [rejected])} == {
        "MAPPING_SOURCE_COVERAGE", "MAPPING_TARGET_COVERAGE"}
