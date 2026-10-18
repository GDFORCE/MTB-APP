from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.anchor_status import record_is_actual
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.identity import activity_logical_key, event_logical_key
from app.domain.schedule.models import (
    AssignmentFact, ConditionState, EvaluationResult, Event, HolidayCalendar,
    OccurrenceFact, OccurrenceFactKind, PatientConditionStatus, PatientContext,
    ProcessTransition, QualifierCategory, QualifierScope,
    ScheduleStatus, UniversalSchedule, ValidationIssue,
    RollingHorizon,
    UnscheduledOccurrence,
)

#: WP3 patient facts live in the append-only ``uctsm_patient_states`` table
#: under these reserved state codes (``state_value`` = the typed fact as JSON,
#: ``effective_at`` = its effective time, ``created_at`` = its recorded time).
#: They are typed inputs, never condition fields: ``_context`` keeps them out
#: of ``PatientContext.state``.
PROCESS_TRANSITION_STATE_CODE = "uctsm:process_transition"
ASSIGNMENT_FACT_STATE_CODE = "uctsm:assignment_fact"
#: A holiday calendar supplied for the patient's site (until a site store exists).
SITE_HOLIDAY_CALENDAR_STATE_CODE = "uctsm:site_holiday_calendar"
RESERVED_STATE_CODES = frozenset({
    PROCESS_TRANSITION_STATE_CODE, ASSIGNMENT_FACT_STATE_CODE, SITE_HOLIDAY_CALENDAR_STATE_CODE,
})

#: ``PatientEventOccurrence.metadata`` keys for WP3 facts. A row without them
#: keeps its previous meaning (actual_date = performed; status MISSED = missed).
FACT_KIND_KEY = "fact_kind"
SUPERSEDES_KEY = "supersedes_occurrence_id"
EFFECTIVE_AT_KEY = "effective_at"
BOOKED_KEY = "booked_date"
END_KEY = "end_date"


def occurrence_fact_from_row(
    row: "db.PatientEventOccurrence", event_code: str, occurrence_index: int,
) -> OccurrenceFact | None:
    """The typed fact one occurrence row records, or None for a row that
    records nothing the engine acts on."""
    metadata = row.metadata_json or {}
    kind = metadata.get(FACT_KIND_KEY)
    value: date | datetime | None = row.actual_date
    if kind == OccurrenceFactKind.BOOKED.value or row.occurrence_type == "BOOKING":
        kind = OccurrenceFactKind.BOOKED.value
        booked = metadata.get(BOOKED_KEY) or (row.scheduled_date.isoformat() if row.scheduled_date else None)
        value = date.fromisoformat(str(booked)[:10]) if booked else None
    elif kind is None:
        if row.actual_date is not None:
            kind = OccurrenceFactKind.PERFORMED.value
        elif row.status == "MISSED":
            kind = OccurrenceFactKind.MISSED.value
        else:
            return None
    if kind in {OccurrenceFactKind.PERFORMED.value, OccurrenceFactKind.BOOKED.value} and value is None:
        return None
    end = metadata.get(END_KEY)
    effective = metadata.get(EFFECTIVE_AT_KEY)
    recorded = row.recorded_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
    return OccurrenceFact(
        id=str(row.id), event_code=event_code, occurrence_index=occurrence_index,
        kind=OccurrenceFactKind(kind), value=value,
        end_value=date.fromisoformat(str(end)[:10]) if end else None,
        effective_at=date.fromisoformat(str(effective)[:10]) if effective else value,
        recorded_at=recorded,
        supersedes=metadata.get(SUPERSEDES_KEY),
        source=metadata.get("source"),
    )
from app.domain.schedule.validator import ScheduleValidator

if False:  # pragma: no cover - typing only
    from app.authorization.maker_checker import MakerCheckerPolicy, Provenance


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_protocol_approved_versions(trial_id: UUID):
    """Approved schedule versions under a trial's CURRENT protocol version.

    AD-4d: before this existed, "which schedule version currently applies to
    this trial" was answered by two independently-written queries -- the
    canonical Add-Patient endpoint filtered by ``Protocol.current_version_id``;
    ``OperationalBridgeService._approved_version`` did not. A schedule approved
    under a SUPERSEDED protocol version was therefore enrollable through the
    operational bridge and refused by canonical Add-Patient -- two different
    answers to one question. Every caller now resolves it through this one
    query, using the stricter of the two prior behaviours.
    """
    return (
        select(db.ScheduleVersion)
        .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
        .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
        .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
        .where(
            db.Protocol.trial_id == trial_id,
            db.Protocol.current_version_id == db.ProtocolVersion.id,
            db.ScheduleVersion.status == ScheduleStatus.APPROVED.value,
        )
    )


def in_force_today(versions: list[db.ScheduleVersion]) -> list[db.ScheduleVersion]:
    """Doc 9 s8: a version approved but not yet effective must not look available."""
    today = date.today()
    return [item for item in versions if item.effective_from is None or item.effective_from <= today]


def _enrollable_protocol_version(session: Session, trial_id: UUID) -> tuple[UUID | None, list[db.ScheduleVersion]]:
    """The protocol version new patients are enrolled under, and its approved versions.

    Normally the protocol's ``current_version_id``. An amendment (a protocol
    version uploaded after the current one) replaces it for NEW enrolments once
    one of its schedule versions is approved AND in force today; until then the
    current version keeps taking enrolments (doc 9 s8, PT-05). A superseded
    (older) protocol version is never returned - AD-4d is unchanged.
    """
    protocol = session.scalar(select(db.Protocol).where(db.Protocol.trial_id == trial_id))
    if protocol is None:
        return None, []
    current = session.get(db.ProtocolVersion, protocol.current_version_id) if protocol.current_version_id else None
    rows = session.execute(
        select(db.ScheduleVersion, db.ProtocolVersion)
        .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
        .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
        .where(db.ProtocolVersion.protocol_id == protocol.id,
               db.ScheduleVersion.status == ScheduleStatus.APPROVED.value)
    ).all()
    by_protocol_version: dict[UUID, list[db.ScheduleVersion]] = {}
    created: dict[UUID, datetime | None] = {}
    for version, protocol_version in rows:
        by_protocol_version.setdefault(protocol_version.id, []).append(version)
        created[protocol_version.id] = protocol_version.created_at
    chosen = current.id if current is not None else None
    chosen_rank: tuple | None = None
    for protocol_version_id, versions in by_protocol_version.items():
        if current is not None and protocol_version_id != current.id:
            uploaded = created.get(protocol_version_id)
            if uploaded is None or current.created_at is None or uploaded < current.created_at:
                continue  # superseded, never enrollable again
        in_force = in_force_today(versions)
        if not in_force:
            continue
        rank = (created.get(protocol_version_id) or datetime.min.replace(tzinfo=timezone.utc),
                max((item.approved_at or datetime.min.replace(tzinfo=timezone.utc)) for item in in_force))
        rank = tuple(value.replace(tzinfo=None) if value.tzinfo else value for value in rank)
        if chosen_rank is None or rank > chosen_rank:
            chosen, chosen_rank = protocol_version_id, rank
    return chosen, by_protocol_version.get(chosen, []) if chosen is not None else []


def resolve_current_schedule_version(
    session: Session, trial_id: UUID, schedule_definition_id: UUID,
) -> db.ScheduleVersion:
    """The one schedule version a new enrolment/link against this definition should use.

    The linked definition may belong to another protocol version of the same
    trial than the enrollable one (building an amendment draft re-points the
    operational link at the amendment's definition before it is approved, and an
    older link still names the original). It is then mapped to the matching
    definition of the enrollable protocol version - same schedule type and name,
    or the only one - never guessed between several.
    """
    protocol_version_id, approved_all = _enrollable_protocol_version(session, trial_id)
    approved = [item for item in approved_all if item.schedule_definition_id == schedule_definition_id]
    if not approved and approved_all:
        linked = session.get(db.ScheduleDefinition, schedule_definition_id)
        same_trial = linked is not None and session.scalar(
            select(db.Protocol.trial_id)
            .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
            .where(db.ProtocolVersion.id == linked.protocol_version_id)) == trial_id
        if same_trial and linked.protocol_version_id != protocol_version_id:
            definition_ids = {item.schedule_definition_id for item in approved_all}
            candidates = [session.get(db.ScheduleDefinition, item) for item in definition_ids]
            matches = [item for item in candidates if item is not None
                       and item.schedule_type == linked.schedule_type and item.name == linked.name]
            if len(matches) != 1:
                same_type = [item for item in candidates if item is not None and item.schedule_type == linked.schedule_type]
                matches = same_type if len(same_type) == 1 else (candidates if len(candidates) == 1 else [])
            if len(matches) == 1:
                approved = [item for item in approved_all if item.schedule_definition_id == matches[0].id]
            elif candidates:
                raise ValueError(
                    "the approved protocol version has several schedules and none matches the "
                    "linked one; choose the schedule this patient is enrolled under")
    approved.sort(key=lambda item: item.version_number, reverse=True)
    versions_in_force = in_force_today(approved)
    if versions_in_force:
        return versions_in_force[0]
    if approved:
        soonest = min(item.effective_from for item in approved if item.effective_from is not None)
        raise ValueError(
            "no approved canonical schedule is in force today; the approved "
            f"version takes effect on {soonest.isoformat()}"
        )
    raise ValueError("no approved canonical schedule is available for the linked trial")


class ScheduleReviewService:
    def __init__(self, session: Session, maker_checker: "MakerCheckerPolicy | None" = None):
        self.session = session
        self.repository = ScheduleRepository(session)
        # Deployment-owned policy; resolved per approval when not injected, so a
        # caller cannot weaken it and a direct service call is still covered.
        self._maker_checker = maker_checker

    # -- P3-04 maker-checker ---------------------------------------------------
    def mark_material_edit(self, version: db.ScheduleVersion, editor_id: UUID) -> None:
        """Record WHO made a material change, on the version, in this transaction."""
        editors = [str(item) for item in (version.material_editor_ids or [])]
        if str(editor_id) not in editors:
            editors.append(str(editor_id))
        version.material_editor_ids = editors
        version.last_material_editor_id = editor_id
        version.last_material_edit_at = utc_now()

    def _provenance(self, version: db.ScheduleVersion) -> "Provenance":
        from app.authorization.maker_checker import Provenance

        editors = tuple(str(item) for item in (version.material_editor_ids or []))
        last = str(version.last_material_editor_id) if version.last_material_editor_id else None
        legacy: tuple[str, ...] = ()
        if last is None and not editors:
            from app.authorization.maker_checker import legacy_correctors

            rows = self.session.execute(select(
                db.ReviewDecision.reviewer_id, db.ReviewDecision.field_path,
                db.ReviewDecision.previous_value, db.ReviewDecision.new_value).where(
                db.ReviewDecision.schedule_version_id == version.id,
                db.ReviewDecision.decision.in_(["CORRECT", "ADD"]),
            )).all()
            legacy = legacy_correctors(tuple(row) for row in rows)
        return Provenance(last, editors, legacy)

    def enforce_maker_checker(self, version: db.ScheduleVersion, approver_id: UUID):
        """Raise ``MakerCheckerViolation`` when the policy forbids this approver.

        This is the lowest shared approval boundary: approve-version,
        approve-table, the review endpoint and direct service calls all end in
        ``approve`` below, so the rule cannot be bypassed by choosing an alias.
        """
        from app.authorization.maker_checker import MakerCheckerPolicy, enforce

        policy = self._maker_checker or MakerCheckerPolicy.from_environment()
        return enforce(policy, str(approver_id), self._provenance(version))

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
        from app.authorization.maker_checker import is_material_event_change
        changed = [key for key, value in event.model_dump(mode="json").items()
                   if before.model_dump(mode="json").get(key) != value
                   and key not in {"metadata", "evidence_refs", "updated_at", "created_at"}]
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

    def submit_for_review(self, schedule_version_id: UUID, *, actor_id: UUID) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status not in {ScheduleStatus.DRAFT.value, ScheduleStatus.VALIDATION_REQUIRED.value,
                                  ScheduleStatus.EXTRACTED.value, ScheduleStatus.REJECTED.value}:
            raise ValueError(f"cannot submit schedule in {version.status} state")
        version.status = ScheduleStatus.IN_REVIEW.value
        self._audit(version, actor_id, "SCHEDULE_SUBMITTED_FOR_REVIEW")

    def resolve_extraction_issue(self, schedule_version_id: UUID, issue_id: UUID,
                                 *, reviewer_id: UUID, reason: str) -> None:
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
        issue.status = "RESOLVED"
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="VALIDATION_ISSUE",
            entity_id=issue.id, decision="RESOLVE", reviewer_id=reviewer_id, reason=reason,
            previous_value={"status": "OPEN", "message": issue.message},
            new_value={"status": "RESOLVED"},
        ))
        self._audit(version, reviewer_id, "EXTRACTION_FINDING_RESOLVED", reason)

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
            is_amendment = (current is None or current.created_at is None
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

    def list_pending_review(
        self, *, organization_id: UUID, trial_ids: "frozenset[UUID] | None" = None,
    ) -> list[dict[str, object]]:
        """Every IN_REVIEW schedule version across this org's trials.

        The cross-trial "what needs my decision" list `schedule-review.tsx`
        used to provide from a separate `schedule_reviews` Mongo collection -
        this is its canonical replacement, queried directly off the same
        tables `approve`/`reject` already act on rather than a second,
        independently-maintained record of "what's pending".
        """
        query = (
            select(
                db.Trial.id, db.Trial.study_title, db.Trial.protocol_id,
                db.ScheduleDefinition.id, db.ScheduleDefinition.name,
                db.ScheduleVersion.id, db.ScheduleVersion.version_number,
                db.ScheduleVersion.updated_at, db.ProtocolVersion.version_label,
            )
            .select_from(db.ScheduleVersion)
            .join(db.ScheduleDefinition,
                  db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
            .join(db.ProtocolVersion,
                  db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .join(db.Trial, db.Trial.id == db.Protocol.trial_id)
            .where(
                db.ScheduleVersion.status == ScheduleStatus.IN_REVIEW.value,
                db.Trial.organization_id == organization_id,
            )
            .order_by(db.ScheduleVersion.updated_at)
        )
        if trial_ids is not None:
            # Authorised scope is applied in the query, so an unauthorised
            # trial is never read, counted or returned.
            query = query.where(db.Trial.id.in_(list(trial_ids)))
        rows = self.session.execute(query).all()
        return [
            {
                "trial_id": str(trial_id),
                "trial_name": study_title or protocol_id or "Untitled trial",
                "schedule_definition_id": str(definition_id),
                "schedule_name": schedule_name,
                "schedule_version_id": str(version_id),
                "version_number": version_number,
                "protocol_version_label": version_label,
                "submitted_at": updated_at.isoformat() if updated_at else None,
            }
            for (
                trial_id, study_title, protocol_id, definition_id, schedule_name,
                version_id, version_number, updated_at, version_label,
            ) in rows
        ]

    def _organization_id(self, version: db.ScheduleVersion) -> UUID:
        return self.session.execute(
            select(db.Trial.organization_id)
            .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
            .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
            .where(db.ScheduleDefinition.id == version.schedule_definition_id)
        ).scalar_one()

    def _revision(self, schedule_version_id: UUID) -> str | None:
        try:
            from app.services.review_view import review_revision

            return review_revision(self.repository.get(schedule_version_id))
        except Exception:  # the stamp must never block an approval
            return None

    def _audit(self, version: db.ScheduleVersion, actor_id: UUID, action: str, reason: str | None = None) -> None:
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=actor_id,
            action=action, entity_type="SCHEDULE_VERSION", entity_id=version.id,
            before=None, after={"status": version.status}, reason=reason,
        ))


class PatientScheduleService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    @staticmethod
    def result_date(item: object | None) -> date | None:
        if item is None:
            return None
        timing = getattr(item, "timing", None)
        value = getattr(timing, "nominal_start", None) if timing else None
        return value.date() if isinstance(value, datetime) else value

    def logical_key_for_row(self, row: db.PatientEvent) -> str:
        if row.logical_key:
            return row.logical_key
        event = self.session.get(db.Event, row.event_definition_id)
        return event_logical_key(
            event.code if event else str(row.event_definition_id), row.occurrence_index)

    def current_events(self, patient_id: UUID, schedule_version_id: UUID) -> list[db.PatientEvent]:
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.schedule_version_id == schedule_version_id,
            db.PatientSchedule.current_evaluation_id.is_not(None),
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None:
            return []
        return list(self.session.scalars(select(db.PatientEvent).where(
            db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id,
        )))

    def protected_event_ids(self, events: list[db.PatientEvent]) -> set[UUID]:
        protected = {
            item.id for item in events
            if item.status in {"COMPLETED", "MISSED"} or item.protected_history
        }
        ids = [item.id for item in events]
        if ids:
            protected.update(self.session.scalars(select(db.PatientEventOccurrence.patient_event_id).where(
                db.PatientEventOccurrence.patient_event_id.in_(ids),
                db.PatientEventOccurrence.actual_date.is_not(None),
            )))
        return protected

    def evaluate_result(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        horizon: date,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        rolling: RollingHorizon | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> EvaluationResult:
        schedule = self.repository.get(schedule_version_id)
        context = self._context(
            patient, schedule_version_id,
            condition_overrides=condition_overrides,
            anchor_overrides=anchor_overrides,
            state_overrides=state_overrides,
            planned_anchor_overrides=planned_anchor_overrides,
            process_transition_overrides=process_transition_overrides,
            assignment_fact_overrides=assignment_fact_overrides,
        )
        return ScheduleEvaluator().evaluate(
            schedule, context, horizon=horizon, rolling=rolling)

    def patient_conditions(
        self, patient_id: UUID,
        *,
        overrides: dict[str, PatientConditionStatus] | None = None,
    ) -> dict[str, PatientConditionStatus]:
        """Current condition state per code, newest non-superseded record wins."""
        conditions: dict[str, PatientConditionStatus] = {}
        for row in self.session.scalars(select(db.PatientCondition).where(
            db.PatientCondition.patient_id == patient_id,
            db.PatientCondition.superseded_by_id.is_(None),
        ).order_by(db.PatientCondition.recorded_at)):
            conditions[row.condition_code] = PatientConditionStatus(
                condition_code=row.condition_code, state=ConditionState(row.state),
                occurrence_date=row.occurrence_date, resolution_date=row.resolution_date,
                occurrence_index=row.occurrence_index,
            )
        conditions.update(overrides or {})
        return conditions

    def _context(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> PatientContext:
        # Doc C6: planned and actual anchor values are kept apart. Merging them
        # let a planned surgery date date the post-op visits and send reminders
        # as if surgery had happened. Which dict a record lands in is decided by
        # the same rule the Patient Profile uses (anchor_status.record_is_actual).
        anchors: dict[str, date | datetime] = {}
        planned_anchors: dict[str, date | datetime] = {}
        rows = self.session.execute(
            select(
                db.Anchor.code, db.Anchor.derivation_rule,
                db.PatientAnchor.value_date, db.PatientAnchor.value_datetime,
                db.PatientAnchor.status, db.PatientAnchor.source_type,
            )
            .join(db.PatientAnchor, db.PatientAnchor.anchor_definition_id == db.Anchor.id)
            .where(
                db.PatientAnchor.patient_id == patient.id,
                db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]),
            )
            .order_by(db.PatientAnchor.recorded_at)
        )
        for code, derivation_rule, value_date, value_datetime, status, source_type in rows:
            value = value_datetime or value_date
            target = anchors if record_is_actual(status, source_type) else planned_anchors
            selection = str((derivation_rule or {}).get("selection", "LAST")).upper()
            if selection == "FIRST" and code in target:
                continue
            target[code] = value
        anchors.update(anchor_overrides or {})
        planned_anchors.update(planned_anchor_overrides or {})
        actual_event_values: dict[str, list[date | datetime]] = {}
        occurrence_rows = self.session.execute(
            select(db.Event.code, db.PatientEvent.occurrence_index, db.PatientEventOccurrence)
            .join(db.PatientEvent, db.PatientEvent.id == db.PatientEventOccurrence.patient_event_id)
            .join(db.PatientSchedule, db.PatientSchedule.id == db.PatientEvent.patient_schedule_id)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(db.PatientSchedule.patient_id == patient.id)
            .order_by(db.PatientEventOccurrence.recorded_at)
        ).all()
        # PT-02: a correction supersedes by id. The corrected row stays in the
        # table (history); only the acting value feeds evaluation.
        superseded = {
            str((row.metadata_json or {}).get(SUPERSEDES_KEY))
            for _, _, row in occurrence_rows
            if (row.metadata_json or {}).get(SUPERSEDES_KEY)
        }
        occurrence_facts: list[OccurrenceFact] = []
        for code, occurrence_index, row in occurrence_rows:
            fact = occurrence_fact_from_row(row, code, occurrence_index)
            if fact is not None:
                occurrence_facts.append(fact)
            if row.actual_date is None or str(row.id) in superseded:
                continue
            if fact is not None and fact.kind != OccurrenceFactKind.PERFORMED:
                continue
            actual_event_values.setdefault(code, []).append(row.actual_date)
        for values in actual_event_values.values():
            values.sort()
        activity_actuals: dict[str, date | datetime] = {}
        activity_statuses: dict[str, str] = {}
        for record in self.session.scalars(select(db.PatientActivityRecord).where(
            db.PatientActivityRecord.patient_id == patient.id,
            db.PatientActivityRecord.superseded_by_id.is_(None),
        ).order_by(db.PatientActivityRecord.recorded_at)):
            activity_statuses[record.logical_key] = record.status
            if record.actual_time is not None:
                activity_actuals[record.logical_key] = record.actual_time
            else:
                activity_actuals.pop(record.logical_key, None)
        state: dict[str, object] = {}
        state_effective_at: dict[str, datetime] = {}
        process_transitions: list[ProcessTransition] = []
        assignment_history: list[AssignmentFact] = []
        site_calendars: dict[str, HolidayCalendar] = {}
        for row in self.session.scalars(select(db.PatientState).where(
            db.PatientState.patient_id == patient.id,
        ).order_by(db.PatientState.effective_at, db.PatientState.created_at)):
            if row.state_code == PROCESS_TRANSITION_STATE_CODE:
                process_transitions.append(ProcessTransition.model_validate(row.state_value))
                continue
            if row.state_code == ASSIGNMENT_FACT_STATE_CODE:
                assignment_history.append(AssignmentFact.model_validate(row.state_value))
                continue
            if row.state_code == SITE_HOLIDAY_CALENDAR_STATE_CODE:
                calendar = HolidayCalendar.model_validate(row.state_value)
                site_calendars[calendar.code] = calendar
                continue
            state[row.state_code] = row.state_value
            state_effective_at[row.state_code] = row.effective_at
        process_transitions.extend(process_transition_overrides or [])
        assignment_history.extend(assignment_fact_overrides or [])
        for code, (value, effective_at) in (state_overrides or {}).items():
            state[code] = value
            state_effective_at[code] = effective_at
        # Doc 10 s14: unscheduled visits the site created are INPUTS to
        # evaluation, so regeneration reproduces them rather than erasing them.
        unscheduled = [
            UnscheduledOccurrence(
                event_code=row.event_code, occurred_on=row.occurred_on,
                reason=row.reason, visit_mode=row.visit_mode,
                created_by=row.created_by, recorded_at=row.recorded_at,
            )
            for row in self.session.scalars(select(db.PatientUnscheduledVisit).where(
                db.PatientUnscheduledVisit.patient_id == patient.id,
            ).order_by(db.PatientUnscheduledVisit.occurred_on))
        ]
        arm_code = self.session.get(db.Arm, patient.arm_id).code if patient.arm_id else None
        cohort_code = self.session.get(db.Cohort, patient.cohort_id).code if patient.cohort_id else None
        population_code = self.session.get(db.Population, patient.population_id).code if patient.population_id else None
        return PatientContext(
            patient_id=patient.id, schedule_version_id=schedule_version_id,
            anchors=anchors, planned_anchors=planned_anchors,
            event_values={key: list(values) for key, values in actual_event_values.items()},
            actual_event_values=actual_event_values,
            activity_actuals=activity_actuals, activity_statuses=activity_statuses,
            conditions=self.patient_conditions(patient.id, overrides=condition_overrides),
            unscheduled_occurrences=unscheduled,
            state=state,
            state_effective_at=state_effective_at, arm_code=arm_code,
            cohort_code=cohort_code, population_code=population_code,
            dimension_values=patient.dimension_values or {},
            occurrence_facts=occurrence_facts,
            process_transitions=process_transitions,
            assignment_history=assignment_history,
            holiday_calendars=list(site_calendars.values()),
        )

    def _persist_activities(
        self, row: db.PatientEvent, item: object,
        *, schedule_version_id: UUID | None = None, protocol_version_id: UUID | None = None,
    ) -> None:
        """Materialize the day-wise activity schedule for one visit occurrence."""
        activities = getattr(item, "activities", None) or []
        if not activities:
            return
        self.session.flush()
        event_code = getattr(item, "event_code", None) or row.logical_key
        occurrence_index = getattr(item, "occurrence_index", row.occurrence_index)

        def as_datetime(value: date | datetime | None) -> datetime | None:
            if value is None or isinstance(value, datetime):
                return value
            return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)

        for activity in activities:
            timing = activity.timing
            self.session.add(db.PatientActivity(
                patient_event_id=row.id,
                activity_definition_id=activity.activity_definition_id,
                activity_code=activity.activity_code,
                logical_key=activity_logical_key(
                    event_code, occurrence_index,
                    activity.activity_code or str(activity.activity_definition_id),
                ),
                status=activity.status.value,
                requiredness=activity.requiredness.value,
                sequence_number=activity.sequence_number,
                planned_time=as_datetime(timing.nominal_start) if timing else None,
                earliest_time=as_datetime(timing.earliest) if timing else None,
                latest_time=as_datetime(timing.latest) if timing else None,
                actual_time=as_datetime(activity.actual_time),
                timing_resolution=timing.model_dump(mode="json") if timing else {},
                generation_reason=activity.explanation,
                schedule_version_id=schedule_version_id,
                protocol_version_id=protocol_version_id,
            ))

    def _persist_confinements(
        self,
        patient_schedule_id: UUID,
        schedule_evaluation_id: UUID,
        confinements: list,
        *,
        prior_evaluation_id: UUID | None,
        schedule_version_id: UUID,
        protocol_version_id: UUID | None,
    ) -> list["db.PatientConfinementEpisode"]:
        """Materialize this evaluation's confinement episodes (AD-8).

        Before this, ``result.confinements`` was computed on every evaluation
        and then discarded - every consumer (the operational bridge, the
        deviation report, reminder delivery) recomputed it fresh from a
        transient ``EvaluationResult`` instead of reading a persisted row, the
        same way ``PatientEvent`` already is.

        Mirrors ``evaluate()``'s own event reconciliation: a DISCHARGED
        episode's actual dates are protected across re-evaluation once
        recorded, found via ``episode_code`` as the stable logical key
        (Step 1's identity discipline - an episode is not per-occurrence
        indexed the way an event is, so there is nothing else to key on).
        """
        def as_date(value: date | datetime | None) -> date | None:
            return value.date() if isinstance(value, datetime) else value

        prior_by_key: dict[str, db.PatientConfinementEpisode] = {}
        if prior_evaluation_id is not None:
            prior_by_key = {
                item.logical_key: item
                for item in self.session.scalars(select(db.PatientConfinementEpisode).where(
                    db.PatientConfinementEpisode.schedule_evaluation_id == prior_evaluation_id,
                ))
            }
        persisted: list[db.PatientConfinementEpisode] = []
        for item in confinements:
            logical_key = item.episode_code
            previous = prior_by_key.get(logical_key)
            protected = previous is not None and previous.status == "DISCHARGED"
            row = db.PatientConfinementEpisode(
                patient_schedule_id=patient_schedule_id, schedule_evaluation_id=schedule_evaluation_id,
                episode_definition_id=item.episode_definition_id, episode_code=item.episode_code,
                logical_key=logical_key,
                supersedes_patient_confinement_episode_id=previous.id if previous else None,
                protected_history=protected,
                status=(previous.status if protected and previous else item.status.value),
                planned_admission=(previous.planned_admission if protected and previous else as_date(item.planned_admission)),
                actual_admission=(previous.actual_admission if protected and previous else as_date(item.actual_admission)),
                planned_discharge=(previous.planned_discharge if protected and previous else as_date(item.planned_discharge)),
                actual_discharge=(previous.actual_discharge if protected and previous else as_date(item.actual_discharge)),
                extended_by=(previous.extended_by if protected and previous else list(item.extended_by)),
                explanation=(previous.explanation if protected and previous else dict(item.explanation)),
                schedule_version_id=schedule_version_id, protocol_version_id=protocol_version_id,
            )
            self.session.add(row)
            persisted.append(row)
        return persisted

    def create_unscheduled_visit(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurred_on: date,
        reason: str,
        visit_mode: str | None = None,
        actor_id: UUID | None = None,
    ) -> db.PatientUnscheduledVisit:
        """Create one occurrence of a protocol-defined unscheduled visit.

        Doc 10 s13-s15. The definition must already exist in the patient's own
        approved schedule and be marked ON_DEMAND: this creates an OCCURRENCE of
        something the protocol allows, never a new protocol requirement, so the
        master schedule and every other patient are untouched.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "an unscheduled visit needs a reason; without one it cannot be "
                "reviewed against the protocol later"
            )
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        definition = self.session.scalar(select(db.Event).where(
            db.Event.schedule_version_id == patient.current_schedule_version_id,
            db.Event.code == event_code,
        ))
        if definition is None:
            raise KeyError(f"event {event_code} is not defined in this patient's schedule")
        if (definition.activation or "SCHEDULED") != "ON_DEMAND":
            raise ValueError(
                f"{event_code} is a scheduled protocol visit; only a protocol-defined "
                "unscheduled visit can be created on demand"
            )
        allowed = list(definition.allowed_visit_modes or [])
        if visit_mode is not None and allowed and visit_mode not in allowed:
            raise ValueError(
                f"visit mode {visit_mode!r} is not permitted for {event_code}; "
                f"the protocol allows {', '.join(sorted(allowed))}"
            )
        record = db.PatientUnscheduledVisit(
            patient_id=patient.id, event_definition_id=definition.id,
            event_code=event_code, occurred_on=occurred_on, reason=reason.strip(),
            visit_mode=visit_mode, created_by=actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def record_activity(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurrence_index: int,
        activity_code: str,
        status: str,
        actual_time: datetime | None,
        actor_id: UUID | None,
        reason: str | None = None,
    ) -> db.PatientActivityRecord:
        """Record an intra-day actual time or completion decision.

        Corrections supersede rather than overwrite, so the execution history a
        reviewer sees stays append-only and auditable.
        """
        if status not in {"COMPLETED", "NOT_DONE", "PENDING"}:
            raise ValueError("activity status must be COMPLETED, NOT_DONE, or PENDING")
        if status == "COMPLETED" and actual_time is None:
            raise ValueError("a completed activity requires its actual time")
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        logical_key = activity_logical_key(event_code, occurrence_index, activity_code)
        record = db.PatientActivityRecord(
            patient_id=patient.id, event_code=event_code, occurrence_index=occurrence_index,
            activity_code=activity_code, logical_key=logical_key, status=status,
            actual_time=actual_time, recorded_by=actor_id, reason=reason,
        )
        self.session.add(record)
        self.session.flush()
        superseded = [
            item for item in self.session.scalars(select(db.PatientActivityRecord).where(
                db.PatientActivityRecord.patient_id == patient.id,
                db.PatientActivityRecord.logical_key == logical_key,
                db.PatientActivityRecord.superseded_by_id.is_(None),
                db.PatientActivityRecord.id != record.id,
            ))
        ]
        for item in superseded:
            item.superseded_by_id = record.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ACTIVITY_RECORDED", entity_type="PATIENT", entity_id=patient.id,
            before=({
                "status": superseded[-1].status,
                "actual_time": superseded[-1].actual_time.isoformat() if superseded[-1].actual_time else None,
            } if superseded else None),
            after={
                "logical_key": logical_key, "status": status,
                "actual_time": actual_time.isoformat() if actual_time else None,
            },
            reason=reason,
        ))
        return record

    def record_occurrence_fact(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        logical_key: str,
        kind: OccurrenceFactKind,
        value: date | None = None,
        end_value: date | None = None,
        effective_at: date | None = None,
        supersedes_occurrence_id: UUID | None = None,
        actor_id: UUID | None = None,
        reason: str | None = None,
        source: str | None = None,
    ) -> db.PatientEventOccurrence:
        """Record a performed / missed / booked fact for one logical occurrence.

        PT-02: append-only. A correction names the row it supersedes; that row
        stays as history (its PatientEvent stays protected) and the new value is
        what later evaluations use. PT-04: a booking never sets an actual date
        and never changes the rule. PT-07: nothing here is derived from a date
        passing - a performed fact is only what a person records.
        """
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        row = next((item for item in self.current_events(
            patient.id, patient.current_schedule_version_id) if item.logical_key == logical_key), None)
        if row is None:
            raise KeyError(f"occurrence {logical_key} is not in the patient's current plan")
        if kind in {OccurrenceFactKind.PERFORMED, OccurrenceFactKind.BOOKED} and value is None:
            raise ValueError(f"a {kind.value.lower()} fact needs its date")
        previous = None
        if supersedes_occurrence_id is not None:
            previous = self.session.get(db.PatientEventOccurrence, supersedes_occurrence_id)
            if previous is None:
                raise KeyError("the occurrence record being corrected does not exist")
            previous_event = self.session.get(db.PatientEvent, previous.patient_event_id)
            if previous_event is None or previous_event.logical_key != logical_key:
                raise ValueError("a correction must supersede a record of the same occurrence")
        metadata: dict[str, object] = {FACT_KIND_KEY: kind.value}
        if supersedes_occurrence_id is not None:
            metadata[SUPERSEDES_KEY] = str(supersedes_occurrence_id)
        if effective_at is not None:
            metadata[EFFECTIVE_AT_KEY] = effective_at.isoformat()
        if end_value is not None:
            metadata[END_KEY] = end_value.isoformat()
        if source is not None:
            metadata["source"] = source
        if kind == OccurrenceFactKind.BOOKED:
            metadata[BOOKED_KEY] = value.isoformat()
        occurrence = db.PatientEventOccurrence(
            patient_event_id=row.id,
            occurrence_type="BOOKING" if kind == OccurrenceFactKind.BOOKED else "VISIT",
            scheduled_date=row.nominal_start_date,
            actual_date=value if kind == OccurrenceFactKind.PERFORMED else None,
            status={
                OccurrenceFactKind.PERFORMED: "COMPLETED",
                OccurrenceFactKind.MISSED: "MISSED",
                OccurrenceFactKind.BOOKED: "BOOKED",
            }[kind],
            recorded_by=actor_id, recorded_at=datetime.now(timezone.utc),
            metadata_json=metadata,
        )
        self.session.add(occurrence)
        if kind == OccurrenceFactKind.PERFORMED:
            row.status = "COMPLETED"
        elif kind == OccurrenceFactKind.MISSED:
            row.status = "MISSED"
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action=("PATIENT_OCCURRENCE_FACT_CORRECTED" if previous is not None
                    else "PATIENT_OCCURRENCE_FACT_RECORDED"),
            entity_type="PATIENT_EVENT", entity_id=row.id,
            before=({
                "occurrence_id": str(previous.id), "actual_date": (
                    previous.actual_date.isoformat() if previous.actual_date else None),
                "status": previous.status, "metadata": previous.metadata_json,
            } if previous is not None else None),
            after={
                "occurrence_id": str(occurrence.id), "logical_key": logical_key,
                "kind": kind.value, "value": value.isoformat() if value else None,
                "metadata": metadata,
            },
            reason=reason,
        ))
        return occurrence

    def record_patient_fact(
        self,
        patient: db.Patient,
        *,
        state_code: str,
        value: dict[str, object],
        effective_at: date | datetime,
        actor_id: UUID | None,
        source_reference: dict[str, object] | None = None,
    ) -> db.PatientState:
        """Append one reserved-code WP3 fact (process transition, assignment
        fact, site calendar) to the patient's append-only state history."""
        if state_code not in RESERVED_STATE_CODES:
            raise ValueError(f"{state_code!r} is not a typed WP3 fact")
        effective = (effective_at if isinstance(effective_at, datetime)
                     else datetime.combine(effective_at, datetime.min.time(), tzinfo=timezone.utc))
        row = db.PatientState(
            patient_id=patient.id, state_code=state_code, state_value=value,
            effective_at=effective, recorded_by=actor_id,
            source_reference=source_reference or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def evaluate(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        horizon: date,
        schedule_version_id: UUID | None = None,
        idempotency_key: str | None = None,
        prior_events_override: list[db.PatientEvent] | None = None,
        rolling: RollingHorizon | None = None,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> tuple[db.ScheduleEvaluation, list[db.PatientEvent]]:
        """Evaluate one patient against a schedule version.

        AD-4d: ``schedule_version_id`` lets a caller that already resolved the
        target version (``impact_service.confirm()``, which is in the middle of
        MOVING a patient to a new version) pass it explicitly instead of relying
        on a just-written ``patient.current_schedule_version_id`` being visible
        to a second, independent read inside this method. When omitted, the
        patient's currently pinned version is used -- resolved exactly once,
        here, and threaded through the rest of this call rather than re-read
        later.
        """
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        resolved_version_id = (
            schedule_version_id if schedule_version_id is not None
            else patient.current_schedule_version_id
        )
        if resolved_version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        if idempotency_key:
            existing = self.session.scalar(select(db.ScheduleGenerationRun).where(
                db.ScheduleGenerationRun.patient_id == patient_id,
                db.ScheduleGenerationRun.idempotency_key == idempotency_key,
            ))
            if existing:
                evaluation = self.session.scalar(select(db.ScheduleEvaluation).where(
                    db.ScheduleEvaluation.generation_run_id == existing.id,
                ))
                assert evaluation is not None
                events = list(self.session.scalars(select(db.PatientEvent).where(
                    db.PatientEvent.schedule_evaluation_id == evaluation.id,
                )))
                return evaluation, events
        schedule = self.repository.get(resolved_version_id)
        prior_events = (
            prior_events_override if prior_events_override is not None
            else self.current_events(patient.id, schedule.schedule_version_id)
        )
        started = utc_now()
        # Doc 3 s7-s8: an open-ended protocol persists a bounded rolling set of
        # upcoming occurrences, not every cycle the date horizon would allow.
        result = self.evaluate_result(
            patient, schedule.schedule_version_id, horizon=horizon, rolling=rolling)
        run = db.ScheduleGenerationRun(
            patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            idempotency_key=idempotency_key, evaluator_version=result.evaluator_version,
            status="COMPLETED", started_at=started, completed_at=utc_now(),
        )
        self.session.add(run)
        self.session.flush()
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient.id,
            db.PatientSchedule.schedule_version_id == schedule.schedule_version_id,
        ))
        if patient_schedule is None:
            patient_schedule = db.PatientSchedule(
                patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            )
            self.session.add(patient_schedule)
            self.session.flush()
        # Captured before it is overwritten below, so confinement reconciliation
        # can find the episodes the OUTGOING evaluation produced.
        prior_evaluation_id = patient_schedule.current_evaluation_id
        patient_schedule.status = "ACTIVE"
        evaluation = db.ScheduleEvaluation(
            patient_schedule_id=patient_schedule.id, generation_run_id=run.id,
            input_snapshot=result.input_snapshot,
            output_summary={"statuses": [item.status.value for item in result.events], "count": len(result.events)},
            evaluator_version=result.evaluator_version, evaluated_at=result.evaluated_at,
        )
        self.session.add(evaluation)
        self.session.flush()
        persisted = []
        prior_by_key = {self.logical_key_for_row(item): item for item in prior_events}
        protected_ids = self.protected_event_ids(prior_events)
        reconciliation = {key: 0 for key in ("UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
        for item in result.events:
            timing = item.timing
            def as_date(value: date | datetime | None) -> date | None:
                return value.date() if isinstance(value, datetime) else value
            logical_key = event_logical_key(item.event_code, item.occurrence_index)
            previous = prior_by_key.pop(logical_key, None)
            protected = previous is not None and previous.id in protected_ids
            status = previous.status if protected else item.status.value
            nominal_start = previous.nominal_start_date if protected else (as_date(timing.nominal_start) if timing else None)
            nominal_end = previous.nominal_end_date if protected else (as_date(timing.nominal_end) if timing else None)
            earliest = previous.earliest_date if protected else (as_date(timing.earliest) if timing else None)
            latest = previous.latest_date if protected else (as_date(timing.latest) if timing else None)
            if previous is None:
                change = "ADDED"
            elif protected:
                change = "PROTECTED"
            elif previous.nominal_start_date != nominal_start:
                change = "MOVED"
            elif previous.status != status:
                change = "STATUS_CHANGED"
            else:
                change = "UNCHANGED"
            reconciliation[change] += 1
            explanation = dict(item.explanation)
            explanation["reconciliation"] = {
                "change": change,
                "supersedes_patient_event_id": str(previous.id) if previous else None,
            }
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=item.event_definition_id, occurrence_index=item.occurrence_index,
                logical_occurrence_id=(previous.logical_occurrence_id if previous and previous.logical_occurrence_id else uuid4()),
                logical_key=logical_key,
                supersedes_patient_event_id=previous.id if previous else None,
                protected_history=protected,
                status=status,
                nominal_start_date=nominal_start, nominal_end_date=nominal_end,
                earliest_date=earliest, latest_date=latest,
                timing_resolution=(previous.timing_resolution if protected and previous else (timing.model_dump(mode="json") if timing else {})),
                applicability_result={"value": item.applicability_result},
                condition_result={"value": item.condition_result},
                dependency_result=item.dependency_result,
                generation_reason=explanation,
                visit_mode=item.visit_mode,
                unscheduled_reason=item.unscheduled_reason,
                schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id,
            )
            self.session.add(row)
            self._persist_activities(
                row, item, schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id)
            persisted.append(row)
        for logical_key, previous in prior_by_key.items():
            protected = previous.id in protected_ids
            change = "PROTECTED" if protected else "CANCELLED"
            reconciliation[change] += 1
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=previous.event_definition_id,
                occurrence_index=previous.occurrence_index,
                logical_occurrence_id=previous.logical_occurrence_id or uuid4(),
                logical_key=logical_key, supersedes_patient_event_id=previous.id,
                protected_history=protected,
                status=previous.status if protected else "CANCELLED",
                nominal_start_date=previous.nominal_start_date,
                nominal_end_date=previous.nominal_end_date,
                earliest_date=previous.earliest_date, latest_date=previous.latest_date,
                timing_resolution=previous.timing_resolution,
                applicability_result=previous.applicability_result,
                condition_result=previous.condition_result,
                dependency_result=previous.dependency_result,
                generation_reason={
                    **(previous.generation_reason or {}),
                    "reconciliation": {
                        "change": change,
                        "supersedes_patient_event_id": str(previous.id),
                    },
                },
                schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id,
            )
            self.session.add(row)
            persisted.append(row)
        self._persist_confinements(
            patient_schedule.id, evaluation.id, result.confinements,
            prior_evaluation_id=prior_evaluation_id,
            schedule_version_id=schedule.schedule_version_id,
            protocol_version_id=schedule.schedule_metadata.protocol_version_id,
        )
        self.session.flush()
        evaluation.output_summary = {
            **evaluation.output_summary,
            "persisted_count": len(persisted),
            "reconciliation": reconciliation,
        }
        patient_schedule.current_evaluation_id = evaluation.id
        patient_schedule.generation_run_id = run.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_EVALUATED",
            entity_type="PATIENT", entity_id=patient.id, before=None,
            after={
                "evaluation_id": str(evaluation.id), "event_count": len(persisted),
                "reconciliation": reconciliation,
            },
            reason=reason,
            metadata_json={"evaluator_version": result.evaluator_version},
        ))
        return evaluation, persisted
