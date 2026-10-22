"""Builders shared by the contract suites."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.application.authoring import (
    ProtocolRecord,
    ProtocolVersionRecord,
    ScheduleDefinitionRecord,
    TrialRecord,
)
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, ConditionalAction, ConfinementDefinition, Dependency,
    DependencyMode, Event, Evidence, Qualifier, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule, ValidationIssue,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, OffsetTiming, TemporalAmount, TemporalBasis,
)

ORG = UUID("11111111-1111-4111-8111-111111111111")


def seed_definition(uow, *, org: UUID = ORG, name: str = "Primary") -> ScheduleDefinitionRecord:
    trial = TrialRecord(organization_id=org, external_trial_id=f"ext-{uuid4().hex[:8]}",
                        study_title="Study")
    protocol = ProtocolRecord(organization_id=org, trial_id=trial.id, protocol_number="ABC-123")
    version = ProtocolVersionRecord(
        organization_id=org, trial_id=trial.id, protocol_id=protocol.id, version_label="1",
        document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
        effective_date=date(2026, 3, 1))
    definition = ScheduleDefinitionRecord(
        organization_id=org, trial_id=trial.id, protocol_id=protocol.id,
        protocol_version_id=version.id, name=name)

    def write(tx):
        tx.authoring.add_trial(trial)
        tx.authoring.add_protocol(protocol)
        tx.authoring.add_protocol_version(version)
        tx.authoring.add_definition(definition)

    uow.run(write)
    return definition


def amount(days: int) -> TemporalAmount:
    return TemporalAmount(value=days, unit="DAY")


def build_schedule(
    definition: ScheduleDefinitionRecord, *, status=ScheduleStatus.VALIDATION_REQUIRED,
    version_number: int = 1,
) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=2, source_text="Day 30")
    dose = Event(
        code="DOSE", protocol_label="Dose", display_name="Dose", event_type="TREATMENT",
        sequence_number=1,
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=amount(0)),
        evidence_refs=[evidence.id], dependency_mode=DependencyMode.NOMINAL)
    follow_up = Event(
        code="FOLLOW_UP", protocol_label="Follow-up", display_name="Follow-up", event_type="VISIT",
        sequence_number=2,
        timing=OffsetTiming(
            reference=EventReference(event_code="DOSE", temporal_basis=TemporalBasis.CURRENT_PLANNED),
            offset=amount(7)),
        dependencies=[
            Dependency(source_event_code="DOSE", dependency_type="TEMPORAL"),
            Dependency(source_event_code="GHOST", dependency_type="TEMPORAL"),  # preserved invalid draft edge
        ],
        dependency_mode=DependencyMode.NOMINAL, evidence_refs=[evidence.id],
        qualifiers=[Qualifier(
            marker="a", text="Only when clinically indicated", scope="VISIT", category="CONDITION",
            target_codes=["FOLLOW_UP"], resolved=True, evidence_refs=[evidence.id])],
        conditional_actions=[ConditionalAction(
            action_type="MANUAL_REVIEW", target_code="FOLLOW_UP",
            condition=ComparisonCondition(
                operator="EQUALS", left=FieldOperand(field="patient.symptomatic"),
                right=LiteralOperand(value=True)),
            evidence_refs=[evidence.id])],
        confinement=ConfinementDefinition(
            admission_event_code="DOSE", dose_event_codes=["DOSE"],
            discharge_event_code="FOLLOW_UP", evidence_refs=[evidence.id]),
        activities=[Activity(
            protocol_label="ECG", display_name="ECG", activity_type="ASSESSMENT",
            qualifiers=[Qualifier(
                marker="b", text="Arm A only", scope="ACTIVITY", category="APPLICABILITY",
                target_codes=["ECG"], resolved=True, evidence_refs=[evidence.id])])])
    unnumbered = Event(
        code="AD_HOC", protocol_label="Ad hoc", display_name="Ad hoc", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=amount(90)),
        evidence_refs=[evidence.id])
    claims = [
        ClaimEvidence(evidence_id=evidence.id, claim_type=claim, claim_entity_type="EVENT",
                      claim_entity_id=event.id, claim_path=path, confidence=0.9)
        for event in (dose, follow_up, unnumbered)
        for claim, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name=definition.name, status=status, version_number=version_number,
            protocol_version_id=definition.protocol_version_id),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        # inserted out of order on purpose: reads must order by (sequence, code)
        events=[follow_up, unnumbered, dose], evidence=[evidence], claim_evidence=claims,
        validation_issues=[ValidationIssue(
            issue_code="AMBIGUOUS_TIMING", severity="WARNING", message="check", entity_type="EVENT",
            entity_id=follow_up.id)],
    )
