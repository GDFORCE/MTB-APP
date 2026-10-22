"""Shared plumbing of the SQL review services: session, maker-checker provenance, audit rows.

Extracted from ``ScheduleReviewService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, QualifierCategory, QualifierScope, ScheduleStatus, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator
from app.db.repositories import ScheduleRepository
from app.services.schedule_facts import utc_now

if False:  # pragma: no cover - typing only
    from app.authorization.maker_checker import MakerCheckerPolicy, Provenance


class ReviewBase:
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
