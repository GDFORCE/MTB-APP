"""The cross-trial list of schedule versions waiting for a decision.

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


class ReviewQueue(ReviewBase):
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
