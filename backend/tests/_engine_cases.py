"""Shared builders for the schedule-engine case tests (docs/schedule-engine-case-handling.md).

Every schedule built here is APPROVED and carries the claim-level evidence the
validator requires, so the evaluator runs it exactly as it would a real
reviewed protocol. Dates in the tests are the document's worked examples.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, ConditionalDefinition, Event, EvaluatedEvent, Evidence,
    GenericDimension, PatientContext, RepeatBlock, ScheduleMetadata, ScheduleStatus,
    StudyDimension, UniversalSchedule,
)

HORIZON = date(2033, 12, 31)


def anchor(code: str, **changes) -> Anchor:
    values = {
        "code": code, "display_name": code.replace("_", " ").title(),
        "anchor_type": code, "status": "RESOLVED",
    }
    values.update(changes)
    return Anchor(**values)


def visit(code: str, timing, **changes) -> Event:
    values = {
        "code": code, "protocol_label": code,
        "display_name": code.replace("_", " ").title(),
        "event_type": "SITE_VISIT", "timing": timing,
    }
    values.update(changes)
    return Event(**values)


def _event_claims(event: Event, evidence_id) -> list[ClaimEvidence]:
    claim_types = ["EVENT_NAME", "TIMING"]
    for present, claim_type in (
        (event.repeats, "RECURRENCE"),
        (bool(event.applicability), "APPLICABILITY"),
        (bool(event.conditions), "CONDITION"),
        (bool(event.dependencies), "DEPENDENCY"),
        (event.dependency_mode is not None, "DEPENDENCY_MODE"),
        (event.window is not None, "WINDOW"),
    ):
        if present:
            claim_types.append(claim_type)
    return [
        ClaimEvidence(
            evidence_id=evidence_id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=event.id, confidence=0.9,
        )
        for claim_type in claim_types
    ]


def approved(
    events: list[Event],
    *,
    anchors: list[Anchor],
    conditionals: list[ConditionalDefinition] | None = None,
    repeat_blocks: list[RepeatBlock] | None = None,
    arms: list[StudyDimension] | None = None,
    dimensions: list[GenericDimension] | None = None,
    status: ScheduleStatus = ScheduleStatus.APPROVED,
    protocol_version_id=None,
    processes: list | None = None,
    holiday_calendars: list | None = None,
) -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=12, source_text="Protocol rule")
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        claims.extend(_event_claims(event, evidence.id))
    for definition in conditionals or []:
        definition.evidence_refs = [evidence.id]
        claim_types = ["CONDITION", "CONDITIONAL_ACTION"]
        if definition.resolution_condition is not None:
            claim_types.append("CONDITIONAL_RESOLUTION")
        claims.extend(
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="CONDITION", claim_entity_id=definition.id,
                confidence=0.9,
            )
            for claim_type in claim_types
        )
    for block in repeat_blocks or []:
        block.evidence_refs = [evidence.id]
        claims.extend(
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="REPEAT_BLOCK", claim_entity_id=block.id,
                confidence=0.9,
            )
            for claim_type in (
                ("REPEAT_BLOCK", "DEPENDENCY_MODE", "CYCLE_PLAN") if block.cycle_plan
                else ("REPEAT_BLOCK", "DEPENDENCY_MODE"))
        )
    for process in processes or []:
        process.evidence_refs = [evidence.id]
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="PROCESS", claim_entity_type="PROCESS",
            claim_entity_id=process.id, confidence=0.9))
    return UniversalSchedule(
        processes=processes or [], holiday_calendars=holiday_calendars or [],
        schedule_metadata=ScheduleMetadata(
            name="Primary", status=status, protocol_version_id=protocol_version_id),
        anchors=anchors, events=events,
        conditional_definitions=conditionals or [], repeat_blocks=repeat_blocks or [],
        arms=arms or [], dimensions=dimensions or [],
        evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {"patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id}
    values.update(changes)
    return PatientContext(**values)


def evaluate(schedule: UniversalSchedule, ctx: PatientContext, *, horizon: date = HORIZON):
    return ScheduleEvaluator().evaluate(
        schedule, ctx, horizon=horizon, today=date(2026, 9, 29))


def by_code(result) -> dict[str, list[EvaluatedEvent]]:
    grouped: dict[str, list[EvaluatedEvent]] = {}
    for item in result.events:
        grouped.setdefault(item.event_code, []).append(item)
    for items in grouped.values():
        items.sort(key=lambda item: item.occurrence_index)
    return grouped


def only(result, code: str) -> EvaluatedEvent:
    items = by_code(result)[code]
    assert len(items) == 1, f"{code} has {len(items)} occurrences"
    return items[0]


def nominal_dates(result, code: str) -> list[date]:
    return [item.timing.nominal_start for item in by_code(result)[code]]
