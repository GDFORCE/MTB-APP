"""Read models over the patient plane: deviation report, anchor status, enrolment preview.

Behaviour-preserving ports of ``deviation_service``, ``anchor_status_service`` and
``enrolment_preview`` on the repository ports. None of them writes anything.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.schedule.anchor_status import (
    AnchorRecord, AnchorStatusView, resolve_anchor_status,
)
from app.domain.schedule.deviation import (
    ConfinementDeviation, DeviationReport, assess_activity, assess_confinement, assess_visit,
)
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import PatientContext, UniversalSchedule, schedule_origin_anchor
from app.domain.schedule.timing import AnchorReference
from app.domain.schedule.validator import timing_references
from app.application.schedule_projection import feasibility_from_explanation

from .patient_schedule import SUPERSEDES_KEY, PatientScheduleService
from .ports import UnitOfWork
from .schedule_resolution import resolve_current_schedule_version


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


# ----------------------------------------------------------------- deviations
class DeviationService:
    """A patient deviation report from the current confirmed plan.

    Derived, never stored. Windows come from the schedule version the patient is
    pinned to, so a v1 patient is still assessed against v1 after v2 is approved.
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self.patient_schedules = PatientScheduleService(uow)

    def report(
        self, patient_id: UUID, *, organization_id: UUID, today: date | None = None,
    ) -> DeviationReport:
        today = today or date.today()

        def work(tx: UnitOfWork) -> DeviationReport:
            patient = tx.patients.get(organization_id, patient_id)
            if patient.current_schedule_version_id is None:
                raise ValueError("patient is not pinned to an approved schedule")
            version_id = patient.current_schedule_version_id
            report = DeviationReport(
                patient_id=str(patient_id), schedule_version_id=str(version_id), assessed_on=today)
            pointer = tx.evaluations.get_pointer(patient.id, version_id)
            if pointer is None or pointer.status != "ACTIVE" or pointer.current_evaluation_id is None:
                return report
            schedule = tx.schedules.get(version_id)
            names = {item.code: item.display_name for item in schedule.events}
            events = tx.evaluations.list_events(pointer.current_evaluation_id)
            actual_by_key = self._actual_dates(
                self.patient_schedules.occurrences_for(tx, patient.id, version_id))
            for event in events:
                feasibility, windows = feasibility_from_explanation(event.generation_reason)
                report.visits.append(assess_visit(
                    logical_key=event.logical_key, event_code=event.event_code,
                    display_name=names.get(event.event_code, event.event_code),
                    status=event.status, planned_date=event.nominal_start_date,
                    earliest_date=event.earliest_date, latest_date=event.latest_date,
                    actual_date=actual_by_key.get((event.event_code, event.occurrence_index)),
                    today=today, feasibility=feasibility,
                    feasible_windows=[(item.start, item.end) for item in windows] or None))
                for activity in sorted(
                        event.activities, key=lambda item: (item.sequence_number is None,
                                                            item.sequence_number or 0)):
                    report.activities.append(assess_activity(
                        logical_key=activity.logical_key, activity_code=activity.activity_code,
                        display_name=activity.activity_code or "Activity", status=activity.status,
                        planned_time=activity.planned_time, earliest_time=activity.earliest_time,
                        latest_time=activity.latest_time, actual_time=activity.actual_time))
            report.confinements = self._confinements(tx, patient, today)
            return report

        return self._uow.read(work)

    @staticmethod
    def _actual_dates(rows) -> dict[tuple[str, int], date]:
        """``rows``: the applicable version's occurrence facts (explicit lineage)."""
        # PT-02: a corrected record no longer acts; its correction does.
        superseded = {str((row.metadata or {}).get(SUPERSEDES_KEY))
                      for row in rows if (row.metadata or {}).get(SUPERSEDES_KEY)}
        actuals: dict[tuple[str, int], date] = {}
        for row in rows:
            if row.actual_date is None or str(row.id) in superseded:
                continue
            actuals[(row.event_code, row.occurrence_index)] = row.actual_date
        return actuals

    def _confinements(self, tx: UnitOfWork, patient, today: date) -> list[ConfinementDeviation]:
        try:
            evaluation = self.patient_schedules.evaluate_result(
                tx, patient, patient.current_schedule_version_id,
                horizon=today + timedelta(days=365))
        except ValueError:
            return []   # an unapproved or failing schedule has nothing to compare
        return [
            assess_confinement(
                episode_code=episode.episode_code, display_name=episode.display_name,
                planned_admission=_as_date(episode.planned_admission),
                actual_admission=_as_date(episode.actual_admission),
                planned_discharge=_as_date(episode.planned_discharge),
                actual_discharge=_as_date(episode.actual_discharge),
                extended=bool(episode.extended_by))
            for episode in evaluation.confinements
            if episode.status.value not in {"NOT_APPLICABLE", "WAITING_FOR_ANCHOR"}]


# --------------------------------------------------------------- anchor status
def _anchor_codes_in(value: object) -> set[str]:
    """Every anchor code a timing expression reaches, at any nesting depth."""
    found: set[str] = set()
    if isinstance(value, AnchorReference):
        found.add(value.code)
        return found
    if hasattr(value, "model_dump"):
        for field in getattr(type(value), "model_fields", {}):
            found |= _anchor_codes_in(getattr(value, field, None))
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found |= _anchor_codes_in(item)
    return found


class AnchorStatusService:
    """What is KNOWN about each anchor of a patient (doc 1 s22). Computes no date."""

    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self._service = PatientScheduleService(uow)

    def statuses(self, patient_id: UUID, *, organization_id: UUID) -> list[AnchorStatusView]:
        def work(tx: UnitOfWork) -> list[AnchorStatusView]:
            patient = tx.patients.get(organization_id, patient_id)
            if patient.current_schedule_version_id is None:
                return []
            schedule = tx.schedules.get(patient.current_schedule_version_id)
            required = self._required_anchor_codes(tx, schedule, patient)
            records = self._records_by_anchor(tx, patient_id)
            with_actuals = {
                row.event_code for row in self._service.occurrences_for(
                    tx, patient_id, patient.current_schedule_version_id)
                if row.actual_date is not None}
            return [resolve_anchor_status(
                anchor_code=anchor.code, display_name=anchor.display_name,
                records=records.get(anchor.code, []), applicable=anchor.code in required,
                source_event_code=anchor.source_event_code,
                source_event_has_actual=(anchor.source_event_code in with_actuals
                                         if anchor.source_event_code else False))
                for anchor in schedule.anchors]

        return self._uow.read(work)

    def _required_anchor_codes(self, tx: UnitOfWork, schedule: UniversalSchedule, patient) -> set[str]:
        """Anchors some part of THIS patient's schedule actually depends on."""
        applicable_codes = {item.code for item in schedule.events if not item.applicability}
        try:
            context = self._service.build_context(
                tx, patient, schedule, schedule.schedule_version_id)
            from app.domain.schedule.condition import TruthValue
            from app.domain.schedule.evaluator import _evaluate_applicability

            for event in schedule.events:
                if not event.applicability:
                    continue
                if _evaluate_applicability(event.applicability, context) != TruthValue.FALSE:
                    applicable_codes.add(event.code)
        except Exception:  # noqa: BLE001 - showing an unneeded anchor is recoverable
            applicable_codes = {item.code for item in schedule.events}
        required: set[str] = set()
        cycle_starts = {
            block.cycle_plan.code: block.cycle_plan.start_reference.code
            for block in schedule.repeat_blocks if block.cycle_plan is not None}
        for event in schedule.events:
            if event.code not in applicable_codes:
                continue
            required.update(_anchor_codes_in(event.timing))
            for rule in (event.recurrence, event.recurrence_plan, event.calendar_recurrence):
                if rule is not None and isinstance(rule.start_reference, AnchorReference):
                    required.add(rule.start_reference.code)
            cycle_reference = getattr(event.timing, "cycle_reference", None)
            if cycle_reference in cycle_starts:
                required.add(cycle_starts[cycle_reference])
        return required

    @staticmethod
    def _records_by_anchor(tx: UnitOfWork, patient_id: UUID) -> dict[str, list[AnchorRecord]]:
        """Oldest first. The last live record is current; earlier ones superseded."""
        grouped: dict[str, list] = {}
        for row in tx.facts.list_anchors(patient_id):
            grouped.setdefault(row.anchor_code, []).append(row)
        result: dict[str, list[AnchorRecord]] = {}
        for code, items in grouped.items():
            usable = [item for item in items if item.status != "PENDING_CONFIRMATION"]
            last_usable = usable[-1] if usable else None
            result[code] = [
                AnchorRecord(
                    value=item.value_datetime or item.value_date, record_status=item.status,
                    source_type=item.source_type, recorded_at=item.recorded_at,
                    # An unconfirmed candidate is not a value the schedule uses.
                    superseded=(item is not last_usable))
                for item in items]
        return result


# ------------------------------------------------------------ enrolment preview
def split_planned_anchors(
    dates: dict[str, date], sources: dict[str, str] | None,
) -> tuple[dict[str, date], dict[str, date]]:
    """PT-01: separate dates the site marked PLANNED from actual ones."""
    planned_codes = {code for code, source in (sources or {}).items()
                     if str(source).strip().upper() == "PLANNED"}
    actual = {code: value for code, value in dates.items() if code not in planned_codes}
    planned = {code: value for code, value in dates.items() if code in planned_codes}
    return actual, planned


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


def dimension_id(values: list[object], label: str | None) -> UUID | None:
    """Resolve a dimension label to its id; a missing or ambiguous one is refused."""
    if not label:
        return None
    normalized = label.strip().casefold()
    matches = [item for item in values if normalized in {
        item.code.casefold(), item.protocol_label.casefold(), item.display_name.casefold()}]
    if len(matches) != 1:
        raise ValueError(f"dimension assignment {label!r} is missing or ambiguous")
    return matches[0].id


def preview_enrolment(
    uow: UnitOfWork, *, organization_id: UUID, trial_id: UUID, definition_id: UUID,
    baseline: date, horizon: date, anchor_dates: dict[str, date] | None = None,
    arm_label: str | None = None, cohort_label: str | None = None,
    population_label: str | None = None, dimension_values: dict[str, list[str]] | None = None,
    expected_version_id: UUID | None = None, anchor_date_sources: dict[str, str] | None = None,
) -> dict:
    """Read-only enrolment preview, using the same approved schedule as enrolment."""
    def work(tx: UnitOfWork) -> dict:
        try:
            trial = tx.authoring.get_trial(organization_id, trial_id)
        except KeyError:
            trial = None
        if trial is None:
            raise ValueError("Canonical trial is not available in this organization.")
        version = resolve_current_schedule_version(tx, organization_id, trial_id, definition_id)
        if expected_version_id and version.id != expected_version_id:
            raise ValueError("The approved schedule changed. Reload and preview it again.")
        schedule = tx.schedules.get(version.id)

        def dimension_code(values, label):
            selected = dimension_id(values, label)
            return next((item.code for item in values if item.id == selected), None)

        origin = schedule_origin_anchor(schedule)
        if origin is not None and (anchor_date_sources or {}).get(origin.code, "").upper() == "PLANNED":
            raise ValueError("The schedule origin date is recorded as actual; it cannot be planned.")
        actual_dates, planned_dates = split_planned_anchors(
            enrollment_anchors(schedule, baseline, anchor_dates or {}), anchor_date_sources)
        context = PatientContext(
            patient_id=uuid4(), schedule_version_id=version.id, anchors=actual_dates,
            planned_anchors=planned_dates,
            arm_code=dimension_code(schedule.arms, arm_label),
            cohort_code=dimension_code(schedule.cohorts, cohort_label),
            population_code=dimension_code(schedule.populations, population_label),
            dimension_values=dimension_values or {})
        from app.domain.schedule.enrolment import validate_assignment

        selected = {key: values[0] for key, values in (dimension_values or {}).items()
                    if len(values) == 1}
        selected.update({key: value for key, value in {
            "ARM": context.arm_code, "COHORT": context.cohort_code,
            "POPULATION": context.population_code}.items() if value is not None})
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
                    event.explanation.get("reason")
                    or event.status.value.replace("_", " ").title())
            rows.append({
                "visit_template_id": f"{event.event_definition_id}:{event.occurrence_index}",
                "visit_number": len(rows) + 1,
                "name": definitions[event.event_definition_id].display_name,
                "event_code": event.event_code,
                "scheduled_date": timing.nominal_start if timing else None,
                "window_start": timing.earliest if timing else None,
                "window_end": timing.latest if timing else None,
                "status": event.status.value, "explanation": event.explanation,
                "manual_review_reason": reason})
        return {
            "schedule_version_id": str(version.id), "schedule_definition_id": str(definition_id),
            "baseline_date": baseline, "horizon": horizon, "visits": rows,
            "repeat_summaries": [item.model_dump(mode="json") for item in result.repeat_summaries]}

    return uow.read(work)
