"""Deterministic footnote and qualifier completeness (MTB requirement doc 7).

Doc 7 sections 22 to 27. The LLM decides what a footnote MEANS; this layer only
proves nothing the document contained was silently dropped on the way into the
schedule.
"""

from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction.completeness import (
    check_marker_completeness, check_qualifier_conflicts, run_completeness_checks,
    scan_markers,
)
from app.domain.schedule.models import (
    Activity, Anchor, Event, Qualifier, QualifierCategory, QualifierScope,
    ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming

# The schedule table from doc 7 section 4, as it arrives from a PDF text layer.
SOA_PAGE = {
    "page_number": 42,
    "text": (
        "Table 3 Schedule of Assessments\n"
        "Activity        Day 1     Week 4\n"
        "ECG             Xa        X\n"
        "CT Scan         Xb        X\n"
        "\n"
        "a. ECG should be performed pre-dose.\n"
        "b. CT scan should be performed only in subjects with measurable disease.\n"
    ),
}


def qualifier(marker: str, text: str, **changes) -> Qualifier:
    values = {
        "marker": marker, "text": text, "scope": QualifierScope.CELL,
        "category": QualifierCategory.TIMING, "target_codes": ["C1D1"],
        "resolved": True, "evidence_refs": [],
    }
    values.update(changes)
    return Qualifier(**values)


def schedule_with(qualifiers: list[Qualifier], *, activity_qualifiers=None) -> UniversalSchedule:
    activity = Activity(
        code="ECG", protocol_label="ECG", display_name="ECG", activity_type="ASSESSMENT",
        qualifiers=activity_qualifiers or [],
    )
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary"),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        events=[Event(
            code="C1D1", protocol_label="C1D1", display_name="Cycle 1 Day 1",
            event_type="SITE_VISIT",
            timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
            qualifiers=qualifiers, activities=[activity],
        )],
    )


# --- scanning -------------------------------------------------------------------

def test_markers_and_footnote_definitions_are_both_detected():
    scan = scan_markers([SOA_PAGE])
    assert scan.marker_set() == {"a", "b"}
    assert scan.footnote_set() == {"a", "b"}
    assert any("pre-dose" in item.text for item in scan.footnotes)
    assert all(item.page_number == 42 for item in scan.markers)


def test_superscript_markers_are_folded_to_their_plain_form():
    scan = scan_markers([{
        "page_number": 7,
        "text": "ECG   Xᵃ   X\nCT    X¹\n"
                "a. Perform pre-dose.\n1. Only for measurable disease.\n",
    }])
    assert scan.marker_set() == {"a", "1"}


def test_symbol_markers_are_detected():
    scan = scan_markers([{
        "page_number": 3,
        "text": "Biopsy   X*   X\n* Optional for consenting participants only.\n",
    }])
    assert "*" in scan.marker_set()
    assert "*" in scan.footnote_set()


def test_a_numbered_visit_row_is_not_mistaken_for_a_footnote():
    scan = scan_markers([{
        "page_number": 1, "text": "1 Screening\n2 Baseline\n",
    }])
    assert scan.footnote_set() == set()


# --- doc 7 s23: every marker must resolve ---------------------------------------

def test_a_marked_cell_that_lost_its_marker_blocks_approval():
    """The X survived extraction but the footnote meaning did not."""
    issues = check_marker_completeness(schedule_with([]), scan_markers([SOA_PAGE]))
    codes = {item.issue_code for item in issues}

    assert "UNRESOLVED_QUALIFIER" in codes
    unresolved = next(item for item in issues if item.issue_code == "UNRESOLVED_QUALIFIER")
    assert unresolved.blocking is True
    assert unresolved.details["markers"] == ["a", "b"]
    assert unresolved.details["pages"] == [42]


def test_fully_resolved_markers_produce_no_findings():
    schedule = schedule_with([
        qualifier("a", "ECG should be performed pre-dose."),
        qualifier("b", "Only subjects with measurable disease.",
                  category=QualifierCategory.APPLICABILITY),
    ])
    assert check_marker_completeness(schedule, scan_markers([SOA_PAGE])) == []


def test_a_partially_resolved_table_still_reports_the_missing_marker():
    schedule = schedule_with([qualifier("a", "ECG should be performed pre-dose.")])
    issues = check_marker_completeness(schedule, scan_markers([SOA_PAGE]))
    assert issues[0].details["markers"] == ["b"]


def test_an_activity_level_qualifier_also_counts_as_resolution():
    schedule = schedule_with(
        [qualifier("a", "ECG should be performed pre-dose.")],
        activity_qualifiers=[qualifier(
            "b", "Only subjects with measurable disease.",
            scope=QualifierScope.ACTIVITY, target_codes=["ECG"],
            category=QualifierCategory.APPLICABILITY)],
    )
    assert check_marker_completeness(schedule, scan_markers([SOA_PAGE])) == []


# --- doc 7 s25/s26: orphans on either side --------------------------------------

def test_a_footnote_with_no_marker_anywhere_is_flagged():
    page = {
        "page_number": 9,
        "text": "Activity   Day 1\nECG        X\n"
                "c. Repeat if abnormal until resolved.\n",
    }
    issues = check_marker_completeness(schedule_with([]), scan_markers([page]))
    assert any(
        item.issue_code == "ORPHAN_QUALIFIER" and "c" in item.details.get("markers", [])
        for item in issues
    )


def test_a_marker_with_no_footnote_definition_is_flagged():
    page = {"page_number": 5, "text": "Activity   Day 1\nECG        Xz\n"}
    issues = check_marker_completeness(schedule_with([]), scan_markers([page]))
    assert any(
        item.issue_code == "ORPHAN_QUALIFIER"
        and "no corresponding footnote definition" in item.message
        for item in issues
    )


# --- doc 7 s27: contradiction goes to review, it is not silently resolved --------

def test_two_qualifiers_disagreeing_about_one_target_raise_a_conflict():
    schedule = schedule_with([
        qualifier("a", "CT every 8 weeks.", category=QualifierCategory.REPEAT),
        qualifier("d", "After Week 24, CT every 12 weeks.",
                  category=QualifierCategory.REPEAT),
    ])
    issues = check_qualifier_conflicts(schedule)

    assert len(issues) == 1
    assert issues[0].issue_code == "CONFLICTING_SOURCE"
    assert issues[0].blocking is True
    assert len(issues[0].details["statements"]) == 2


def test_qualifiers_in_different_categories_are_not_a_conflict():
    schedule = schedule_with([
        qualifier("a", "Perform pre-dose.", category=QualifierCategory.TIMING),
        qualifier("b", "Only measurable disease.", category=QualifierCategory.APPLICABILITY),
    ])
    assert check_qualifier_conflicts(schedule) == []


def test_the_same_statement_repeated_is_not_a_conflict():
    schedule = schedule_with([
        qualifier("a", "Perform pre-dose."),
        qualifier("b", "perform pre-dose."),
    ])
    assert check_qualifier_conflicts(schedule) == []


@pytest.mark.parametrize("second_table,second_page,scope,conflict", [
    ("Table 2", 1, "CELL", False),
    ("Table 1", 2, "CELL", True),
    (None, 2, "CELL", True),
    ("Table 2", 2, "GLOBAL", True),
])
def test_reused_marker_is_scoped_to_its_explicit_table_legend(second_table, second_page, scope, conflict):
    from app.domain.schedule.models import Evidence
    from app.domain.schedule.validator import ScheduleValidator
    first = Evidence(evidence_type="FOOTNOTE", page_number=1, table_title="Table 1", source_text="a. Before dose")
    second = Evidence(evidence_type="FOOTNOTE", page_number=second_page, table_title=second_table, source_text="a. After infusion")
    schedule = schedule_with([
        qualifier("a", "Before dose", evidence_refs=[first.id]),
        qualifier("a", "After infusion", evidence_refs=[second.id], scope=scope),
    ])
    schedule.evidence = [first, second]
    issues = ScheduleValidator().validate(schedule)
    marker_conflicts = [issue for issue in issues if issue.issue_code == "CONFLICTING_EVIDENCE" and "marker" in issue.message]
    assert bool(marker_conflicts) == conflict


def test_an_unknown_legend_does_not_hide_a_conflict_between_known_table_scopes():
    from app.domain.schedule.models import Evidence
    from app.domain.schedule.validator import ScheduleValidator
    first = Evidence(evidence_type="FOOTNOTE", table_title="Table 1", source_text="Before dose")
    second = Evidence(evidence_type="FOOTNOTE", table_title="Table 2", source_text="After dose")
    schedule = schedule_with([
        qualifier("a", "Before dose", evidence_refs=[first.id]),
        qualifier("a", "After dose", evidence_refs=[second.id]),
        qualifier("a", "During infusion"),
    ])
    schedule.evidence = [first, second]
    issues = ScheduleValidator().validate(schedule)
    assert any(issue.issue_code == "CONFLICTING_EVIDENCE" and "marker" in issue.message for issue in issues)


def test_the_full_pass_combines_both_kinds_of_finding():
    schedule = schedule_with([
        qualifier("a", "CT every 8 weeks.", category=QualifierCategory.REPEAT),
        qualifier("a", "After Week 24, CT every 12 weeks.",
                  category=QualifierCategory.REPEAT),
    ])
    codes = {item.issue_code for item in run_completeness_checks(schedule, [SOA_PAGE])}
    assert codes == {"UNRESOLVED_QUALIFIER", "CONFLICTING_SOURCE"}
