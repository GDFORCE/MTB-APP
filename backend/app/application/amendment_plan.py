"""The read-only plan of moving ONE patient between two approved versions (P6-03/04).

``build_plan`` is the single calculation behind a simulation, a confirmation re-check, a
deferred execution and a return. It changes nothing: the carried facts it prepares are
returned as unsaved records and evaluated as ``extra_occurrences``.

Facts travel ONLY through the reviewed event mapping, as new rows that carry their own
provenance (``carried_from_occurrence_id``); the original rows are untouched and stay
queryable. An earlier version's event codes are never treated as equivalent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from app.domain.schedule.amendment import (
    SIMULATION_POLICY_VERSION, AdoptionMode, AdoptionPoint, EventMapping, Translation,
    actual_cycle_start, find_cycle_plan, reverse_translate_occurrence, translate_occurrence,
)
from app.domain.schedule.amendment_impact import (
    binding_hash, build_visit_impacts, impact_signature, impact_summary,
)
from app.domain.schedule.anchor_status import record_is_actual
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import EvaluationResult, OccurrenceFactKind, UniversalSchedule

from .amendment_common import (
    VerifiedVersion, context_fingerprint, load_version, mapping_gate, require_approved, rows_of,
)
from .amendments import AmendmentPairRecord, DecisionStatus, DecisionType
from .authority import require_command_plane_authority
from .errors import AmendmentBlockedError, RecordNotFoundError
from .patient_schedule import (
    SUPERSEDES_KEY, PatientScheduleService, occurrence_fact_from_record,
)
from .patients import OccurrenceRecord, PatientEventRecord, PatientRecord
from .ports import UnitOfWork

#: operational commands that change a patient's dates and are NOT carried across versions
UNCARRIED_COMMANDS = frozenset({
    "CANCEL_OCCURRENCE", "OVERRIDE_OCCURRENCE", "PAUSE_TRACK", "RESUME_TRACK", "REBASE_TRACK",
    "STOP_TRACK"})
CARRY_PROVENANCE = "carried_from_occurrence_id"
CARRY_VERSION = "carried_from_schedule_version_id"

Translator = Callable[[str, int], "tuple[Translation, tuple[str, int] | None, str | None]"]


def blocker(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


@dataclass
class Plan:
    direction: str
    patient: PatientRecord
    pair: AmendmentPairRecord
    from_version: VerifiedVersion
    to_version: VerifiedVersion
    point: AdoptionPoint
    horizon: date
    #: the business clock this calculation, its preview and its commit all use
    as_of: date = date(1970, 1, 1)
    #: the effective point the reconciliation applies; None = immediate, or not known yet
    effective_date: date | None = None
    #: True while a deferred point (event / cycle) has not happened: results are indicative
    provisional: bool = False
    final: list[PatientEventRecord] = field(default_factory=list)
    baseline: dict[str, Any] = field(default_factory=dict)
    mappings: list[EventMapping] = field(default_factory=list)
    mapping_record_revision: int = 0
    mapping_revision: str = ""
    mapping_engine_version: str = ""
    blockers: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    carried: list[OccurrenceRecord] = field(default_factory=list)
    retained: list[dict[str, Any]] = field(default_factory=list)
    prior: list[PatientEventRecord] = field(default_factory=list)
    translated_prior: list[PatientEventRecord] = field(default_factory=list)
    result: EvaluationResult | None = None
    impacts: list[dict[str, Any]] = field(default_factory=list)
    bindings: dict[str, Any] = field(default_factory=dict)
    dimension_ids: dict[str, UUID | None] = field(default_factory=dict)
    current_decision: Any = None
    open_decision: Any = None
    adoption_decision: Any = None

    @property
    def ok(self) -> bool:
        return not self.blockers

    @property
    def summary(self) -> dict[str, int]:
        return impact_summary(self.impacts)

    def signature(self, *, strict: bool) -> dict[str, str]:
        return impact_signature(self.impacts, strict=strict)

    def view(self) -> dict[str, Any]:
        return {
            "direction": self.direction, "patient_id": str(self.patient.id),
            "from": self.from_version.identity(), "to": self.to_version.identity(),
            "adoption_point": self.point.model_dump(mode="json"), "horizon": self.horizon.isoformat(),
            "as_of": self.as_of.isoformat(),
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "provisional": self.provisional,
            "provisional_note": (
                "The adoption point has not happened yet, so no effective date exists. Visits "
                "before the point will keep their current dates when it does; everything marked "
                "provisional is recalculated then, and a changed effect needs your confirmation "
                "again." if self.provisional else None),
            "baseline": self.baseline,
            "summary": self.summary, "visits": self.impacts, "blockers": self.blockers,
            "warnings": self.warnings, "carried_fact_count": len(self.carried),
            "retained_in_earlier_version": self.retained,
            "mapping_revision": self.mapping_revision, "binding_hash": binding_hash(self.bindings)}


# ------------------------------------------------------------------------ helpers
def effective_rows(tx: UnitOfWork, patient_id: UUID, version_id: UUID) -> list[OccurrenceRecord]:
    """The version's occurrence facts as the engine sees them: not superseded, and stating
    a fact the engine acts on, oldest recorded first."""
    rows = sorted(tx.facts.list_occurrences(patient_id, schedule_version_id=version_id),
                  key=lambda item: (item.recorded_at, str(item.id)))
    superseded = {str((row.metadata or {}).get(SUPERSEDES_KEY))
                  for row in rows if (row.metadata or {}).get(SUPERSEDES_KEY)}
    return [row for row in rows
            if str(row.id) not in superseded and occurrence_fact_from_record(row) is not None]


def _dimension_codes(patient: PatientRecord, schedule: UniversalSchedule) -> dict[str, str | None]:
    def code(dimensions, dimension_id, fallback):
        if fallback:
            return fallback
        return next((item.code for item in dimensions if item.id == dimension_id), None) \
            if dimension_id is not None else None

    return {"arm": code(schedule.arms, patient.arm_id, patient.arm_code),
            "cohort": code(schedule.cohorts, patient.cohort_id, patient.cohort_code),
            "population": code(schedule.populations, patient.population_id, patient.population_code)}


def _remap_dimensions(
    patient: PatientRecord, source: UniversalSchedule, target: UniversalSchedule,
) -> tuple[PatientRecord, dict[str, UUID | None], list[dict[str, str]]]:
    codes = _dimension_codes(patient, source)
    problems: list[dict[str, str]] = []
    ids: dict[str, UUID | None] = {}
    pools = {"arm": target.arms, "cohort": target.cohorts, "population": target.populations}
    for name, code in codes.items():
        if code is None:
            ids[name] = None
            continue
        match = next((item for item in pools[name] if item.code == code), None)
        if match is None:
            problems.append(blocker(
                "ASSIGNMENT_NOT_IN_TARGET", name,
                f"the patient is in {name} {code!r}, which the later version does not define"))
            ids[name] = None
        else:
            ids[name] = match.id
    updated = patient.model_copy(update={
        "arm_id": ids.get("arm"), "cohort_id": ids.get("cohort"),
        "population_id": ids.get("population"),
        "arm_code": codes["arm"], "cohort_code": codes["cohort"], "population_code": codes["population"]})
    return updated, ids, problems


def _carry(
    tx: UnitOfWork, patient: PatientRecord, *, from_id: UUID, to_id: UUID,
    translate: Translator, pair_id: UUID,
) -> tuple[list[OccurrenceRecord], list[dict[str, Any]], list[dict[str, str]]]:
    """New rows for the destination version, one per effective fact that has a unique,
    reviewed destination. Anything else is a blocker, a retained history note, or skipped
    because it is itself a copy that came FROM the destination."""
    carried: list[OccurrenceRecord] = []
    retained: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    destination = tx.facts.list_occurrences(patient.id, schedule_version_id=to_id)
    already = {str((row.metadata or {}).get(CARRY_PROVENANCE)) for row in destination
               if (row.metadata or {}).get(CARRY_PROVENANCE)}
    originals = {str(row.id): row for row in tx.facts.list_occurrences(
        patient.id, schedule_version_id=from_id)}
    for row in effective_rows(tx, patient.id, from_id):
        meta = row.metadata or {}
        if meta.get(CARRY_VERSION) == str(to_id):
            continue                      # a copy of a fact that originated on the destination
        if str(row.id) in already:
            continue                      # this fact was carried before
        kind, target, reason = translate(row.event_code, row.occurrence_index)
        label = row.logical_key
        if kind == Translation.NONE:
            retained.append({"logical_key": label, "occurrence_id": str(row.id),
                             "status": row.status})
            continue
        if kind == Translation.UNRESOLVED or target is None:
            problems.append(blocker(
                "FACT_UNMAPPED", label,
                f"{label} ({row.status.lower()}) has no reviewed destination: {reason}"))
            continue
        new_meta = {key: value for key, value in meta.items() if key != SUPERSEDES_KEY}
        supersedes = meta.get(SUPERSEDES_KEY)
        if supersedes and str(supersedes) in originals:
            origin = (originals[str(supersedes)].metadata or {}).get(CARRY_PROVENANCE)
            if origin:
                new_meta[SUPERSEDES_KEY] = str(origin)   # a correction of a carried copy
        new_meta.update({
            CARRY_PROVENANCE: str(row.id), CARRY_VERSION: str(from_id),
            "carried_by_pair_id": str(pair_id), "source": "amendment_mapping"})
        if meta.get("source"):
            new_meta["original_source"] = meta["source"]
        carried.append(row.model_copy(update={
            "id": uuid4(), "schedule_version_id": to_id, "event_code": target[0],
            "occurrence_index": target[1], "logical_key": f"{target[0]}:{target[1]}",
            "metadata": new_meta}))
    seen: dict[tuple[str, str], str] = {}
    for item in carried:
        fact = occurrence_fact_from_record(item)
        if fact is None:
            continue
        key = (item.logical_key, fact.kind.value)
        if key in seen:
            problems.append(blocker(
                "CARRY_CONFLICT", item.logical_key,
                f"two earlier facts would both become {fact.kind.value.lower()} on "
                f"{item.logical_key}; the mapping must say which occurrence goes where"))
        seen[key] = str(item.id)
    return carried, retained, problems


def _uncarried_state(
    tx: UnitOfWork, org: UUID, patient: PatientRecord, *, from_id: UUID, translate: Translator,
) -> list[dict[str, str]]:
    """Patient state that is scoped to the version being left and has no mapping-based
    carry. Moving over it would silently change the patient's dates, so it blocks."""
    problems: list[dict[str, str]] = []
    records = tx.ledger.list_for_patient(org, patient.id)
    reversed_ids = {row.reverses_command_id for row in records if row.reverses_command_id}
    active = [row for row in records
              if row.schedule_version_id == from_id and row.command_type in UNCARRIED_COMMANDS
              and row.id not in reversed_ids]
    if active:
        problems.append(blocker(
            "ACTIVE_COMMANDS_NOT_CARRIED", "ledger",
            f"{len(active)} patient-specific command(s) (pause, stop, rebase, override or "
            "cancellation) apply to the current version and cannot move with the patient; "
            "reverse them first"))
    for entry in tx.facts.list_current_activity_entries(patient.id):
        kind, target, _ = translate(entry.event_code, entry.occurrence_index)
        if kind == Translation.UNRESOLVED or (
                kind == Translation.TARGET and target is not None and target[0] != entry.event_code):
            problems.append(blocker(
                "ACTIVITY_RECORDS_NOT_CARRIED", entry.logical_key,
                f"activity records on {entry.event_code} cannot follow it to a re-coded visit"))
            break
    for visit in tx.facts.list_unscheduled_visits(patient.id):
        kind, target, _ = translate(visit.event_code, 0)
        if kind == Translation.UNRESOLVED or (
                kind == Translation.TARGET and target is not None and target[0] != visit.event_code):
            problems.append(blocker(
                "UNSCHEDULED_NOT_CARRIED", visit.event_code,
                f"an unscheduled {visit.event_code} visit cannot follow its visit to a re-coded one"))
            break
    return problems


def translated_prior(
    prior: list[PatientEventRecord], translate: Translator, destination: UniversalSchedule,
    to_id: UUID, *, effective_date: date | None = None,
) -> list[PatientEventRecord]:
    """The patient's stored projection re-keyed into the destination version, so the new
    evaluation reconciles against it (logical identity, original dates, history) rather
    than starting from nothing. Visits with no destination are left out - their history
    stays on the earlier version. Before an effective point, an un-performed visit keeps
    its earlier-version date."""
    definitions = {item.code: item for item in destination.events}
    out: list[PatientEventRecord] = []
    for row in prior:
        kind, target, _ = translate(row.event_code, row.occurrence_index)
        if kind != Translation.TARGET or target is None:
            continue
        definition = definitions.get(target[0])
        if definition is None:
            continue
        before_effective = (effective_date is not None and row.nominal_start_date is not None
                            and row.nominal_start_date < effective_date)
        out.append(row.model_copy(update={
            "event_code": target[0], "occurrence_index": target[1],
            "logical_key": f"{target[0]}:{target[1]}", "schedule_version_id": to_id,
            "event_definition_id": definition.id,
            "protected_history": bool(row.protected_history or before_effective)}))
    return out


# ------------------------------------------------------------------------- the plan
def build_plan(
    tx: UnitOfWork, *, organization_id: UUID, patient_id: UUID, direction: str,
    to_schedule_version_id: UUID | None, point: AdoptionPoint, horizon: date,
    as_of: date, effective_date: date | None = None, at_execution: bool = False,
) -> Plan:
    """Calculate (never write) what the move does. ``direction`` is ADOPT (to a later
    approved version) or RETURN (back to the version the last adoption left)."""
    patient = tx.patients.get(organization_id, patient_id)
    require_command_plane_authority(patient)
    current_id = patient.current_schedule_version_id
    if current_id is None:
        raise AmendmentBlockedError("the patient is not on an approved schedule",
                                    [blocker("PATIENT_NOT_PINNED", "patient", "no current version")])
    adoption = None
    if direction == DecisionType.RETURN.value:
        history = tx.amendments.list_decisions_for_patient(organization_id, patient_id)
        adoption = next((row for row in reversed(history)
                         if row.decision_type == DecisionType.ADOPT
                         and row.status == DecisionStatus.APPLIED
                         and row.to_schedule_version_id == current_id), None)
        if adoption is None:
            raise AmendmentBlockedError(
                "the patient is not on a version they were moved to by an adoption",
                [blocker("NOTHING_TO_RETURN_FROM", "patient", "no applied adoption to this version")])
        to_id = adoption.from_schedule_version_id
        pair = tx.amendments.get_pair(organization_id, adoption.pair_id)
    else:
        if to_schedule_version_id is None:
            raise AmendmentBlockedError("choose the later version",
                                        [blocker("TARGET_REQUIRED", "to", "no target version")])
        to_id = to_schedule_version_id
        pair = tx.amendments.find_pair(organization_id, current_id, to_id)
        if pair is None:
            raise AmendmentBlockedError(
                "compare the two versions and review the event mapping first",
                [blocker("PAIR_NOT_PREPARED", "pair", "no prepared comparison of these versions")])
    from_version = load_version(tx, organization_id, current_id)
    to_version = load_version(tx, organization_id, to_id)
    require_approved(from_version, "current")
    require_approved(to_version, "target")
    earlier = load_version(tx, organization_id, pair.from_schedule_version_id)
    later = load_version(tx, organization_id, pair.to_schedule_version_id)
    record = tx.amendments.get_mapping_set(organization_id, pair.id)
    mappings = rows_of(record)
    effective, provisional = effective_date, False
    if direction == DecisionType.ADOPT.value and point.mode in {
            AdoptionMode.ADOPT_ON_DATE, AdoptionMode.ADOPT_AT_EVENT, AdoptionMode.ADOPT_AT_CYCLE}:
        if effective is None and point.mode == AdoptionMode.ADOPT_ON_DATE:
            effective = point.on_date
        elif effective is None:
            state = trigger_state(tx, patient, from_version.schedule, from_version.id, point, as_of)
            if state["due"] and state["effective_date"]:
                effective = date.fromisoformat(state["effective_date"])
        provisional = effective is None
    plan = Plan(
        direction=direction, patient=patient, pair=pair, from_version=from_version,
        to_version=to_version, point=point, horizon=horizon, as_of=as_of,
        effective_date=effective, provisional=provisional, mappings=mappings,
        mapping_record_revision=record.revision, mapping_revision=record.mapping_revision,
        mapping_engine_version=record.engine_version, adoption_decision=adoption)
    # -- gates ---------------------------------------------------------------------------
    for item in mapping_gate(earlier.schedule, later.schedule, record,
                             tx.amendments.list_mapping_decisions(organization_id, pair.id)):
        plan.blockers.append(blocker(item["code"], item["path"], item["detail"]))
    if (pair.from_content_hash, pair.to_content_hash) != (
            earlier.header.content_hash or pair.from_content_hash,
            later.header.content_hash or pair.to_content_hash):
        plan.blockers.append(blocker(
            "PAIR_STALE", "pair", "an approved version no longer matches the prepared comparison"))
    for version, label in ((from_version, "current"), (to_version, "target")):
        if version.hash_status.value == "UNVERIFIED_LEGACY":
            side = plan.warnings if version is from_version and direction == "ADOPT" else plan.blockers
            side.append(blocker(
                "VERSION_UNVERIFIED_LEGACY", label,
                f"the {label} version was approved before content hashes existed; its content "
                "cannot be proven unchanged"))
    if direction == DecisionType.ADOPT.value:
        effective = to_version.header.effective_from
        if (effective is not None and effective > as_of and not at_execution
                and point.mode == AdoptionMode.ADOPT_IMMEDIATELY):
            plan.blockers.append(blocker(
                "NOT_YET_EFFECTIVE", "to",
                f"the later version takes effect on {effective.isoformat()}; choose that date or "
                "later"))
        if point.mode == AdoptionMode.ADOPT_ON_DATE and point.on_date is not None:
            if point.on_date <= as_of and not at_execution:
                plan.blockers.append(blocker(
                    "DATE_NOT_FUTURE", "adoption_point",
                    "a date in the past or today is an immediate adoption; choose it explicitly"))
            if effective is not None and point.on_date < effective:
                plan.blockers.append(blocker(
                    "DATE_BEFORE_EFFECTIVE", "adoption_point",
                    f"the later version is not in force before {effective.isoformat()}"))
        plan.blockers.extend(_point_problems(tx, patient, from_version.schedule, point))
        history = tx.amendments.list_decisions_for_patient(organization_id, patient_id)
        if any(row.decision_type == DecisionType.RETURN and row.to_schedule_version_id == current_id
               and row.from_schedule_version_id == to_id and row.status == DecisionStatus.APPLIED
               for row in history) or tx.facts.list_occurrences(
                patient.id, schedule_version_id=to_id):
            plan.blockers.append(blocker(
                "TARGET_HAS_HISTORY", "to",
                "the patient already has recorded history on that version; moving back onto it "
                "is not supported yet"))
    open_decision = tx.amendments.find_open_decision(organization_id, patient_id)
    plan.open_decision = open_decision
    if open_decision is not None:
        plan.warnings.append(blocker(
            "OPEN_DECISION", str(open_decision.id),
            f"the patient has a {open_decision.status.value.lower()} amendment decision; "
            "cancel or replace it before a new one"))
    if plan.blockers and any(item["code"].startswith("MAPPING_") or item["code"] in {
            "PAIR_STALE", "VERSION_UNVERIFIED_LEGACY"} for item in plan.blockers):
        plan.bindings = _bindings(tx, plan, organization_id, None)
        return plan                                       # nothing can be translated safely

    # -- translation -----------------------------------------------------------------
    if direction == DecisionType.ADOPT.value:
        def translate(code: str, index: int):
            return translate_occurrence(mappings, code, index)
    else:
        native_keys = {row.logical_key for row in effective_rows(tx, patient.id, current_id)
                       if (row.metadata or {}).get(CARRY_VERSION) != str(to_id)}

        def translate(code: str, index: int):
            kind, target, reason = reverse_translate_occurrence(mappings, code, index)
            if kind == Translation.UNRESOLVED and f"{code}:{index}" not in native_keys:
                # nothing was recorded on a visit that exists only in the later version:
                # it simply has no earlier counterpart
                return Translation.NONE, None, None
            return kind, target, reason
    # the destination dimension ids by code (the patient keeps their assignment)
    eval_patient, plan.dimension_ids, problems = _remap_dimensions(
        patient, from_version.schedule, to_version.schedule)
    plan.blockers.extend(problems)
    plan.carried, plan.retained, problems = _carry(
        tx, patient, from_id=current_id, to_id=to_id, translate=translate, pair_id=pair.id)
    plan.blockers.extend(problems)
    plan.blockers.extend(_uncarried_state(
        tx, organization_id, patient, from_id=current_id, translate=translate))
    if direction == DecisionType.RETURN.value:
        _fix_return_supersession(tx, plan, current_id)

    service = PatientScheduleService(tx_uow_stub())
    plan.prior = service.current_events(tx, patient.id, current_id)
    plan.translated_prior = translated_prior(
        plan.prior, translate, to_version.schedule, to_id, effective_date=plan.effective_date)
    if not plan.blockers:
        try:
            plan.result = service.evaluate_result(
                tx, eval_patient, to_id, horizon=horizon, extra_occurrences=plan.carried,
                today=as_of)
        except (ImmutableScheduleError, ValueError) as error:
            plan.blockers.append(blocker("TARGET_NOT_EVALUABLE", "to", str(error)))
    if plan.result is not None:
        # The FINAL reconciled projection - the same calculation the commit persists - so the
        # preview can never show a raw date that persistence would override.
        protected = service.protected_event_keys(
            tx, patient.id, plan.translated_prior, to_id, extra_occurrences=plan.carried)
        plan.final, _ = service.reconcile_projection(
            plan.result, plan.translated_prior, protected, organization_id=organization_id,
            patient_id=patient.id, evaluation_id=uuid4(), schedule_version_id=to_id,
            protocol_version_id=to_version.schedule.schedule_metadata.protocol_version_id)
        plan.impacts, problems = build_visit_impacts(
            plan.prior, plan.final, translate, provisional=plan.provisional)
        plan.blockers.extend(problems)
    plan.baseline = _baseline(service, tx, patient, plan, current_id, horizon, as_of)
    if plan.baseline.get("differing_visits"):
        plan.warnings.append(blocker(
            "STORED_CALENDAR_DIFFERS_FROM_BASELINE", "calendar",
            f"{len(plan.baseline['differing_visits'])} visit(s) in the stored calendar differ from a "
            "fresh calculation of the current version under the same business date (for example "
            "an appointment, an exception or an earlier evaluation date); the impact is read "
            "against the stored calendar the staff see"))
    plan.bindings = _bindings(tx, plan, organization_id, plan.result)
    return plan


def _baseline(
    service: PatientScheduleService, tx: UnitOfWork, patient: PatientRecord, plan: Plan,
    current_id: UUID, horizon: date, as_of: date,
) -> dict[str, Any]:
    """The CURRENT version recomputed under the SAME business date and horizon the new version
    is evaluated with: a frozen comparison baseline, kept apart from the stored calendar the
    staff currently see. Informational only; the impact is read against the stored calendar."""
    try:
        fresh = service.evaluate_result(tx, patient, current_id, horizon=horizon, today=as_of)
    except Exception as error:                                # noqa: BLE001
        return {"computed": False, "reason": str(error), "differing_visits": []}
    stored = {row.logical_key: row for row in plan.prior}
    differing = []
    for item in fresh.events:
        key = f"{item.event_code}:{item.occurrence_index}"
        row = stored.get(key)
        if row is None or row.protected_history or row.status in {"COMPLETED", "MISSED", "CANCELLED"}:
            continue
        timing = item.timing
        value = getattr(timing, "nominal_start", None) if timing else None
        value = value.date() if isinstance(value, datetime) else value
        if value != row.nominal_start_date:
            differing.append(key)
    return {"computed": True, "as_of": as_of.isoformat(),
            "evaluator_version": fresh.evaluator_version, "differing_visits": sorted(differing)}


def _fix_return_supersession(tx: UnitOfWork, plan: Plan, current_id: UUID) -> None:
    """A correction of a carried copy supersedes the ORIGINAL on the earlier version."""
    originals = {str(row.id): row for row in tx.facts.list_occurrences(
        plan.patient.id, schedule_version_id=current_id)}
    for index, item in enumerate(plan.carried):
        supersedes = (item.metadata or {}).get(SUPERSEDES_KEY)
        if supersedes and str(supersedes) in originals:
            origin = (originals[str(supersedes)].metadata or {}).get(CARRY_PROVENANCE)
            metadata = dict(item.metadata)
            if origin:
                metadata[SUPERSEDES_KEY] = str(origin)
            else:
                metadata.pop(SUPERSEDES_KEY, None)
            plan.carried[index] = item.model_copy(update={"metadata": metadata})


def tx_uow_stub():
    """``PatientScheduleService`` only needs a unit of work to open its OWN transactions;
    the plan always passes the live ``tx`` explicitly, so none is ever opened here."""
    class _NoUnitOfWork:
        def run(self, fn):                                # pragma: no cover - never used
            raise RuntimeError("the plan runs inside the caller's transaction")

        read = run
    return _NoUnitOfWork()


# --------------------------------------------------------------------- adoption point
def _point_problems(
    tx: UnitOfWork, patient: PatientRecord, schedule: UniversalSchedule, point: AdoptionPoint,
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    if point.mode == AdoptionMode.ADOPT_AT_EVENT:
        event = next((item for item in schedule.events if item.code == point.event_code), None)
        if event is None:
            problems.append(blocker("POINT_EVENT_UNKNOWN", "adoption_point",
                                    f"{point.event_code!r} is not a visit of the current version"))
        else:
            index = point.occurrence_index or 0
            repeats = bool(event.recurrence or event.recurrence_plan or event.calendar_recurrence)
            if index > 0 and not repeats:
                problems.append(blocker(
                    "POINT_OCCURRENCE_MISSING", "adoption_point",
                    f"{point.event_code} does not repeat, so it has no occurrence {index}"))
            termination = getattr(event.recurrence, "termination", None)
            if (termination is not None and termination.type == "COUNT"
                    and termination.count is not None and index >= termination.count):
                problems.append(blocker(
                    "POINT_OCCURRENCE_MISSING", "adoption_point",
                    f"{point.event_code} repeats {termination.count} times; there is no occurrence {index}"))
    elif point.mode == AdoptionMode.ADOPT_AT_CYCLE:
        plan = find_cycle_plan(schedule, point.repeat_block_code or "")
        if plan is None:
            problems.append(blocker(
                "POINT_CYCLE_UNKNOWN", "adoption_point",
                f"{point.repeat_block_code!r} is not a repeat block with a cycle plan in the "
                "current version"))
        else:
            termination = plan.termination
            if termination.type == "COUNT" and termination.count is not None \
                    and (point.cycle_number or 0) > termination.count:
                problems.append(blocker(
                    "POINT_CYCLE_MISSING", "adoption_point",
                    f"{plan.code} has {termination.count} cycles; there is no cycle {point.cycle_number}"))
    return problems


def trigger_state(
    tx: UnitOfWork, patient: PatientRecord, schedule: UniversalSchedule, current_id: UUID,
    point: AdoptionPoint, as_of: date,
) -> dict[str, Any]:
    """Has the stated effective point happened? ``due`` is True only with a REAL date;
    otherwise the decision keeps waiting and no date is invented."""
    rows = effective_rows(tx, patient.id, current_id)
    performed: dict[str, list[tuple[int, date]]] = {}
    for row in rows:
        fact = occurrence_fact_from_record(row)
        if fact is not None and fact.kind == OccurrenceFactKind.PERFORMED and fact.value is not None:
            performed.setdefault(row.event_code, []).append(
                (row.occurrence_index, fact.value if isinstance(fact.value, date) else fact.value.date()))
    if point.mode == AdoptionMode.ADOPT_AT_EVENT:
        hit = [value for index, value in performed.get(point.event_code or "", [])
               if index == (point.occurrence_index or 0)]
        if hit:
            return {"due": True, "effective_date": max(hit).isoformat(),
                    "detail": f"{point.event_code}:{point.occurrence_index} was performed"}
        return {"due": False, "effective_date": None,
                "detail": f"waiting for {point.event_code}:{point.occurrence_index} to be performed"}
    if point.mode == AdoptionMode.ADOPT_AT_CYCLE:
        plan = find_cycle_plan(schedule, point.repeat_block_code or "")
        if plan is None:
            return {"due": False, "effective_date": None, "detail": "the cycle plan is not defined"}
        anchors = {item.anchor_code: (item.value_date or (item.value_datetime.date()
                                                          if item.value_datetime else None))
                   for item in tx.facts.list_anchors(patient.id, statuses={"CONFIRMED"})
                   if record_is_actual(item.status, item.source_type)
                   and (item.value_date or item.value_datetime)}
        start = actual_cycle_start(
            plan, point.cycle_number or 1,
            actual_anchors={key: value for key, value in anchors.items() if value is not None},
            start_event_actuals=[value for _, value in performed.get(plan.start_event_code or "", [])])
        if start is not None:
            return {"due": True, "effective_date": start.isoformat(),
                    "detail": f"cycle {point.cycle_number} actually started"}
        return {"due": False, "effective_date": None,
                "detail": f"waiting for the actual start of cycle {point.cycle_number}"}
    if point.mode == AdoptionMode.ADOPT_ON_DATE and point.on_date is not None:
        if as_of >= point.on_date:
            return {"due": True, "effective_date": point.on_date.isoformat(),
                    "detail": f"{point.on_date.isoformat()} has arrived"}
        return {"due": False, "effective_date": None,
                "detail": f"waiting for {point.on_date.isoformat()}"}
    return {"due": True, "effective_date": None, "detail": "immediate"}


# ---------------------------------------------------------------------------- bindings
def _bindings(
    tx: UnitOfWork, plan: Plan, organization_id: UUID, result: EvaluationResult | None,
) -> dict[str, Any]:
    pointer = tx.evaluations.get_pointer(plan.patient.id, plan.from_version.id)
    pair = plan.pair
    return {
        "direction": plan.direction, "patient_id": str(plan.patient.id),
        "organization_id": str(organization_id), "trial_id": str(plan.patient.trial_id),
        "from_schedule_version_id": str(plan.from_version.id),
        "to_schedule_version_id": str(plan.to_version.id),
        "from_content_hash": plan.from_version.header.content_hash,
        "to_content_hash": plan.to_version.header.content_hash,
        "from_verification": plan.from_version.hash_status.value,
        "to_verification": plan.to_version.hash_status.value,
        "from_header_integrity": plan.from_version.header.header_integrity_hash,
        "to_header_integrity": plan.to_version.header.header_integrity_hash,
        "from_effective_from": _iso(plan.from_version.header.effective_from),
        "to_effective_from": _iso(plan.to_version.header.effective_from),
        "pair_id": str(pair.id), "mapping_set_revision": plan.mapping_record_revision,
        "mapping_revision": plan.mapping_revision, "mapping_engine_version": plan.mapping_engine_version,
        "authority_plane": plan.patient.authority_plane,
        "patient_revision": plan.patient.revision,
        "pointer_evaluation_id": str(pointer.current_evaluation_id) if pointer and pointer.current_evaluation_id else None,
        "pointer_revision": pointer.revision if pointer else None,
        "context_fingerprint": context_fingerprint(tx, organization_id, plan.patient.id),
        "adoption_point": plan.point.model_dump(mode="json"), "horizon": plan.horizon.isoformat(),
        "as_of": plan.as_of.isoformat(),
        "effective_date": plan.effective_date.isoformat() if plan.effective_date else None,
        "policy_version": SIMULATION_POLICY_VERSION,
        "evaluator_version": result.evaluator_version if result is not None else None,
        "schema_version": plan.from_version.header.schema_version}


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


#: bindings that legitimately move while a deferred decision waits; everything else
#: (versions, hashes, mapping, authority, pair, direction) must still match at execution
DEFERRED_VOLATILE = frozenset({
    "patient_revision", "pointer_evaluation_id", "pointer_revision", "context_fingerprint",
    "horizon", "evaluator_version", "as_of", "effective_date"})


def default_horizon(as_of: date) -> date:
    return as_of + timedelta(days=365)
