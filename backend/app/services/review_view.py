"""SQL review workflow and compatibility exports.

Pure review DTO formatting lives in app.application.review_projection. Existing
imports keep their function/exception identities; SQL mutation logic stays here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, ScheduleStatus, UniversalSchedule, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator

from app.application.review_projection import (
    ReviewVersion,
    ReviewRun,
    NON_ACKNOWLEDGEABLE,
    REVIEW_VIEW_VERSION,
    EXPORT_SCHEMA_VERSION,
    REVIEWER_EVIDENCE_TYPE,
    IMMUTABLE_STATUSES,
    COVERAGE_FAILURE_CODES,
    WINDOW_ISSUE_CODES,
    UNCOMPILED_DISPOSITIONS,
    DISPOSITION_LABELS,
    TIMING_REVIEW_TYPES,
    SPAN_TIMING_TYPES,
    UNRESOLVED_INTERPRETATION,
    _HANDLED_EVENT_FIELDS,
    _BOOKKEEPING_METADATA,
    StaleRevisionError,
    ApprovalBlockedError,
    _canonical,
    _sorted_by_id,
    _rules_payload,
    semantic_fingerprint,
    review_revision,
    _amount,
    _Names,
    _relative,
    describe_timing,
    _rename_references,
    _constraint_tree,
    _walk_tree,
    _tree_unsupported,
    _condition_unsupported,
    describe_termination,
    WEEKDAY_NAMES,
    NEEDS_REVIEW,
    describe_calendar_recurrence,
    describe_recurrence,
    describe_condition,
    describe_applicability,
    _window_text,
    _issue_ref,
    _issue_heading,
    _collect_references,
    _citations,
    _ledger_coverage,
    _coverage_hooks,
    _extraction_status,
    build_review_view,
    _issue_statement_ids,
    _statement_view,
    _restriction_view,
    _source_coverage_view,
    activity_owner_ids,
    _activity_view,
    _anchor_views,
    PROCESS_ACTION_WORDS,
    HOLD_RESUME_LABELS,
    describe_action,
    process_actions_for,
    process_summary,
    _row_constraints,
    _label,
    export_json,
    export_csv,
)

# --- writes -----------------------------------------------------------------------


def _slug(value: str) -> str:
    code = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return code or "VISIT"


def approval_blockers(session: Session, schedule_version_id: UUID) -> list[str]:
    """THE approval gate, shared by every approval route (approve-version,
    approve-table, the service). Empty means approvable."""
    return ReviewWorkflow(session).view(schedule_version_id)["approval_blockers"]


class ReviewWorkflow:
    """Revision-guarded edits, question resolution and approval for one draft."""

    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    # -- reading -------------------------------------------------------------

    def context(self, schedule_version_id: UUID) -> tuple[UniversalSchedule, db.ScheduleVersion, db.ExtractionRun | None]:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        schedule = self.repository.get(schedule_version_id)
        run = self.session.get(db.ExtractionRun, version.extraction_run_id) if version.extraction_run_id else None
        return schedule, version, run

    def accountability(self, schedule_version_id: UUID) -> dict[str, Any] | None:
        """The extraction accountability ledger, or None (agent A's store first,
        then the version's own metadata as a fallback)."""
        from app.services.extraction_service import ExtractionService

        ledger = ExtractionService(self.session).accountability_for_version(schedule_version_id)
        if isinstance(ledger, dict) and ledger:
            return ledger
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        fallback = (version.metadata_json or {}).get("accountability") if version is not None else None
        return fallback if isinstance(fallback, dict) and fallback else None

    def build(self, schedule: UniversalSchedule, version: db.ScheduleVersion,
              run: db.ExtractionRun | None = None) -> dict[str, Any]:
        if run is None and version.extraction_run_id:
            run = self.session.get(db.ExtractionRun, version.extraction_run_id)
        return build_review_view(schedule, version=version, run=run,
                                 accountability=self.accountability(version.id))

    def view(self, schedule_version_id: UUID) -> dict[str, Any]:
        schedule, version, run = self.context(schedule_version_id)
        return self.build(schedule, version, run)

    def provenance(self, schedule_version_id: UUID) -> dict[str, Any]:
        schedule, version, run = self.context(schedule_version_id)
        definition = self.session.get(db.ScheduleDefinition, version.schedule_definition_id)
        protocol_version = self.session.get(db.ProtocolVersion, definition.protocol_version_id)
        return {
            "schedule_definition_id": str(definition.id),
            "schedule_name": definition.name,
            "protocol_version_id": str(protocol_version.id),
            "protocol_version_label": protocol_version.version_label,
            "document_name": protocol_version.document_name,
            "document_hash": protocol_version.document_hash,
            "extraction_run_id": str(run.id) if run else None,
            "model": {"provider": run.provider, "name": run.model_name, "version": run.model_version} if run else None,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "approved_at": version.approved_at.isoformat() if version.approved_at else None,
            "approved_by": str(version.approved_by) if version.approved_by else None,
        }

    # -- guards --------------------------------------------------------------

    def _editable(self, schedule_version_id: UUID, expected_revision: str) -> tuple[UniversalSchedule, db.ScheduleVersion]:
        version = self.session.get(db.ScheduleVersion, schedule_version_id, with_for_update=True)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in IMMUTABLE_STATUSES:
            raise ImmutableScheduleError("approved schedule versions cannot be edited; start an amendment")
        schedule = self.repository.get(schedule_version_id)
        current = review_revision(schedule)
        if expected_revision != current:
            raise StaleRevisionError(expected_revision, current)
        return schedule, version

    def _organization_id(self, version: db.ScheduleVersion) -> UUID:
        return self.session.execute(
            select(db.Trial.organization_id)
            .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
            .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
            .where(db.ScheduleDefinition.id == version.schedule_definition_id)
        ).scalar_one()

    def _reviewer_evidence(self, version: db.ScheduleVersion, *, reviewer_id: UUID, rationale: str,
                           action: str, entity_type: str, entity_id: UUID, claims: Iterable[str],
                           detail: dict[str, Any] | None = None) -> UUID:
        """Record a reviewer statement as evidence distinct from model interpretation (RV-02)."""
        definition = self.session.get(db.ScheduleDefinition, version.schedule_definition_id)
        evidence_id = uuid4()
        self.session.add(db.Evidence(
            id=evidence_id, protocol_version_id=definition.protocol_version_id,
            schedule_version_id=version.id, evidence_type=REVIEWER_EVIDENCE_TYPE,
            source_text=rationale,
            source_locator={"kind": "REVIEWER_DECISION"},
            extraction_context={
                "origin": "REVIEWER", "action": action, "reviewer_id": str(reviewer_id),
                "recorded_at": datetime.now(timezone.utc).isoformat(), **(detail or {}),
            },
        ))
        self.session.flush()
        for claim in dict.fromkeys(claims):
            self.session.add(db.ClaimEvidence(
                schedule_version_id=version.id, evidence_id=evidence_id, claim_type=claim,
                claim_entity_type=entity_type, claim_entity_id=entity_id,
                interpretation={"source": "REVIEWER_DECISION", "action": action},
            ))
        return evidence_id

    def _mark_material_edit(self, version: db.ScheduleVersion, editor_id: UUID) -> None:
        from app.services.schedule_service import ScheduleReviewService

        ScheduleReviewService(self.session).mark_material_edit(version, editor_id)

    def _revalidate(self, version: db.ScheduleVersion) -> list[ValidationIssue]:
        self.session.flush()
        issues = ScheduleValidator().validate(self.repository.get(version.id))
        self.repository.replace_validation_issues(version.id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value
        self.session.flush()
        return issues

    def _record(self, version: db.ScheduleVersion, *, reviewer_id: UUID, action: str, entity_type: str,
                entity_id: UUID | None, field_path: str | None, before: Any, after: Any, reason: str,
                decision: str = "CORRECT") -> None:
        self.session.add(db.ReviewDecision(
            schedule_version_id=version.id, entity_type=entity_type, entity_id=entity_id,
            field_path=field_path, decision=decision, previous_value=before, new_value=after,
            reviewer_id=reviewer_id, reason=reason,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id, action=action,
            entity_type=entity_type, entity_id=entity_id or version.id, before=before, after=after,
            reason=reason,
        ))

    def _result(self, schedule_version_id: UUID, before_view_rows: dict[str, Any] | None = None) -> dict[str, Any]:
        view = self.view(schedule_version_id)
        changed = []
        if before_view_rows is not None:
            after_rows = {row["id"]: row for row in view["rows"]}
            for row_id in set(before_view_rows) | set(after_rows):
                old, new = before_view_rows.get(row_id), after_rows.get(row_id)
                if old is None or new is None or any(
                    old.get(key) != new.get(key) for key in (
                        "visit_name", "timing_summary", "window_summary", "window_status",
                        "applicability_summary", "review_status", "pending_reasons", "issue_refs",
                        "question_refs")):
                    changed.append(row_id)
        return {"revision": view["revision"], "changed_row_ids": sorted(changed), "view": view}

    # -- semantic edit (UI-07) ------------------------------------------------

    EDITABLE_FIELDS = {
        "display_name": "EVENT_NAME", "protocol_label": "EVENT_NAME", "event_type": "EVENT_NAME",
        "timing": "TIMING", "window": "WINDOW", "recurrence": "RECURRENCE",
        "recurrence_plan": "RECURRENCE", "applicability": "APPLICABILITY",
        "visit_mode": "VISIT_MODE", "allowed_visit_modes": "VISIT_MODE",
        "dependency_mode": "DEPENDENCY_MODE",
        # Weekday series incl. its policies; a policy is only ever what the
        # reviewer explicitly sends - nothing is defaulted.
        "calendar_recurrence": "RECURRENCE",
    }

    def edit_event(self, schedule_version_id: UUID, event_id: UUID, *, reviewer_id: UUID,
                   expected_revision: str, rationale: str, changes: dict[str, Any],
                   confirm_row: bool = False) -> dict[str, Any]:
        if not rationale.strip():
            raise ValueError("Say why this change is being made; it becomes part of the audit trail.")
        unknown = set(changes) - set(self.EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"These fields cannot be edited here: {', '.join(sorted(unknown))}")
        if not changes and not confirm_row:
            raise ValueError("Nothing to change.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        before_rows = {row["id"]: row for row in self.build(schedule, version)["rows"]}
        before = next((item for item in schedule.events if item.id == event_id), None)
        if before is None:
            raise KeyError("visit not found on this draft")
        payload = before.model_dump(mode="json", by_alias=True)
        changed_fields = [key for key, value in changes.items() if payload.get(key) != value]
        payload.update(changes)
        if confirm_row:
            payload["requires_review"] = False
            if payload.get("interpretation_status") in UNRESOLVED_INTERPRETATION | {"EXTRACTED", "INFERRED"}:
                payload["interpretation_status"] = "CONFIRMED"
        metadata = dict(payload.get("metadata") or {})
        metadata["last_edit"] = {
            "by": str(reviewer_id), "at": datetime.now(timezone.utc).isoformat(),
            "fields": changed_fields, "confirmed": confirm_row, "rationale": rationale.strip(),
        }
        superseded_window_issues: list[str] = []
        if "window" in changes:
            # The reviewer has now stated the window (or stated that there is
            # none). That decision supersedes the compiler's note that the
            # protocol's wording could not be applied; the note is kept as history.
            note = metadata.pop("window_as_stated", None)
            if note is not None:
                metadata["last_edit"]["superseded_window_note"] = note
                if "window" not in changed_fields:
                    changed_fields.append("window")
            for issue_row in self.session.scalars(select(db.ValidationIssue).where(
                    db.ValidationIssue.schedule_version_id == version.id,
                    db.ValidationIssue.entity_id == event_id,
                    db.ValidationIssue.issue_code.in_(sorted(WINDOW_ISSUE_CODES)),
                    db.ValidationIssue.status == "OPEN")):
                if (issue_row.details or {}).get("origin") != "EXTRACTION":
                    continue
                issue_row.status = "RESOLVED"
                issue_row.resolved_by = reviewer_id
                issue_row.resolved_at = datetime.now(timezone.utc)
                issue_row.resolution_note = f"Window stated by reviewer: {rationale.strip()}"
                superseded_window_issues.append(str(issue_row.id))
            if superseded_window_issues:
                metadata["last_edit"]["resolved_issue_ids"] = superseded_window_issues
        payload["metadata"] = metadata
        try:
            after = Event.model_validate(payload)
        except Exception as error:  # pydantic: the patch is not a valid rule
            raise ValueError(f"The edited rule is not valid: {error}") from error
        claims = [self.EDITABLE_FIELDS[key] for key in changed_fields]
        if confirm_row:
            claims.append("REVIEW_CONFIRMATION")
        # Maker-checker provenance: a label rename or a row confirmation is not a
        # change of clinical meaning; anything else is.
        from app.authorization.maker_checker import is_material_event_change
        material_edit = bool(changed_fields) and is_material_event_change(changed_fields)
        evidence_id = self._reviewer_evidence(
            version, reviewer_id=reviewer_id, rationale=rationale.strip(), action="EDIT_VISIT",
            entity_type="EVENT", entity_id=event_id, claims=claims,
            detail={"fields": changed_fields, "confirmed": confirm_row})
        after.evidence_refs = [*after.evidence_refs, evidence_id]
        self.repository.replace_event(schedule_version_id, after)
        self._revalidate(version)
        if material_edit:
            self._mark_material_edit(version, reviewer_id)
        self._record(version, reviewer_id=reviewer_id, action="SCHEDULE_EVENT_EDITED", entity_type="EVENT",
                     entity_id=event_id, field_path=",".join(changed_fields) or "review",
                     before={key: payload_before for key, payload_before in before.model_dump(mode="json").items()
                             if key in changed_fields},
                     after={key: value for key, value in changes.items() if key in changed_fields},
                     reason=rationale.strip())
        self.session.flush()
        return self._result(schedule_version_id, before_rows)

    # -- Add Visit (UI-07) ----------------------------------------------------

    def add_event(self, schedule_version_id: UUID, *, reviewer_id: UUID, expected_revision: str,
                  rationale: str, event: dict[str, Any]) -> dict[str, Any]:
        if not rationale.strip():
            raise ValueError("Say why this visit is being added; it becomes part of the audit trail.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        before_rows = {row["id"]: row for row in self.build(schedule, version)["rows"]}
        used = {item.code for item in schedule.events}
        name = str(event.get("display_name") or "").strip()
        if not name:
            raise ValueError("A visit needs a name.")
        code = str(event.get("code") or _slug(name))
        base, suffix = code, 2
        while code in used:
            code, suffix = f"{base}_{suffix}", suffix + 1
        now = datetime.now(timezone.utc).isoformat()
        sequence = event.get("sequence_number")
        if sequence is None:
            sequence = max([item.sequence_number or 0 for item in schedule.events] + [0]) + 1
        activities = []
        for index, activity in enumerate(event.get("activities") or [], 1):
            activity = {"display_name": activity} if isinstance(activity, str) else dict(activity)
            label = str(activity.get("display_name") or "").strip()
            if not label:
                continue
            activities.append({
                "code": activity.get("code") or _slug(label), "protocol_label": label,
                "display_name": label, "activity_type": activity.get("activity_type") or "ASSESSMENT",
                "requiredness": activity.get("requiredness") or "REQUIRED",
                "sequence_number": index, "interpretation_status": "CONFIRMED",
            })
        payload = {
            "code": code, "protocol_label": name, "display_name": name,
            "event_type": event.get("event_type") or "VISIT",
            "sequence_number": sequence,
            "timing": event.get("timing"),
            # Never defaulted: an unstated window / mode stays unstated.
            "window": event.get("window"),
            "visit_mode": event.get("visit_mode"),
            "allowed_visit_modes": event.get("allowed_visit_modes") or [],
            "applicability": event.get("applicability") or [],
            "recurrence": event.get("recurrence"),
            "activities": activities,
            "interpretation_status": "CONFIRMED",
            "metadata": {"origin": "MANUAL", "rationale": rationale.strip(),
                         "review_state": "REVIEWER_ENTERED", "created_by": str(reviewer_id),
                         "created_at": now},
        }
        try:
            new_event = Event.model_validate(payload)
        except Exception as error:
            raise ValueError(f"The new visit is not a valid rule: {error}") from error
        self.session.add(db.Event(
            id=new_event.id, schedule_version_id=version.id, code=new_event.code,
            protocol_label=new_event.protocol_label, display_name=new_event.display_name,
            event_type=new_event.event_type, sequence_number=new_event.sequence_number,
            timing=new_event.model_dump(mode="json")["timing"], conditions=[],
            interpretation_status=new_event.interpretation_status.value,
            requires_review=False, evidence_refs=[], metadata_json=new_event.metadata,
            activation=new_event.activation, allowed_visit_modes=list(new_event.allowed_visit_modes),
            visit_mode=new_event.visit_mode,
        ))
        self.session.flush()
        claims = ["EVENT_NAME", "TIMING"]
        if new_event.window is not None:
            claims.append("WINDOW")
        if new_event.recurrence is not None:
            claims.append("RECURRENCE")
        if new_event.applicability:
            claims.append("APPLICABILITY")
        evidence_id = self._reviewer_evidence(
            version, reviewer_id=reviewer_id, rationale=rationale.strip(), action="ADD_VISIT",
            entity_type="EVENT", entity_id=new_event.id, claims=claims, detail={"origin": "MANUAL"})
        new_event.evidence_refs = [evidence_id]
        for activity in new_event.activities:
            activity.evidence_refs = [evidence_id]
            self.session.add(db.ClaimEvidence(
                schedule_version_id=version.id, evidence_id=evidence_id, claim_type="ACTIVITY",
                claim_entity_type="ACTIVITY", claim_entity_id=activity.id,
                interpretation={"source": "REVIEWER_DECISION", "action": "ADD_VISIT"},
            ))
        self.repository.replace_event(schedule_version_id, new_event)
        self._revalidate(version)
        self._mark_material_edit(version, reviewer_id)
        self._record(version, reviewer_id=reviewer_id, action="SCHEDULE_EVENT_ADDED", entity_type="EVENT",
                     entity_id=new_event.id, field_path=None, before=None,
                     after=new_event.model_dump(mode="json"), reason=rationale.strip(), decision="ADD")
        self.session.flush()
        result = self._result(schedule_version_id, before_rows)
        result["event_id"] = str(new_event.id)
        return result

    # -- shared questions (UI-06) ----------------------------------------------

    def resolve_question(self, schedule_version_id: UUID, question_id: str, *, reviewer_id: UUID,
                         expected_revision: str, rationale: str, action: str,
                         anchor_code: str | None = None, display_name: str | None = None,
                         anchor_type: str | None = None) -> dict[str, Any]:
        if not rationale.strip():
            raise ValueError("Say why this answer is correct; it becomes part of the audit trail.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        view = self.build(schedule, version)
        before_rows = {row["id"]: row for row in view["rows"]}
        question = next((item for item in view["shared_review_questions"] if item["id"] == question_id), None)
        if question is None:
            raise KeyError("question not found on this draft (it may already be resolved)")
        if action not in question.get("actions", []):
            raise ValueError(f"{action} does not answer this question; use one of {', '.join(question.get('actions', []))}")
        anchors = {row.code: row for row in self.session.scalars(
            select(db.Anchor).where(db.Anchor.schedule_version_id == version.id))}
        reason = rationale.strip()
        if action == "CHOOSE_ORIGIN":
            if anchor_code not in anchors:
                raise ValueError("Choose one of this draft's anchors as the schedule origin.")
            before = {code: {"status": row.status, "derivation_rule": row.derivation_rule} for code, row in anchors.items()}
            for code, row in anchors.items():
                rule = dict(row.derivation_rule or {})
                if code == anchor_code:
                    rule["role"] = "SCHEDULE_ORIGIN"
                    rule["confirmed_by_reviewer"] = True
                    row.status = "RESOLVED"
                elif rule.get("role") == "SCHEDULE_ORIGIN":
                    rule.pop("role")
                if row.status == "AMBIGUOUS":
                    row.status = "RESOLVED"
                row.derivation_rule = rule
            metadata = dict(version.metadata_json or {})
            extensions = [item for item in metadata.get("extensions") or []
                          if item.get("type") != "UNRESOLVED_SCHEDULE_ORIGIN"]
            metadata["extensions"] = extensions
            version.metadata_json = metadata
            entity_id = anchors[anchor_code].id
            after = {"origin": anchor_code}
            claims = ["ANCHOR_ORIGIN"]
        elif action == "CONFIRM_ANCHOR":
            code = (question.get("anchor") or {}).get("code")
            row = anchors.get(code)
            if row is None:
                raise KeyError("anchor not found")
            before = {"status": row.status}
            row.status = "RESOLVED"
            entity_id, after, claims = row.id, {"status": "RESOLVED"}, ["ANCHOR"]
        elif action == "DEFINE_ANCHOR":
            code = (question.get("anchor") or {}).get("code")
            if not code or code in anchors:
                raise ValueError("This anchor already exists.")
            if not (display_name or "").strip() or not (anchor_type or "").strip():
                raise ValueError("Name the event this anchor stands for and its type.")
            row = db.Anchor(schedule_version_id=version.id, code=code, display_name=display_name.strip(),
                            protocol_label=display_name.strip(), anchor_type=anchor_type.strip().upper(),
                            status="RESOLVED", evidence_refs=[], derivation_rule={"defined_by_reviewer": True})
            self.session.add(row)
            self.session.flush()
            entity_id, before, after, claims = row.id, None, {"code": code, "display_name": display_name}, ["ANCHOR"]
        elif action == "REMAP_ANCHOR":
            missing = (question.get("anchor") or {}).get("code")
            if anchor_code not in anchors:
                raise ValueError("Choose an existing anchor to count these visits from.")
            before, after = {"anchor": missing}, {"anchor": anchor_code}

            def remap(value: Any) -> Any:
                if isinstance(value, dict):
                    if value.get("kind") == "ANCHOR" and value.get("code") == missing:
                        value = {**value, "code": anchor_code}
                    return {key: remap(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [remap(item) for item in value]
                return value

            for event in schedule.events:
                if str(event.id) not in question["affected_row_ids"]:
                    continue
                updated = Event.model_validate(remap(event.model_dump(mode="json", by_alias=True)))
                self.repository.replace_event(schedule_version_id, updated)
            entity_id, claims = None, []
        else:
            raise ValueError(f"Unsupported resolution {action}")
        self.session.flush()
        if entity_id is not None:
            evidence_id = self._reviewer_evidence(
                version, reviewer_id=reviewer_id, rationale=reason, action=action,
                entity_type="ANCHOR", entity_id=entity_id, claims=claims,
                detail={"question_id": question_id})
            anchor_row = self.session.get(db.Anchor, entity_id)
            anchor_row.evidence_refs = [*(anchor_row.evidence_refs or []), str(evidence_id)]
        for issue in question["issues"]:
            if issue["origin"] != "EXTRACTION":
                continue
            try:
                row = self.session.get(db.ValidationIssue, UUID(issue["id"]))
            except ValueError:
                row = None
            if row is not None and row.schedule_version_id == version.id and row.status == "OPEN":
                row.status = "RESOLVED"
                row.resolved_by = reviewer_id
                row.resolved_at = datetime.now(timezone.utc)
                row.resolution_note = f"{action}: {reason}"
        self.session.flush()
        # B1: statements this question held are released by a recorded reviewer
        # decision in the stored ledger (never by silently ignoring them).
        from app.services.extraction_service import ExtractionService

        held = {item for issue in question["issues"] for item in issue.get("statement_ids") or []}
        ledger = self.accountability(schedule_version_id) or {}
        for entry in (ledger.get("anchors") or {}).get("unmatched") or []:
            if isinstance(entry, dict) and entry.get("question_id") == question_id:
                held |= {str(item) for item in entry.get("fact_ids") or []}
        released = ExtractionService(self.session).record_statement_decisions(
            schedule_version_id, statement_ids=sorted(held), actor_id=reviewer_id, reason=reason,
            action=action, question_id=question_id,
            resolved_issue_codes=sorted({issue["code"] for issue in question["issues"]}),
        ) if held else []
        after = {**after, "released_statement_ids": released}
        self._revalidate(version)
        if action != "CONFIRM_ANCHOR":   # confirming the extracted reading is not a change
            self._mark_material_edit(version, reviewer_id)
        self._record(version, reviewer_id=reviewer_id, action="REVIEW_QUESTION_RESOLVED",
                     entity_type="ANCHOR" if entity_id else "SCHEDULE_VERSION", entity_id=entity_id,
                     field_path=question_id, before=before, after={**after, "action": action},
                     reason=reason, decision="RESOLVE")
        self.session.flush()
        return self._result(schedule_version_id, before_rows)

    # -- holiday calendar (reviewed attachment) ----------------------------------

    def attach_holiday_calendar(self, schedule_version_id: UUID, *, reviewer_id: UUID,
                                expected_revision: str, rationale: str,
                                calendar: dict[str, Any]) -> dict[str, Any]:
        """Attach (or replace, by code) a schedule-level holiday calendar so a
        SKIP / SHIFT holiday policy has stated dates to act on. A reviewer
        decision with a rationale; stored on the draft and part of its revision."""
        from app.db.repositories import HOLIDAY_CALENDARS_METADATA_KEY
        from app.domain.schedule.models import HolidayCalendar

        if not rationale.strip():
            raise ValueError("Say where this holiday calendar comes from; it is recorded.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        before_rows = {row["id"]: row for row in self.build(schedule, version)["rows"]}
        try:
            typed = HolidayCalendar.model_validate(calendar)
        except Exception as error:
            raise ValueError(f"The holiday calendar is not valid: {error}") from error
        metadata = dict(version.metadata_json or {})
        existing = [item for item in metadata.get(HOLIDAY_CALENDARS_METADATA_KEY) or []]
        previous = next((item for item in existing if item.get("code") == typed.code), None)
        payload = typed.model_dump(mode="json")
        metadata[HOLIDAY_CALENDARS_METADATA_KEY] = [
            item for item in existing if item.get("code") != typed.code] + [payload]
        # ScheduleMetadata forbids unknown keys; reviewer decisions about the
        # version itself are recorded as typed extension entries.
        metadata["extensions"] = [*(metadata.get("extensions") or []), {
            "type": "HOLIDAY_CALENDAR_DECISION", "code": typed.code, "version": typed.version,
            "by": str(reviewer_id), "at": datetime.now(timezone.utc).isoformat(),
            "rationale": rationale.strip(), "replaced": previous is not None,
        }]
        version.metadata_json = metadata
        self.session.flush()
        self._revalidate(version)
        self._mark_material_edit(version, reviewer_id)
        self._record(version, reviewer_id=reviewer_id, action="SCHEDULE_HOLIDAY_CALENDAR_ATTACHED",
                     entity_type="SCHEDULE_VERSION", entity_id=version.id,
                     field_path=f"holiday_calendars.{typed.code}", before=previous, after=payload,
                     reason=rationale.strip(), decision="ADD")
        self.session.flush()
        return self._result(schedule_version_id, before_rows)

    # -- tracks (P5-01 reviewed membership and policy) -----------------------------

    def set_tracks(self, schedule_version_id: UUID, *, reviewer_id: UUID, expected_revision: str,
                   rationale: str, tracks: list[dict[str, Any]], track_links: list[dict[str, Any]],
                   membership: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Replace the draft's tracks, cross-track rules and event membership as ONE
        reviewed decision. Track identity is kept by code across edits; nothing is
        defaulted - an event not named stays exactly as it was. The validator then
        says what is still unresolved, so an incomplete draft can be completed here."""
        from app.db.repositories import (
            EVENT_TRACK_KEY, TRACK_LINKS_METADATA_KEY, TRACKS_METADATA_KEY,
        )
        from app.domain.schedule.models import ScheduleTrack, TrackLink

        if not rationale.strip():
            raise ValueError("Say why these track rules are correct; it is recorded.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        before_rows = {row["id"]: row for row in self.build(schedule, version)["rows"]}
        existing = {item.code: item for item in schedule.tracks}
        built: list[ScheduleTrack] = []
        for item in tracks:
            payload = {key: value for key, value in item.items() if key != "events"}
            prior = existing.get(payload.get("code"))
            if prior is not None:
                payload["id"] = str(prior.id)
                payload.setdefault("evidence_refs", [str(ref) for ref in prior.evidence_refs])
            try:
                built.append(ScheduleTrack.model_validate(payload))
            except Exception as error:
                raise ValueError(f"Track {payload.get('code')!r} is not valid: {error}") from error
        codes = [item.code for item in built]
        if len(codes) != len(set(codes)):
            raise ValueError("Two tracks share a code.")
        try:
            links = [TrackLink.model_validate(item) for item in track_links]
        except Exception as error:
            raise ValueError(f"A cross-track rule is not valid: {error}") from error
        by_code = {item.code: item for item in built}
        event_rows = {row.code: row for row in self.session.scalars(
            select(db.Event).where(db.Event.schedule_version_id == schedule_version_id))}
        for event_code, choice in membership.items():
            row = event_rows.get(event_code)
            if row is None:
                raise ValueError(f"Event {event_code!r} is not part of this schedule.")
            scope = choice.get("scope")
            metadata = dict(row.metadata_json or {})
            if scope == "TRACK":
                track = by_code.get(str(choice.get("track_code")))
                if track is None:
                    raise ValueError(f"{event_code} names a track that is not defined here.")
                metadata[EVENT_TRACK_KEY] = {"scope": "TRACK", "track_id": str(track.id)}
            elif scope == "GLOBAL":
                metadata[EVENT_TRACK_KEY] = {"scope": "GLOBAL", "track_id": None}
            elif scope in (None, "UNCLASSIFIED"):
                metadata.pop(EVENT_TRACK_KEY, None)
            else:
                raise ValueError(f"{event_code}: choose a track, global, or leave it unclassified.")
            row.metadata_json = metadata
        # an event whose track was removed must not keep pointing at it
        for row in event_rows.values():
            marker = (row.metadata_json or {}).get(EVENT_TRACK_KEY) or {}
            if marker.get("scope") == "TRACK" and marker.get("track_id") not in {
                    str(item.id) for item in built}:
                metadata = dict(row.metadata_json or {})
                metadata.pop(EVENT_TRACK_KEY, None)
                row.metadata_json = metadata
        metadata = dict(version.metadata_json or {})
        previous = {"tracks": metadata.get(TRACKS_METADATA_KEY) or [],
                    "track_links": metadata.get(TRACK_LINKS_METADATA_KEY) or []}
        after = {"tracks": [item.model_dump(mode="json") for item in built],
                 "track_links": [item.model_dump(mode="json") for item in links]}
        metadata[TRACKS_METADATA_KEY] = after["tracks"]
        metadata[TRACK_LINKS_METADATA_KEY] = after["track_links"]
        metadata["extensions"] = [*(metadata.get("extensions") or []), {
            "type": "TRACK_DECISION", "by": str(reviewer_id),
            "at": datetime.now(timezone.utc).isoformat(), "rationale": rationale.strip(),
            "tracks": codes}]
        version.metadata_json = metadata
        self.session.flush()
        self._revalidate(version)
        self._mark_material_edit(version, reviewer_id)
        self._record(version, reviewer_id=reviewer_id, action="SCHEDULE_TRACKS_REVIEWED",
                     entity_type="SCHEDULE_VERSION", entity_id=version.id, field_path="tracks",
                     before=previous, after={**after, "membership": membership},
                     reason=rationale.strip(), decision="UPDATE")
        self.session.flush()
        return self._result(schedule_version_id, before_rows)

    # -- approval (UI-08, RV-04, RV-05) -----------------------------------------

    def approve(self, schedule_version_id: UUID, *, reviewer_id: UUID, expected_revision: str,
                reason: str, effective_from: Any = None) -> dict[str, Any]:
        from app.services.schedule_service import ScheduleReviewService

        if not reason.strip():
            raise ValueError("Add a note confirming your review of the schedule.")
        schedule, version = self._editable(schedule_version_id, expected_revision)
        run = self.session.get(db.ExtractionRun, version.extraction_run_id) if version.extraction_run_id else None
        # Gates are recomputed here from the stored draft; nothing the client
        # sends can mark a version approvable.
        view = self.build(schedule, version, run)
        if not view["can_approve"]:
            raise ApprovalBlockedError(view["approval_blockers"])
        ScheduleReviewService(self.session).approve_reviewed_table(
            schedule_version_id, reviewer_id=reviewer_id, reason=reason.strip(),
            effective_from=effective_from)
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_VERSION_APPROVED_AT_REVISION", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=None,
            after={"revision": view["revision"], "semantic_fingerprint": view["semantic_fingerprint"]},
            reason=reason.strip(),
        ))
        self.session.flush()
        return {"status": version.status, "revision": view["revision"],
                "semantic_fingerprint": view["semantic_fingerprint"]}

