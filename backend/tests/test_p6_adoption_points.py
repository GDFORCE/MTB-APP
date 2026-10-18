"""P6-04: the stated effective point is validated against the patient's CURRENT version and
triggered only by a real actual date - never by an assumed occurrence index."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.application.amendment_plan import _point_problems, trigger_state
from app.application.patients import OccurrenceRecord, PatientAnchorRecord, PatientRecord
from app.domain.schedule.amendment import (
    AdoptionMode, AdoptionPoint, actual_cycle_start, find_cycle_plan,
)
from app.domain.schedule.models import (
    CycleDependencyMode, CyclePlan, DependencyMode, RecurrenceTermination, RepeatBlock,
)
from app.domain.schedule.timing import (
    AnchorReference, CycleDayTiming, CycleSelector, PositiveTemporalAmount, TimeUnit,
)
from app.persistence.memory import InMemoryUnitOfWork

from _engine_cases import anchor, approved, visit

ORG = uuid4()


def schedule():
    events = [visit("DAY_1", CycleDayTiming(
        cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=1))]
    block = RepeatBlock(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment",
        event_codes=["DAY_1"], dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT,
        cycle_plan=CyclePlan(
            code="CYCLE", start_reference=AnchorReference(code="C1D1"),
            length=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            dependency_mode=CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
            start_event_code="DAY_1", termination=RecurrenceTermination(type="COUNT", count=6)))
    return approved(events, anchors=[anchor("C1D1")], repeat_blocks=[block])


class Fixture:
    def __init__(self):
        self.uow = InMemoryUnitOfWork()
        self.schedule = schedule()
        self.version = self.schedule.schedule_version_id
        self.patient = PatientRecord(
            organization_id=ORG, trial_id=uuid4(), patient_code="P-1",
            current_schedule_version_id=self.version, authority_plane="MONGO")
        self.uow.run(lambda tx: tx.patients.add(self.patient))

    def perform(self, index: int, on: date, code: str = "DAY_1"):
        self.uow.run(lambda tx: tx.facts.add_occurrence(OccurrenceRecord(
            organization_id=ORG, patient_id=self.patient.id, schedule_version_id=self.version,
            event_code=code, occurrence_index=index, logical_key=f"{code}:{index}",
            occurrence_type="VISIT", actual_date=on, status="COMPLETED",
            metadata={"fact_kind": "PERFORMED"})))

    def anchor(self, code: str, on: date, *, source: str = "ACTUAL_EVENT", status="CONFIRMED"):
        self.uow.run(lambda tx: tx.facts.add_anchor(PatientAnchorRecord(
            organization_id=ORG, patient_id=self.patient.id, anchor_definition_id=uuid4(),
            anchor_code=code, value_date=on, status=status, source_type=source)))

    def state(self, point: AdoptionPoint, today=date(2026, 12, 1)):
        return self.uow.read(lambda tx: trigger_state(
            tx, self.patient, self.schedule, self.version, point, today))


def cycle(n: int) -> AdoptionPoint:
    return AdoptionPoint(mode=AdoptionMode.ADOPT_AT_CYCLE, repeat_block_code="TREATMENT", cycle_number=n)


def test_a_cycle_start_is_the_nth_actual_date_not_occurrence_index_n_minus_one():
    f = Fixture()
    f.perform(0, date(2026, 10, 5))
    f.perform(2, date(2026, 11, 2))              # occurrence 1 was never recorded
    assert f.state(cycle(1)) == {
        "due": True, "effective_date": "2026-10-05", "detail": "cycle 1 actually started"}
    second = f.state(cycle(2))
    assert second["due"] and second["effective_date"] == "2026-11-02"   # N-1 = index 1 would be absent
    third = f.state(cycle(3))
    assert third["due"] is False and third["effective_date"] is None    # nothing invented


def test_a_person_recorded_cycle_anchor_wins_and_a_planned_one_is_ignored():
    f = Fixture()
    f.perform(0, date(2026, 10, 5))
    f.anchor("CYCLE_3_START", date(2026, 12, 1), source="PLANNED")
    assert f.state(cycle(3))["due"] is False                          # an expectation is not an actual
    f.anchor("CYCLE_3_START", date(2026, 11, 30))
    third = f.state(cycle(3))
    assert third["due"] and third["effective_date"] == "2026-11-30"


def test_cycle_one_may_start_from_the_plans_own_actual_anchor():
    f = Fixture()
    assert f.state(cycle(1))["due"] is False
    f.anchor("C1D1", date(2026, 10, 5))
    assert f.state(cycle(1))["effective_date"] == "2026-10-05"


def test_an_event_point_needs_that_exact_occurrence_to_be_performed():
    f = Fixture()
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="DAY_1", occurrence_index=1)
    f.perform(0, date(2026, 10, 5))
    waiting = f.state(point)
    assert waiting["due"] is False and waiting["effective_date"] is None
    f.perform(1, date(2026, 10, 26))
    assert f.state(point) == {"due": True, "effective_date": "2026-10-26",
                              "detail": "DAY_1:1 was performed"}


def test_a_date_point_is_due_on_the_date_and_not_before():
    f = Fixture()
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 1, 4))
    assert f.state(point, today=date(2027, 1, 3))["due"] is False
    assert f.state(point, today=date(2027, 1, 4)) == {
        "due": True, "effective_date": "2027-01-04", "detail": "2027-01-04 has arrived"}


@pytest.mark.parametrize("point,code", [
    (cycle(7), "POINT_CYCLE_MISSING"),
    (AdoptionPoint(mode=AdoptionMode.ADOPT_AT_CYCLE, repeat_block_code="NOPE", cycle_number=1),
     "POINT_CYCLE_UNKNOWN"),
    (AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="DAY_9", occurrence_index=0),
     "POINT_EVENT_UNKNOWN"),
    (AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="DAY_1", occurrence_index=2),
     "POINT_OCCURRENCE_MISSING"),
])
def test_a_point_that_does_not_exist_in_the_current_version_is_refused(point, code):
    f = Fixture()
    problems = f.uow.read(lambda tx: _point_problems(tx, f.patient, f.schedule, point))
    assert [p["code"] for p in problems] == [code]


def test_a_valid_cycle_point_has_no_problems_and_the_domain_contract_matches():
    f = Fixture()
    assert f.uow.read(lambda tx: _point_problems(tx, f.patient, f.schedule, cycle(6))) == []
    plan = find_cycle_plan(f.schedule, "TREATMENT")
    assert plan is not None and find_cycle_plan(f.schedule, "NOPE") is None
    # the SAME contract the evaluator uses, on plain values
    starts = [date(2026, 10, 5), date(2026, 11, 2)]
    assert actual_cycle_start(plan, 2, actual_anchors={}, start_event_actuals=starts) == date(2026, 11, 2)
    assert actual_cycle_start(plan, 3, actual_anchors={}, start_event_actuals=starts) is None
    assert actual_cycle_start(plan, 3, actual_anchors={"CYCLE_3_START": date(2026, 12, 2)},
                              start_event_actuals=starts) == date(2026, 12, 2)
    assert actual_cycle_start(plan, 2, actual_anchors={}, start_event_actuals=[starts[1], starts[0]]) \
        == date(2026, 11, 2)                          # date order, not recording order
