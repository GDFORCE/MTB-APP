"""P7-05: a manual-only rule never gets an automatic date; a person's recorded fact is what dependent visits use."""
from datetime import date
from uuid import uuid4

from app.application.operational_bridge import OperationalBridgeService
from app.application.patient_schedule import PatientScheduleService
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.models import OccurrenceFactKind
from app.observability.metrics import METRICS
import test_pdf_manual_rules as manual

pytest_plugins = ["tests.contract.conftest"]
TODAY = date(2026, 1, 20)


def native_patient(uow):
    draft, service, editor, reviewer = manual.setup(uow)
    manual.declare(draft, service, editor)
    manual.approve(draft, service, reviewer)
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=reviewer, reason="Reviewed manual schedule")
    enrolled = OperationalBridgeService(uow).enroll_patient(
        organization_id=draft.org, actor_id=uuid4(), canonical_trial_id=draft.trial.id,
        schedule_definition_id=draft.header().schedule_definition_id, external_patient_id=f"p-{uuid4().hex[:6]}",
        patient_code=f"M{uuid4().hex[:5]}", baseline_date=date(2026, 1, 1), horizon=date(2026, 12, 31))
    uow.run(lambda tx: tx.patients.save(tx.patients.get(draft.org, enrolled.patient_id).model_copy(
        update={"authority_plane": "MONGO"})))                         # a native plane patient (trusted enrolment)
    return draft, enrolled.patient_id, PatientScheduleService(uow)


def calendar(service, draft, patient):
    return {row.logical_key: row for row in service.current_calendar(patient, organization_id=draft.org)}


def test_the_manual_visit_has_no_date_stays_visible_and_blocks_what_depends_on_it(uow):
    draft, patient, service = native_patient(uow)
    rows = calendar(service, draft, patient)
    manual_row = rows["C1D1:0"]
    assert manual_row.planned_start is None and manual_row.status == "BLOCKED" and manual_row.rule_handling == "MANUAL_ONLY"
    assert rows["LAB_AFTER_TX:0"].planned_start is None                      # nothing invented downstream either
    assert rows["IMAGING_BASELINE:0"].planned_start == date(2026, 1, 31)      # unrelated visits are untouched


def test_a_recorded_manual_fact_is_used_by_dependents_and_the_manual_visit_still_gets_no_planned_date(uow):
    draft, patient, service = native_patient(uow)
    outcome = service.record_actual_occurrence(
        patient, organization_id=draft.org, logical_key="C1D1:0", kind=OccurrenceFactKind.PERFORMED,
        visit_date=date(2026, 1, 12), treatment_date=date(2026, 1, 12), actor_id=uuid4(), reason="Manual workflow done",
        horizon=date(2026, 12, 31), today=TODAY)
    assert outcome.reevaluated
    rows = calendar(service, draft, patient)
    done = rows["C1D1:0"]
    assert done.completed and done.actual_visit_date == date(2026, 1, 12) and done.actual_treatment_date == date(2026, 1, 12)
    assert done.planned_start is None and done.rule_handling == "MANUAL_ONLY"      # the fact is stored, no target invented
    assert rows["LAB_AFTER_TX:0"].planned_start == date(2026, 1, 19)                # recorded treatment + 7 days
    assert rows["IMAGING_BASELINE:0"].planned_start == date(2026, 1, 31)


def test_a_fact_outside_the_manual_rule_cannot_be_recorded_for_a_visit_that_does_not_exist(uow):
    draft, patient, service = native_patient(uow)
    import pytest
    with pytest.raises(Exception):
        service.record_actual_occurrence(
            patient, organization_id=draft.org, logical_key="GHOST:0", kind=OccurrenceFactKind.PERFORMED,
            visit_date=date(2026, 1, 12), actor_id=uuid4(), reason="x", horizon=date(2026, 12, 31), today=TODAY)


def test_manual_decisions_are_counted_by_action_without_any_patient_or_rule_detail(uow):
    before = {action: METRICS.manual_decisions.value(action=action) for action in ("DECLARE", "APPROVE")}
    native_patient(uow)
    assert METRICS.manual_decisions.value(action="DECLARE") == before["DECLARE"] + 1
    assert METRICS.manual_decisions.value(action="APPROVE") == before["APPROVE"] + 1
    assert "C1D1" not in METRICS.render().split("uctsm_manual_rule_decisions_total")[1].split("# HELP")[0]
