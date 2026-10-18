"""Isolated, tenant-scoped demo data for the in-app UCTSM workbench.

Port of ``app.services.demo_service`` onto the repository ports: the trial,
protocol, definition, draft schedule, patient and audit event commit as ONE unit
of work, so a half-seeded demo can never be observed.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, PositiveTemporalAmount, ProtocolDefinedTiming, TemporalAmount,
    TriggeredTiming,
    TriggerWithin, Window,
)

from .authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from .patients import PatientRecord
from .ports import UnitOfWork
from .records import AuditRecord

DEMO_PROTOCOL_NUMBER = "UCTSM-DEMO-001"
DEMO_PATIENT_CODE = "DEMO-P001"


def build_demo_schedule(
    protocol_version_id: UUID, name: str, description: str | None,
) -> UniversalSchedule:
    evidence = Evidence(
        evidence_type="DEMO_PROTOCOL_TEXT", page_number=84,
        section_title="Safety and Progression Follow-up",
        source_text=(
            "Safety follow-up occurs 30 days after last dose, ±5 days. "
            "Following disease progression, perform assessment within 7 days."
        ),
    )
    last_dose = Anchor(
        code="LAST_DOSE", display_name="Last Dose", anchor_type="LAST_DOSE",
        status="RESOLVED", evidence_refs=[evidence.id],
    )
    progression_anchor = Anchor(
        code="PROGRESSION", display_name="Disease Progression",
        anchor_type="PROGRESSION", status="RESOLVED", evidence_refs=[evidence.id],
    )
    safety = Event(
        code="SAFETY_FOLLOW_UP", protocol_label="Safety Follow-up",
        display_name="Safety Follow-up", event_type="SAFETY_ASSESSMENT",
        timing=NominalWindowTiming(
            nominal=OffsetTiming(
                reference=AnchorReference(code="LAST_DOSE"),
                offset=TemporalAmount(value=30, unit="DAY"),
            ),
            window=Window(
                before=NonNegativeTemporalAmount(value=5, unit="DAY"),
                after=NonNegativeTemporalAmount(value=5, unit="DAY"),
            ),
        ),
        evidence_refs=[evidence.id],
    )
    progression = Event(
        code="PROGRESSION_ASSESSMENT", protocol_label="Progression Assessment",
        display_name="Progression Assessment", event_type="ASSESSMENT",
        timing=TriggeredTiming(
            trigger=AnchorReference(code="PROGRESSION"),
            timing_after_trigger=TriggerWithin(
                duration=PositiveTemporalAmount(value=7, unit="DAY"),
            ),
        ),
        conditions=[ComparisonCondition(
            operator="EQUALS", left=FieldOperand(field="patient.progression"),
            right=LiteralOperand(value=True),
        )],
        evidence_refs=[evidence.id],
    )
    # Doc 10 s13-s15. Every real protocol has one of these, and it is the
    # case most likely to be mishandled, so the demo carries it.
    unscheduled = Event(
        code="UNSCHEDULED_VISIT", protocol_label="Unscheduled Visit",
        display_name="Unscheduled Visit", event_type="UNSCHEDULED",
        activation="ON_DEMAND", allowed_visit_modes=["CLINIC", "TELEPHONE"],
        timing=ProtocolDefinedTiming(handler="on-demand"),
        evidence_refs=[evidence.id],
    )
    claims = []
    for event in (safety, progression, unscheduled):
        claims.extend([
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="EVENT_NAME",
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="display_name", confidence=1,
            ),
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="TIMING",
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="timing", confidence=1,
            ),
        ])
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="CONDITION",
        claim_entity_type="EVENT", claim_entity_id=progression.id,
        claim_path="conditions", confidence=1,
    ))
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name=name, description=description,
            protocol_version_id=protocol_version_id,
            status=ScheduleStatus.VALIDATION_REQUIRED,
        ),
        anchors=[last_dose, progression_anchor], events=[safety, progression, unscheduled],
        evidence=[evidence], claim_evidence=claims,
    )
    return schedule


class DemoService:
    """Creates isolated, tenant-scoped test data for the in-app UCTSM workbench."""

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    def seed(self, *, organization_id: UUID, actor_id: UUID) -> dict[str, str]:
        def work(tx: UnitOfWork) -> dict[str, str]:
            existing = tx.authoring.find_trial_by_protocol_id(
                organization_id, DEMO_PROTOCOL_NUMBER)
            if existing is not None:
                patient = next(
                    (item for item in tx.patients.list_for_trial(organization_id, existing.id)
                     if item.patient_code == DEMO_PATIENT_CODE), None)
                if patient is not None and patient.current_schedule_version_id:
                    return {
                        "trial_id": str(existing.id), "patient_id": str(patient.id),
                        "schedule_version_id": str(patient.current_schedule_version_id),
                    }

            trial = TrialRecord(
                organization_id=organization_id, protocol_id=DEMO_PROTOCOL_NUMBER,
                study_title="Universal Schedule Interactive Test",
                indication="Demonstration only", sponsor_name="Local UCTSM Demo",
                metadata={"demo": True})
            tx.authoring.add_trial(trial)
            protocol = ProtocolRecord(
                organization_id=organization_id, trial_id=trial.id,
                protocol_number=DEMO_PROTOCOL_NUMBER)
            tx.authoring.add_protocol(protocol)
            protocol_version = ProtocolVersionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                version_label="Demo 1.0", document_name="uctsm-demo-protocol.txt",
                document_uri="private://uctsm-demo/uctsm-demo-protocol.txt",
                document_hash="d" * 64, uploaded_by=actor_id,
                extraction_status="READY_FOR_REVIEW", metadata={"demo": True})
            tx.authoring.add_protocol_version(protocol_version)
            tx.authoring.set_current_protocol_version(protocol.id, protocol_version.id)
            definition = ScheduleDefinitionRecord(
                organization_id=organization_id, trial_id=trial.id, protocol_id=protocol.id,
                protocol_version_id=protocol_version.id, name="Primary Demo Schedule",
                description="Interactive UCTSM review and patient scheduling test",
                schedule_type="PRIMARY")
            tx.authoring.add_definition(definition)
            schedule = build_demo_schedule(
                protocol_version.id, definition.name, definition.description)
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
            patient = PatientRecord(
                organization_id=organization_id, trial_id=trial.id,
                patient_code=DEMO_PATIENT_CODE,
                authority_plane="MONGO",          # native to this plane
                current_schedule_version_id=schedule.schedule_version_id)
            tx.patients.add(patient)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="UCTSM_DEMO_SEEDED", entity_type="TRIAL", entity_id=trial.id,
                before=None, after={
                    "schedule_version_id": str(schedule.schedule_version_id),
                    "patient_id": str(patient.id)}))
            return {
                "trial_id": str(trial.id), "patient_id": str(patient.id),
                "schedule_version_id": str(schedule.schedule_version_id),
            }

        return self._uow.run(work)
