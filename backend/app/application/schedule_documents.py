"""The stored shape of one schedule version, shared by every adapter.

``build_parts`` turns the domain ``UniversalSchedule`` into the pieces a store
keeps (a header plus separate event / evidence / claim / issue documents) and
``assemble`` turns them back. Both the MongoDB adapter and the in-memory fake use
exactly these functions, so the two cannot disagree about what is persisted.

Behaviour deliberately preserved from the SQL repository this replaces:
* a dependency naming an event that is not in the version is dropped at persist;
* an event may not depend on itself;
* a renamed event code is followed by the dependencies that point at it;
* events read back ordered by (sequence_number, code);
* stored payloads are JSON-mode, so dates stay ISO strings and timings round-trip
  exactly as the SQL JSON columns held them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic_core import to_jsonable_python

from app.domain.schedule import models as domain
from app.domain.schedule.temporal_basis import upgrade_event_payload

from .authoring import ScheduleDefinitionRecord, ScheduleVersionHeader
from .records import utc_now

VALIDATOR_VERSION = "uctsm-validator.v1"


def to_json(value: object | None) -> Any:
    if value is None:
        return None
    return to_jsonable_python(value, by_alias=True)


def _event_document(event: domain.Event, *, valid_codes: set[str]) -> dict[str, Any]:
    for dependency in event.dependencies:
        if dependency.source_event_code == event.code:
            raise ValueError("an event cannot depend on itself")
    kept = [item for item in event.dependencies if item.source_event_code in valid_codes]
    stored = event.model_copy(update={"dependencies": kept}, deep=True)
    return {
        "id": event.id, "code": event.code, "sequence_number": event.sequence_number,
        "event": to_json(stored),
    }


def build_parts(
    schedule: domain.UniversalSchedule, *, definition: ScheduleDefinitionRecord,
    extraction_run_id: UUID | None,
) -> dict[str, Any]:
    meta = schedule.schedule_metadata
    if meta.protocol_version_id is None:
        raise ValueError("protocol_version_id is required to persist schedule evidence")
    now = utc_now()
    header = ScheduleVersionHeader(
        id=schedule.schedule_version_id, organization_id=definition.organization_id,
        trial_id=definition.trial_id, protocol_id=definition.protocol_id,
        protocol_version_id=definition.protocol_version_id,
        schedule_definition_id=definition.id, version_number=meta.version_number,
        status=meta.status.value, extraction_run_id=extraction_run_id,
        schema_version=schedule.schema_version, created_at=schedule.created_at, updated_at=now,
    )
    content = {
        "metadata": to_json(meta.model_dump(
            mode="python", exclude={"status", "version_number", "protocol_version_id"})),
        "processes": to_json(schedule.processes),
        "holiday_calendars": to_json(schedule.holiday_calendars),
        "dimensions": to_json(schedule.dimensions),
        "conditional_definitions": to_json(schedule.conditional_definitions),
        "repeat_blocks": to_json(schedule.repeat_blocks),
        "confinement_episodes": to_json(schedule.confinement_episodes),
        "epochs": [to_json(item) for item in schedule.epochs],
        "arms": [to_json(item) for item in schedule.arms],
        "cohorts": [to_json(item) for item in schedule.cohorts],
        "populations": [to_json(item) for item in schedule.populations],
        "anchors": [to_json(item) for item in schedule.anchors],
    }
    codes = {item.code for item in schedule.events}
    return {
        "header": header,
        "content": content,
        "events": [_event_document(item, valid_codes=codes) for item in schedule.events],
        "evidence": [
            {"id": item.id, "protocol_version_id": meta.protocol_version_id, "evidence": to_json(item)}
            for item in schedule.evidence
        ],
        "claims": [{"id": uuid4(), "claim": to_json(item)} for item in schedule.claim_evidence],
        "issues": [issue_document(item) for item in schedule.validation_issues],
    }


def issue_document(issue: domain.ValidationIssue) -> dict[str, Any]:
    return {
        "id": issue.id, "validator_version": VALIDATOR_VERSION, "status": issue.status.value,
        "resolved_by": None, "resolved_at": None, "resolution_note": None,
        "issue": to_json(issue),
    }


def event_sort_key(doc: dict[str, Any]) -> tuple[int, int, str]:
    sequence = doc.get("sequence_number")
    return (0 if sequence is None else 1, sequence or 0, doc["code"])


def assemble(
    *, header: ScheduleVersionHeader, definition: ScheduleDefinitionRecord,
    content: dict[str, Any], events: list[dict[str, Any]], evidence: list[dict[str, Any]],
    claims: list[dict[str, Any]], issues: list[dict[str, Any]],
) -> domain.UniversalSchedule:
    metadata = dict(content.get("metadata") or {})
    metadata.update({
        "name": metadata.get("name", definition.name),
        "description": metadata.get("description", definition.description),
        "schedule_type": metadata.get("schedule_type", definition.schedule_type),
        "protocol_version_id": definition.protocol_version_id,
        "version_number": header.version_number,
        "status": header.status,
        "effective_from": header.effective_from,
    })
    ordered = sorted(events, key=event_sort_key)
    return domain.UniversalSchedule(
        schedule_version_id=header.id,
        schedule_metadata=domain.ScheduleMetadata.model_validate(metadata),
        epochs=[domain.Epoch.model_validate(item) for item in content.get("epochs", [])],
        arms=[domain.StudyDimension.model_validate(item) for item in content.get("arms", [])],
        cohorts=[domain.StudyDimension.model_validate(item) for item in content.get("cohorts", [])],
        populations=[domain.StudyDimension.model_validate(item) for item in content.get("populations", [])],
        anchors=[domain.Anchor.model_validate(item) for item in content.get("anchors", [])],
        # P1-01: events stored before ``temporal_basis`` existed are upgraded here.
        events=[domain.Event.model_validate(upgrade_event_payload(item["event"])) for item in ordered],
        dimensions=[domain.GenericDimension.model_validate(item) for item in content.get("dimensions") or []],
        conditional_definitions=[
            domain.ConditionalDefinition.model_validate(item)
            for item in content.get("conditional_definitions") or []],
        repeat_blocks=[domain.RepeatBlock.model_validate(item) for item in content.get("repeat_blocks") or []],
        confinement_episodes=[
            domain.ConfinementEpisodeDefinition.model_validate(item)
            for item in content.get("confinement_episodes") or []],
        processes=[domain.ProcessDefinition.model_validate(item) for item in content.get("processes") or []],
        holiday_calendars=[
            domain.HolidayCalendar.model_validate(item) for item in content.get("holiday_calendars") or []],
        evidence=[domain.Evidence.model_validate(item["evidence"]) for item in evidence],
        claim_evidence=[domain.ClaimEvidence.model_validate(item["claim"]) for item in claims],
        validation_issues=[domain.ValidationIssue.model_validate(item["issue"]) for item in issues],
        created_at=header.created_at,
    )


def rename_dependency_source(event_json: dict[str, Any], old: str, new: str) -> bool:
    """Follow a renamed event code through one stored event's dependencies."""
    changed = False
    for dependency in event_json.get("dependencies") or []:
        if dependency.get("source_event_code") == old:
            dependency["source_event_code"] = new
            changed = True
    return changed


def stamp(now: datetime | None = None) -> datetime:
    return now or utc_now()
