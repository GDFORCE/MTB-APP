"""Read-only enrollment preview, using the same approved schedule as enrollment."""
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import PatientContext, schedule_origin_anchor
from app.domain.schedule.validator import timing_references
from app.services.operational_bridge import OperationalBridgeService
from app.services.schedule_service import resolve_current_schedule_version


def enrollment_anchors(schedule, baseline: date, additional: dict[str, date]) -> dict[str, date]:
    origin = schedule_origin_anchor(schedule)
    if origin is None:
        raise ValueError("The approved schedule has no resolved schedule origin.")
    known = {anchor.code: anchor for anchor in schedule.anchors}
    for code in additional:
        if code not in known:
            raise ValueError(f"Unknown protocol anchor: {code}")
        if known[code].source_condition_code:
            raise ValueError(f"Record {code} through the condition confirmation workflow.")
    if origin.code in additional and additional[origin.code] != baseline:
        raise ValueError("The baseline and schedule origin dates disagree.")
    return {origin.code: baseline, **additional}


def preview_enrolment(
    session: Session, *, organization_id: UUID, trial_id: UUID,
    definition_id: UUID, baseline: date, horizon: date,
    anchor_dates: dict[str, date] | None = None, arm_label: str | None = None,
    cohort_label: str | None = None, population_label: str | None = None,
    dimension_values: dict[str, list[str]] | None = None,
    expected_version_id: UUID | None = None,
) -> dict:
    trial = session.get(db.Trial, trial_id)
    if trial is None or trial.organization_id != organization_id:
        raise ValueError("Canonical trial is not available in this organization.")
    version = resolve_current_schedule_version(session, trial_id, definition_id)
    if expected_version_id and version.id != expected_version_id:
        raise ValueError("The approved schedule changed. Reload and preview it again.")
    schedule = ScheduleRepository(session).get(version.id)
    def dimension_code(values, label):
        selected = OperationalBridgeService._dimension_id(values, label)
        return next((item.code for item in values if item.id == selected), None)
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=version.id,
        anchors=enrollment_anchors(schedule, baseline, anchor_dates or {}),
        arm_code=dimension_code(schedule.arms, arm_label),
        cohort_code=dimension_code(schedule.cohorts, cohort_label),
        population_code=dimension_code(schedule.populations, population_label),
        dimension_values=dimension_values or {},
    )
    from app.domain.schedule.enrolment import validate_assignment
    selected = {key: values[0] for key, values in (dimension_values or {}).items() if len(values) == 1}
    selected.update({key: value for key, value in {
        "ARM": context.arm_code, "COHORT": context.cohort_code, "POPULATION": context.population_code,
    }.items() if value is not None})
    problems = validate_assignment(schedule, selected)
    if problems:
        raise ValueError("; ".join(problems))
    result = ScheduleEvaluator().evaluate(schedule, context, horizon=horizon)
    definitions = {event.id: event for event in schedule.events}
    rows = []
    for event in result.events:
        timing = event.timing
        definition = definitions[event.event_definition_id]
        reason = ""
        if timing is not None and timing.nominal_start is None:
            if timing.earliest and timing.latest:
                reason = f"Allowed between {timing.earliest} and {timing.latest}"
            elif timing.latest:
                reason = f"Due no later than {timing.latest}"
            elif timing.earliest:
                reason = f"Allowed from {timing.earliest}"
        if timing is None:
            waiting = [anchor.display_name for anchor in schedule.anchors
                if anchor.code not in context.anchors and any(
                    getattr(reference, "code", None) == anchor.code
                    for reference in timing_references(definition.timing))]
            reason = ("Awaiting " + ", ".join(waiting) + " date") if waiting else str(
                event.explanation.get("reason") or event.status.value.replace("_", " ").title())
        rows.append({
            "visit_template_id": f"{event.event_definition_id}:{event.occurrence_index}",
            "visit_number": len(rows) + 1,
            "name": definitions[event.event_definition_id].display_name,
            "event_code": event.event_code,
            "scheduled_date": timing.nominal_start if timing else None,
            "window_start": timing.earliest if timing else None,
            "window_end": timing.latest if timing else None,
            "status": event.status.value,
            "explanation": event.explanation,
            "manual_review_reason": reason,
        })
    return {
        "schedule_version_id": str(version.id), "schedule_definition_id": str(definition_id),
        "baseline_date": baseline, "horizon": horizon, "visits": rows,
        "repeat_summaries": [item.model_dump(mode="json") for item in result.repeat_summaries],
    }
