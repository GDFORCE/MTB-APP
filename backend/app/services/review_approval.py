"""Submit, approve and reject: the approval gate and its maker-checker boundary.

Extracted from ``ScheduleReviewService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, QualifierCategory, QualifierScope, ScheduleStatus, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator
from app.services.review_base import ReviewBase
from app.services.schedule_facts import utc_now
from app.services.schedule_version_resolution import current_protocol_approved_versions


class ReviewApproval(ReviewBase):
    def submit_for_review(self, schedule_version_id: UUID, *, actor_id: UUID) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status not in {ScheduleStatus.DRAFT.value, ScheduleStatus.VALIDATION_REQUIRED.value,
                                  ScheduleStatus.EXTRACTED.value, ScheduleStatus.REJECTED.value}:
            raise ValueError(f"cannot submit schedule in {version.status} state")
        version.status = ScheduleStatus.IN_REVIEW.value
        self._audit(version, actor_id, "SCHEDULE_SUBMITTED_FOR_REVIEW")

    def approve_reviewed_table(self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str,
                               effective_from: date | None = None) -> None:
        """One explicit table confirmation, with existing gates and bulk audit.

        The API commits this transaction atomically. Source findings are never
        dismissed by this action.
        """
        if not reason.strip():
            raise ValueError("Add a note confirming your review of the schedule table.")
        version = self.session.get(db.ScheduleVersion, schedule_version_id, with_for_update=True)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value, "ARCHIVED"}:
            raise ImmutableScheduleError("this schedule is no longer editable")
        # Refuse a self-approval before any confirmation is written; ``approve``
        # enforces it again at the commit (the lowest shared boundary).
        self.enforce_maker_checker(version, reviewer_id)
        issues = ScheduleValidator().validate(self.repository.get(schedule_version_id))
        if ScheduleValidator.blocking(issues):
            raise ValueError("Resolve the flagged schedule items before approval.")
        if version.status != ScheduleStatus.IN_REVIEW.value:
            self.submit_for_review(schedule_version_id, actor_id=reviewer_id)
        self.bulk_confirm_all_fields(schedule_version_id, reviewer_id=reviewer_id, reason=reason.strip())
        self.session.flush()
        self.approve(schedule_version_id, reviewer_id=reviewer_id, comment=reason.strip(),
                     effective_from=effective_from)

    def approve(
        self,
        schedule_version_id: UUID,
        *,
        reviewer_id: UUID,
        comment: str | None = None,
        effective_from: date | None = None,
    ) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id, with_for_update=True)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status != ScheduleStatus.IN_REVIEW.value:
            raise ValueError("only an in-review schedule can be approved")
        # P3-04: segregation of duties, read from the locked row so a concurrent
        # edit cannot slip between the check and the commit.
        maker_checker = self.enforce_maker_checker(version, reviewer_id)
        schedule = self.repository.get(schedule_version_id)
        if any(event.metadata.get("rule_handling") for event in schedule.events):
            raise ValueError("Manual-only rule review requires the Mongo-native review workflow.")
        issues = ScheduleValidator().validate(schedule)
        self.repository.replace_validation_issues(schedule_version_id, issues)
        blocking = ScheduleValidator.blocking(issues)
        if blocking:
            raise ValueError(f"approval blocked by {len(blocking)} validation issue(s)")
        # THE approval gate, shared by every route that ends here (approve-version,
        # approve-table and the review decision): pending rows, source coverage
        # and uncompiled statements block as well as validator findings (UI-08,
        # RV-01). Imported lazily: review_view imports this module.
        from app.services.review_view import approval_blockers

        blockers = approval_blockers(self.session, schedule_version_id)
        if blockers:
            raise ValueError("Approval is blocked: " + " ".join(blockers))
        decisions = list(self.session.scalars(select(db.ReviewDecision).where(
            db.ReviewDecision.schedule_version_id == schedule_version_id,
            db.ReviewDecision.reviewer_id == reviewer_id,
            db.ReviewDecision.decision.in_(["APPROVE", "CONFIRM", "CORRECT"]),
        )))
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
        before = {"status": version.status}
        version.status = ScheduleStatus.APPROVED.value
        # Doc 9 s8: an amendment can be approved before it applies. Recorded
        # separately so nobody reads "approved today" as "in force today".
        version.effective_from = effective_from
        version.approved_by = reviewer_id
        version.approved_at = utc_now()
        definition = self.session.get(db.ScheduleDefinition, version.schedule_definition_id)
        protocol_version = self.session.get(db.ProtocolVersion, definition.protocol_version_id)
        protocol = self.session.get(db.Protocol, protocol_version.protocol_id)
        # A replacement extraction can recover an unapproved first draft. It
        # must not remain unusable behind that old draft's protocol pointer.
        # An already-approved protocol keeps its explicit amendment lifecycle.
        if protocol.current_version_id != protocol_version.id:
            current = self.session.get(db.ProtocolVersion, protocol.current_version_id) \
                if protocol.current_version_id else None
            nothing_approved = self.session.scalar(
                current_protocol_approved_versions(protocol.trial_id).limit(1)) is None
            # The explicit amendment step: approving a version of a LATER protocol
            # version makes it the one new patients enrol under, once in force.
            # Enrolled patients stay pinned to their version (PT-05). A version
            # approved for a future date leaves the pointer; the resolver switches
            # on the effective date.
            from app.application.version_intent import activation_eligible
            is_amendment = activation_eligible(protocol_version.metadata_json) and (current is None or current.created_at is None
                            or protocol_version.created_at is None
                            or protocol_version.created_at >= current.created_at)
            in_force = effective_from is None or effective_from <= date.today()
            if nothing_approved or (is_amendment and in_force):
                before["protocol_current_version_id"] = str(protocol.current_version_id) \
                    if protocol.current_version_id else None
                protocol.current_version_id = protocol_version.id
        self.session.add(db.ReviewDecision(
            schedule_version_id=version.id, decision="APPROVE",
            reviewer_id=reviewer_id, comment=comment,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_APPROVED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=before,
            after={"status": version.status, "approved_at": version.approved_at.isoformat(),
                   "maker_checker": maker_checker.audit(
                       approver_id=str(reviewer_id),
                       revision=self._revision(schedule_version_id))},
            reason=comment,
        ))

    def reject(self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None or version.status != ScheduleStatus.IN_REVIEW.value:
            raise ValueError("only an in-review schedule can be rejected")
        version.status = ScheduleStatus.REJECTED.value
        version.rejection_reason = reason
        self.session.add(db.ReviewDecision(
            schedule_version_id=version.id, decision="REJECT",
            reviewer_id=reviewer_id, reason=reason,
        ))
        self._audit(version, reviewer_id, "SCHEDULE_REJECTED", reason)
