"""Compare manually authored rules with the frozen, independently read BP11 PDF.

This is a subset comparison of the pure domain, not extraction, HTTP, UI,
blinding, a signed clinical corpus, or the unresolved source interpretations.
"""
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pytest

from _engine_cases import anchor, approved, context, evaluate, visit
from app.domain.schedule.condition import (
    AllCondition, ComparisonCondition, FieldOperand, LiteralOperand, TruthValue, evaluate_condition,
)
from app.domain.schedule.timing import (
    AnchorReference, NonNegativeTemporalAmount, OffsetTiming, PositiveTemporalAmount,
    ProtocolDayTiming, TemporalAmount, TimeUnit, Window, WithinTiming,
)

PACK = Path(__file__).resolve().parents[2] / "docs/protocol-benchmarks/bp11-301-v2.1"
ORACLE_BYTES = (PACK / "oracle-draft.json").read_bytes()
ORACLE = json.loads(ORACLE_BYTES)
CASES = {row["id"]: row for row in ORACLE["scenarios"]}
VISITS = [row for row in ORACLE["visits"] if row["study_day"] is not None]


def manual_calendar():
    events = []
    for row in VISITS:
        window = None if row["window_days"] is None else Window(
            before=NonNegativeTemporalAmount(value=row["window_days"], unit=TimeUnit.DAY),
            after=NonNegativeTemporalAmount(value=row["window_days"], unit=TimeUnit.DAY))
        events.append(visit(row["code"], ProtocolDayTiming(
            reference=AnchorReference(code="BASELINE"), day=row["study_day"]),
            window=window, visit_mode="TELEPHONE" if row["visit_number"] == 11 else None))
    schedule = approved(events, anchors=[anchor("BASELINE")])
    # The helper's APPROVED flag enables a pure evaluator fixture. No clinical
    # approval, signature, persisted draft or patient is created by this test.
    schedule.evidence[0].page_number = 49
    schedule.evidence[0].source_text = "Schedule of Assessments"
    return schedule


def test_expected_results_are_the_frozen_independent_draft_and_remain_unsigned():
    assert hashlib.sha256(ORACLE_BYTES).hexdigest() == "71bbf071011f0f4b6ee7a89316b6394066180b12d01ec2ca919c3993c74c82e3"
    assert ORACLE["clinical_release_qualified"] is False


@pytest.mark.parametrize("expected", VISITS, ids=lambda row: row["code"])
def test_calendar_targets_and_stated_windows_match_the_source(expected):
    schedule = manual_calendar()
    rows = {row.event_code: row for row in evaluate(schedule, context(schedule,
        anchors={"BASELINE": date.fromisoformat(ORACLE["baseline_example"])})).events}
    row = rows[expected["code"]]
    assert row.status.value == "RESOLVED"
    assert row.timing.nominal_start.isoformat() == expected["target_date"]
    if expected["window_days"] is not None:
        assert row.timing.earliest.isoformat() == expected["earliest"]
        assert row.timing.latest.isoformat() == expected["latest"]
    else:
        assert next(event for event in schedule.events if event.code == expected["code"]).window is None
    if expected["visit_number"] == 11:
        assert row.visit_mode == "TELEPHONE"


def test_no_baseline_means_no_invented_calendar_dates():
    schedule = manual_calendar()
    rows = evaluate(schedule, context(schedule)).events
    assert len(rows) == 10
    assert all(row.timing is None and row.status.value == "WAITING_FOR_ANCHOR" for row in rows)


def test_an_actual_dose_does_not_rebase_the_explicit_baseline_rules():
    schedule = manual_calendar()
    ctx = context(schedule, anchors={"BASELINE": date(2026, 1, 1)},
                  actual_event_values={"W04": [date(2026, 2, 1)]})
    rows = {row.event_code: row for row in evaluate(schedule, ctx).events}
    assert rows["W04"].timing.nominal_start == date(2026, 1, 29)
    assert rows["W08"].timing.nominal_start == date(2026, 2, 26)
    eos = next(row for row in VISITS if row["week"] == 40)
    assert rows[eos["code"]].timing.nominal_start == date(2026, 10, 8)


@pytest.mark.parametrize("case_id", ["TIME01", "TIME02", "TIME03"])
def test_pk_window_uses_the_explicit_planned_dose_clock(case_id):
    case = CASES[case_id]
    event = visit("PK", WithinTiming(reference=AnchorReference(code="PLANNED_DOSE"),
        duration=PositiveTemporalAmount(value=2, unit=TimeUnit.HOUR), direction="BEFORE"))
    schedule = approved([event], anchors=[anchor("PLANNED_DOSE")])
    row = evaluate(schedule, context(schedule, anchors={
        "PLANNED_DOSE": datetime.fromisoformat(case["given"]["planned_dose"])})).events[0]
    sample = datetime.fromisoformat(case["given"]["pk_sample"])
    assert (row.timing.earliest <= sample <= row.timing.latest) == case["expect"]["within_predose_two_hours"]


def test_observation_uses_actual_administration_and_requires_its_clock():
    case = CASES["TIME04"]
    event = visit("OBSERVATION_END", OffsetTiming(reference=AnchorReference(code="ACTUAL_ADMINISTRATION"),
        offset=TemporalAmount(value=60, unit=TimeUnit.MINUTE)))
    schedule = approved([event], anchors=[anchor("ACTUAL_ADMINISTRATION")])
    row = evaluate(schedule, context(schedule, anchors={"ACTUAL_ADMINISTRATION":
        datetime.fromisoformat(case["given"]["actual_administration"])})).events[0]
    assert row.timing.nominal_start.isoformat() == case["expect"]["earliest_observation_end"]
    waiting = evaluate(schedule, context(schedule, anchors={"ACTUAL_ADMINISTRATION": date(2026, 1, 29)})).events[0]
    assert waiting.timing is None and waiting.status.value == "WAITING_FOR_ANCHOR"
    assert waiting.explanation["reason_code"] == "TIME_OF_DAY_REQUIRED"


@pytest.mark.parametrize("case_id", ["ET01", "ET02"])
def test_explicit_early_termination_rule_waits_or_uses_last_actual(case_id):
    case = CASES[case_id]
    event = visit("EARLY_EOS", OffsetTiming(reference=AnchorReference(code="LAST_ACTUAL_DOSE"),
        offset=TemporalAmount(value=4, unit=TimeUnit.WEEK)), window=Window(
        before=NonNegativeTemporalAmount(value=7, unit=TimeUnit.DAY),
        after=NonNegativeTemporalAmount(value=7, unit=TimeUnit.DAY)))
    schedule = approved([event], anchors=[anchor("LAST_ACTUAL_DOSE")])
    actual = case["given"]["last_actual_administration"]
    ctx = context(schedule, anchors={"LAST_ACTUAL_DOSE": date.fromisoformat(actual)} if actual else {},
                  planned_anchors={"LAST_ACTUAL_DOSE": date(2026, 1, 29)})
    row = evaluate(schedule, ctx).events[0]
    if actual is None:
        assert row.timing is None and row.status.value == "WAITING_FOR_ANCHOR"
    else:
        assert row.timing.nominal_start.isoformat() == case["expect"]["target_date"]
        assert row.timing.earliest.isoformat() == case["expect"]["earliest"]
        assert row.timing.latest.isoformat() == case["expect"]["latest"]


@pytest.mark.parametrize("case_id", ["COND01", "COND02", "COND03", "COND04"])
def test_stool_predicate_is_risk_and_strictly_greater_than_twice_uln(case_id):
    case = CASES[case_id]
    condition = AllCondition(conditions=[
        ComparisonCondition(operator="EQUALS", left=FieldOperand(field="parasite_risk"),
                            right=LiteralOperand(value=True)),
        ComparisonCondition(operator="GT", left=FieldOperand(field="eosinophils_in_ULN_units"),
                            right=LiteralOperand(value=2)),
    ])
    actual = evaluate_condition(condition, case["given"])
    expected = {True: TruthValue.TRUE, False: TruthValue.FALSE, None: TruthValue.UNKNOWN}
    assert actual == expected[case["expect"]["condition_satisfied"]]
