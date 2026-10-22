"""Explicit unsupported/manual-only handling; never an automatic timing fallback."""
from datetime import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from .timing import StrictModel

KEY = "rule_handling"


class RuleHandling(StrictModel):
    kind: Literal["UNSUPPORTED", "MANUAL_ONLY"]
    reason: str = Field(min_length=3)
    owner_id: UUID
    evidence_refs: list[UUID] = Field(min_length=1)
    policy_reference: str = Field(min_length=3)
    status: Literal["PENDING", "APPROVED"] = "PENDING"
    requested_by: UUID
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    approval_decision_id: UUID | None = None
    event_fingerprint: str


def fingerprint(event) -> str:
    payload = event.model_dump(mode="json")
    payload["metadata"].pop(KEY, None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def handling(event) -> RuleHandling | None:
    raw = event.metadata.get(KEY)
    return RuleHandling.model_validate(raw) if raw is not None else None


def approved_manual(event) -> bool:
    try:
        rule = handling(event)
    except ValidationError:
        return False
    return bool(rule and rule.kind == "MANUAL_ONLY" and rule.status == "APPROVED"
                and rule.approved_by is not None and rule.approved_by != rule.requested_by
                and rule.approval_decision_id is not None and rule.approved_at is not None
                and rule.event_fingerprint == fingerprint(event))


def validation_issues(schedule, issues):
    from .models import Severity, ValidationIssue

    permitted = set(); extra = []; evidence = {row.id for row in schedule.evidence}
    for event in schedule.events:
        if KEY not in event.metadata:
            continue
        try:
            rule = handling(event)
            valid = set(rule.evidence_refs) <= evidence
        except ValidationError:
            rule = None; valid = False
        if event.repeats and rule is not None:
            extra.append(ValidationIssue(issue_code="MANUAL_SERIES_UNSUPPORTED", severity=Severity.ERROR,
                blocking=True, entity_type="EVENT", entity_id=event.id,
                message="A repeating manual series needs an explicit occurrence-handling policy before approval.",
                details={"field_path": f"events/{event.code}/metadata/{KEY}"}))
        if rule is not None and valid and approved_manual(event):
            permitted.add(event.id)
        else:
            code = "UNSUPPORTED_RULE" if rule and rule.kind == "UNSUPPORTED" else "MANUAL_REVIEW_REQUIRED"
            extra.append(ValidationIssue(
                issue_code=code, severity=Severity.ERROR, blocking=True, entity_type="EVENT", entity_id=event.id,
                message="This rule needs an explicit independent manual-only decision; no automatic date is permitted.",
                details={"field_path": f"events/{event.code}/metadata/{KEY}"}))
    # Only the timing/capability gap can be handled manually. Missing evidence,
    # foreign references, contradictions and downstream capability gaps stay blocking.
    handled_codes = {"UNSUPPORTED_OPERATOR", "UNSUPPORTED_TIMING", "AMBIGUOUS_TIMING"}
    return [row for row in issues if not (row.entity_id in permitted and row.issue_code in handled_codes)] + extra
