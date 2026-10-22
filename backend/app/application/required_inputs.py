"""The list of patient details a clinic must enter, reviewed and acknowledged before a schedule is approved.

The derived part comes from the rules (``domain.schedule.required_inputs``); a reviewer may add custom entries
(for example "ask for the patient's weight"). Everything the reviewer does is an append-only review decision, so the
list and its acknowledgement are auditable and cannot be edited after approval. An acknowledgement is bound to the
fingerprint of the list it was given for: change the list (or the rules behind it) and the acknowledgement lapses.
Approval is refused while a non-empty list has no valid acknowledgement.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.domain.schedule.required_inputs import KINDS, RequiredInput, derive_required_inputs, inputs_hash

from .authoring import ReviewDecisionRecord
from .manual_rules import ManualRuleService
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

FIELD = "required_inputs"
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")
FROZEN = {"APPROVED", "SUPERSEDED", "ARCHIVED", "REJECTED"}
NOT_ACKNOWLEDGED = "REQUIRED_INPUTS_NOT_ACKNOWLEDGED"


def _custom_from(decisions) -> dict[str, RequiredInput]:
    custom: dict[str, RequiredInput] = {}
    for row in sorted((d for d in decisions if d.field_path == FIELD), key=lambda d: (d.created_at, str(d.id))):
        if row.decision == "INPUT_ADD":
            value = row.new_value
            custom[value["code"]] = RequiredInput(code=value["code"], label=value["label"], kind=value["kind"],
                                                  source="CUSTOM", reason=value["reason"])
        elif row.decision == "INPUT_REMOVE":
            custom.pop(row.new_value["code"], None)
    return custom


def effective_inputs(schedule, decisions) -> tuple[list[RequiredInput], list[RequiredInput], list[RequiredInput]]:
    derived = derive_required_inputs(schedule)
    custom = sorted(_custom_from(decisions).values(), key=lambda item: item.code)
    return derived, custom, sorted([*derived, *custom], key=lambda item: item.code)


def acknowledgement(decisions, digest: str) -> dict[str, Any] | None:
    """An acknowledgement of EXACTLY this list (an older one for a different list never counts)."""
    matching = [d for d in decisions if d.field_path == FIELD and d.decision == "INPUTS_ACK"
                and d.new_value.get("list_hash") == digest]
    latest = max(matching, key=lambda d: (d.created_at, str(d.id)), default=None)
    return None if latest is None else {"list_hash": digest, "reviewer_id": str(latest.reviewer_id),
                                        "reason": latest.reason}


def approval_blocker(tx: UnitOfWork, schedule) -> str | None:
    decisions = tx.schedules.list_decisions(schedule.schedule_version_id)
    _, _, effective = effective_inputs(schedule, decisions)
    if not effective:
        return None
    return None if acknowledgement(decisions, inputs_hash(effective)) else NOT_ACKNOWLEDGED


class RequiredInputsService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def view(self, organization_id: UUID, trial_id: UUID, version_id: UUID) -> dict[str, Any]:
        def read(tx):
            header, schedule = ManualRuleService.scope(tx, organization_id, trial_id, version_id)
            decisions = tx.schedules.list_decisions(version_id)
            derived, custom, effective = effective_inputs(schedule, decisions)
            digest = inputs_hash(effective)
            ack = acknowledgement(decisions, digest)
            return {"schedule_version_id": str(version_id), "status": header.status, "list_hash": digest,
                    "derived": [row.as_dict() for row in derived], "custom": [row.as_dict() for row in custom],
                    "effective": [row.as_dict() for row in effective],
                    "acknowledged": bool(ack) if effective else True,
                    "needs_acknowledgement": bool(effective), "acknowledgement": ack}
        return self.uow.read(read)

    def for_enrolment(self, organization_id: UUID, trial_id: UUID, definition_id: UUID) -> dict[str, Any]:
        """What the Add Patient form must ask for against the CURRENT approved version of a schedule definition."""
        from .schedule_resolution import resolve_current_schedule_version

        def read(tx):
            trial = tx.authoring.get_trial(organization_id, trial_id)
            header = resolve_current_schedule_version(tx, organization_id, trial.id, definition_id)
            schedule = tx.schedules.get(header.id)
            decisions = tx.schedules.list_decisions(header.id)
            effective = effective_inputs(schedule, decisions)[2]
            enforced = bool(effective and acknowledgement(decisions, inputs_hash(effective)))
            return {"schedule_version_id": str(header.id), "enforced": enforced,
                    "entries": [row.as_dict() for row in effective] if enforced else []}
        return self.uow.read(read)

    def _editable(self, tx, organization_id, trial_id, version_id):
        header, schedule = ManualRuleService.scope(tx, organization_id, trial_id, version_id)
        if header.status in FROZEN:
            raise ValueError("this schedule version is frozen; its required details can no longer change")
        return header, schedule

    def add_custom(self, organization_id: UUID, trial_id: UUID, version_id: UUID, *, reviewer_id: UUID,
                   code: str, label: str, kind: str, reason: str) -> dict[str, Any]:
        code = code.strip().upper()
        if not CODE.match(code):
            raise ValueError("the code must be 2-41 characters: capital letters, digits and underscores")
        if kind not in KINDS:
            raise ValueError("kind must be DATE, DATETIME, TEXT or NUMBER")
        if len(label.strip()) < 2 or len(reason.strip()) < 3:
            raise ValueError("give the entry a label and say why the protocol needs it")

        def work(tx):
            _, schedule = self._editable(tx, organization_id, trial_id, version_id)
            full = f"CUSTOM:{code}"
            decisions = tx.schedules.list_decisions(version_id)
            if full in {row.code for row in effective_inputs(schedule, decisions)[2]}:
                raise ValueError("that entry already exists")
            value = {"code": full, "label": label.strip(), "kind": kind, "reason": reason.strip()}
            self._decide(tx, organization_id, version_id, reviewer_id, "INPUT_ADD", value, reason)
            return value
        return self.uow.run(work)

    def remove_custom(self, organization_id: UUID, trial_id: UUID, version_id: UUID, *, reviewer_id: UUID,
                      code: str, reason: str) -> dict[str, Any]:
        if len(reason.strip()) < 3:
            raise ValueError("say why the entry is removed")

        def work(tx):
            _, schedule = self._editable(tx, organization_id, trial_id, version_id)
            if code not in _custom_from(tx.schedules.list_decisions(version_id)):
                raise KeyError("custom entry not found")
            self._decide(tx, organization_id, version_id, reviewer_id, "INPUT_REMOVE", {"code": code}, reason)
            return {"code": code}
        return self.uow.run(work)

    def acknowledge(self, organization_id: UUID, trial_id: UUID, version_id: UUID, *, reviewer_id: UUID,
                    reason: str) -> dict[str, Any]:
        if len(reason.strip()) < 3:
            raise ValueError("state that you reviewed the list")

        def work(tx):
            _, schedule = self._editable(tx, organization_id, trial_id, version_id)
            effective = effective_inputs(schedule, tx.schedules.list_decisions(version_id))[2]
            digest = inputs_hash(effective)
            self._decide(tx, organization_id, version_id, reviewer_id, "INPUTS_ACK",
                         {"list_hash": digest, "entries": [row.code for row in effective]}, reason)
            return {"list_hash": digest, "entries": [row.code for row in effective]}
        return self.uow.run(work)

    @staticmethod
    def _decide(tx, organization_id, version_id, reviewer_id, decision, value, reason):
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version_id, entity_type="SCHEDULE_VERSION", entity_id=version_id,
            field_path=FIELD, decision=decision, new_value=value, reviewer_id=reviewer_id,
            reason=reason.strip(), created_at=utc_now()))
        if decision != "INPUTS_ACK":              # the list changed: a material edit for maker-checker
            tx.schedules.record_material_edit(version_id, editor_id=reviewer_id, at=utc_now())
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=reviewer_id, action=f"SCHEDULE_{decision}",
            entity_type="SCHEDULE_VERSION", entity_id=version_id, reason=reason.strip(), after=value))
