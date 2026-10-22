"""A narrative summary and the schedule table describe the same timepoint: the table's exact values
are used and the choice is shown; a narrative that really disagrees stays a conflict."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_extraction_compiler import compile_facts, event  # noqa: E402
from app.domain.schedule.timing import RangeTiming, OffsetTiming  # noqa: E402

IMPLANT = {"what": "Implant", "kind": "anchor", "anchor": {"type": "RECORDED"},
           "evidence": [{"page": 14, "section": "Synopsis", "quote": "Date of valve implant"}]}


def narrative(what, timing, quote="data will be collected at 1 year after implant"):
    return {"what": what, "kind": "visit", "timing": timing,
            "evidence": [{"page": 14, "section": "Synopsis", "quote": quote}]}


def table(what, timing, window=None, quote="365 days"):
    return {"what": what, "kind": "visit", "timing": timing, **({"window": window} if window else {}),
            "evidence": [{"page": 23, "section": "Table 1: Schedule of Events",
                          "table": "Table 1: Schedule of Events", "quote": quote}]}


def codes(result, *, blocking):
    return {issue.issue_code for issue in result.issues if issue.blocking == blocking}


def test_the_table_wins_over_a_narrative_restatement_and_the_choice_is_visible():
    result = compile_facts(
        IMPLANT,
        narrative("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "1 YEAR"}),
        table("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "365 DAY"},
              {"before": "30 DAY", "after": "30 DAY"}))
    visit = event(result, "E_1_YEAR")
    assert isinstance(visit.timing, OffsetTiming)
    assert (visit.timing.offset.value, visit.timing.offset.unit) == (365, "DAY")       # the table's value
    assert (visit.window.before.value, visit.window.after.value) == (30, 30)           # and its window
    assert "CONFLICTING_SOURCE" not in codes(result, blocking=True)
    notice = [i for i in result.issues if i.issue_code == "CONFLICT_RESOLVED_BY_TABLE_PRIORITY"]
    assert len(notice) == 1 and not notice[0].blocking
    assert notice[0].details["alternatives"][0]["citations"][0]["page"] == 14      # the narrative stays queryable
    assert notice[0].entity_id == visit.id


def test_a_range_in_months_agrees_with_a_range_in_days():
    result = compile_facts(
        IMPLANT,
        narrative("3-6 Months", {"pattern": "RANGE", "from": "Implant", "start": "3 MONTH", "end": "6 MONTH"}),
        table("3-6 Months", {"pattern": "RANGE", "from": "Implant", "start": "90 DAY", "end": "180 DAY"},
              quote="+90-180 days"))
    assert "CONFLICTING_SOURCE" not in codes(result, blocking=True)
    assert any(i.issue_code == "CONFLICT_RESOLVED_BY_TABLE_PRIORITY" for i in result.issues)
    visit = event(result, "E_3_6_MONTHS")
    assert isinstance(visit.timing, RangeTiming)
    assert (visit.timing.start.value, visit.timing.end.value, visit.timing.end.unit) == (90, 180, "DAY")


def test_a_narrative_that_really_disagrees_with_the_table_is_still_a_blocking_conflict():
    result = compile_facts(
        IMPLANT,
        narrative("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "6 MONTH"}),
        table("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "365 DAY"}))
    assert "CONFLICTING_SOURCE" in codes(result, blocking=True)
    assert not any(i.issue_code == "CONFLICT_RESOLVED_BY_TABLE_PRIORITY" for i in result.issues)


def test_two_narrative_statements_or_two_table_cells_are_never_resolved_by_priority():
    two_narrative = compile_facts(
        IMPLANT,
        narrative("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "1 YEAR"}),
        narrative("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "400 DAY"}, quote="at about 400 days"))
    assert "CONFLICTING_SOURCE" in codes(two_narrative, blocking=True)
    two_tables = compile_facts(
        IMPLANT,
        table("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "365 DAY"}),
        table("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "366 DAY"}, quote="366 days"))
    assert "CONFLICTING_SOURCE" in codes(two_tables, blocking=True)


def test_a_different_reference_is_not_treated_as_a_restatement():
    result = compile_facts(
        IMPLANT,
        {"what": "Registration", "kind": "anchor", "anchor": {"type": "RECORDED"},
         "evidence": [{"page": 14, "section": "Synopsis", "quote": "Date of registration"}]},
        narrative("1 Year", {"pattern": "OFFSET", "from": "Registration", "offset": "1 YEAR"}),
        table("1 Year", {"pattern": "OFFSET", "from": "Implant", "offset": "365 DAY"}))
    assert "CONFLICTING_SOURCE" in codes(result, blocking=True)
