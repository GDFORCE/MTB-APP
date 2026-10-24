"""Synthetic source documents and reviewed expectations for live model checks.

Replay responses are only fixtures. Live runs receive source text, never the
gold facts/checks. Missing clinical values remain missing in both paths.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gold import Expectations


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    source: str
    metadata: dict
    reply: dict
    expected: Expectations


def amount(number, unit="DAY"):
    return {"value": number, "unit": unit}


def fact(name, quote, *, kind="visit", **fields):
    return {"what": name, "kind": kind, "evidence": [{"page": 1, "quote": quote}], **fields}


def timing(pattern, reference="Registration", **fields):
    return {"pattern": pattern, "from": {"name": reference}, **fields}


def check(name, kind="visit", **fields):
    return {"name": name, "kind": kind, "fields": fields}


def make_case(identifier, title, statements, facts, checks, *, day_one=True,
              metadata=None, table_cells=None, footnotes=None, **expectations):
    base = ["Synthetic protocol LAB-001, for software testing only.",
            "Registration is a recorded reference date.", "End of therapy is a recorded reference date.",
            "Surgery is a recorded reference date."]
    day = "Day 1 is Registration; use clinical-day counting with no Day 0."
    if day_one:
        base.append(day)
    overview = {"trial": {"title": "Synthetic protocol LAB-001"}, "schedule_options": [],
                "day_numbering": {"day_1_anchor": "Registration", "counting_convention": "CLINICAL_DAY",
                                  "evidence": [{"page": 1, "quote": day}]} if day_one else {},
                "anchor_names": ["Registration", "End of therapy", "Surgery"],
                "visit_names": [f["what"] for f in facts if f["kind"] == "visit"],
                "arms": [], "parts": [], "cohorts": [], "populations": [], "blocks": []}
    overview.update(metadata or {})
    anchors = [fact(name, f"{name} is a recorded reference date.", kind="anchor", anchor={"type": "RECORDED"})
               for name in overview["anchor_names"] if name in {"Registration", "End of therapy", "Surgery"}]
    return Case(identifier, title, "\n".join([*base, *statements]), overview,
                {"facts": anchors + facts, "table_cells": table_cells or [], "footnotes": footnotes or []},
                Expectations(checks=checks, **expectations))


def build_cases() -> list[Case]:
    cases = []
    def add(*args, **kwargs):
        cases.append(make_case(*args, **kwargs))

    quote = "Visit 3 occurs 4 weeks after Registration, with a window of 3 days before and 3 days after."
    add("offset-window", "Offset and explicit window", [quote],
        [fact("Visit 3", quote, timing=timing("OFFSET", offset=amount(4, "WEEK")),
              window={"before": amount(3), "after": amount(3)})],
        [check("Visit 3", **{"timing.pattern": "OFFSET", "timing.from.name": "Registration",
                            "timing.offset": amount(4, "WEEK"), "window.before": amount(3), "window.after": amount(3)})])

    quote = "Screening labs must occur within 28 days before Registration. First dose is a separate date."
    add("screening-anchor", "Screening uses Registration", [quote],
        [fact("Screening labs", quote, timing=timing("WITHIN_BEFORE", duration=amount(28)))],
        [check("Screening labs", **{"timing.pattern": "WITHIN_BEFORE", "timing.from.name": "Registration", "timing.duration": amount(28)})])

    quote = "Visit 4 occurs 8 weeks after Registration. No visit tolerance is stated."
    add("unstated-window", "No invented tolerance", [quote],
        [fact("Visit 4", quote, timing=timing("OFFSET", offset=amount(8, "WEEK")))],
        [check("Visit 4", **{"timing.offset": amount(8, "WEEK"), "window": None})])

    quote = "Day 15 visit may be up to 3 days late. An early tolerance is not stated."
    add("one-sided-window", "One-sided window stays one-sided", [quote],
        [fact("Day 15 visit", quote, timing={"pattern": "PROTOCOL_DAY", "day": 15}, window={"after": amount(3)})],
        [check("Day 15 visit", **{"window.before": None, "window.after": amount(3)})])

    quote = "Day 8 visit is performed on protocol Day 8."
    add("clinical-day", "Explicit Day 1 convention", [quote],
        [fact("Day 8 visit", quote, timing={"pattern": "PROTOCOL_DAY", "day": 8})],
        [check("Day 8 visit", **{"timing.pattern": "PROTOCOL_DAY", "timing.day": 8})])
    add("missing-day-one", "Missing Day 1 must block", [quote],
        [fact("Day 8 visit", quote, timing={"pattern": "PROTOCOL_DAY", "day": 8})],
        [check("Day 8 visit", **{"timing.day": 8})], day_one=False,
        required_issue_codes=["UNRESOLVED_SCHEDULE_ORIGIN"])

    quote = "Close-out occurs on 15 January 2027."
    add("absolute-date", "Absolute protocol date", [quote],
        [fact("Close-out", quote, timing={"pattern": "ABSOLUTE", "value": "2027-01-15"})],
        [check("Close-out", **{"timing.pattern": "ABSOLUTE", "timing.value": "2027-01-15"})])

    quote = "PET scan occurs 12 to 16 weeks after End of therapy."
    add("range", "Range from a milestone", [quote],
        [fact("PET scan", quote, timing=timing("RANGE", "End of therapy", start=amount(12, "WEEK"), end=amount(16, "WEEK")))],
        [check("PET scan", **{"timing.pattern": "RANGE", "timing.from.name": "End of therapy", "timing.start": amount(12, "WEEK"), "timing.end": amount(16, "WEEK")})])

    quote = "MRI occurs at least 12 weeks after End of therapy, including the 12-week boundary."
    add("minimum-delay", "Inclusive minimum delay", [quote],
        [fact("MRI", quote, timing=timing("NO_EARLIER_THAN", "End of therapy", duration=amount(12, "WEEK"), lower_inclusive=True))],
        [check("MRI", **{"timing.pattern": "NO_EARLIER_THAN", "timing.lower_inclusive": True})])

    quote = "Follow-up repeats every 3 months after Registration for 4 visits, first at month 3, on the nominal grid."
    repeat = {"interval": amount(3, "MONTH"), "includes_start": False, "end": {"type": "COUNT", "count": 4}}
    add("counted-repeat", "Stated repeat count and first occurrence", [quote],
        [fact("Follow-up", quote, timing=timing("REPEAT"), repeat=repeat, dependency_mode="NOMINAL")],
        [check("Follow-up", **{"repeat.interval": amount(3, "MONTH"), "repeat.includes_start": False, "repeat.end.count": 4})])

    quote = "Imaging repeats every 12 weeks after Registration until the end of study. The first occurrence is not specified."
    add("missing-first-repeat", "Missing repeat start stays unresolved", [quote],
        [fact("Imaging", quote, timing=timing("REPEAT"), repeat={"interval": amount(12, "WEEK"), "end": {"type": "OPEN_ENDED"}})],
        [check("Imaging", **{"repeat.includes_start": None})], required_issue_codes=["AMBIGUOUS_TIMING"])

    quote = "Treatment visits are Day 1 and Day 8 of each 21-day cycle for 6 cycles. Cycles stay on the nominal grid from Registration."
    cycle = {"start_anchor": "Registration", "length": amount(21)}
    add("cycle-days", "Cycle days and cycle count", [quote],
        [fact(f"Treatment Day {day}", quote, timing={"pattern": "CYCLE_DAY", "day": day, "cycle": cycle}, block="Treatment cycles",
              dependency_mode="NOMINAL", repeat={"interval": amount(21), "end": {"type": "COUNT", "count": 6}}) for day in (1, 8)],
        [check(f"Treatment Day {day}", **{"timing.day": day, "timing.cycle.length": amount(21), "repeat.end.count": 6}) for day in (1, 8)],
        metadata={"blocks": [{"name": "Treatment cycles", "dependency_mode": "NOMINAL", "evidence": [{"page": 1, "quote": quote}]}]})

    quote = "Follow-up starts 3 months after End of therapy, every 3 months through month 24 inclusive, then every 6 months through month 60 inclusive, then annually until end of study."
    segments = [{"interval": amount(3, "MONTH"), "end": amount(24, "MONTH"), "includes_start": False, "end_inclusive": True},
                {"interval": amount(6, "MONTH"), "start": amount(24, "MONTH"), "end": amount(60, "MONTH"), "end_inclusive": True},
                {"interval": amount(12, "MONTH"), "start": amount(60, "MONTH")}]
    add("phased-followup", "Phased recurrence stays one plan", [quote],
        [fact("Follow-up", quote, timing=timing("PHASED_REPEAT", "End of therapy", segments=segments),
              repeat={"interval": amount(12, "MONTH"), "end": {"type": "OPEN_ENDED"}})],
        [check("Follow-up", **{"timing.pattern": "PHASED_REPEAT", "timing.segments.0.interval": amount(3, "MONTH"),
                               "timing.segments.1.interval": amount(6, "MONTH"), "timing.segments.2.interval": amount(12, "MONTH")})])

    quote = "For Neuropathy, hold Drug B only; resume Drug B after the investigator explicitly confirms recovery."
    add("condition-hold", "Condition targets the stated component", [quote],
        [fact("Neuropathy", quote, kind="condition", timing={"pattern": "HOLD_RESUME"},
              condition={"name": "Neuropathy", "effect": "PAUSE", "target_components": ["Drug B"], "resume_trigger": "EXPLICIT_RESUME"})],
        [check("Neuropathy", "condition", **{"condition.effect": "PAUSE", "condition.target_components": ["Drug B"]})])

    quote = "MRI is an activity at Treatment Day 1 on even cycles only."
    add("activity-occurrences", "Activity occurrence restrictions", [quote],
        [fact("MRI", quote, kind="activity", at_visits=["Treatment Day 1"], occurrences={"parity": "EVEN"})],
        [check("MRI", "activity", **{"at_visits": ["Treatment Day 1"], "occurrences.parity": "EVEN"})])

    quote = "SAE reporting: submit serious adverse event reports within 24 hours; this is a reporting duty, not a patient visit."
    add("non-schedule", "Reporting duty is not a visit", [quote],
        [fact("SAE reporting", quote, kind="activity", statement_type="NON_SCHEDULE", non_schedule_reason="REPORTING")],
        [check("SAE reporting", "activity", **{"statement_type": "NON_SCHEDULE"})], forbidden_visit_names=["SAE reporting"])

    quote = "Site review: each site performs an administrative review every 3 months, not a patient assessment."
    add("site-scope", "Site rule cannot become a patient visit", [quote],
        [fact("Site review", quote, kind="activity", scope="SITE", statement_type="NON_SCHEDULE", non_schedule_reason="ADMINISTRATIVE")],
        [check("Site review", "activity", **{"scope": "SITE"})], forbidden_visit_names=["Site review"])

    first = "Echo occurs 4 weeks after Registration."
    second = "Contradictory instruction: Echo occurs 6 weeks after Registration. Neither statement supersedes the other."
    add("conflict", "Conflicting readings are both retained", [first, second],
        [fact("Echo", quote, timing=timing("OFFSET", offset=amount(weeks, "WEEK")), conflict_group="echo") for quote, weeks in [(first, 4), (second, 6)]],
        [check("Echo", **{"timing.offset": amount(weeks, "WEEK")}) for weeks in (4, 6)])

    quote = "Safety review occurs within 3 working days after Registration."
    add("business-days", "Unsupported business-day rule is visible", [quote],
        [fact("Safety review", quote, timing=timing("BUSINESS_DAYS", duration=amount(3)))],
        [check("Safety review", **{"timing.pattern": "BUSINESS_DAYS"})])

    quote = "Pre-op assessment occurs within 14 days before the planned Surgery date."
    add("planned-anchor", "Explicit planned anchor acceptance", [quote],
        [fact("Pre-op assessment", quote, timing={"pattern": "WITHIN_BEFORE", "from": {"name": "Surgery", "accepts": "PLANNED_OR_ACTUAL"}, "duration": amount(14)})],
        [check("Pre-op assessment", **{"timing.from.accepts": "PLANNED_OR_ACTUAL", "timing.from.name": "Surgery"})])

    quote = "Bone scan is pre-treatment; no reference date or timing interval is specified."
    add("unresolved", "Ambiguous wording remains unresolved", [quote],
        [fact("Bone scan", quote, timing={"pattern": "UNRESOLVED", "reason": "No reference date or interval is specified."})],
        [check("Bone scan", **{"timing.pattern": "UNRESOLVED"})], required_issue_codes=["AMBIGUOUS_TIMING"])

    heading = "Table 1: Schedule of Assessments. Visit 1 is 7 days after Registration; Visit 2 is 14 days after Registration."
    table = "Assessment | Visit 1 | Visit 2\nCBC | X[a] | X\na: CBC must also be collected 1 day before Surgery."
    note = "CBC must also be collected 1 day before Surgery."
    add("table-footnote", "Every marked cell and footnote", [heading, table],
        [fact("Visit 1", heading, timing=timing("OFFSET", offset=amount(7))),
         fact("Visit 2", heading, timing=timing("OFFSET", offset=amount(14))),
         fact("CBC pre-surgery", note, kind="activity", timing=timing("OFFSET", "Surgery", offset=amount(-1)))],
        [check("CBC pre-surgery", "activity", **{"timing.from.name": "Surgery", "timing.offset": amount(-1)})],
        table_cells=[{"table": "Table 1", "page": 1, "row": "CBC", "column": f"Visit {n}", "mark": "X", "footnotes": ["a"] if n == 1 else []} for n in (1, 2)],
        footnotes=[{"table": "Table 1", "page": 1, "label": "a", "text": note, "effect": "RULE", "effect_reason": "Additional CBC timing."}],
        minimum_table_cells=2, required_footnote_labels=["a"])

    quote = "There are two independent Schedules of Assessments: Main study and Substudy. Main visit is 7 days after Registration in Main study; Substudy visit is 14 days after Registration in Substudy."
    add("multiple-schedules", "Independent schedules remain separate", [quote],
        [fact("Main visit", quote, schedule_option="main", timing=timing("OFFSET", offset=amount(7))),
         fact("Substudy visit", quote, schedule_option="substudy", timing=timing("OFFSET", offset=amount(14)))],
        [check("Main visit", **{"timing.offset": amount(7)}), check("Substudy visit", **{"timing.offset": amount(14)})],
        metadata={"schedule_options": [{"id": "main", "label": "Main study"}, {"id": "substudy", "label": "Substudy"}]}, expected_schedule_options=2)
    return cases


CASES = build_cases()
BY_ID = {case.id: case for case in CASES}
