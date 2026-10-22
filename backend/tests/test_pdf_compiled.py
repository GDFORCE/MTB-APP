from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.application.compiled_schedules import compiled_for
from app.domain.schedule.compiled import artifact_key, compile_schedule
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import PatientContext, ValidationIssue
from app.domain.schedule.validator import ScheduleValidator
from test_p6_approval_hash import Draft

pytest_plugins = ["tests.contract.conftest"]


def approved(uow):
    from app.application.schedule_review import ScheduleReviewService
    draft = Draft(uow)
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=uuid4(), reason="Reviewed synthetic rules")
    return draft


def test_validation_is_reused_only_for_matching_approved_content(uow):
    draft = approved(uow); schedule = draft.schedule()
    first = uow.run(lambda tx: compiled_for(tx, schedule, draft.org))
    with patch.object(ScheduleValidator, "validate", side_effect=AssertionError("cache should be reused")):
        cached = uow.run(lambda tx: compiled_for(tx, schedule, draft.org))
        context = PatientContext(patient_id=uuid4(), schedule_version_id=draft.version,
            anchors={"BASELINE": date(2026, 1, 1)})
        actual = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2026, 2, 28),
            today=date(2026, 1, 1), compiled=cached)
    full = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2026, 2, 28), today=date(2026, 1, 1))
    assert actual.model_dump(exclude={"evaluated_at"}) == full.model_dump(exclude={"evaluated_at"})
    assert cached.artifact_hash == first.artifact_hash


def test_content_engine_validator_and_extraction_findings_invalidate_artifacts(uow, monkeypatch):
    draft = approved(uow); schedule = draft.schedule()
    compiled = compile_schedule(schedule, evaluator_version=ScheduleEvaluator.version)
    changed = schedule.model_copy(deep=True); changed.events[0].display_name += " changed"
    with pytest.raises(ValueError, match="stale|damaged"):
        compiled.verify_for(changed, ScheduleEvaluator.version)
    with pytest.raises(ValueError, match="stale|damaged"):
        compiled.verify_for(schedule, "different-evaluator")
    changed = schedule.model_copy(deep=True)
    changed.validation_issues.append(ValidationIssue(issue_code="EXTRACTION_GAP", severity="ERROR",
        message="Source coverage incomplete", blocking=True, details={"origin": "EXTRACTION"}))
    assert artifact_key(changed, ScheduleEvaluator.version) != compiled.cache_key
    with pytest.raises(ValueError, match="stale|damaged"):
        compiled.verify_for(changed, ScheduleEvaluator.version)
    monkeypatch.setattr(ScheduleValidator, "version", "test-next-validator")
    with pytest.raises(ValueError, match="stale|damaged"):
        compiled.verify_for(schedule, ScheduleEvaluator.version)


def test_damaged_payload_is_refused_and_drafts_are_not_cached(uow):
    draft = approved(uow); schedule = draft.schedule()
    compiled = compile_schedule(schedule, evaluator_version=ScheduleEvaluator.version)
    compiled.event_order.reverse()
    with pytest.raises(ValueError, match="stale|damaged"):
        compiled.verify_for(schedule, ScheduleEvaluator.version)
    other = Draft(uow)
    good = compile_schedule(other.schedule(), evaluator_version=ScheduleEvaluator.version)
    with pytest.raises(ValueError, match="only approved"):
        uow.run(lambda tx: tx.schedules.save_compiled(other.version, good))


def test_service_approval_persists_a_reusable_artifact_in_the_same_transaction(uow):
    from app.application.schedule_review import ScheduleReviewService
    draft = Draft(uow)
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=uuid4(), reason="Reviewed synthetic rules")
    schedule = draft.schedule()
    cached = uow.read(lambda tx: tx.schedules.get_compiled(draft.version, artifact_key(schedule, ScheduleEvaluator.version)))
    assert cached is not None
    cached.verify_for(schedule, ScheduleEvaluator.version)
