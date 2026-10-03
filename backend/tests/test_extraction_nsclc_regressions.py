"""Regressions from the first live read of a real protocol (NCT01757288).

Each fact below is shaped exactly like what the model returned; each test pins
the compiler behaviour that was wrong in that run.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction.compiler import compile_schedule  # noqa: E402
from app.extraction.facts import DocumentFacts, ScheduleFact  # noqa: E402


def fact(**values) -> ScheduleFact:
    values.setdefault("evidence", [{"page": 22, "section": "7.1.2", "quote": values.get("what", "x")}])
    return ScheduleFact.model_validate(values)


def compile_facts(*facts, blocks=None):
    document = DocumentFacts(
        facts=list(facts), blocks=blocks or [],
        day_numbering={"day_1_anchor": "Start of protocol treatment", "counting_convention": "CLINICAL_DAY"})
    result = compile_schedule(document)
    return result.schedule, result.issues


def test_a_toxicity_hold_with_a_prose_threshold_is_kept_not_dropped():
    item = fact(
        what="Other toxicities >= grade 3: withhold until <= grade 1, then resume at next lower dose",
        kind="condition", timing={"pattern": "HOLD_RESUME"},
        condition={"name": "Grade 3 non-hematologic toxicity", "effect": "PAUSE",
                   "targets": ["Consolidation chemotherapy"],
                   "resume_when": "resolution to <= grade 1 or baseline"})
    assert item.condition.resume_when is None
    assert item.condition.criteria_text == {"resume_when": "resolution to <= grade 1 or baseline"}


def test_x2_cycles_is_a_count_not_cycle_number_two():
    item = fact(
        what="Paclitaxel 200 mg/m2 IV/3h, q3 wks x2 cycles", kind="visit", block="Consolidation",
        timing={"pattern": "CYCLE_DAY", "day": 1, "from": {"name": "Start of consolidation"},
                "cycle": {"start_anchor": "Start of consolidation", "length": {"value": 21, "unit": "DAY"},
                          "number": 2}},
        dependency_mode="NOMINAL")
    schedule, _ = compile_facts(item)
    plans = [block.cycle_plan for block in schedule.repeat_blocks if block.cycle_plan]
    assert len(plans) == 1
    assert plans[0].termination.type == "COUNT" and plans[0].termination.count == 2
    event = next(e for e in schedule.events if e.code.startswith("PACLITAXEL"))
    assert event.timing.type == "CYCLE_DAY" and event.timing.cycle.type == "CURRENT"


def test_a_named_cycle_keeps_its_number():
    item = fact(
        what="MRI at Cycle 3 Day 1", kind="visit", block="Consolidation",
        timing={"pattern": "CYCLE_DAY", "day": 1, "from": {"name": "Start of consolidation"},
                "cycle": {"start_anchor": "Start of consolidation", "length": {"value": 21, "unit": "DAY"},
                          "number": 3}},
        evidence=[{"page": 22, "quote": "MRI at Cycle 3 Day 1 (x3 cycles total)"}])
    from app.extraction.compiler import _cycle_count_written_as_number
    assert _cycle_count_written_as_number(item).timing.cycle.number == 3


def test_a_generic_cycle_1_day_1_start_is_the_blocks_named_start():
    common = {"kind": "visit", "block": "Consolidation", "dependency_mode": "NOMINAL",
              "repeat": {"interval": {"value": 21, "unit": "DAY"}, "end": {"type": "COUNT", "count": 2}}}
    day1 = fact(what="Carboplatin day 1", timing={
        "pattern": "CYCLE_DAY", "day": 1, "cycle": {"start_anchor": "Cycle 1 Day 1",
                                                     "length": {"value": 21, "unit": "DAY"}}}, **common)
    day8 = fact(what="Nab-paclitaxel day 8", timing={
        "pattern": "CYCLE_DAY", "day": 8, "cycle": {"start_anchor": "Beginning of consolidation therapy",
                                                     "length": {"value": 21, "unit": "DAY"}}}, **common)
    schedule, issues = compile_facts(day1, day8)
    plan = next(block.cycle_plan for block in schedule.repeat_blocks if block.cycle_plan)
    assert plan.start_reference.code == "BEGINNING_OF_CONSOLIDATION_THERAPY"
    assert not [i for i in issues if "different Cycle 1 starts" in i.message]


def test_three_phased_follow_up_facts_become_one_plan():
    def phase(what, interval, start, end=None, **segment):
        return fact(what=what, kind="visit", timing={
            "pattern": "PHASED_REPEAT", "from": {"name": "Completion of all protocol treatment"},
            "segments": [{"interval": interval, "start": start, "end": end, **segment}]})
    parts = [
        phase("Follow up every 3 months for 2 years", {"value": 3, "unit": "MONTH"},
              {"value": 0, "unit": "MONTH"}, {"value": 24, "unit": "MONTH"},
              end_inclusive=True, includes_start=False),
        phase("Follow up every 6 months for years 3 and 4", {"value": 6, "unit": "MONTH"},
              {"value": 24, "unit": "MONTH"}, {"value": 48, "unit": "MONTH"},
              end_inclusive=True, includes_start=False),
        phase("Annual follow up for life", {"value": 12, "unit": "MONTH"},
              {"value": 48, "unit": "MONTH"}, includes_start=False),
    ]
    schedule, _ = compile_facts(*parts)
    plans = [e for e in schedule.events if e.recurrence_plan is not None]
    assert len(plans) == 1
    assert [s.interval.value for s in plans[0].recurrence_plan.segments] == [3, 6, 12]


def test_phases_that_do_not_meet_are_not_merged():
    def phase(what, start, end):
        return fact(what=what, kind="visit", timing={
            "pattern": "PHASED_REPEAT", "from": {"name": "End of therapy"},
            "segments": [{"interval": {"value": 3, "unit": "MONTH"}, "start": start, "end": end}]})
    schedule, _ = compile_facts(
        phase("Early", {"value": 0, "unit": "MONTH"}, {"value": 12, "unit": "MONTH"}),
        phase("Late", {"value": 24, "unit": "MONTH"}, {"value": 36, "unit": "MONTH"}))
    assert len([e for e in schedule.events if e.recurrence_plan is not None]) == 2
