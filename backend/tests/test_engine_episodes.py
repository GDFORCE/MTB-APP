"""WP3 - condition episodes (AT-10).

Low ANC: repeat a blood count weekly until resolved, and one follow-up visit
3 days after onset. First episode 05-Oct-2026 resolved 20-Oct; it recurs on
10-Nov. Synthetic.
"""

from __future__ import annotations

from datetime import date

from _engine_cases import anchor, approved, by_code, context, evaluate, visit

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.identity import EPISODE_INDEX_STRIDE
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState, PatientConditionStatus,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount, TimeUnit

FIRST = PatientConditionStatus(condition_code="ANC_LOW", state=ConditionState.RESOLVED,
                               occurrence_date=date(2026, 10, 5),
                               resolution_date=date(2026, 10, 20))
SECOND = PatientConditionStatus(condition_code="ANC_LOW", state=ConditionState.RECURRED,
                                occurrence_date=date(2026, 11, 10), occurrence_index=1)


def schedule():
    low = ComparisonCondition(operator="LT", left=FieldOperand(field="anc"),
                              right=LiteralOperand(value=1000))
    ok = ComparisonCondition(operator="GTE", left=FieldOperand(field="anc"),
                             right=LiteralOperand(value=1000))
    rule = ConditionalDefinition(
        code="ANC_LOW", protocol_label="ANC < 1000", display_name="Low ANC", condition=low,
        resolution_condition=ok, actions=[
            ConditionalAction(action_type="REPEAT_EVENT", target_code="CBC", condition=low,
                              parameters={"interval": {"value": 7, "unit": "DAY"}}),
            ConditionalAction(action_type="ADD_EVENT", target_code="REVIEW", condition=low)])
    at = OffsetTiming(reference=AnchorReference(code="ANC_ONSET"),
                      offset=TemporalAmount(value=3, unit=TimeUnit.DAY))
    return approved([visit("CBC", at, event_type="LABORATORY"), visit("REVIEW", at)],
                    anchors=[anchor("ANC_ONSET", anchor_type="CONDITION",
                                    source_condition_code="ANC_LOW")],
                    conditionals=[rule])


def run(current):
    built = schedule()
    ctx = context(built, conditions={"ANC_LOW": current})
    return by_code(evaluate(built, ctx, horizon=date(2026, 12, 1)))


def plan(items):
    return [(item.occurrence_index, item.status.value,
             item.timing.nominal_start if item.timing else None) for item in items]


def test_first_episode_keeps_the_historical_numbering():
    active = PatientConditionStatus(condition_code="ANC_LOW", state=ConditionState.ACTIVE,
                                    occurrence_date=date(2026, 10, 5),
                                    resolution_date=date(2026, 10, 20))
    result = run(active)
    assert plan(result["CBC"]) == [(0, "RESOLVED", date(2026, 10, 12)),
                                   (1, "RESOLVED", date(2026, 10, 19))]
    assert plan(result["REVIEW"]) == [(0, "RESOLVED", date(2026, 10, 8))]


def test_at10_a_recurrence_gets_its_own_identities():
    """Episode 1's occurrences are numbered from EPISODE_INDEX_STRIDE, so they
    can never take over - and overwrite - episode 0's persisted occurrences
    (the scenario test shows those rows stay exactly as they were)."""
    result = run(SECOND)
    assert plan(result["CBC"]) == [
        (EPISODE_INDEX_STRIDE, "RESOLVED", date(2026, 11, 17)),
        (EPISODE_INDEX_STRIDE + 1, "RESOLVED", date(2026, 11, 24)),
        (EPISODE_INDEX_STRIDE + 2, "RESOLVED", date(2026, 12, 1))]
    assert result["CBC"][0].explanation["condition_episode"] == {
        "condition": "ANC_LOW", "episode": 1, "ordinal": 0}
    assert plan(result["REVIEW"]) == [(EPISODE_INDEX_STRIDE, "RESOLVED", date(2026, 11, 13))]
    assert result["REVIEW"][0].explanation["condition_episode"]["episode"] == 1


def test_resolving_an_episode_is_bounded_by_its_resolution_date():
    """A resolved episode never generates repeats (existing doc 2 s17 rule);
    its recorded history is protected by the persistence layer."""
    resolved = FIRST
    assert run(resolved)["CBC"][0].status.value == "WAITING_FOR_CONDITION"
