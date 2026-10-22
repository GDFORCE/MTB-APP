"""Transactional command/history scenarios (P8-04).

A sequence runs through the REAL application services (approval, patient enrolment, actual-occurrence
command, evaluations) on a fresh in-memory unit of work, then compares the committed calendar with
hand-written expectations: exact dates, exactly which visits moved, whether an evaluation was created,
and that every earlier evaluation and its projections still exist unchanged. Nothing is read from the
evaluator to build an expectation.
"""
from __future__ import annotations

from datetime import date
from uuid import NAMESPACE_URL, uuid5

from app.application.authoring import ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord
from app.application.patient_schedule import PatientScheduleService
from app.application.patients import PatientAnchorRecord, PatientRecord
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.models import OccurrenceFactKind, ScheduleStatus
from app.persistence.memory import InMemoryUnitOfWork

from .models import SequenceScenario


def _world(sequence: SequenceScenario, uow=None):
    uow = uow or InMemoryUnitOfWork()
    org = uuid5(NAMESPACE_URL, f"mtb/quality/sequence/{sequence.id}/org")
    reviewer = uuid5(NAMESPACE_URL, f"mtb/quality/sequence/{sequence.id}/reviewer")
    schedule = sequence.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.DRAFT

    def setup(tx):
        trial = TrialRecord(organization_id=org)
        tx.authoring.add_trial(trial)
        protocol = ProtocolRecord(organization_id=org, trial_id=trial.id, protocol_number=f"SEQ-{sequence.id}")
        tx.authoring.add_protocol(protocol)
        version = ProtocolVersionRecord(
            organization_id=org, trial_id=trial.id, protocol_id=protocol.id, version_label="1.0",
            document_name="synthetic.txt", document_uri="private://synthetic", document_hash="c" * 64)
        tx.authoring.add_protocol_version(version)
        definition = ScheduleDefinitionRecord(
            organization_id=org, trial_id=trial.id, protocol_id=protocol.id,
            protocol_version_id=version.id, name="Primary")
        tx.authoring.add_definition(definition)
        schedule.schedule_metadata.protocol_version_id = version.id
        tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
        tx.schedules.transition(schedule.schedule_version_id, to_status="IN_REVIEW")
        return trial.id
    trial_id = uow.run(setup)
    ScheduleReviewService(uow).approve_reviewed_table(
        schedule.schedule_version_id, reviewer_id=reviewer, reason="Synthetic engineering fixture reviewed")

    def enrol(tx):
        patient = PatientRecord(
            organization_id=org, trial_id=trial_id, patient_code="SEQ-1", external_patient_id=f"seq-{sequence.id}",
            current_schedule_version_id=schedule.schedule_version_id, authority_plane="MONGO")
        tx.patients.add(patient)
        for anchor in schedule.anchors:
            if anchor.code in sequence.anchors:
                tx.facts.add_anchor(PatientAnchorRecord(
                    organization_id=org, patient_id=patient.id, anchor_definition_id=anchor.id,
                    anchor_code=anchor.code, value_date=sequence.anchors[anchor.code], status="CONFIRMED",
                    source_type="SITE_ENTRY"))
        return patient.id
    return uow, org, reviewer, uow.run(enrol)


def _calendar(service, patient_id, org):
    return {row.logical_key: row for row in service.current_calendar(patient_id, organization_id=org)}


def run_sequence(sequence: SequenceScenario) -> dict:
    try:
        uow, org, actor, patient_id = _world(sequence)
        service = PatientScheduleService(uow)
        service.evaluate(patient_id, organization_id=org, horizon=sequence.horizon, today=sequence.as_of,
                         idempotency_key=f"{sequence.id}-initial")
        failures, unsafe = [], 0
        for number, step in enumerate(sequence.steps, start=1):
            before = _calendar(service, patient_id, org)
            evaluations_before = uow.read(lambda tx: _history(tx, patient_id, org))
            outcome = service.record_actual_occurrence(
                patient_id, organization_id=org, logical_key=step.logical_key, kind=OccurrenceFactKind.PERFORMED,
                visit_date=step.visit_date, treatment_date=step.treatment_date, actor_id=actor,
                reason="synthetic engineering step", horizon=sequence.horizon, today=step.recorded_on,
                idempotency_key=f"{sequence.id}-{number}")
            after = _calendar(service, patient_id, org)
            moved = sorted(key for key in after if key in before and before[key].planned_start != after[key].planned_start)
            if moved != sorted(step.expect_moved):
                failures.append({"step": number, "field": "moved", "expected": sorted(step.expect_moved), "actual": moved})
            if outcome.reevaluated != step.expect_reevaluated:
                failures.append({"step": number, "field": "reevaluated", "expected": step.expect_reevaluated,
                                 "actual": outcome.reevaluated})
            evaluations_after = uow.read(lambda tx: _history(tx, patient_id, org))
            expected_count = len(evaluations_before) + (1 if step.expect_reevaluated else 0)
            if len(evaluations_after) != expected_count:
                failures.append({"step": number, "field": "evaluation_count", "expected": expected_count,
                                 "actual": len(evaluations_after)})
            # history: every earlier evaluation keeps its identity and its projected dates
            for old_id, old_dates in evaluations_before.items():
                if evaluations_after.get(old_id) != old_dates:
                    failures.append({"step": number, "field": "history_changed", "evaluation": str(old_id)})
            for expected in step.expected:
                row = after.get(expected.logical_key)
                actual = None if row is None else {"planned": row.planned_start, "earliest": row.earliest,
                    "latest": row.latest, "completed": row.completed, "actual_visit": row.actual_visit_date,
                    "actual_treatment": row.actual_treatment_date}
                wanted = {"planned": expected.planned, "earliest": expected.earliest, "latest": expected.latest,
                          "completed": expected.completed, "actual_visit": expected.actual_visit,
                          "actual_treatment": expected.actual_treatment}
                wanted = {key: value for key, value in wanted.items()
                          if value is not None or key in {"planned"}}
                if actual is None or {key: actual[key] for key in wanted} != wanted:
                    failures.append({"step": number, "field": expected.logical_key, "expected": _plain(wanted),
                                     "actual": _plain(actual)})
                    if actual is not None and actual["planned"] is not None and actual["planned"] != expected.planned:
                        unsafe += 1                              # a generated target the oracle does not state
        return {"id": sequence.id, "kind": "SEQUENCE", "passed": not failures, "unsafe_dates": unsafe,
                "failures": failures, "safety_critical": sequence.safety_critical,
                "steps": len(sequence.steps)}
    except Exception as error:                                   # noqa: BLE001 - never leak payload text
        return {"id": sequence.id, "kind": "SEQUENCE", "passed": False, "unsafe_dates": 0,
                "error": type(error).__name__, "safety_critical": sequence.safety_critical}


def _history(tx, patient_id, org):
    patient = tx.patients.get(org, patient_id)
    result = {}
    for evaluation in tx.evaluations.list_evaluations(patient.id, patient.current_schedule_version_id):
        result[evaluation.id] = tuple(sorted(
            (row.logical_key, row.nominal_start_date, row.earliest_date, row.latest_date)
            for row in tx.evaluations.list_events(evaluation.id)))
    return result


def _plain(value):
    if value is None:
        return None
    return {key: (item.isoformat() if isinstance(item, date) else item) for key, item in value.items()}
