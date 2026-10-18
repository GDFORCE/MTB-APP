"""The isolated, synthetic Mongo-native pilot: trial ``MTB-UCTSM-PILOT-001``.

A NEW local/development trial with synthetic patients only. It is built entirely
through the repository ports, in the command plane the patients will live in:
a track-aware schedule drafted by one actor, approved by a DIFFERENT actor through
the shared maker-checker boundary, and native patients enrolled with explicit
``authority_plane="MONGO"``, an anchor fact and a first immutable evaluation. No
existing trial, patient or SQL record is read, mirrored or changed, and an
existing non-pilot trial with the same code is refused rather than repurposed.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence, HoldResumePolicy,
    ProcessDefinition, ProcessKind, RecurrenceBasis, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, ScheduleStatus, ScheduleTrack, TrackLink, TrackRebasePolicy,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, NonNegativeTemporalAmount, OffsetTiming,
    PositiveTemporalAmount, TemporalAmount, TemporalBasis, TimeUnit, Window,
)

from .authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from .patient_schedule import PatientScheduleService
from .patients import AssignmentRecord, PatientAnchorRecord, PatientRecord
from .errors import DuplicateRecordError, StaleRevisionError
from .ports import UnitOfWork
from .records import AuditRecord
from .schedule_review import ScheduleReviewService

PILOT_PROTOCOL_NUMBER = "MTB-UCTSM-PILOT-001"
PILOT_PATIENT_CODES = ("PILOT-P001", "PILOT-P002")
PILOT_BASELINE = date(2026, 1, 1)
PILOT_ENVIRONMENT = "local-synthetic"


class PilotRefusedError(ValueError):
    """The code already belongs to something that is not this pilot."""


def _event(code: str, name: str, days: int, **extra) -> Event:
    return Event(
        code=code, protocol_label=name, display_name=name, event_type=extra.pop("event_type", "VISIT"),
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"),
                            offset=TemporalAmount(value=days, unit=TimeUnit.DAY)), **extra)


def build_pilot_schedule(protocol_version_id: UUID, name: str) -> UniversalSchedule:
    evidence = Evidence(
        evidence_type="SYNTHETIC_PROTOCOL_TEXT", page_number=1,
        section_title="Synthetic pilot schedule",
        source_text="Treatment Day 1 at Day 7; weekly dosing; labs 7 days after the treatment "
                    "date; baseline imaging at Day 30 (+/-3 days). Synthetic, not a real protocol.")
    c1d1 = _event("C1D1", "Cycle 1 Day 1", 7, event_type="TREATMENT")
    dose = _event("DOSE", "Weekly dose", 14, recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=7, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="BASELINE"),
        termination=RecurrenceTermination(type="COUNT", count=4),
        basis=RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, basis_status="STATED"))
    lab = Event(
        code="LAB_AFTER_TX", protocol_label="Labs after treatment", display_name="Labs after treatment",
        event_type="VISIT",
        timing=OffsetTiming(
            reference=EventReference(event_code="C1D1", temporal_basis=TemporalBasis.ACTUAL_TREATMENT),
            offset=TemporalAmount(value=7, unit=TimeUnit.DAY)),
        dependencies=[Dependency(source_event_code="C1D1", dependency_type="TEMPORAL")],
        dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    imaging = _event("IMAGING_BASELINE", "Baseline imaging", 30,
                     window=Window(before=NonNegativeTemporalAmount(value=3, unit=TimeUnit.DAY),
                                   after=NonNegativeTemporalAmount(value=3, unit=TimeUnit.DAY)))
    events = [c1d1, dose, lab, imaging]
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        paths = [("EVENT_NAME", "display_name"), ("TIMING", "timing")]
        if event.recurrence is not None:
            paths.append(("RECURRENCE", "recurrence"))
        if event.dependencies:
            paths += [("DEPENDENCY", "dependencies"), ("DEPENDENCY_MODE", "dependency_mode")]
        if event.window is not None:
            paths.append(("WINDOW", "window"))
        for claim_type, path in paths:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path, confidence=1))

    treat = ScheduleTrack(
        code="TREATMENT", track_type="TREATMENT", display_name="Treatment", anchor_code="BASELINE",
        rebase_policy=TrackRebasePolicy.MANUAL_REBASE, process_code="TREATMENT_PROC",
        stoppable=True, review_state="CONFIRMED", evidence_refs=[evidence.id])
    labs = ScheduleTrack(
        code="LABS", track_type="LABORATORY", display_name="Laboratory", anchor_code="BASELINE",
        rebase_policy=TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE, review_state="CONFIRMED",
        evidence_refs=[evidence.id])
    imag = ScheduleTrack(
        code="IMAGING", track_type="IMAGING", display_name="Imaging", anchor_code="BASELINE",
        rebase_policy=TrackRebasePolicy.FIXED_FROM_ANCHOR, review_state="CONFIRMED",
        evidence_refs=[evidence.id])
    for code, track in (("C1D1", treat), ("DOSE", treat), ("LAB_AFTER_TX", labs),
                        ("IMAGING_BASELINE", imag)):
        event = next(item for item in events if item.code == code)
        event.track_id, event.track_scope = track.id, "TRACK"
    process = ProcessDefinition(
        code="TREATMENT_PROC", display_name="Treatment", kind=ProcessKind.INTERVENTION,
        event_codes=["C1D1", "DOSE"], hold_resume_policy=HoldResumePolicy.SHIFT_BY_HOLD,
        evidence_refs=[evidence.id])
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="PROCESS", claim_entity_type="PROCESS",
        claim_entity_id=process.id, claim_path="definition", confidence=1))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name=name, description="Synthetic Mongo-native pilot schedule",
            protocol_version_id=protocol_version_id, status=ScheduleStatus.VALIDATION_REQUIRED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED", derivation_rule={"role": "SCHEDULE_ORIGIN"},
                        evidence_refs=[evidence.id])],
        events=events, evidence=[evidence], claim_evidence=claims, processes=[process],
        tracks=[treat, labs, imag],
        track_links=[TrackLink(source_track_code="TREATMENT", target_track_code="LABS",
                               relation="FOLLOWS_ACTUAL", review_state="CONFIRMED",
                               evidence_refs=[evidence.id])])


PILOT_AMENDMENT_LABEL = "Pilot 2.0"


def build_pilot_amendment_schedule(protocol_version_id: UUID, name: str) -> UniversalSchedule:
    """The synthetic Amendment 1 of the pilot (NOT a real protocol): treatment Day 1 moves
    from Day 7 to Day 10, weekly dosing runs five weeks instead of four, the imaging visit
    is re-coded (same visit name -> a PROPOSED rename a reviewer must confirm) and a safety
    follow-up call is added. Built from the V1 builder so the two share nothing but content
    - identifiers are fresh, exactly as for a real amendment."""
    schedule = build_pilot_schedule(protocol_version_id, name)
    schedule.schedule_metadata.version_number = 2
    schedule.schedule_metadata.description = "Synthetic pilot amendment 1"
    by_code = {item.code: item for item in schedule.events}
    by_code["C1D1"].timing.offset.value = 10
    by_code["DOSE"].recurrence.termination.count = 5
    by_code["IMAGING_BASELINE"].code = "IMAGING_BL"
    evidence = schedule.evidence[0]
    safety = _event("SAFETY_CALL", "Safety follow-up call", 45, event_type="CALL")
    safety.evidence_refs = [evidence.id]
    safety.track_scope = "GLOBAL"
    schedule.events.append(safety)
    for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
        schedule.claim_evidence.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=safety.id, claim_path=path, confidence=1))
    return schedule


def pilot_external_trial_id(organization_id: UUID) -> str:
    """Deterministic and organization-scoped: also the concurrency fence (the store's
    unique (organization, external trial id) index admits exactly one pilot trial)."""
    return f"pilot-{organization_id}"


def pilot_external_patient_id(organization_id: UUID, code: str) -> str:
    return f"pilot-{organization_id}-{code}"


class PilotService:
    """Idempotent, resumable and reconciling. Each step is its own transaction and is
    safe to repeat; ``seed`` always walks every step and repairs what is missing, so a
    failure at any point is healed by simply seeding again (no history is deleted and
    a non-pilot trial is never repurposed)."""

    _RETRIES = 3

    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self._review = ScheduleReviewService(uow)
        self._patients = PatientScheduleService(uow)

    def seed(
        self, *, organization_id: UUID, editor_id: UUID, approver_id: UUID,
        baseline: date = PILOT_BASELINE,
    ) -> dict[str, object]:
        if editor_id == approver_id:
            raise PilotRefusedError(
                "the pilot is approved by a different person than its editor (maker-checker)")
        trial_id, version_id = self._retrying(
            lambda: self._ensure_draft(organization_id, editor_id))
        self._retrying(lambda: self._ensure_approved(version_id, approver_id))
        for code in PILOT_PATIENT_CODES:
            self._retrying(lambda code=code: self._ensure_patient(
                organization_id, trial_id, version_id, code, baseline, editor_id))
        return self.describe(organization_id, trial_id)

    def seed_amendment(
        self, *, organization_id: UUID, editor_id: UUID, approver_id: UUID,
        builder=None,
    ) -> dict[str, object]:
        """Add (idempotently) the synthetic Amendment 1 of the pilot: a new protocol
        version and schedule version that declares it amends Pilot 1.0, drafted by the
        editor and approved by a DIFFERENT person. No patient is moved: adoption is a
        separate, authorised decision per patient."""
        if editor_id == approver_id:
            raise PilotRefusedError(
                "the pilot amendment is approved by a different person than its editor")
        trial_id, v1_id, v2_id = self._retrying(
            lambda: self._ensure_amendment_draft(organization_id, editor_id, builder))
        self._retrying(lambda: self._ensure_approved(v2_id, approver_id))
        description = self.describe(organization_id, trial_id)
        description.update({"from_schedule_version_id": str(v1_id),
                            "to_schedule_version_id": str(v2_id),
                            "amendment_label": PILOT_AMENDMENT_LABEL})
        return description

    def _ensure_amendment_draft(
        self, organization_id: UUID, editor_id: UUID, builder=None,
    ) -> tuple[UUID, UUID, UUID]:
        def work(tx: UnitOfWork) -> tuple[UUID, UUID, UUID]:
            trial = tx.authoring.find_trial_by_protocol_id(organization_id, PILOT_PROTOCOL_NUMBER)
            if trial is None or not (trial.metadata or {}).get("pilot"):
                raise PilotRefusedError("seed the pilot before its amendment")
            headers = tx.schedules.list_headers(organization_id=organization_id, trial_id=trial.id)
            roots = [item for item in headers if item.based_on_schedule_version_id is None]
            if not roots:
                raise PilotRefusedError("the pilot has no base schedule to amend")
            base = min(roots, key=lambda item: item.created_at)
            existing = next((item for item in headers
                             if item.based_on_schedule_version_id == base.id), None)
            if existing is not None:
                return trial.id, base.id, existing.id
            if base.status != "APPROVED":
                raise PilotRefusedError("the pilot base schedule must be approved first")
            protocol = tx.authoring.find_protocol_for_trial(trial.id)
            definition = tx.authoring.get_definition(base.schedule_definition_id)
            version = ProtocolVersionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                version_label=PILOT_AMENDMENT_LABEL, amendment_number="1",
                document_name="pilot-synthetic-protocol-amendment-1.txt",
                document_uri="private://pilot/synthetic-amendment-1.txt", document_hash="8" * 64,
                uploaded_by=editor_id, extraction_status="READY_FOR_REVIEW",
                metadata={"pilot": True, "synthetic": True})
            tx.authoring.add_protocol_version(version)
            new_definition = ScheduleDefinitionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                protocol_version_id=version.id, name=definition.name,
                schedule_type=definition.schedule_type)
            tx.authoring.add_definition(new_definition)
            schedule = (builder or build_pilot_amendment_schedule)(version.id, new_definition.name)
            tx.schedules.persist_draft(schedule, schedule_definition_id=new_definition.id)
            tx.schedules.set_lineage(schedule.schedule_version_id, base.id)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=editor_id, action="PILOT_AMENDMENT_SEEDED",
                entity_type="TRIAL", entity_id=trial.id, before=None,
                after={"from": str(base.id), "to": str(schedule.schedule_version_id),
                       "label": PILOT_AMENDMENT_LABEL}))
            return trial.id, base.id, schedule.schedule_version_id

        return self._uow.run(work)

    def _retrying(self, step):
        """A concurrent seeder that wins a unique key (or a revision) is not an error:
        re-run the step, which now finds and reconciles what the winner wrote."""
        for attempt in range(self._RETRIES):
            try:
                return step()
            except (DuplicateRecordError, StaleRevisionError):
                if attempt == self._RETRIES - 1:
                    raise
        raise AssertionError("unreachable")

    # -- step 1: trial + protocol + draft schedule (one transaction) -------------------
    def _ensure_draft(self, organization_id: UUID, editor_id: UUID) -> tuple[UUID, UUID]:
        external_trial_id = pilot_external_trial_id(organization_id)

        def work(tx: UnitOfWork) -> tuple[UUID, UUID]:
            existing = tx.authoring.find_trial_by_protocol_id(organization_id, PILOT_PROTOCOL_NUMBER)
            if existing is None:
                existing = tx.authoring.find_trial_by_external_id(organization_id, external_trial_id)
            if existing is not None:
                if not (existing.metadata or {}).get("pilot"):
                    raise PilotRefusedError(
                        f"{PILOT_PROTOCOL_NUMBER} already names a non-pilot trial; not repurposed")
                if existing.external_trial_id != external_trial_id:
                    tx.authoring.set_trial_external_id(
                        organization_id, existing.id, external_trial_id)
                headers = tx.schedules.list_headers(
                    organization_id=organization_id, trial_id=existing.id)
                if not headers:
                    raise PilotRefusedError(
                        "the pilot trial exists without a schedule; it is not repaired by "
                        "inventing history - remove the empty synthetic trial and seed again")
                latest = max(headers, key=lambda item: item.version_number)
                return existing.id, latest.id
            trial = TrialRecord(
                organization_id=organization_id, protocol_id=PILOT_PROTOCOL_NUMBER,
                study_title="MTB UCTSM synthetic pilot", indication="Synthetic - not a real study",
                sponsor_name="Local pilot", external_trial_id=external_trial_id,
                metadata={"pilot": True, "environment": PILOT_ENVIRONMENT, "synthetic": True})
            tx.authoring.add_trial(trial)
            protocol = ProtocolRecord(
                organization_id=organization_id, trial_id=trial.id,
                protocol_number=PILOT_PROTOCOL_NUMBER)
            tx.authoring.add_protocol(protocol)
            version = ProtocolVersionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                version_label="Pilot 1.0", document_name="pilot-synthetic-protocol.txt",
                document_uri="private://pilot/synthetic.txt", document_hash="9" * 64,
                uploaded_by=editor_id, extraction_status="READY_FOR_REVIEW",
                metadata={"pilot": True, "synthetic": True})
            tx.authoring.add_protocol_version(version)
            tx.authoring.set_current_protocol_version(protocol.id, version.id)
            definition = ScheduleDefinitionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                protocol_version_id=version.id, name="Pilot schedule", schedule_type="PRIMARY")
            tx.authoring.add_definition(definition)
            schedule = build_pilot_schedule(version.id, definition.name)
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=editor_id, action="PILOT_SEEDED",
                entity_type="TRIAL", entity_id=trial.id, before=None,
                after={"protocol_number": PILOT_PROTOCOL_NUMBER, "environment": PILOT_ENVIRONMENT,
                       "schedule_version_id": str(schedule.schedule_version_id)}))
            return trial.id, schedule.schedule_version_id

        return self._uow.run(work)

    # -- step 2: two-person approval through the shared maker-checker boundary ---------
    def _ensure_approved(self, version_id: UUID, approver_id: UUID) -> None:
        status = self._uow.read(lambda tx: tx.schedules.get_header(version_id).status)
        if status == "APPROVED":
            return
        self._review.approve_reviewed_table(
            version_id, reviewer_id=approver_id,
            reason="Synthetic pilot schedule reviewed against its own source text")

    # -- step 3: ONE transaction per patient: record + anchor + assignment + evaluation ---
    def _ensure_patient(
        self, organization_id: UUID, trial_id: UUID, version_id: UUID, code: str,
        baseline: date, actor_id: UUID,
    ) -> None:
        external = pilot_external_patient_id(organization_id, code)
        horizon = baseline + timedelta(days=365)

        def work(tx: UnitOfWork) -> None:
            schedule = tx.schedules.get(version_id)
            anchor = next(item for item in schedule.anchors if item.code == "BASELINE")
            patient = next((item for item in tx.patients.list_for_trial(organization_id, trial_id)
                            if item.patient_code == code), None)
            if patient is None:
                patient = PatientRecord(
                    organization_id=organization_id, trial_id=trial_id, patient_code=code,
                    external_patient_id=external, current_schedule_version_id=version_id,
                    authority_plane="MONGO")
                tx.patients.add(patient)
                tx.audit.append(AuditRecord(
                    organization_id=organization_id, actor_id=actor_id,
                    action="PILOT_PATIENT_ENROLLED", entity_type="PATIENT", entity_id=patient.id,
                    before=None, after={"authority_plane": "MONGO", "patient_code": code}))
            else:
                if patient.authority_plane != "MONGO":
                    raise PilotRefusedError(
                        f"{code} exists with another authority; it is not relabelled")
                if patient.external_patient_id != external:
                    # a synthetic pilot patient seeded before ids were organization-scoped
                    patient = tx.patients.save(patient.model_copy(
                        update={"external_patient_id": external}))
                if patient.current_schedule_version_id != version_id:
                    raise PilotRefusedError(
                        f"{code} is pinned to another schedule version; not moved by the seed")
            if not tx.facts.list_anchors(patient.id, statuses={"CONFIRMED", "PROVISIONAL"}):
                tx.facts.add_anchor(PatientAnchorRecord(
                    organization_id=organization_id, patient_id=patient.id,
                    anchor_definition_id=anchor.id, anchor_code="BASELINE", value_date=baseline,
                    status="CONFIRMED", source_type="OPERATIONAL_ENROLMENT"))
            if not tx.impacts.list_assignments(patient.id):
                tx.impacts.add_assignment(AssignmentRecord(
                    organization_id=organization_id, patient_id=patient.id,
                    schedule_version_id=version_id, assignment_type="ENROLMENT",
                    reason="Native pilot enrolment", assigned_by=actor_id))
            if tx.evaluations.get_pointer(patient.id, version_id) is None:
                # inside the SAME transaction: the patient is never visible without it
                self._patients.evaluate_in(
                    tx, patient.id, organization_id=organization_id, horizon=horizon,
                    schedule_version_id=version_id,
                    idempotency_key=f"pilot-first-evaluation:{trial_id}:{code}",
                    actor_id=actor_id, reason="Native pilot enrolment: first evaluation")

        self._uow.run(work)

    # -- description ---------------------------------------------------------------
    def describe(self, organization_id: UUID, trial_id: UUID) -> dict[str, object]:
        def read(tx: UnitOfWork) -> dict[str, object]:
            trial = tx.authoring.get_trial(organization_id, trial_id)
            patients = tx.patients.list_for_trial(organization_id, trial_id)
            rows = []
            for item in patients:
                version = item.current_schedule_version_id
                pointer = (tx.evaluations.get_pointer(item.id, version)
                           if version is not None else None)
                rows.append({
                    "patient_id": str(item.id), "patient_code": item.patient_code,
                    "external_patient_id": item.external_patient_id,
                    "authority_plane": item.authority_plane,
                    "schedule_version_id": str(version),
                    "evaluated": pointer is not None})
            return {
                "trial_id": str(trial.id), "external_trial_id": trial.external_trial_id,
                "protocol_number": PILOT_PROTOCOL_NUMBER, "environment": PILOT_ENVIRONMENT,
                "complete": (len(rows) == len(PILOT_PATIENT_CODES)
                             and all(row["evaluated"] for row in rows)),
                "patients": rows}
        return self._uow.read(read)
