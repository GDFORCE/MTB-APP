"""Compiler -> WP2 constraint language (AND / OR / UNSUPPORTED).

Independent synthetic facts only; names are invented and AT-17 re-runs the
same shapes under unrelated names. The compiled draft is evaluated with the
real engine where the claim is about dates.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _engine_cases import context, evaluate, only  # noqa: E402
from app.domain.schedule.models import FeasibilityStatus, ScheduleStatus  # noqa: E402
from app.domain.schedule.timing import ConstraintTiming  # noqa: E402
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.extraction import ledger as L  # noqa: E402
from app.extraction.compiler import compile_schedule  # noqa: E402
from app.extraction.facts import DocumentFacts, ScheduleFact  # noqa: E402
from app.extraction.reader import ReaderLimits  # noqa: E402

import test_extraction_accountability as acc  # noqa: E402


def fact(what, quote, *, fid, page=10, **values):
    values.setdefault("kind", "visit")
    return ScheduleFact.model_validate({
        "id": fid, "what": what, "evidence": [{"page": page, "quote": quote, "verification": "TEXT_MATCHED"}],
        **values})


def anchor_fact(name, fid):
    return fact(name, f"{name} is recorded", fid=fid, kind="anchor", anchor={"type": "RECORDED"})


def compile_facts(*facts, day_1=None):
    numbering = {"day_1_anchor": day_1, "counting_convention": "CLINICAL_DAY"} if day_1 else {}
    return compile_schedule(DocumentFacts(facts=list(facts), day_numbering=numbering))


def event(result, code):
    return next(item for item in result.schedule.events if item.code == code)


def blocking(result, entity_id=None):
    return {issue.issue_code for issue in ScheduleValidator.blocking(result.issues)
            if entity_id is None or issue.entity_id == entity_id}


def statements(result, what):
    return [item for item in result.accountability["statements"] if item["what"] == what]


def leaves(node):
    items = getattr(node, "items", None)
    if items is None:
        return [node]
    return [leaf for item in items for leaf in leaves(item)]


def bound_leaves(evt):
    found = []
    if isinstance(evt.timing, ConstraintTiming):
        found += leaves(evt.timing.constraint)
    if evt.constraints is not None:
        found += leaves(evt.constraints)
    return found


def range_and_minimum(scan="Response scan", end_a="End of all therapy", end_b="End of radiotherapy"):
    return [
        anchor_fact(end_a, "a1"), anchor_fact(end_b, "a2"),
        fact(scan, f"{scan} 12 to 16 weeks after {end_a.lower()}", fid="f1",
             timing={"pattern": "RANGE", "from": end_a, "start": "12 WEEK", "end": "16 WEEK",
                     "lower_inclusive": True, "upper_inclusive": True}),
        fact(scan, f"{scan} at least 12 weeks after {end_b.lower()}", fid="f2", page=11,
             timing={"pattern": "NO_EARLIER_THAN", "from": end_b, "duration": "12 WEEK"}),
    ]


# --- AND ------------------------------------------------------------------------------

def test_an_and_pair_compiles_to_constraints_with_every_bounds_evidence():
    result = compile_facts(*range_and_minimum())
    scan = event(result, "RESPONSE_SCAN")
    nodes = bound_leaves(scan)
    kinds = sorted((node.type, node.value.reference.code) for node in nodes if hasattr(node, "value"))
    # The range from A is kept (as legacy timing or nodes) and the minimum from B is added.
    assert ("LOWER_BOUND", "END_OF_RADIOTHERAPY") in kinds
    evidence = {str(item.id): item.source_text for item in result.schedule.evidence}
    for node in nodes:
        assert node.evidence_refs and all(ref in evidence for ref in node.evidence_refs)
    assert {item["disposition"] for item in statements(result, "Response scan")} == {L.COMPILED}
    assert any(rel["type"] == "AND" for item in statements(result, "Response scan") for rel in item["relations"])
    assert not {"MISSING_EVIDENCE", "UNSUPPORTED_OPERATOR", "CONSTRAINT_INFEASIBLE",
                "UNRESOLVED_STATEMENT"} & blocking(result, scan.id)


def test_the_compiled_and_pair_intersects_when_evaluated():
    result = compile_facts(*range_and_minimum(), day_1="End of all therapy")
    assert not ScheduleValidator.blocking(result.issues)  # the draft itself is approvable
    schedule = result.schedule.model_copy(update={"schedule_metadata": result.schedule.schedule_metadata.model_copy(
        update={"status": ScheduleStatus.APPROVED})})
    a, b = date(2027, 1, 4), date(2027, 1, 18)
    scan = only(evaluate(schedule, context(schedule, anchors={"END_OF_ALL_THERAPY": a,
                                                             "END_OF_RADIOTHERAPY": b})), "RESPONSE_SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    [interval] = scan.feasible_intervals
    assert interval.lower.value == b + timedelta(weeks=12)  # the later minimum binds
    assert interval.upper.value == a + timedelta(weeks=16)


def test_a_bound_whose_boundary_day_is_unstated_stays_an_unresolved_statement():
    facts = range_and_minimum()
    facts[3] = fact("Response scan", "Response scan 12 weeks or so after end of radiotherapy", fid="f2",
                    timing={"pattern": "NO_EARLIER_THAN", "from": "End of radiotherapy", "duration": "12 WEEK"})
    result = compile_facts(*facts)
    scan = event(result, "RESPONSE_SCAN")
    assert "UNRESOLVED_STATEMENT" in blocking(result, scan.id)
    pending = [item for item in statements(result, "Response scan") if item["statement_id"] == "f2"]
    assert pending[0]["disposition"] == L.PENDING_INTERPRETATION
    assert "boundary day" in pending[0]["reason"] or "boundary" in json.dumps(pending[0])


def test_incompatible_bounds_are_kept_and_reported_infeasible_not_resolved():
    result = compile_facts(
        anchor_fact("Milestone", "a1"),
        fact("Check", "Check at least 16 weeks after milestone", fid="f1",
             timing={"pattern": "NO_EARLIER_THAN", "from": "Milestone", "duration": "16 WEEK"}),
        fact("Check", "Check no later than 12 weeks after milestone", fid="f2",
             timing={"pattern": "NO_LATER_THAN", "from": "Milestone", "duration": "12 WEEK"}))
    check = event(result, "CHECK")
    assert len(bound_leaves(check)) == 2
    assert "CONSTRAINT_INFEASIBLE" in blocking(result, check.id)


# --- UNSUPPORTED ------------------------------------------------------------------------

def test_a_business_day_rule_is_an_unsupported_node_blocking_its_dependents():
    # WP3 made weekday series executable (test_extraction_wp3.py); a business-day
    # offset still has no operator.
    result = compile_facts(
        anchor_fact("Surgery", "a1"),
        fact("Wound check", "Wound check within 3 working days of surgery", fid="f1",
             timing={"pattern": "BUSINESS_DAYS", "from": "Surgery"}),
        fact("Suture removal", "7 days after the wound check", fid="f2",
             timing={"pattern": "OFFSET", "offset": "7 DAY",
                     "from": {"name": "Wound check", "kind": "EVENT", "occurrence": "FIRST"}},
             dependency_mode="ACTUAL_PREVIOUS_EVENT"))
    check = event(result, "WOUND_CHECK")
    assert check.timing.constraint.type == "UNSUPPORTED"
    unsupported = [issue for issue in result.issues if issue.issue_code == "UNSUPPORTED_OPERATOR"]
    assert unsupported and unsupported[0].details["source_operator"] == "BUSINESS_DAY_OFFSET"
    assert "SUTURE_REMOVAL" in unsupported[0].details["affected_dependents"]
    [item] = statements(result, "Wound check")
    assert item["disposition"] == L.UNSUPPORTED and item["operator"] == "BUSINESS_DAY_OFFSET"


def test_weekday_wording_without_named_weekdays_is_never_a_daily_series():
    result = compile_facts(
        anchor_fact("Start of radiotherapy", "a1"),
        fact("Radiotherapy fraction", "Radiotherapy once daily, 5 days per week, 30 fractions", fid="f1",
             timing={"pattern": "REPEAT", "from": "Start of radiotherapy"},
             repeat={"interval": "1 DAY", "includes_start": True, "end": {"type": "COUNT", "count": 30}}))
    fraction = event(result, "RADIOTHERAPY_FRACTION")
    assert fraction.recurrence is None and fraction.calendar_recurrence is None
    assert fraction.timing.type == "UNRESOLVED" and "weekdays" in fraction.timing.reason
    assert statements(result, "Radiotherapy fraction")[0]["disposition"] == L.PENDING_INTERPRETATION


def test_business_day_and_holiday_rules_name_their_operators():
    result = compile_facts(
        anchor_fact("Surgery", "a1"),
        fact("Wound check", "Wound check within 3 working days of surgery", fid="f1",
             timing={"pattern": "BUSINESS_DAYS", "from": "Surgery"}),
        fact("Infusion", "Infusions missed for public holidays are given the next day", fid="f2",
             timing={"pattern": "HOLIDAY_RULE", "from": "Surgery"}))
    operators = {event(result, code).timing.constraint.source_operator for code in ("WOUND_CHECK", "INFUSION")}
    assert operators == {"BUSINESS_DAY_OFFSET", "HOLIDAY_ADJUSTMENT"}
    assert {item["operator"] for what in ("Wound check", "Infusion") for item in statements(result, what)} == operators


# --- OR ----------------------------------------------------------------------------------

def alternatives(**extra):
    return [
        anchor_fact("Admission", "a1"), anchor_fact("Discharge", "a2"),
        fact("Follow-up call", "within 7 days after admission (no later than day 7)", fid="f1",
             alternative_group="call", timing={"pattern": "WITHIN_AFTER", "from": "Admission", "duration": "7 DAY",
                                               "lower_inclusive": True, "upper_inclusive": True}, **extra),
        fact("Follow-up call", "or within 3 days after discharge", fid="f2", alternative_group="call",
             timing={"pattern": "WITHIN_AFTER", "from": "Discharge", "duration": "3 DAY",
                     "lower_inclusive": True, "upper_inclusive": True}, **extra),
    ]


def test_stated_alternatives_become_any_not_an_intersection():
    result = compile_facts(*alternatives())
    call = event(result, "FOLLOW_UP_CALL")
    assert call.timing.type == "CONSTRAINED" and call.timing.constraint.type == "ANY"
    assert len(call.timing.constraint.items) == 2
    assert {item["disposition"] for item in statements(result, "Follow-up call")} == {L.COMPILED}
    assert any(rel["type"] == "OR" for item in statements(result, "Follow-up call") for rel in item["relations"])
    assert "CONFLICTING_SOURCE" not in blocking(result)


def test_a_contradiction_is_never_merged_into_any():
    result = compile_facts(*alternatives(conflict_group="disagree"))
    call = event(result, "FOLLOW_UP_CALL")
    assert call.interpretation_status == "CONFLICTING"
    assert not isinstance(call.timing, ConstraintTiming)
    assert "CONFLICTING_SOURCE" in blocking(result, call.id)


# --- generalisation -----------------------------------------------------------------------

def _shape(result, code, names):
    """The compiled constraint structure with entity codes replaced by roles."""
    evt = event(result, code)
    text = json.dumps({"timing": evt.timing.model_dump(mode="json"),
                       "constraints": evt.constraints.model_dump(mode="json") if evt.constraints else None},
                      sort_keys=True)
    for role, name in names.items():
        text = text.replace(name, role)
    data = json.loads(text)

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in {"id", "evidence_refs"}}
        if isinstance(value, list):
            return sorted((clean(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value
    return clean(data)


def test_at17_renamed_entities_compile_to_the_same_constraint_structure():
    first = compile_facts(*range_and_minimum())
    second = compile_facts(*range_and_minimum(scan="Quarterly imaging", end_a="Consolidation over",
                                              end_b="Boost complete"))
    assert _shape(first, "RESPONSE_SCAN", {"A": "END_OF_ALL_THERAPY", "B": "END_OF_RADIOTHERAPY"}) == \
        _shape(second, "QUARTERLY_IMAGING", {"A": "CONSOLIDATION_OVER", "B": "BOOST_COMPLETE"})


def test_constraints_do_not_depend_on_fact_order():
    facts = range_and_minimum()
    forward = compile_facts(*facts)
    backward = compile_facts(*reversed(facts))
    names = {"A": "END_OF_ALL_THERAPY", "B": "END_OF_RADIOTHERAPY"}
    assert _shape(forward, "RESPONSE_SCAN", names) == _shape(backward, "RESPONSE_SCAN", names)


def test_batch_order_invariance_through_the_reader():
    base = [acc.REG, acc.LABS, acc.VISIT_2,
            acc.fact("Visit 2", 3, "Consent within 14 days prior to study entry",
                     timing={"pattern": "NO_LATER_THAN", "from": "Registration", "duration": "6 WEEK",
                             "upper_inclusive": True})]
    one = acc.extract(acc.Scripted(base))
    two = acc.extract(acc.Scripted(base, reverse=True), limits=ReaderLimits(pages_per_batch=1))
    assert acc.normalized(one) == acc.normalized(two)
    assert acc.event(one, "VISIT_2").constraints is not None
