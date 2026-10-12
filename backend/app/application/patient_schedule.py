"""Patient schedule workflow on the repository ports (no database imports).

A behaviour-preserving port of ``app.services.schedule_service.PatientScheduleService``.
What changes is the storage model, not the clinical logic:

* projections are IMMUTABLE. The SQL service flipped ``PatientEvent.status`` in
  place when a visit was recorded; here a visit's completion is derived from the
  facts recorded against its LOGICAL key (patient, version, event, index), so it
  follows the visit through every re-evaluation (P1-03 by construction);
* the evaluation pointer carries a revision, and two concurrent evaluations are
  serialized by the store rather than by a row lock.

Deliberately NOT changed (they belong to P1-02 / P6 and are covered by their own
tests): actual dates are still collected per event code across every version of
the patient's history, positionally by date.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, TypeVar
from uuid import UUID, uuid4

from app.domain.schedule.anchor_status import record_is_actual
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.identity import activity_logical_key, event_logical_key
from app.domain.schedule.models import (
    AssignmentFact, ConditionState, EvaluationResult, HolidayCalendar, OccurrenceFact,
    OccurrenceFactKind, PatientConditionStatus, PatientContext, ProcessTransition,
    RollingHorizon, UnscheduledOccurrence,
)

from .errors import DuplicateRecordError, StaleRevisionError
from .patients import (
    ActivityEntryRecord, ConfinementEpisodeRecord, EvaluationRecord, OccurrenceRecord,
    PatientActivityEntry, PatientEventRecord, PatientRecord, PatientStateRecord,
    UnscheduledVisitRecord,
)
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

T = TypeVar("T")

PROCESS_TRANSITION_STATE_CODE = "uctsm:process_transition"
ASSIGNMENT_FACT_STATE_CODE = "uctsm:assignment_fact"
SITE_HOLIDAY_CALENDAR_STATE_CODE = "uctsm:site_holiday_calendar"
RESERVED_STATE_CODES = frozenset({
    PROCESS_TRANSITION_STATE_CODE, ASSIGNMENT_FACT_STATE_CODE, SITE_HOLIDAY_CALENDAR_STATE_CODE,
})

FACT_KIND_KEY = "fact_kind"
SUPERSEDES_KEY = "supersedes_occurrence_id"
EFFECTIVE_AT_KEY = "effective_at"
BOOKED_KEY = "booked_date"
END_KEY = "end_date"

_RETRIES = 3
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def occurrence_fact_from_record(row: OccurrenceRecord) -> OccurrenceFact | None:
    """The typed fact one occurrence record states, or None when it records
    nothing the engine acts on."""
    metadata = row.metadata or {}
    kind = metadata.get(FACT_KIND_KEY)
    value: date | datetime | None = row.actual_date
    if kind == OccurrenceFactKind.BOOKED.value or row.occurrence_type == "BOOKING":
        kind = OccurrenceFactKind.BOOKED.value
        booked = metadata.get(BOOKED_KEY) or (row.scheduled_date.isoformat() if row.scheduled_date else None)
        value = date.fromisoformat(str(booked)[:10]) if booked else None
    elif kind is None:
        if row.actual_date is not None:
            kind = OccurrenceFactKind.PERFORMED.value
        elif row.status == "MISSED":
            kind = OccurrenceFactKind.MISSED.value
        else:
            return None
    if kind in {OccurrenceFactKind.PERFORMED.value, OccurrenceFactKind.BOOKED.value} and value is None:
        return None
    end = metadata.get(END_KEY)
    effective = metadata.get(EFFECTIVE_AT_KEY)
    return OccurrenceFact(
        id=str(row.id), event_code=row.event_code, occurrence_index=row.occurrence_index,
        kind=OccurrenceFactKind(kind), value=value,
        end_value=date.fromisoformat(str(end)[:10]) if end else None,
        effective_at=date.fromisoformat(str(effective)[:10]) if effective else value,
        recorded_at=row.recorded_at or _EPOCH,
        supersedes=metadata.get(SUPERSEDES_KEY),
        source=metadata.get("source"),
    )


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _as_instant(value: date | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


class PatientScheduleService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    # ------------------------------------------------------------------ plumbing
    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        """One atomic operation, retried when a concurrent writer won the race.

        A stale pointer or a lost sequence/idempotency slot means somebody else
        evaluated this patient first; re-running recalculates against what they
        wrote, so two commits can never both be "the latest" evaluation.
        """
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------------ reading
    @staticmethod
    def result_date(item: object | None) -> date | None:
        if item is None:
            return None
        value = getattr(item, "nominal_start", None)
        return _as_date(value)

    def current_events(
        self, tx: UnitOfWork, patient_id: UUID, schedule_version_id: UUID,
    ) -> list[PatientEventRecord]:
        """The latest evaluation's projections with recorded outcomes applied.

        The stored projection is immutable; "completed" / "missed" is what the
        newest un-superseded fact for that logical visit says.
        """
        pointer = tx.evaluations.get_pointer(patient_id, schedule_version_id)
        if pointer is None or pointer.current_evaluation_id is None:
            return []
        events = tx.evaluations.list_events(pointer.current_evaluation_id)
        outcomes = self._outcomes(tx, patient_id)
        return [self._overlay(item, outcomes) for item in events]

    def _outcomes(self, tx: UnitOfWork, patient_id: UUID) -> dict[tuple[str, int], str]:
        """logical visit -> COMPLETED | MISSED, from the newest effective fact."""
        rows = tx.facts.list_occurrences(patient_id)
        superseded = {str((row.metadata or {}).get(SUPERSEDES_KEY))
                      for row in rows if (row.metadata or {}).get(SUPERSEDES_KEY)}
        outcome: dict[tuple[str, int], str] = {}
        for row in rows:  # oldest first, so the newest wins
            if str(row.id) in superseded:
                continue
            if row.status in {"COMPLETED", "MISSED"}:
                outcome[(row.event_code, row.occurrence_index)] = row.status
        return outcome

    @staticmethod
    def _overlay(item: PatientEventRecord, outcomes: dict[tuple[str, int], str]) -> PatientEventRecord:
        status = outcomes.get((item.event_code, item.occurrence_index))
        return item.model_copy(update={"status": status}) if status else item

    def protected_event_keys(
        self, tx: UnitOfWork, patient_id: UUID, events: list[PatientEventRecord],
    ) -> set[tuple[str, int]]:
        """Visits whose dates and status a re-evaluation must carry forward."""
        with_actual = {
            (row.event_code, row.occurrence_index)
            for row in tx.facts.list_occurrences(patient_id) if row.actual_date is not None}
        protected = {
            (item.event_code, item.occurrence_index) for item in events
            if item.status in {"COMPLETED", "MISSED"} or item.protected_history}
        protected.update(
            key for key in with_actual
            if any((item.event_code, item.occurrence_index) == key for item in events))
        return protected

    def patient_conditions(
        self, tx: UnitOfWork, patient_id: UUID,
        *, overrides: dict[str, PatientConditionStatus] | None = None,
    ) -> dict[str, PatientConditionStatus]:
        conditions: dict[str, PatientConditionStatus] = {}
        for row in tx.facts.list_current_conditions(patient_id):
            conditions[row.condition_code] = PatientConditionStatus(
                condition_code=row.condition_code, state=ConditionState(row.state),
                occurrence_date=row.occurrence_date, resolution_date=row.resolution_date,
                occurrence_index=row.occurrence_index,
            )
        conditions.update(overrides or {})
        return conditions

    # ------------------------------------------------------------------ context
    def build_context(
        self, tx: UnitOfWork, patient: PatientRecord, schedule, schedule_version_id: UUID,
        *,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> PatientContext:
        # Doc C6: planned and actual anchor values are kept apart.
        anchors: dict[str, date | datetime] = {}
        planned_anchors: dict[str, date | datetime] = {}
        selection_by_code = {
            item.code: str((item.derivation_rule or {}).get("selection", "LAST")).upper()
            for item in schedule.anchors}
        for row in tx.facts.list_anchors(patient.id, statuses={"CONFIRMED", "PROVISIONAL"}):
            value = row.value_datetime or row.value_date
            target = anchors if record_is_actual(row.status, row.source_type) else planned_anchors
            if selection_by_code.get(row.anchor_code, "LAST") == "FIRST" and row.anchor_code in target:
                continue
            target[row.anchor_code] = value
        anchors.update(anchor_overrides or {})
        planned_anchors.update(planned_anchor_overrides or {})

        occurrences = tx.facts.list_occurrences(patient.id)
        # PT-02: a correction supersedes by id; the corrected record stays stored.
        superseded = {
            str((row.metadata or {}).get(SUPERSEDES_KEY))
            for row in occurrences if (row.metadata or {}).get(SUPERSEDES_KEY)
        }
        actual_event_values: dict[str, list[date | datetime]] = {}
        occurrence_facts: list[OccurrenceFact] = []
        for row in occurrences:
            fact = occurrence_fact_from_record(row)
            if fact is not None:
                occurrence_facts.append(fact)
            if row.actual_date is None or str(row.id) in superseded:
                continue
            if fact is not None and fact.kind != OccurrenceFactKind.PERFORMED:
                continue
            actual_event_values.setdefault(row.event_code, []).append(row.actual_date)
        for values in actual_event_values.values():
            values.sort()

        activity_actuals: dict[str, date | datetime] = {}
        activity_statuses: dict[str, str] = {}
        for entry in tx.facts.list_current_activity_entries(patient.id):
            activity_statuses[entry.logical_key] = entry.status
            if entry.actual_time is not None:
                activity_actuals[entry.logical_key] = entry.actual_time
            else:
                activity_actuals.pop(entry.logical_key, None)

        state: dict[str, object] = {}
        state_effective_at: dict[str, datetime] = {}
        process_transitions: list[ProcessTransition] = []
        assignment_history: list[AssignmentFact] = []
        site_calendars: dict[str, HolidayCalendar] = {}
        for row in tx.facts.list_states(patient.id):
            if row.state_code == PROCESS_TRANSITION_STATE_CODE:
                process_transitions.append(ProcessTransition.model_validate(row.state_value))
                continue
            if row.state_code == ASSIGNMENT_FACT_STATE_CODE:
                assignment_history.append(AssignmentFact.model_validate(row.state_value))
                continue
            if row.state_code == SITE_HOLIDAY_CALENDAR_STATE_CODE:
                calendar = HolidayCalendar.model_validate(row.state_value)
                site_calendars[calendar.code] = calendar
                continue
            state[row.state_code] = row.state_value
            state_effective_at[row.state_code] = row.effective_at
        process_transitions.extend(process_transition_overrides or [])
        assignment_history.extend(assignment_fact_overrides or [])
        for code, (value, effective_at) in (state_overrides or {}).items():
            state[code] = value
            state_effective_at[code] = effective_at

        # Doc 10 s14: unscheduled visits the site created are INPUTS to evaluation.
        unscheduled = [
            UnscheduledOccurrence(
                event_code=row.event_code, occurred_on=row.occurred_on, reason=row.reason,
                visit_mode=row.visit_mode, created_by=row.created_by, recorded_at=row.recorded_at)
            for row in tx.facts.list_unscheduled_visits(patient.id)
        ]
        arm_code = patient.arm_code or self._code_of(schedule.arms, patient.arm_id)
        cohort_code = patient.cohort_code or self._code_of(schedule.cohorts, patient.cohort_id)
        population_code = patient.population_code or self._code_of(
            schedule.populations, patient.population_id)
        return PatientContext(
            patient_id=patient.id, schedule_version_id=schedule_version_id,
            anchors=anchors, planned_anchors=planned_anchors,
            event_values={key: list(values) for key, values in actual_event_values.items()},
            actual_event_values=actual_event_values,
            activity_actuals=activity_actuals, activity_statuses=activity_statuses,
            conditions=self.patient_conditions(tx, patient.id, overrides=condition_overrides),
            unscheduled_occurrences=unscheduled, state=state,
            state_effective_at=state_effective_at, arm_code=arm_code,
            cohort_code=cohort_code, population_code=population_code,
            dimension_values=patient.dimension_values or {},
            occurrence_facts=occurrence_facts, process_transitions=process_transitions,
            assignment_history=assignment_history, holiday_calendars=list(site_calendars.values()),
        )

    @staticmethod
    def _code_of(dimensions, dimension_id: UUID | None) -> str | None:
        if dimension_id is None:
            return None
        return next((item.code for item in dimensions if item.id == dimension_id), None)

    def evaluate_result(
        self, tx: UnitOfWork, patient: PatientRecord, schedule_version_id: UUID, *, horizon: date,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        rolling: RollingHorizon | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> EvaluationResult:
        schedule = tx.schedules.get(schedule_version_id)
        context = self.build_context(
            tx, patient, schedule, schedule_version_id,
            condition_overrides=condition_overrides, anchor_overrides=anchor_overrides,
            state_overrides=state_overrides, planned_anchor_overrides=planned_anchor_overrides,
            process_transition_overrides=process_transition_overrides,
            assignment_fact_overrides=assignment_fact_overrides)
        return ScheduleEvaluator().evaluate(schedule, context, horizon=horizon, rolling=rolling)

    # ------------------------------------------------------------------ evaluate
    def evaluate(
        self, patient_id: UUID, *, organization_id: UUID, horizon: date,
        schedule_version_id: UUID | None = None, idempotency_key: str | None = None,
        prior_events_override: list[PatientEventRecord] | None = None,
        rolling: RollingHorizon | None = None, actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> tuple[EvaluationRecord, list[PatientEventRecord]]:
        """Evaluate one patient against a schedule version (AD-4d: the caller may
        name the version explicitly; otherwise the pinned one is used)."""
        return self._run(lambda tx: self._evaluate(
            tx, patient_id, organization_id=organization_id, horizon=horizon,
            schedule_version_id=schedule_version_id, idempotency_key=idempotency_key,
            prior_events_override=prior_events_override, rolling=rolling,
            actor_id=actor_id, reason=reason))

    def _evaluate(
        self, tx: UnitOfWork, patient_id: UUID, *, organization_id: UUID, horizon: date,
        schedule_version_id: UUID | None, idempotency_key: str | None,
        prior_events_override: list[PatientEventRecord] | None,
        rolling: RollingHorizon | None, actor_id: UUID | None, reason: str | None,
    ) -> tuple[EvaluationRecord, list[PatientEventRecord]]:
        patient = tx.patients.get(organization_id, patient_id)  # KeyError -> "patient not found"
        resolved = schedule_version_id if schedule_version_id is not None \
            else patient.current_schedule_version_id
        if resolved is None:
            raise ValueError("patient is not pinned to an approved schedule")
        if idempotency_key:
            existing = tx.evaluations.find_by_idempotency(patient_id, idempotency_key)
            if existing is not None:
                return existing, tx.evaluations.list_events(existing.id)
        schedule = tx.schedules.get(resolved)
        prior = prior_events_override if prior_events_override is not None \
            else self.current_events(tx, patient.id, schedule.schedule_version_id)
        started = utc_now()
        result = self.evaluate_result(tx, patient, schedule.schedule_version_id, horizon=horizon,
                                      rolling=rolling)
        pointer = tx.evaluations.get_pointer(patient.id, schedule.schedule_version_id)
        evaluation_id = uuid4()
        protocol_version_id = schedule.schedule_metadata.protocol_version_id

        prior_by_key = {event_logical_key(item.event_code, item.occurrence_index): item for item in prior}
        protected = self.protected_event_keys(tx, patient.id, prior)
        reconciliation = {key: 0 for key in (
            "UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
        events: list[PatientEventRecord] = []
        for item in result.events:
            timing = item.timing
            logical_key = event_logical_key(item.event_code, item.occurrence_index)
            previous = prior_by_key.pop(logical_key, None)
            keep = previous is not None and (
                (previous.event_code, previous.occurrence_index) in protected)
            status = previous.status if keep else item.status.value
            nominal_start = previous.nominal_start_date if keep else (
                _as_date(timing.nominal_start) if timing else None)
            nominal_end = previous.nominal_end_date if keep else (
                _as_date(timing.nominal_end) if timing else None)
            earliest = previous.earliest_date if keep else (_as_date(timing.earliest) if timing else None)
            latest = previous.latest_date if keep else (_as_date(timing.latest) if timing else None)
            if previous is None:
                change = "ADDED"
            elif keep:
                change = "PROTECTED"
            elif previous.nominal_start_date != nominal_start:
                change = "MOVED"
            elif previous.status != status:
                change = "STATUS_CHANGED"
            else:
                change = "UNCHANGED"
            reconciliation[change] += 1
            explanation = dict(item.explanation)
            explanation["reconciliation"] = {
                "change": change,
                "supersedes_patient_event_id": str(previous.id) if previous else None,
            }
            events.append(PatientEventRecord(
                organization_id=organization_id, patient_id=patient.id, evaluation_id=evaluation_id,
                schedule_version_id=schedule.schedule_version_id, protocol_version_id=protocol_version_id,
                event_definition_id=item.event_definition_id, event_code=item.event_code,
                occurrence_index=item.occurrence_index,
                logical_occurrence_id=(previous.logical_occurrence_id if previous else uuid4()),
                logical_key=logical_key,
                supersedes_patient_event_id=previous.id if previous else None,
                protected_history=keep, status=status,
                nominal_start_date=nominal_start, nominal_end_date=nominal_end,
                earliest_date=earliest, latest_date=latest,
                timing_resolution=(previous.timing_resolution if keep and previous else (
                    timing.model_dump(mode="json") if timing else {})),
                applicability_result={"value": item.applicability_result},
                condition_result={"value": item.condition_result},
                dependency_result=item.dependency_result, generation_reason=explanation,
                visit_mode=item.visit_mode, unscheduled_reason=item.unscheduled_reason,
                activities=self._activity_entries(
                    item, schedule.schedule_version_id, protocol_version_id),
            ))
        for logical_key, previous in prior_by_key.items():
            keep = (previous.event_code, previous.occurrence_index) in protected
            change = "PROTECTED" if keep else "CANCELLED"
            reconciliation[change] += 1
            events.append(PatientEventRecord(
                organization_id=organization_id, patient_id=patient.id, evaluation_id=evaluation_id,
                schedule_version_id=schedule.schedule_version_id, protocol_version_id=protocol_version_id,
                event_definition_id=previous.event_definition_id, event_code=previous.event_code,
                occurrence_index=previous.occurrence_index,
                logical_occurrence_id=previous.logical_occurrence_id, logical_key=logical_key,
                supersedes_patient_event_id=previous.id, protected_history=keep,
                status=previous.status if keep else "CANCELLED",
                nominal_start_date=previous.nominal_start_date,
                nominal_end_date=previous.nominal_end_date,
                earliest_date=previous.earliest_date, latest_date=previous.latest_date,
                timing_resolution=previous.timing_resolution,
                applicability_result=previous.applicability_result,
                condition_result=previous.condition_result,
                dependency_result=previous.dependency_result,
                generation_reason={**(previous.generation_reason or {}), "reconciliation": {
                    "change": change, "supersedes_patient_event_id": str(previous.id)}},
            ))
        confinements = self._confinements(
            tx, patient, evaluation_id, result.confinements, pointer=pointer,
            schedule_version_id=schedule.schedule_version_id, protocol_version_id=protocol_version_id)
        completed = utc_now()
        evaluation = EvaluationRecord(
            id=evaluation_id, organization_id=organization_id, patient_id=patient.id,
            schedule_version_id=schedule.schedule_version_id, idempotency_key=idempotency_key,
            evaluator_version=result.evaluator_version, status="COMPLETED",
            input_snapshot=result.input_snapshot,
            output_summary={
                "statuses": [item.status.value for item in result.events],
                "count": len(result.events), "persisted_count": len(events),
                "reconciliation": reconciliation},
            started_at=started, completed_at=completed, evaluated_at=result.evaluated_at,
            sequence=tx.evaluations.count_evaluations(patient.id, schedule.schedule_version_id) + 1,
        )
        tx.evaluations.save_evaluation(
            evaluation, events, confinements,
            expected_pointer_revision=pointer.revision if pointer is not None else None)
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=actor_id, action="PATIENT_SCHEDULE_EVALUATED",
            entity_type="PATIENT", entity_id=patient.id, before=None,
            after={"evaluation_id": str(evaluation.id), "event_count": len(events),
                   "reconciliation": reconciliation},
            reason=reason, metadata={"evaluator_version": result.evaluator_version}))
        return evaluation, events

    @staticmethod
    def _activity_entries(
        item: object, schedule_version_id: UUID, protocol_version_id: UUID | None,
    ) -> list[PatientActivityEntry]:
        """Day-wise activity schedule of one visit occurrence."""
        event_code = getattr(item, "event_code", None)
        occurrence_index = getattr(item, "occurrence_index", 0)
        entries = []
        for activity in getattr(item, "activities", None) or []:
            timing = activity.timing
            entries.append(PatientActivityEntry(
                activity_definition_id=activity.activity_definition_id,
                activity_code=activity.activity_code,
                logical_key=activity_logical_key(
                    event_code, occurrence_index,
                    activity.activity_code or str(activity.activity_definition_id)),
                status=activity.status.value, requiredness=activity.requiredness.value,
                sequence_number=activity.sequence_number,
                planned_time=_as_instant(timing.nominal_start) if timing else None,
                earliest_time=_as_instant(timing.earliest) if timing else None,
                latest_time=_as_instant(timing.latest) if timing else None,
                actual_time=_as_instant(activity.actual_time),
                timing_resolution=timing.model_dump(mode="json") if timing else {},
                generation_reason=activity.explanation,
                schedule_version_id=schedule_version_id, protocol_version_id=protocol_version_id))
        return entries

    def _confinements(
        self, tx: UnitOfWork, patient: PatientRecord, evaluation_id: UUID, confinements: list,
        *, pointer, schedule_version_id: UUID, protocol_version_id: UUID | None,
    ) -> list[ConfinementEpisodeRecord]:
        """AD-8: persist this evaluation's confinement episodes. A DISCHARGED
        episode's dates are protected across re-evaluation, found by its code."""
        prior_by_key: dict[str, ConfinementEpisodeRecord] = {}
        if pointer is not None and pointer.current_evaluation_id is not None:
            prior_by_key = {
                item.logical_key: item
                for item in tx.evaluations.list_confinements(pointer.current_evaluation_id)}
        rows: list[ConfinementEpisodeRecord] = []
        for item in confinements:
            previous = prior_by_key.get(item.episode_code)
            keep = previous is not None and previous.status == "DISCHARGED"
            rows.append(ConfinementEpisodeRecord(
                organization_id=patient.organization_id, patient_id=patient.id,
                evaluation_id=evaluation_id, episode_definition_id=item.episode_definition_id,
                episode_code=item.episode_code, logical_key=item.episode_code,
                supersedes_id=previous.id if previous else None, protected_history=keep,
                status=(previous.status if keep else item.status.value),
                planned_admission=(previous.planned_admission if keep else _as_date(item.planned_admission)),
                actual_admission=(previous.actual_admission if keep else _as_date(item.actual_admission)),
                planned_discharge=(previous.planned_discharge if keep else _as_date(item.planned_discharge)),
                actual_discharge=(previous.actual_discharge if keep else _as_date(item.actual_discharge)),
                extended_by=(list(previous.extended_by) if keep else list(item.extended_by)),
                explanation=(dict(previous.explanation) if keep else dict(item.explanation)),
                schedule_version_id=schedule_version_id, protocol_version_id=protocol_version_id,
            ))
        return rows

    # ------------------------------------------------------------------ recording
    def create_unscheduled_visit(
        self, patient_id: UUID, *, organization_id: UUID, event_code: str, occurred_on: date,
        reason: str, visit_mode: str | None = None, actor_id: UUID | None = None,
    ) -> UnscheduledVisitRecord:
        """One occurrence of a protocol-defined unscheduled visit (doc 10 s13-s15)."""
        if not reason or not reason.strip():
            raise ValueError(
                "an unscheduled visit needs a reason; without one it cannot be "
                "reviewed against the protocol later")

        def work(tx: UnitOfWork) -> UnscheduledVisitRecord:
            patient = tx.patients.get(organization_id, patient_id)
            if patient.current_schedule_version_id is None:
                raise ValueError("patient has no active schedule version")
            schedule = tx.schedules.get(patient.current_schedule_version_id)
            definition = next((item for item in schedule.events if item.code == event_code), None)
            if definition is None:
                raise KeyError(f"event {event_code} is not defined in this patient's schedule")
            if (definition.activation or "SCHEDULED") != "ON_DEMAND":
                raise ValueError(
                    f"{event_code} is a scheduled protocol visit; only a protocol-defined "
                    "unscheduled visit can be created on demand")
            allowed = list(definition.allowed_visit_modes or [])
            if visit_mode is not None and allowed and visit_mode not in allowed:
                raise ValueError(
                    f"visit mode {visit_mode!r} is not permitted for {event_code}; "
                    f"the protocol allows {', '.join(sorted(allowed))}")
            record = UnscheduledVisitRecord(
                organization_id=organization_id, patient_id=patient.id,
                event_definition_id=definition.id, event_code=event_code, occurred_on=occurred_on,
                reason=reason.strip(), visit_mode=visit_mode, created_by=actor_id)
            tx.facts.add_unscheduled_visit(record)
            return record

        return self._run(work)

    def record_activity(
        self, patient_id: UUID, *, organization_id: UUID, event_code: str, occurrence_index: int,
        activity_code: str, status: str, actual_time: datetime | None, actor_id: UUID | None,
        reason: str | None = None,
    ) -> ActivityEntryRecord:
        """An intra-day actual time or completion decision; corrections supersede."""
        if status not in {"COMPLETED", "NOT_DONE", "PENDING"}:
            raise ValueError("activity status must be COMPLETED, NOT_DONE, or PENDING")
        if status == "COMPLETED" and actual_time is None:
            raise ValueError("a completed activity requires its actual time")

        def work(tx: UnitOfWork) -> ActivityEntryRecord:
            patient = tx.patients.get(organization_id, patient_id)
            logical_key = activity_logical_key(event_code, occurrence_index, activity_code)
            record = ActivityEntryRecord(
                organization_id=organization_id, patient_id=patient.id, event_code=event_code,
                occurrence_index=occurrence_index, activity_code=activity_code,
                logical_key=logical_key, status=status, actual_time=actual_time,
                recorded_by=actor_id, reason=reason)
            tx.facts.add_activity_entry(record)
            superseded = tx.facts.supersede_activity_entries(patient.id, logical_key, record.id)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_ACTIVITY_RECORDED", entity_type="PATIENT", entity_id=patient.id,
                before=({"status": superseded[-1].status,
                         "actual_time": superseded[-1].actual_time.isoformat()
                         if superseded[-1].actual_time else None} if superseded else None),
                after={"logical_key": logical_key, "status": status,
                       "actual_time": actual_time.isoformat() if actual_time else None},
                reason=reason))
            return record

        return self._run(work)

    def record_occurrence_fact(
        self, patient_id: UUID, *, organization_id: UUID, logical_key: str,
        kind: OccurrenceFactKind, value: date | None = None, end_value: date | None = None,
        effective_at: date | None = None, supersedes_occurrence_id: UUID | None = None,
        actor_id: UUID | None = None, reason: str | None = None, source: str | None = None,
    ) -> OccurrenceRecord:
        """Record a performed / missed / booked fact for one logical occurrence.

        PT-02 append-only; PT-04 a booking never sets an actual date; PT-07
        nothing is derived from a date passing.
        """
        return self._run(lambda tx: self._record_fact(
            tx, patient_id, organization_id=organization_id, logical_key=logical_key, kind=kind,
            value=value, end_value=end_value, effective_at=effective_at,
            supersedes_occurrence_id=supersedes_occurrence_id, actor_id=actor_id,
            reason=reason, source=source))

    def _record_fact(
        self, tx: UnitOfWork, patient_id: UUID, *, organization_id: UUID, logical_key: str,
        kind: OccurrenceFactKind, value: date | None, end_value: date | None,
        effective_at: date | None, supersedes_occurrence_id: UUID | None,
        actor_id: UUID | None, reason: str | None, source: str | None,
    ) -> OccurrenceRecord:
        patient = tx.patients.get(organization_id, patient_id)
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        version_id = patient.current_schedule_version_id
        row = next((item for item in self.current_events(tx, patient.id, version_id)
                    if item.logical_key == logical_key), None)
        if row is None:
            raise KeyError(f"occurrence {logical_key} is not in the patient's current plan")
        if kind in {OccurrenceFactKind.PERFORMED, OccurrenceFactKind.BOOKED} and value is None:
            raise ValueError(f"a {kind.value.lower()} fact needs its date")
        previous: OccurrenceRecord | None = None
        if supersedes_occurrence_id is not None:
            previous = next((item for item in tx.facts.list_occurrences(patient.id)
                             if item.id == supersedes_occurrence_id), None)
            if previous is None:
                raise KeyError("the occurrence record being corrected does not exist")
            if previous.logical_key != logical_key:
                raise ValueError("a correction must supersede a record of the same occurrence")
        metadata: dict[str, object] = {FACT_KIND_KEY: kind.value}
        if supersedes_occurrence_id is not None:
            metadata[SUPERSEDES_KEY] = str(supersedes_occurrence_id)
        if effective_at is not None:
            metadata[EFFECTIVE_AT_KEY] = effective_at.isoformat()
        if end_value is not None:
            metadata[END_KEY] = end_value.isoformat()
        if source is not None:
            metadata["source"] = source
        if kind == OccurrenceFactKind.BOOKED:
            assert value is not None
            metadata[BOOKED_KEY] = value.isoformat()
        occurrence = OccurrenceRecord(
            organization_id=organization_id, patient_id=patient.id, schedule_version_id=version_id,
            event_code=row.event_code, occurrence_index=row.occurrence_index, logical_key=logical_key,
            occurrence_type="BOOKING" if kind == OccurrenceFactKind.BOOKED else "VISIT",
            scheduled_date=row.nominal_start_date,
            actual_date=value if kind == OccurrenceFactKind.PERFORMED else None,
            status={OccurrenceFactKind.PERFORMED: "COMPLETED", OccurrenceFactKind.MISSED: "MISSED",
                    OccurrenceFactKind.BOOKED: "BOOKED"}[kind],
            recorded_by=actor_id, metadata=metadata)
        tx.facts.add_occurrence(occurrence)
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=actor_id,
            action=("PATIENT_OCCURRENCE_FACT_CORRECTED" if previous is not None
                    else "PATIENT_OCCURRENCE_FACT_RECORDED"),
            entity_type="PATIENT_EVENT", entity_id=row.id,
            before=({"occurrence_id": str(previous.id),
                     "actual_date": previous.actual_date.isoformat() if previous.actual_date else None,
                     "status": previous.status, "metadata": previous.metadata}
                    if previous is not None else None),
            after={"occurrence_id": str(occurrence.id), "logical_key": logical_key,
                   "kind": kind.value, "value": value.isoformat() if value else None,
                   "metadata": metadata},
            reason=reason))
        return occurrence

    def record_patient_fact(
        self, patient_id: UUID, *, organization_id: UUID, state_code: str,
        value: dict[str, object], effective_at: date | datetime, actor_id: UUID | None,
        source_reference: dict[str, object] | None = None,
    ) -> PatientStateRecord:
        """Append one reserved-code WP3 fact (process transition, assignment
        fact, site calendar) to the patient's append-only state history."""
        if state_code not in RESERVED_STATE_CODES:
            raise ValueError(f"{state_code!r} is not a typed WP3 fact")
        effective = effective_at if isinstance(effective_at, datetime) else datetime.combine(
            effective_at, datetime.min.time(), tzinfo=timezone.utc)

        def work(tx: UnitOfWork) -> PatientStateRecord:
            patient = tx.patients.get(organization_id, patient_id)
            record = PatientStateRecord(
                organization_id=organization_id, patient_id=patient.id, state_code=state_code,
                state_value=value, effective_at=effective, recorded_by=actor_id,
                source_reference=source_reference or {})
            tx.facts.append_state(record)
            return record

        return self._run(work)

