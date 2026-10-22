"""Schedule review and approval on the repository ports (no database imports).

A behaviour-preserving port of ``app.services.schedule_service.ScheduleReviewService``.
Every guard, message and audit entry is the same; what changes is that a status
change is one guarded write (the store refuses a write racing an approval) and
organisation scope comes from the version's own header instead of a four-table
join.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Callable, TypeVar
from uuid import UUID

from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, QualifierCategory, QualifierScope, ScheduleStatus, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator

from .authoring import ReviewDecisionRecord
from .schedule_documents import approval_stamp
from .errors import DuplicateRecordError, ImmutableRecordError, StaleRevisionError
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

T = TypeVar("T")

_FROZEN = {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}
_SUBMITTABLE = {
    ScheduleStatus.DRAFT.value, ScheduleStatus.VALIDATION_REQUIRED.value,
    ScheduleStatus.EXTRACTED.value, ScheduleStatus.REJECTED.value,
}
_RETRIES = 3


def approval_blockers(tx: UnitOfWork, schedule_version_id: UUID) -> list[str]:
    """THE approval gate: pending rows, source coverage and uncompiled
    statements block as well as validator findings. Empty means approvable."""
    from .review_projection import build_review_view

    header = tx.schedules.get_header(schedule_version_id)
    schedule = tx.schedules.get(schedule_version_id)
    run = tx.authoring.get_run(header.extraction_run_id) if header.extraction_run_id else None
    accountability = _accountability(run, schedule_version_id)
    version = SimpleNamespace(
        id=header.id, status=header.status, metadata_json={},
        extraction_run_id=header.extraction_run_id)
    return build_review_view(
        schedule, version=version, run=run, accountability=accountability)["approval_blockers"]


def _accountability(run, schedule_version_id: UUID):
    """The coverage / statement ledger stored with the run that produced a draft."""
    options = (run.trace or {}).get("options") if run is not None else None
    matches = [entry for entry in options or []
               if isinstance(entry, dict) and entry.get("accountability")]
    for entry in matches:
        if entry.get("schedule_version_id") == str(schedule_version_id):
            return entry["accountability"]
    return matches[0]["accountability"] if len(matches) == 1 else None


from app.domain.schedule.resolutions import checked_resolution  # noqa: E402

class ScheduleReviewService:
    # P3-04: deployment-owned policy, resolved per approval unless injected.
    _maker_checker = None

    def __init__(self, uow: UnitOfWork, maker_checker=None):
        self._maker_checker = maker_checker
        self._uow = uow

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _audit(tx: UnitOfWork, header, actor_id: UUID | None, action: str,
               reason: str | None = None, *, status: str | None = None) -> None:
        tx.audit.append(AuditRecord(
            organization_id=header.organization_id, actor_id=actor_id, action=action,
            entity_type="SCHEDULE_VERSION", entity_id=header.id, before=None,
            after={"status": status or header.status}, reason=reason))

    @staticmethod
    def _require_not_frozen(header, message: str) -> None:
        if header.status in _FROZEN:
            raise ImmutableScheduleError(message)

    # ------------------------------------------------------------- actions
    def validate(self, schedule_version_id: UUID) -> list[ValidationIssue]:
        def work(tx: UnitOfWork) -> list[ValidationIssue]:
            schedule = tx.schedules.get(schedule_version_id)
            issues = ScheduleValidator().validate(schedule)
            header = tx.schedules.get_header(schedule_version_id)
            self._require_not_frozen(
                header, "approved schedule versions cannot be revalidated or edited")
            tx.schedules.replace_validation_issues(schedule_version_id, issues)
            tx.schedules.transition(
                schedule_version_id, to_status=ScheduleStatus.VALIDATION_REQUIRED.value)
            tx.audit.append(AuditRecord(
                organization_id=header.organization_id, actor_id=None,
                action="SCHEDULE_VALIDATED", entity_type="SCHEDULE_VERSION", entity_id=header.id,
                before=None,
                after={"blocking": len(ScheduleValidator.blocking(issues)), "issues": len(issues)}))
            return issues

        return self._run(work)

    def correct_event(
        self, schedule_version_id: UUID, event: Event, *, reviewer_id: UUID, reason: str,
    ) -> list[ValidationIssue]:
        def work(tx: UnitOfWork) -> list[ValidationIssue]:
            header = tx.schedules.get_header(schedule_version_id)
            self._require_not_frozen(header, "approved schedule versions cannot be corrected")
            before_schedule = tx.schedules.get(schedule_version_id)
            before = next((item for item in before_schedule.events if item.id == event.id), None)
            if before is None:
                raise KeyError("event definition not found")
            unresolved = {"AMBIGUOUS", "CONFLICTING", "UNRESOLVED"}
            if (before.interpretation_status.value in unresolved
                    and event.interpretation_status.value not in unresolved):
                # P7-03: an unresolved reading is settled only by the evidence-backed resolution
                # (ConflictResolutionService), never by editing the status.
                raise ValueError("an unresolved interpretation can only be settled by a reviewed "
                                 "conflict resolution that names its supporting evidence")
            tx.schedules.replace_event(schedule_version_id, event)
            corrected = tx.schedules.get(schedule_version_id)
            issues = ScheduleValidator().validate(corrected)
            tx.schedules.replace_validation_issues(schedule_version_id, issues)
            tx.schedules.transition(
                schedule_version_id, to_status=ScheduleStatus.VALIDATION_REQUIRED.value)
            from app.authorization.maker_checker import is_material_event_change, material_event_changes
            changed = material_event_changes(before.model_dump(mode="json"), event.model_dump(mode="json"))
            if is_material_event_change(changed):
                tx.schedules.record_material_edit(
                    schedule_version_id, editor_id=reviewer_id, at=utc_now())
            tx.schedules.add_decision(ReviewDecisionRecord(
                schedule_version_id=schedule_version_id, entity_type="EVENT", entity_id=event.id,
                decision="CORRECT", previous_value=before.model_dump(mode="json"),
                new_value=event.model_dump(mode="json"), reviewer_id=reviewer_id, reason=reason))
            tx.audit.append(AuditRecord(
                organization_id=header.organization_id, actor_id=reviewer_id,
                action="SCHEDULE_EVENT_CORRECTED", entity_type="EVENT", entity_id=event.id,
                before=before.model_dump(mode="json"), after=event.model_dump(mode="json"),
                reason=reason))
            return issues

        return self._run(work)

    def bulk_confirm_all_fields(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str,
    ) -> int:
        """Confirm every required field with ONE reviewer action - and say so (doc s14)."""
        if not (reason or "").strip():
            raise ValueError("bulk confirmation must record why it was used")
        return self._run(lambda tx: self._bulk_confirm(tx, schedule_version_id, reviewer_id, reason))

    def _bulk_confirm(
        self, tx: UnitOfWork, schedule_version_id: UUID, reviewer_id: UUID, reason: str,
    ) -> int:
        header = tx.schedules.get_header(schedule_version_id)
        self._require_not_frozen(header, "approved schedule versions cannot be reviewed again")
        schedule = tx.schedules.get(schedule_version_id)
        confirmed = 0
        bulk_comment = (
            "Bulk-confirmed via the 'Confirm all fields' action - this field "
            "was not reviewed individually.")
        for event in schedule.events:
            fields = ["display_name", "timing"]
            if event.conditions:
                fields.append("conditions")
            if event.applicability:
                fields.append("applicability")
            if event.repeats:
                fields.append("recurrence")
            if event.activities:
                fields.append("activities")
            for field_path in fields:
                tx.schedules.add_decision(ReviewDecisionRecord(
                    schedule_version_id=schedule_version_id, entity_type="EVENT",
                    entity_id=event.id, field_path=field_path, decision="CONFIRM",
                    reviewer_id=reviewer_id, reason=reason, comment=bulk_comment))
                confirmed += 1
        tx.audit.append(AuditRecord(
            organization_id=header.organization_id, actor_id=reviewer_id,
            action="SCHEDULE_BULK_CONFIRMED", entity_type="SCHEDULE_VERSION", entity_id=header.id,
            before=None, after={"fields_confirmed": confirmed, "event_count": len(schedule.events)},
            reason=reason))
        return confirmed

    def resolve_qualifier(
        self, schedule_version_id: UUID, qualifier_id: UUID, *, reviewer_id: UUID, decision: str,
        reason: str, category: str | None = None, text: str | None = None,
        scope: str | None = None, target_codes: list[str] | None = None,
    ) -> list[ValidationIssue]:
        """Turn one extracted footnote into reviewed schedule meaning (doc s6)."""
        decision = (decision or "").strip().upper()
        allowed = {"ACCEPT", "EDIT", "NOT_APPLICABLE", "ESCALATE"}
        if decision not in allowed:
            raise ValueError(f"decision must be one of {', '.join(sorted(allowed))}")
        if not (reason or "").strip():
            raise ValueError("a qualifier resolution must record why it was made")

        def work(tx: UnitOfWork) -> list[ValidationIssue]:
            header = tx.schedules.get_header(schedule_version_id)
            self._require_not_frozen(header, "approved schedule versions cannot be corrected")
            schedule = tx.schedules.get(schedule_version_id)
            owner_event = None
            target = None
            for event in schedule.events:
                for item in event.qualifiers:
                    if item.id == qualifier_id:
                        owner_event, target = event, item
                for activity in event.activities:
                    for item in activity.qualifiers:
                        if item.id == qualifier_id:
                            owner_event, target = event, item
            if owner_event is None or target is None:
                raise KeyError("qualifier not found on this schedule version")
            before = target.model_dump(mode="json")
            if decision == "ESCALATE":
                target.resolved = False
            elif decision == "NOT_APPLICABLE":
                target.category = QualifierCategory.NOT_APPLICABLE
                target.resolved = True
            else:
                if decision == "EDIT":
                    if text is not None and text.strip():
                        target.text = text.strip()
                    if scope is not None:
                        target.scope = QualifierScope(scope)
                    if target_codes is not None:
                        target.target_codes = list(target_codes)
                if category is not None:
                    target.category = QualifierCategory(category)
                if target.category == QualifierCategory.UNRESOLVED:
                    raise ValueError(
                        "a resolved qualifier needs a category other than UNRESOLVED; "
                        "state what the footnote means, or escalate it")
                if target.scope != QualifierScope.GLOBAL and not target.target_codes:
                    raise ValueError(
                        "a non-global qualifier must name the visit or activity it "
                        "applies to before it can be resolved")
                target.resolved = True
            tx.schedules.replace_event(schedule_version_id, owner_event)
            issues = ScheduleValidator().validate(tx.schedules.get(schedule_version_id))
            tx.schedules.replace_validation_issues(schedule_version_id, issues)
            tx.schedules.transition(
                schedule_version_id, to_status=ScheduleStatus.VALIDATION_REQUIRED.value)
            after = target.model_dump(mode="json")
            # Accepting the extracted reading is a confirmation; changing what the
            # footnote MEANS is a material edit (maker-checker provenance).
            if {k: v for k, v in before.items() if k != "resolved"} != {
                    k: v for k, v in after.items() if k != "resolved"}:
                tx.schedules.record_material_edit(
                    schedule_version_id, editor_id=reviewer_id, at=utc_now())
            tx.schedules.add_decision(ReviewDecisionRecord(
                schedule_version_id=schedule_version_id, entity_type="QUALIFIER",
                entity_id=qualifier_id, field_path="resolved", decision=decision,
                previous_value=before, new_value=after, reviewer_id=reviewer_id, reason=reason))
            tx.audit.append(AuditRecord(
                organization_id=header.organization_id, actor_id=reviewer_id,
                action="QUALIFIER_RESOLVED", entity_type="QUALIFIER", entity_id=qualifier_id,
                before=before, after=after, reason=reason))
            return issues

        return self._run(work)

    def record_decision(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, decision: str,
        entity_type: str | None = None, entity_id: UUID | None = None,
        field_path: str | None = None, previous_value: object | None = None,
        new_value: object | None = None, reason: str | None = None, comment: str | None = None,
    ) -> ReviewDecisionRecord:
        if decision.upper().startswith("MANUAL_"):
            raise ValueError("manual-rule decisions require the dedicated authenticated workflow")
        def work(tx: UnitOfWork) -> ReviewDecisionRecord:
            header = tx.schedules.get_header(schedule_version_id)
            self._require_not_frozen(header, "approved schedule versions cannot be reviewed again")
            record = ReviewDecisionRecord(
                schedule_version_id=schedule_version_id, entity_type=entity_type,
                entity_id=entity_id, field_path=field_path, decision=decision,
                previous_value=previous_value, new_value=new_value, reviewer_id=reviewer_id,
                reason=reason, comment=comment)
            tx.schedules.add_decision(record)
            tx.audit.append(AuditRecord(
                organization_id=header.organization_id, actor_id=reviewer_id,
                action="REVIEW_DECISION_RECORDED", entity_type=entity_type or "SCHEDULE_VERSION",
                entity_id=entity_id or header.id, before=previous_value, after=new_value,
                reason=reason, metadata={"decision": decision, "field_path": field_path}))
            return record

        return self._run(work)

    def submit_for_review(self, schedule_version_id: UUID, *, actor_id: UUID) -> None:
        self._run(lambda tx: self._submit(tx, schedule_version_id, actor_id))

    def _submit(self, tx: UnitOfWork, schedule_version_id: UUID, actor_id: UUID) -> None:
        header = tx.schedules.get_header(schedule_version_id)
        if header.status not in _SUBMITTABLE:
            raise ValueError(f"cannot submit schedule in {header.status} state")
        tx.schedules.transition(
            schedule_version_id, to_status=ScheduleStatus.IN_REVIEW.value,
            expected=set(_SUBMITTABLE))
        self._audit(tx, header, actor_id, "SCHEDULE_SUBMITTED_FOR_REVIEW",
                    status=ScheduleStatus.IN_REVIEW.value)

    def resolve_extraction_issue(
        self, schedule_version_id: UUID, issue_id: UUID, *, reviewer_id: UUID, reason: str,
        resolution: str | None = None,
    ) -> None:
        if not reason.strip():
            raise ValueError("Explain the source review or correction that resolves this finding.")

        def work(tx: UnitOfWork) -> None:
            header = tx.schedules.get_header(schedule_version_id)
            self._require_not_frozen(header, "approved schedules cannot be corrected")
            issue = next((item for item in tx.schedules.list_issues(schedule_version_id)
                          if item.id == issue_id), None)
            if issue is None:
                raise KeyError("extraction finding not found")
            if issue.status.value != "OPEN" or issue.details.get("origin") != "EXTRACTION":
                raise ValueError("Only open extraction findings can be resolved by source review.")
            if issue.issue_code in {"SCHEDULE_OPTION_FAILED", "SCHEDULE_DISCOVERY_FAILED"}:
                raise ValueError(
                    "Retry extraction to recover the missing schedule coverage before approval.")
            resolved_as = checked_resolution(resolution, issue.issue_code)
            tx.schedules.set_issue_status(
                schedule_version_id, issue_id, "RESOLVED", resolved_by=reviewer_id, note=reason)
            tx.schedules.add_decision(ReviewDecisionRecord(
                schedule_version_id=schedule_version_id, entity_type="VALIDATION_ISSUE",
                entity_id=issue.id, decision="RESOLVE", reviewer_id=reviewer_id, reason=reason,
                previous_value={"status": "OPEN", "message": issue.message},
                new_value={"status": "RESOLVED", **({"resolution": resolved_as} if resolved_as else {})}))
            self._audit(tx, header, reviewer_id, "EXTRACTION_FINDING_RESOLVED", reason)

        self._run(work)

    def approve_reviewed_table(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str,
        effective_from: date | None = None,
        organization_id: UUID | None = None, trial_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> None:
        """One explicit table confirmation, with the existing gates and bulk audit."""
        if not reason.strip():
            raise ValueError("Add a note confirming your review of the schedule table.")

        def work(tx: UnitOfWork) -> None:
            header = tx.schedules.get_header(schedule_version_id)
            if organization_id is not None or trial_id is not None:
                if header.organization_id != organization_id or header.trial_id != trial_id:
                    raise KeyError("schedule not found")
            if expected_revision is not None and header.revision != expected_revision:
                raise StaleRevisionError("the schedule changed; reload and review it again")
            if header.status in {*_FROZEN, "ARCHIVED"}:
                raise ImmutableScheduleError("this schedule is no longer editable")
            issues = ScheduleValidator().validate(tx.schedules.get(schedule_version_id))
            if ScheduleValidator.blocking(issues):
                raise ValueError("Resolve the flagged schedule items before approval.")
            if header.status != ScheduleStatus.IN_REVIEW.value:
                self._submit(tx, schedule_version_id, reviewer_id)
            self._bulk_confirm(tx, schedule_version_id, reviewer_id, reason.strip())
            self._approve(tx, schedule_version_id, reviewer_id, reason.strip(), effective_from)

        self._run(work)

    def approve(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, comment: str | None = None,
        effective_from: date | None = None,
    ) -> None:
        self._run(lambda tx: self._approve(
            tx, schedule_version_id, reviewer_id, comment, effective_from))

    def _approve(
        self, tx: UnitOfWork, schedule_version_id: UUID, reviewer_id: UUID,
        comment: str | None, effective_from: date | None,
    ) -> None:
        header = tx.schedules.get_header(schedule_version_id)
        if header.status != ScheduleStatus.IN_REVIEW.value:
            raise ValueError("only an in-review schedule can be approved")
        # P3-04: segregation of duties at the lowest shared approval boundary of
        # the repository-port plane (approve and approve_reviewed_table both
        # end here).
        maker_checker = self._enforce_maker_checker(tx, header, reviewer_id)
        schedule = tx.schedules.get(schedule_version_id)
        from .manual_rules import verify_manual_decisions
        verify_manual_decisions(tx, schedule)
        from .required_inputs import approval_blocker
        unacknowledged = approval_blocker(tx, schedule)
        if unacknowledged:
            raise ValueError("approval blocked: review and acknowledge the patient details a clinic must enter "
                             "(" + unacknowledged + ")")
        issues = ScheduleValidator().validate(schedule)
        tx.schedules.replace_validation_issues(schedule_version_id, issues)
        blocking = ScheduleValidator.blocking(issues)
        if blocking:
            raise ValueError(f"approval blocked by {len(blocking)} validation issue(s)")
        blockers = approval_blockers(tx, schedule_version_id)
        if blockers:
            raise ValueError("Approval is blocked: " + " ".join(blockers))
        decisions = tx.schedules.list_decisions(
            schedule_version_id, reviewer_id=reviewer_id,
            decisions={"APPROVE", "CONFIRM", "CORRECT"})
        confirmed = {(item.entity_id, item.field_path) for item in decisions}
        required = {(event.id, path) for event in schedule.events for path in ("display_name", "timing")}
        for event in schedule.events:
            if event.activities:
                required.add((event.id, "activities"))
            if event.conditions:
                required.add((event.id, "conditions"))
            if event.applicability:
                required.add((event.id, "applicability"))
            if event.repeats:
                required.add((event.id, "recurrence"))
        missing = required - confirmed
        if missing:
            raise ValueError(f"approval requires {len(missing)} outstanding field review(s)")
        before = {"status": header.status}
        approved_at = utc_now()
        # P6-01: the content hash and the header integrity hash are computed from the
        # content just read and stamped in the SAME guarded write that approves it;
        # ``expected_revision`` makes that write fail if the version was edited after
        # this read, so a stored hash can never describe other content.
        stamp = approval_stamp(
            header, schedule, reviewer_id=reviewer_id, approved_at=approved_at,
            effective_from=effective_from)
        approved = tx.schedules.mark_approved(
            schedule_version_id, reviewer_id=reviewer_id, approved_at=approved_at,
            effective_from=effective_from, expected_revision=header.revision, **stamp)
        from app.domain.schedule.compiled import compile_schedule
        from app.domain.schedule.evaluator import ScheduleEvaluator
        tx.schedules.save_compiled(schedule_version_id, compile_schedule(
            tx.schedules.get(schedule_version_id), evaluator_version=ScheduleEvaluator.version, issues=issues))
        protocol = tx.authoring.get_protocol(header.protocol_id)
        protocol_version = tx.authoring.get_protocol_version(header.protocol_version_id)
        # A replacement extraction can recover an unapproved first draft; an
        # already-approved protocol keeps its explicit amendment lifecycle.
        if protocol.current_version_id != protocol_version.id:
            current = tx.authoring.get_protocol_version(protocol.current_version_id) \
                if protocol.current_version_id else None
            nothing_approved = not any(
                item.protocol_version_id == protocol.current_version_id
                for item in tx.schedules.list_headers(
                    organization_id=header.organization_id, status="APPROVED",
                    trial_id=header.trial_id)
                if item.id != schedule_version_id)
            from .version_intent import activation_eligible
            is_amendment = activation_eligible(protocol_version.metadata,
                declared_lineage=header.based_on_schedule_version_id is not None) and (current is None or current.created_at is None
                            or protocol_version.created_at is None
                            or protocol_version.created_at >= current.created_at)
            in_force = effective_from is None or effective_from <= date.today()
            if nothing_approved or (is_amendment and in_force):
                before["protocol_current_version_id"] = (
                    str(protocol.current_version_id) if protocol.current_version_id else None)
                tx.authoring.set_current_protocol_version(protocol.id, protocol_version.id)
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=header.id, decision="APPROVE", reviewer_id=reviewer_id,
            comment=comment))
        tx.audit.append(AuditRecord(
            organization_id=header.organization_id, actor_id=reviewer_id,
            action="SCHEDULE_APPROVED", entity_type="SCHEDULE_VERSION", entity_id=header.id,
            before=before,
            after={"status": approved.status, "approved_at": approved_at.isoformat(),
                   "maker_checker": maker_checker.audit(
                       approver_id=str(reviewer_id), revision=None)},
            reason=comment))

    def _enforce_maker_checker(self, tx: UnitOfWork, header, approver_id: UUID):
        from app.authorization.maker_checker import MakerCheckerPolicy, Provenance, enforce

        editors = tuple(str(item) for item in header.material_editor_ids)
        last = str(header.last_material_editor_id) if header.last_material_editor_id else None
        legacy: tuple[str, ...] = ()
        if last is None and not editors:
            from app.authorization.maker_checker import legacy_correctors
            legacy = legacy_correctors(
                (item.reviewer_id, item.field_path, item.previous_value, item.new_value)
                for item in tx.schedules.list_decisions(
                    header.id, decisions={"CORRECT", "ADD"}))
        policy = self._maker_checker or MakerCheckerPolicy.from_environment()
        return enforce(policy, str(approver_id), Provenance(last, editors, legacy))

    def reject(self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str) -> None:
        def work(tx: UnitOfWork) -> None:
            try:
                header = tx.schedules.get_header(schedule_version_id)
            except KeyError as error:
                raise ValueError("only an in-review schedule can be rejected") from error
            if header.status != ScheduleStatus.IN_REVIEW.value:
                raise ValueError("only an in-review schedule can be rejected")
            try:
                tx.schedules.transition(
                    schedule_version_id, to_status=ScheduleStatus.REJECTED.value,
                    expected={ScheduleStatus.IN_REVIEW.value}, rejection_reason=reason)
            except ImmutableRecordError as error:
                raise ValueError("only an in-review schedule can be rejected") from error
            tx.schedules.add_decision(ReviewDecisionRecord(
                schedule_version_id=header.id, decision="REJECT", reviewer_id=reviewer_id,
                reason=reason))
            self._audit(tx, header, reviewer_id, "SCHEDULE_REJECTED", reason,
                        status=ScheduleStatus.REJECTED.value)

        self._run(work)

    def list_pending_review(self, *, organization_id: UUID) -> list[dict[str, object]]:
        """Every IN_REVIEW schedule version across this organisation's trials."""
        def work(tx: UnitOfWork) -> list[dict[str, object]]:
            rows = []
            for header in tx.schedules.list_headers(
                    organization_id=organization_id, status=ScheduleStatus.IN_REVIEW.value):
                trial = tx.authoring.get_trial(organization_id, header.trial_id)
                definition = tx.authoring.get_definition(header.schedule_definition_id)
                protocol_version = tx.authoring.get_protocol_version(header.protocol_version_id)
                rows.append({
                    "trial_id": str(trial.id),
                    "trial_name": trial.study_title or trial.protocol_id or "Untitled trial",
                    "schedule_definition_id": str(definition.id),
                    "schedule_name": definition.name,
                    "schedule_version_id": str(header.id),
                    "version_number": header.version_number,
                    "protocol_version_label": protocol_version.version_label,
                    "submitted_at": header.updated_at.isoformat() if header.updated_at else None,
                })
            return rows

        return self._uow.read(work)


__all__ = ["ScheduleReviewService", "approval_blockers", "ImmutableScheduleError"]
