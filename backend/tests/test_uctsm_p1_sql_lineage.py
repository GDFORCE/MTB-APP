"""P1-02 on the LIVE SQL path: characterization, not a behaviour change.

The SQL service builds a patient's context from occurrences across the patient's
schedule rows, keyed by event code. Inspection found that is not an accidental
leak: a patient only ever gains a second version through a confirmed impact
proposal (``impact_service`` is the sole writer of ``current_schedule_version_id``
after enrolment), and that is exactly when completed visits must carry across.
These tests pin that contract so a future version filter cannot silently break an
amendment, and prove a visit recorded in version 1 still drives a version-2 rule
that counts from its ACTUAL date only after the approved reassignment.
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import new_trial

from app.domain.schedule.models import Dependency, DependencyMode
from app.domain.schedule.timing import (
    AnchorReference, EventReference, OffsetTiming, TemporalAmount, TemporalBasis, TimeUnit,
)

REGISTRATION = date(2026, 10, 1)


def days(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.DAY)


def version(number: int, v3_offset: int):
    events = [
        visit("V2", OffsetTiming(reference=AnchorReference(code="REGISTRATION"), offset=days(9))),
        visit("V3", OffsetTiming(
            reference=EventReference(event_code="V2", temporal_basis=TemporalBasis.ACTUAL_VISIT),
            offset=days(v3_offset)),
            dependencies=[Dependency(source_event_code="V2", dependency_type="TEMPORAL")],
            dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT),
        visit("IMAGING", OffsetTiming(
            reference=AnchorReference(code="REGISTRATION"), offset=days(30))),
    ]
    schedule = approved(events, anchors=[anchor("REGISTRATION")])
    schedule.schedule_metadata.version_number = number
    return schedule


@pytest.fixture()
def trial():
    trial, engine = new_trial()
    yield trial
    trial.session.close()
    engine.dispose()


def test_a_recorded_visit_carries_into_the_amended_version_only_by_confirmed_reassignment(trial):
    v1 = trial.publish(version(1, 14))
    patient = trial.enrol("P-LINEAGE", anchors={"REGISTRATION": REGISTRATION})
    trial.record_actual(patient, "V2:0", date(2026, 10, 13))
    trial.evaluate(patient)
    assert trial.rows(patient)["V3:0"].nominal_start_date == date(2026, 10, 27)   # 13 Oct + 14
    assert trial.rows(patient)["IMAGING:0"].nominal_start_date == date(2026, 10, 31)

    # Approving version 2 does not move an enrolled patient or touch their facts.
    v2 = trial.publish(version(2, 21))
    trial.evaluate(patient)
    trial.session.refresh(patient)
    assert patient.current_schedule_version_id == v1.schedule_version_id
    assert trial.rows(patient)["V3:0"].nominal_start_date == date(2026, 10, 27)

    proposal = trial.preview(patient, target=v2.schedule_version_id, reason="Amendment 2")
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert changes["V3:0"]["after"]["date"] == "2026-11-03"                       # 13 Oct + 21
    assert changes["IMAGING:0"]["change"] == "UNCHANGED"

    rows = trial.confirm(proposal)
    trial.session.refresh(patient)
    assert patient.current_schedule_version_id == v2.schedule_version_id
    assert rows["V2:0"].status == "COMPLETED"
    assert rows["V3:0"].nominal_start_date == date(2026, 11, 3)
    assert rows["IMAGING:0"].nominal_start_date == date(2026, 10, 31)
