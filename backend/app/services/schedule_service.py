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

from app.services.schedule_facts import (  # noqa: E402,F401 - re-exported: callers import these names from here
    ASSIGNMENT_FACT_STATE_CODE, BOOKED_KEY, EFFECTIVE_AT_KEY, END_KEY, FACT_KIND_KEY,
    PROCESS_TRANSITION_STATE_CODE, RESERVED_STATE_CODES, SITE_HOLIDAY_CALENDAR_STATE_CODE,
    SUPERSEDES_KEY, occurrence_fact_from_row, utc_now,
)
from app.services.schedule_version_resolution import (  # noqa: E402,F401
    _enrollable_protocol_version, current_protocol_approved_versions, in_force_today,
    resolve_current_schedule_version,
)
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.services.review_approval import ReviewApproval  # noqa: E402
from app.services.review_editing import ReviewEditing  # noqa: E402
from app.services.review_queue import ReviewQueue  # noqa: E402

if False:  # pragma: no cover - typing only
    from app.authorization.maker_checker import MakerCheckerPolicy, Provenance


class ScheduleReviewService(ReviewEditing, ReviewApproval, ReviewQueue):
    """Compatibility facade (P12-02): the behaviour lives in ``review_base`` / ``review_editing`` /
    ``review_approval`` / ``review_queue``; every public method, signature and import path is unchanged."""


from app.services.patient_schedule_evaluation import PatientScheduleEvaluation  # noqa: E402


class PatientScheduleService(PatientScheduleEvaluation):
    """Compatibility facade (P12-02): the behaviour lives in ``patient_schedule_reads`` / ``_context`` /
    ``_persistence`` / ``_recording`` / ``_evaluation``; every public method, signature and import path is unchanged."""
