"""Validation, corrections and review decisions on a draft schedule version.

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


from app.domain.schedule.resolutions import checked_resolution  # noqa: E402


class ReviewEditing(ReviewBase):
    def validate(self, schedule_version_id: UUID) -> list[ValidationIssue]:
        schedule = self.repository.get(schedule_version_id)
        issues = ScheduleValidator().validate(schedule)
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        assert version is not None
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be revalidated or edited")
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=None,
            action="SCHEDULE_VALIDATED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=None,
            after={"blocking": len(ScheduleValidator.blocking(issues)), "issues": len(issues)},
        ))
        return issues

    def correct_event(self, schedule_version_id: UUID, event: Event, *, reviewer_id: UUID, reason: str) -> list[ValidationIssue]:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be corrected")
        before_schedule = self.repository.get(schedule_version_id)
        before = next((item for item in before_schedule.events if item.id == event.id), None)
        if before is None:
            raise KeyError("event definition not found")
        self.repository.replace_event(schedule_version_id, event)
        self.session.flush()
        corrected = self.repository.get(schedule_version_id)
        issues = ScheduleValidator().validate(corrected)
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value
        # A whole-event replacement is a material edit unless only a label moved.
        from app.authorization.maker_checker import is_material_event_change, material_event_changes
        changed = material_event_changes(before.model_dump(mode="json"), event.model_dump(mode="json"))
        if is_material_event_change(changed):
            self.mark_material_edit(version, reviewer_id)
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="EVENT", entity_id=event.id,
            decision="CORRECT", previous_value=before.model_dump(mode="json"),
            new_value=event.model_dump(mode="json"), reviewer_id=reviewer_id, reason=reason,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_EVENT_CORRECTED", entity_type="EVENT", entity_id=event.id,
            before=before.model_dump(mode="json"), after=event.model_dump(mode="json"), reason=reason,
        ))
        return issues

    def bulk_confirm_all_fields(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str,
    ) -> int:
        """Confirm every required field with ONE reviewer action - and say so.

        Doc s14: "the current 'Confirm All' behavior must NOT be treated as
        equivalent to field-level review... do not claim 'every field
        individually reviewed' when the user only pressed one bulk button."

        This still writes the same per-field ``ReviewDecision`` rows
        ``approve()`` checks for (so the field-confirmation gate keeps working
        exactly as before), but their comment says plainly that they came from
        one bulk action, and ONE additional, distinctly-named audit event
        (``SCHEDULE_BULK_CONFIRMED``) records the action itself - so a later
        reviewer of the audit trail sees "bulk-confirmed, N fields, by X, on
        DATE, because REASON" rather than N identical-looking individual
        confirmations that imply N separate looks.
        """
        if not (reason or "").strip():
            raise ValueError("bulk confirmation must record why it was used")
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be reviewed again")

        schedule = self.repository.get(schedule_version_id)
        confirmed = 0
        bulk_comment = (
            "Bulk-confirmed via the 'Confirm all fields' action - this field "
            "was not reviewed individually."
        )
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
                self.session.add(db.ReviewDecision(
                    schedule_version_id=schedule_version_id, entity_type="EVENT",
                    entity_id=event.id, field_path=field_path, decision="CONFIRM",
                    reviewer_id=reviewer_id, reason=reason, comment=bulk_comment,
                ))
                confirmed += 1

        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_BULK_CONFIRMED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=None,
            after={"fields_confirmed": confirmed, "event_count": len(schedule.events)},
            reason=reason,
        ))
        return confirmed

    def resolve_qualifier(
        self,
        schedule_version_id: UUID,
        qualifier_id: UUID,
        *,
        reviewer_id: UUID,
        decision: str,
        reason: str,
        category: str | None = None,
        text: str | None = None,
        scope: str | None = None,
        target_codes: list[str] | None = None,
    ) -> list[ValidationIssue]:
        """Turn one extracted footnote into reviewed schedule meaning (doc s6).

        Extraction deliberately refuses to interpret a footnote: it carries the
        marker, its text and its evidence, and marks it UNRESOLVED, which blocks
        approval. That was only half a workflow - nothing could ever clear the
        block, so any protocol with a footnote produced a draft that could never
        be approved. This is the other half.

        The reviewer states what the footnote MEANS; this method records that
        statement, never guesses at it. ACCEPT keeps the extracted reading, EDIT
        replaces it, NOT_APPLICABLE records that it does not govern this
        schedule, and ESCALATE leaves it unresolved on purpose so it stays
        blocking. Every outcome is a ReviewDecision plus an AuditEvent carrying
        the before/after value, who decided, and why.
        """
        decision = (decision or "").strip().upper()
        allowed = {"ACCEPT", "EDIT", "NOT_APPLICABLE", "ESCALATE"}
        if decision not in allowed:
            raise ValueError(f"decision must be one of {', '.join(sorted(allowed))}")
        if not (reason or "").strip():
            raise ValueError("a qualifier resolution must record why it was made")

        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError(
                "approved schedule versions cannot be corrected")

        schedule = self.repository.get(schedule_version_id)
        owner_event = None
        owner_type = ""
        target = None
        for event in schedule.events:
            for item in event.qualifiers:
                if item.id == qualifier_id:
                    owner_event, owner_type, target = event, "EVENT", item
            for activity in event.activities:
                for item in activity.qualifiers:
                    if item.id == qualifier_id:
                        owner_event, owner_type, target = event, "ACTIVITY", item
        if owner_event is None or target is None:
            raise KeyError("qualifier not found on this schedule version")

        before = target.model_dump(mode="json")

        if decision == "ESCALATE":
            # Deliberately still unresolved: the reviewer is saying they cannot
            # answer this yet, which must keep blocking approval rather than
            # quietly passing it through.
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

        self.repository.replace_event(schedule_version_id, owner_event)
        self.session.flush()
        issues = ScheduleValidator().validate(self.repository.get(schedule_version_id))
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value

        after = target.model_dump(mode="json")
        # Accepting the extracted reading is a confirmation; changing what the
        # footnote MEANS is a material edit.
        if {k: v for k, v in before.items() if k != "resolved"} != {
                k: v for k, v in after.items() if k != "resolved"}:
            self.mark_material_edit(version, reviewer_id)
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="QUALIFIER",
            entity_id=qualifier_id, field_path="resolved", decision=decision,
            previous_value=before, new_value=after,
            reviewer_id=reviewer_id, reason=reason,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="QUALIFIER_RESOLVED", entity_type="QUALIFIER",
            entity_id=qualifier_id, before=before, after=after, reason=reason,
        ))
        return issues

    def record_decision(
        self,
        schedule_version_id: UUID,
        *,
        reviewer_id: UUID,
        decision: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        field_path: str | None = None,
        previous_value: object | None = None,
        new_value: object | None = None,
        reason: str | None = None,
        comment: str | None = None,
    ) -> db.ReviewDecision:
        if decision.upper().startswith("MANUAL_"):
            raise ValueError("manual-rule decisions require the dedicated authenticated workflow")
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be reviewed again")
        row = db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type=entity_type,
            entity_id=entity_id, field_path=field_path, decision=decision,
            previous_value=previous_value, new_value=new_value,
            reviewer_id=reviewer_id, reason=reason, comment=comment,
        )
        self.session.add(row)
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="REVIEW_DECISION_RECORDED", entity_type=entity_type or "SCHEDULE_VERSION",
            entity_id=entity_id or version.id, before=previous_value, after=new_value,
            reason=reason, metadata_json={"decision": decision, "field_path": field_path},
        ))
        return row

    def resolve_extraction_issue(self, schedule_version_id: UUID, issue_id: UUID,
                                 *, reviewer_id: UUID, reason: str, resolution: str | None = None) -> None:
        if not reason.strip():
            raise ValueError("Explain the source review or correction that resolves this finding.")
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedules cannot be corrected")
        issue = self.session.get(db.ValidationIssue, issue_id)
        if issue is None or issue.schedule_version_id != schedule_version_id:
            raise KeyError("extraction finding not found")
        if issue.status != "OPEN" or issue.details.get("origin") != "EXTRACTION":
            raise ValueError("Only open extraction findings can be resolved by source review.")
        if issue.issue_code in {"SCHEDULE_OPTION_FAILED", "SCHEDULE_DISCOVERY_FAILED"}:
            raise ValueError("Retry extraction to recover the missing schedule coverage before approval.")
        resolution = checked_resolution(resolution, issue.issue_code)
        issue.status = "RESOLVED"
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="VALIDATION_ISSUE",
            entity_id=issue.id, decision="RESOLVE", reviewer_id=reviewer_id, reason=reason,
            previous_value={"status": "OPEN", "message": issue.message},
            new_value={"status": "RESOLVED", **({"resolution": resolution} if resolution else {})},
        ))
        self._audit(version, reviewer_id, "EXTRACTION_FINDING_RESOLVED", reason)
