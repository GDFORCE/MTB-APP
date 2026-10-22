"""Reviewed resolution of conflicting / ambiguous schedule interpretations (P7-03).

An event whose interpretation is AMBIGUOUS, CONFLICTING or UNRESOLVED blocks approval. A reviewer
may confirm it only by naming the evidence that supports the confirmed reading and giving a reason;
the competing claims stay in the immutable review decision, the edit counts as material for
maker-checker, and an event with no evidence cannot be confirmed at all. Nothing here picks a
"most plausible" reading, and a generic event correction cannot flip an unresolved status.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.schedule.models import InterpretationStatus, ScheduleStatus
from app.domain.schedule.validator import ScheduleValidator

from .authoring import ReviewDecisionRecord
from .manual_rules import ManualRuleService
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

UNRESOLVED = {InterpretationStatus.AMBIGUOUS, InterpretationStatus.CONFLICTING, InterpretationStatus.UNRESOLVED}
QUOTE_LIMIT = 400


def _evidence_row(item) -> dict[str, Any]:
    text = (item.source_text or "").strip()
    return {"evidence_id": str(item.id), "type": item.evidence_type, "page": item.page_number,
            "section": item.section_title, "table": item.table_title, "row": item.row_identifier,
            "column": item.column_identifier,
            "quote": text[:QUOTE_LIMIT] + ("..." if len(text) > QUOTE_LIMIT else "")}


class ConflictResolutionService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def list_conflicts(self, organization_id: UUID, trial_id: UUID, version_id: UUID) -> list[dict[str, Any]]:
        def read(tx):
            _, schedule = ManualRuleService.scope(tx, organization_id, trial_id, version_id)
            by_id = {item.id: item for item in schedule.evidence}
            rows = []
            for event in schedule.events:
                if event.interpretation_status not in UNRESOLVED:
                    continue
                rows.append({
                    "event_id": str(event.id), "event_code": event.code, "display_name": event.display_name,
                    "status": event.interpretation_status.value,
                    "evidence": [_evidence_row(by_id[ref]) for ref in event.evidence_refs if ref in by_id],
                    "missing_evidence_ids": [str(ref) for ref in event.evidence_refs if ref not in by_id],
                    "resolvable": any(ref in by_id and (by_id[ref].source_text or "").strip()
                                      for ref in event.evidence_refs)})
            return rows
        return self.uow.read(read)

    def resolve_event(self, organization_id: UUID, trial_id: UUID, version_id: UUID, event_id: UUID, *,
                      reviewer_id: UUID, chosen_evidence_ids: list[UUID], confirmed_reading: str,
                      reason: str) -> dict[str, Any]:
        if not reason.strip() or not confirmed_reading.strip():
            raise ValueError("a conflict resolution needs the confirmed reading and the reason")
        if not chosen_evidence_ids:
            raise ValueError("name the evidence that supports the confirmed reading")

        def work(tx):
            header, schedule = ManualRuleService.scope(tx, organization_id, trial_id, version_id)
            if header.status in {"APPROVED", "SUPERSEDED", "ARCHIVED", "REJECTED"}:
                raise ValueError("this schedule version is frozen and cannot be corrected")
            event = next((item for item in schedule.events if item.id == event_id), None)
            if event is None:
                raise KeyError("event not found on this schedule version")
            if event.interpretation_status not in UNRESOLVED:
                raise ValueError("this event has no unresolved interpretation")
            evidence = {item.id: item for item in schedule.evidence}
            unknown = [str(ref) for ref in chosen_evidence_ids if ref not in event.evidence_refs or ref not in evidence]
            if unknown:
                raise ValueError("chosen evidence must be reviewed evidence already linked to this event")
            if any(not (evidence[ref].source_text or "").strip() for ref in chosen_evidence_ids):
                raise ValueError("chosen evidence must quote the protocol text")
            competing = [_evidence_row(evidence[ref]) for ref in event.evidence_refs if ref in evidence]
            before = event.model_dump(mode="json")
            event.interpretation_status = InterpretationStatus.CONFIRMED
            event.requires_review = False
            tx.schedules.replace_event(version_id, event)
            issues = ScheduleValidator().validate(tx.schedules.get(version_id))
            tx.schedules.replace_validation_issues(version_id, issues)
            tx.schedules.transition(version_id, to_status=ScheduleStatus.VALIDATION_REQUIRED.value)
            tx.schedules.record_material_edit(version_id, editor_id=reviewer_id, at=utc_now())
            new_value = {"interpretation_status": "CONFIRMED", "confirmed_reading": confirmed_reading.strip(),
                         "chosen_evidence_ids": [str(ref) for ref in chosen_evidence_ids]}
            tx.schedules.add_decision(ReviewDecisionRecord(
                schedule_version_id=version_id, entity_type="EVENT", entity_id=event.id,
                field_path="interpretation_status", decision="RESOLVE_CONFLICT",
                previous_value={"interpretation_status": before["interpretation_status"],
                                "competing_evidence": competing},
                new_value=new_value, reviewer_id=reviewer_id, reason=reason.strip()))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=reviewer_id, action="SCHEDULE_CONFLICT_RESOLVED",
                entity_type="EVENT", entity_id=event.id, reason=reason.strip(),
                before={"interpretation_status": before["interpretation_status"]}, after=new_value))
            return {"event_id": str(event.id), "status": "CONFIRMED",
                    "remaining_blocking": sorted({item.issue_code for item in issues if item.blocking})}
        return self.uow.run(work)
