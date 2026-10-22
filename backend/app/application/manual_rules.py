"""Authorized callers declare a gap; another approver reviews manual-only handling."""
from app.observability.metrics import METRICS
from uuid import UUID, uuid4

from app.domain.schedule.manual_handling import KEY, RuleHandling, fingerprint, handling
from app.domain.schedule.models import ScheduleStatus
from app.domain.schedule.validator import ScheduleValidator

from .authoring import ReviewDecisionRecord
from .errors import ImmutableRecordError, RecordNotFoundError, StaleRevisionError
from .records import AuditRecord, utc_now


class ManualRuleService:
    def __init__(self, uow):
        self.uow = uow

    @staticmethod
    def scope(tx, org, trial, version, *, editable=False, expected_revision=None):
        header = tx.schedules.get_header(version)
        if header.organization_id != org or header.trial_id != trial:
            raise RecordNotFoundError("schedule not found")
        if editable and header.status in {"APPROVED", "SUPERSEDED", "ARCHIVED"}:
            raise ImmutableRecordError("approved schedules cannot be changed")
        if expected_revision is not None and header.revision != expected_revision:
            raise StaleRevisionError("the draft changed; reload its rules")
        return header, tx.schedules.get(version)

    def view(self, *, organization_id, trial_id, version_id):
        def read(tx):
            header, schedule = self.scope(tx, organization_id, trial_id, version_id)
            return {"schedule_version_id": str(version_id), "revision": header.revision, "status": header.status,
                    "events": [{"code": row.code, "display_name": row.display_name,
                                "handling": row.metadata.get(KEY), "evidence_refs": [str(ref) for ref in row.evidence_refs]}
                               for row in schedule.events],
                    "evidence": [row.model_dump(mode="json") for row in schedule.evidence],
                    "issues": [row.model_dump(mode="json") for row in ScheduleValidator().validate(schedule)]}
        return self.uow.run(read)

    def decide(self, *, organization_id, trial_id, version_id, event_code, actor_id,
               expected_revision, action, reason, kind=None, evidence_refs=(), policy_reference=None):
        if not reason or len(reason.strip()) < 3:
            raise ValueError("state the reviewed reason")
        def write(tx):
            header, schedule = self.scope(tx, organization_id, trial_id, version_id,
                                         editable=True, expected_revision=expected_revision)
            event = next((row for row in schedule.events if row.code == event_code), None)
            if event is None:
                raise RecordNotFoundError("event not found")
            before = event.model_copy(deep=True); decision_id = uuid4()
            if action == "DECLARE":
                if not set(evidence_refs) <= {row.id for row in schedule.evidence}:
                    raise ValueError("evidence must belong to this version")
                rule = RuleHandling(kind=kind, reason=reason.strip(), owner_id=actor_id,
                    requested_by=actor_id, evidence_refs=list(evidence_refs), policy_reference=policy_reference,
                    event_fingerprint=fingerprint(event))
            elif action == "APPROVE":
                rule = handling(event)
                if rule is None or rule.kind != "MANUAL_ONLY" or rule.status != "PENDING":
                    raise ValueError("declare pending manual-only handling first")
                if rule.requested_by == actor_id:
                    raise ValueError("another approver must review this manual-only decision")
                if rule.event_fingerprint != fingerprint(event):
                    raise StaleRevisionError("the rule changed since manual handling was requested")
                rule = rule.model_copy(update={"status": "APPROVED", "approved_by": actor_id,
                    "approved_at": utc_now(), "approval_decision_id": decision_id})
            else:
                raise ValueError("unsupported manual-rule decision")
            event.metadata = {**event.metadata, KEY: rule.model_dump(mode="json")}
            tx.schedules.replace_event(version_id, event)
            if action == "DECLARE":
                tx.schedules.record_material_edit(version_id, editor_id=actor_id, at=utc_now())
            tx.schedules.add_decision(ReviewDecisionRecord(
                id=decision_id, schedule_version_id=version_id, entity_type="EVENT", entity_id=event.id,
                field_path=f"metadata/{KEY}", decision="MANUAL_" + action, reviewer_id=actor_id,
                reason=reason.strip(), previous_value=before.metadata.get(KEY), new_value=rule.model_dump(mode="json")))
            tx.schedules.replace_validation_issues(version_id, ScheduleValidator().validate(tx.schedules.get(version_id)))
            tx.audit.append(AuditRecord(organization_id=organization_id, actor_id=actor_id,
                action="SCHEDULE_MANUAL_" + action, entity_type="EVENT", entity_id=event.id,
                reason=reason.strip(), before=before.metadata.get(KEY), after=rule.model_dump(mode="json")))
            METRICS.manual_decisions.inc(action=action)
            return {"decision_id": str(decision_id), "handling": rule.model_dump(mode="json")}
        return self.uow.run(write)


def verify_manual_decisions(tx, schedule):
    """Metadata cannot forge an authenticated decision, even through a generic edit."""
    from app.domain.schedule.manual_handling import approved_manual
    if not any(approved_manual(event) for event in schedule.events):
        return
    decisions = {row.id: row for row in tx.schedules.list_decisions(schedule.schedule_version_id)}
    for event in schedule.events:
        if approved_manual(event):
            rule = handling(event); decision = decisions.get(rule.approval_decision_id)
            if (decision is None or decision.decision != "MANUAL_APPROVE"
                    or decision.reviewer_id != rule.approved_by or decision.entity_id != event.id
                    or decision.new_value != rule.model_dump(mode="json")):
                raise ValueError("manual-only handling has no matching authenticated review decision")
