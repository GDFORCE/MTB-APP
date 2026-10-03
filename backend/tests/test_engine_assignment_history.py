"""WP3 - dated assignment history (RL-11, AT-11).

Arm A and arm B each have a fortnightly visit from Mon 05-Oct-2026; a
crossover to B is effective 16-Nov-2026. Synthetic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from _engine_cases import anchor, approved, by_code, context, evaluate, visit

from app.domain.schedule.facts import assignment_periods
from app.domain.schedule.models import (
    ApplicabilityRule, AssignmentFact, RecurrenceRule, RecurrenceTermination, StudyDimension,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount, TimeUnit,
)

START = date(2026, 10, 5)
CROSSOVER = date(2026, 11, 16)


def arm_visit(code: str, arm: str | None):
    return visit(code, OffsetTiming(reference=AnchorReference(code="START"),
                                    offset=TemporalAmount(value=0, unit=TimeUnit.DAY)),
                 applicability=[ApplicabilityRule(dimension="ARM", values=[arm])] if arm else [],
                 recurrence=RecurrenceRule(
                     interval=PositiveTemporalAmount(value=14, unit=TimeUnit.DAY),
                     start_reference=AnchorReference(code="START"),
                     termination=RecurrenceTermination(type="COUNT", count=8)))


def schedule():
    arms = [StudyDimension(code=code, protocol_label=code, display_name=f"Arm {code}")
            for code in ("A", "B")]
    return approved([arm_visit("A_VISIT", "A"), arm_visit("B_VISIT", "B"), arm_visit("ALL", None)],
                    anchors=[anchor("START")], arms=arms)


def fact(fid: str, value: str | None, effective: date | None, recorded_day: int, **extra):
    return AssignmentFact(id=fid, dimension="ARM", value=value, effective_from=effective,
                          recorded_at=datetime(2026, 12, recorded_day, tzinfo=timezone.utc), **extra)


def run(history, arm="B"):
    built = schedule()
    ctx = context(built, anchors={"START": START}, arm_code=arm, assignment_history=history)
    return by_code(evaluate(built, ctx))


def dated(items):
    return [(item.occurrence_index, item.timing.nominal_start) for item in items if item.timing]


def test_crossover_evaluates_each_period_with_the_assignment_then_in_force():
    history = [fact("base", "A", None, 1), fact("x", "B", CROSSOVER, 1)]
    result = run(history)
    assert all(value < CROSSOVER for _, value in dated(result["A_VISIT"]))
    assert all(value >= CROSSOVER for _, value in dated(result["B_VISIT"]))
    # Identities are the series' own: B's first visit is occurrence 3 (16-Nov).
    assert dated(result["B_VISIT"])[0] == (3, CROSSOVER)
    assert [index for index, _ in dated(result["A_VISIT"])] == [0, 1, 2]
    assert result["A_VISIT"][0].explanation["assignment_period"]["values"] == {"ARM": "A"}
    # A rule that does not depend on the assignment is untouched.
    assert len(dated(result["ALL"])) == 8
    assert "assignment_period" not in result["ALL"][0].explanation


def test_late_entry_with_earlier_effective_date_changes_only_the_affected_period():
    on_time = run([fact("base", "A", None, 1), fact("x", "B", CROSSOVER, 1)])
    # Entered on 20-Dec, effective 02-Nov: only the period after 02-Nov changes.
    late = run([fact("base", "A", None, 1), fact("x", "B", date(2026, 11, 2), 20)])
    assert dated(late["A_VISIT"]) == [item for item in dated(on_time["A_VISIT"])
                                      if item[1] < date(2026, 11, 2)]
    assert dated(late["B_VISIT"])[0] == (2, date(2026, 11, 2))


def test_history_is_not_rewritten_and_a_correction_supersedes_by_id():
    history = [fact("base", "A", None, 1), fact("x", "B", CROSSOVER, 1),
               fact("fix", "B", date(2026, 11, 30), 5, supersedes="x")]
    periods = assignment_periods(history)
    assert [(item.start, item.values["ARM"]) for item in periods] == [
        (None, "A"), (date(2026, 11, 30), "B")]
    result = run(history)
    assert dated(result["B_VISIT"])[0] == (4, date(2026, 11, 30))
    # The superseded fact is still in the input (audit), it just no longer acts.
    assert {item.id for item in history} == {"base", "x", "fix"}


def test_no_history_evaluates_exactly_as_before():
    built = schedule()
    ctx = context(built, anchors={"START": START}, arm_code="B")
    plain = by_code(evaluate(built, ctx))
    assert plain["A_VISIT"][0].status.value == "NOT_APPLICABLE"
    assert len(dated(plain["B_VISIT"])) == 8


def test_unassigned_before_a_late_randomisation_is_not_backfilled():
    result = run([fact("base", None, None, 1), fact("rand", "A", CROSSOVER, 20)], arm="A")
    assert all(value >= CROSSOVER for _, value in dated(result["A_VISIT"]))
    assert dated(result["B_VISIT"]) == []
