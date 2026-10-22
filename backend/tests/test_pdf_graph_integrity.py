"""P7 graph/source failures are caught at the shared validator boundary."""
from uuid import uuid4

from app.application.pilot import build_pilot_schedule
from app.domain.schedule.models import Activity, ClaimEvidence, StudyDimension
from app.domain.schedule.timing import ActivityReference, OffsetTiming
from app.domain.schedule.validator import ScheduleValidator


def schedule():
    return build_pilot_schedule(uuid4(), "Synthetic graph fixture")


def findings(value):
    return ScheduleValidator().validate(value)


def test_duplicate_definition_identity_is_blocking_with_both_locations():
    value = schedule()
    value.events[1].id = value.events[0].id
    issue = next(row for row in findings(value) if row.issue_code == "GRAPH_DUPLICATE_ID")
    assert issue.blocking
    assert issue.details["field_path"].endswith("/id")
    assert issue.details["first_path"].startswith("events/")


def test_indirect_activity_cycle_is_reported_without_a_self_reference():
    value = schedule()
    value.events[0].activities = [Activity(
        code=code, protocol_label=code, display_name=code, activity_type="LAB",
        timing=OffsetTiming(reference=ActivityReference(activity_code=other),
                            offset={"value": 1, "unit": "HOUR"}))
        for code, other in [("A", "B"), ("B", "A")]]
    issue = next(row for row in findings(value) if row.issue_code == "GRAPH_ACTIVITY_CYCLE")
    assert issue.details["activity_codes"] == ["A", "B"]


def test_parent_dimension_cycles_and_missing_parents_have_precise_codes():
    value = schedule()
    value.arms = [StudyDimension(
        code=code, protocol_label=code, display_name=code,
        parent_dimension_type="ARM", parent_code=parent)
        for code, parent in [("A", "B"), ("B", "A"), ("C", "MISSING")]]
    codes = {row.issue_code for row in findings(value)}
    assert {"GRAPH_DIMENSION_CYCLE", "GRAPH_DIMENSION_PARENT_MISSING"} <= codes


def test_evidence_of_another_version_or_absent_entity_cannot_support_a_rule():
    value = schedule()
    value.evidence[0].source_locator["protocol_version_id"] = str(uuid4())
    value.claim_evidence.append(ClaimEvidence(
        evidence_id=value.evidence[0].id, claim_entity_type="EVENT", claim_entity_id=uuid4(),
        claim_type="TIMING"))
    codes = {row.issue_code for row in findings(value)}
    assert {"GRAPH_ORPHAN_CLAIM", "EVIDENCE_VERSION_MISMATCH"} <= codes


def test_validation_is_read_only_and_locations_do_not_depend_on_event_order():
    value = schedule()
    value.evidence[0].source_text = ""
    before = value.model_dump(mode="json")
    first = [(row.issue_code, row.details["field_path"]) for row in findings(value)]
    assert value.model_dump(mode="json") == before
    value.events.reverse()
    second = [(row.issue_code, row.details["field_path"]) for row in findings(value)]
    assert first == second
    assert any(code == "EVIDENCE_QUOTE_MISSING" for code, _ in first)
