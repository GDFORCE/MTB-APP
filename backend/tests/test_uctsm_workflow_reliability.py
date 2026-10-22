"""Regression checks for the upload/review/enrollment seams, without model calls."""
from datetime import timedelta
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.extraction.llm_client import ExtractionModelError
from app.services.enrolment_preview import preview_enrolment
from app.services.operational_bridge import OperationalBridgeService
from app.services.schedule_service import ScheduleReviewService
from test_canonical_journey import (
    session, imported, approve, ORG, REVIEWER, BASELINE, HORIZON,
)


def test_a_repeat_preserves_its_delayed_first_visit_and_window():
    from datetime import date
    from app.domain.schedule.evaluator import ScheduleEvaluator
    from app.domain.schedule.models import Event, RecurrenceRule
    from test_uctsm_domain import schedule_with, context
    event = Event(
        code="W4", protocol_label="W4", display_name="Week 4", event_type="VISIT",
        timing={"type": "NOMINAL_WITH_WINDOW", "nominal": {
            "type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
            "offset": {"value": 28, "unit": "DAY"}},
            "window": {"before": {"value": 3, "unit": "DAY"}, "after": {"value": 3, "unit": "DAY"}}},
        recurrence=RecurrenceRule(
            interval={"value": 28, "unit": "DAY"},
            start_reference={"kind": "ANCHOR", "code": "BASELINE"},
            start_offset={"value": 28, "unit": "DAY"}, termination={"type": "COUNT", "count": 3}),
    )
    schedule = schedule_with(event)
    result = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31))
    assert [item.timing.nominal_start for item in result.events] == [date(2026, 1, 29), date(2026, 2, 26), date(2026, 3, 26)]
    assert result.events[0].timing.earliest == date(2026, 1, 26)
    assert result.events[0].timing.latest == date(2026, 2, 1)


def test_missing_json_lists_and_invalid_entries_cannot_look_like_no_findings():
    from app.extraction.llm_client import parse_json_list
    for answer in ['{}', '{"claims": [42]}']:
        with pytest.raises(ExtractionModelError):
            parse_json_list(answer, "claims")


def preview(session, trial, row, **kwargs):
    return preview_enrolment(
        session, organization_id=ORG, trial_id=trial.id,
        definition_id=row.schedule_definition_id, baseline=BASELINE, horizon=HORIZON,
        expected_version_id=row.schedule_version_id, **kwargs)


def test_canonical_preview_is_read_only_and_matches_enrollment_with_additional_anchors(session):
    trial, row = imported(session)
    with pytest.raises(ValueError, match="no approved"):
        preview(session, trial, row)
    approve(session, row.schedule_version_id)
    schedule = ScheduleRepository(session).get(row.schedule_version_id)
    last_dose = next(anchor.code for anchor in schedule.anchors if anchor.display_name == "Last Dose")
    dates = {last_dose: BASELINE + timedelta(days=60)}
    before = session.scalar(select(func.count()).select_from(db.Patient))
    result = preview(session, trial, row, anchor_dates=dates)
    assert session.scalar(select(func.count()).select_from(db.Patient)) == before
    enrollment = OperationalBridgeService(session).enroll_patient(
        organization_id=ORG, actor_id=REVIEWER, canonical_trial_id=trial.id,
        schedule_definition_id=row.schedule_definition_id, expected_version_id=row.schedule_version_id,
        external_patient_id="preview-patient", patient_code="P-001", baseline_date=BASELINE,
        anchor_dates=dates, horizon=HORIZON,
    )
    assert [(event["event_code"], event["scheduled_date"]) for event in result["visits"]] == [
        (event.event_code, event.nominal_date) for event in enrollment.events]
    assert any(event["scheduled_date"] == BASELINE + timedelta(days=90) for event in result["visits"])


def test_missing_anchor_stays_undated_with_an_explanation(session):
    trial, row = imported(session)
    approve(session, row.schedule_version_id)
    result = preview(session, trial, row)
    waiting = [event for event in result["visits"] if not event["scheduled_date"]]
    assert waiting
    assert all(event["manual_review_reason"] for event in waiting)


def test_unknown_anchor_and_stale_version_are_rejected_before_enrollment(session):
    trial, row = imported(session)
    approve(session, row.schedule_version_id)
    with pytest.raises(ValueError, match="Unknown protocol anchor"):
        preview(session, trial, row, anchor_dates={"MADE_UP": BASELINE})
    with pytest.raises(ValueError, match="changed"):
        preview_enrolment(session, organization_id=ORG, trial_id=trial.id,
                          definition_id=row.schedule_definition_id, baseline=BASELINE,
                          horizon=HORIZON, expected_version_id=uuid4())
    with pytest.raises(ValueError, match="organization"):
        preview_enrolment(session, organization_id=uuid4(), trial_id=trial.id,
                          definition_id=row.schedule_definition_id, baseline=BASELINE, horizon=HORIZON)


def test_validation_preserves_extraction_findings_until_audited_source_resolution(session):
    _, row = imported(session)
    issue_id = uuid4()
    session.add(db.ValidationIssue(
        id=issue_id, schedule_version_id=row.schedule_version_id, validator_version="uctsm-validator.v1",
        issue_code="UNSUPPORTED_PROTOCOL_CONSTRUCT", severity="ERROR", status="OPEN", blocking=True,
        message="A source claim was dropped", details={"origin": "EXTRACTION"},
    ))
    session.commit()
    service = ScheduleReviewService(session)
    issues = service.validate(row.schedule_version_id)
    session.commit()
    assert any(issue.id == issue_id for issue in issues)
    service.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)
    service.bulk_confirm_all_fields(row.schedule_version_id, reviewer_id=REVIEWER, reason="Checked table")
    session.flush()
    with pytest.raises(ValueError, match="approval blocked"):
        service.approve(row.schedule_version_id, reviewer_id=REVIEWER)
    session.rollback()
    service.resolve_extraction_issue(row.schedule_version_id, issue_id,
                                     reviewer_id=REVIEWER, reason="Checked page 8; the claim duplicates the retained visit.")
    session.commit()
    assert not any(issue.id == issue_id for issue in service.validate(row.schedule_version_id))
    assert session.scalar(select(db.ReviewDecision).where(db.ReviewDecision.entity_id == issue_id)).decision == "RESOLVE"


def test_rejected_schedule_can_be_resubmitted(session):
    _, row = imported(session)
    service = ScheduleReviewService(session)
    service.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)
    service.reject(row.schedule_version_id, reviewer_id=REVIEWER, reason="Recheck the table")
    service.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)
    assert session.get(db.ScheduleVersion, row.schedule_version_id).status == "IN_REVIEW"


def test_table_approval_is_one_audited_action_without_bypassing_blockers(session):
    _, row = imported(session)
    service = ScheduleReviewService(session)
    with pytest.raises(ValueError, match="note"):
        service.approve_reviewed_table(row.schedule_version_id, reviewer_id=REVIEWER, reason=" ")
    service.approve_reviewed_table(row.schedule_version_id, reviewer_id=REVIEWER, reason="Checked the table and original source.")
    session.commit()
    assert session.get(db.ScheduleVersion, row.schedule_version_id).status == "APPROVED"
    actions = list(session.scalars(select(db.AuditEvent.action).where(db.AuditEvent.entity_id == row.schedule_version_id)))
    assert actions.count("SCHEDULE_BULK_CONFIRMED") == 1
    assert actions.count("SCHEDULE_APPROVED") == 1


def test_table_approval_does_not_dismiss_source_findings_or_record_false_reviews(session):
    _, row = imported(session)
    issue = db.ValidationIssue(schedule_version_id=row.schedule_version_id, validator_version="v1",
        issue_code="SOURCE_CONTENT_UNMAPPED", severity="ERROR", status="OPEN", blocking=True,
        message="A source assessment is missing", details={"origin": "EXTRACTION"})
    session.add(issue)
    session.commit()
    before = session.scalar(select(func.count()).select_from(db.ReviewDecision))
    with pytest.raises(ValueError, match="flagged"):
        ScheduleReviewService(session).approve_reviewed_table(row.schedule_version_id,
            reviewer_id=REVIEWER, reason="Checked the table")
    assert session.scalar(select(func.count()).select_from(db.ReviewDecision)) == before
    assert session.get(db.ScheduleVersion, row.schedule_version_id).status == "VALIDATION_REQUIRED"
    assert issue.status == "OPEN"


@pytest.mark.parametrize("previously_approved", [False, True])
def test_replacement_approval_recovers_drafts_without_activating_an_amendment(session, previously_approved):
    from pathlib import Path
    import re
    from app.services.native_import import import_native_schedules
    captured = json.loads((Path(__file__).resolve().parents[2] / "docs/audits/uctsm-live-smoke.json").read_text())
    def import_document(marker):
        # Independent extraction runs assign fresh IDs, including source refs.
        identities = {}
        schedule = json.loads(re.sub(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
            lambda match: identities.setdefault(match[0], str(uuid4())),
            json.dumps(captured[0]["schedule"]),
        ))
        return import_native_schedules(
            session, organization_id=ORG, actor_id=REVIEWER, external_trial_id="recovery-trial",
            protocol_number="RECOVERY-001", study_title="Synthetic recovery",
            document_uri=f"private://{marker}.txt", document_name=f"{marker}.txt", document_hash=marker * 64,
            definitions=[{"id": marker, "native_schedule": schedule, "native_issues": []}],
        )
    trial, first = import_document("a")
    protocol = session.scalar(select(db.Protocol).where(db.Protocol.trial_id == trial.id))
    original_version_id = protocol.current_version_id
    if previously_approved:
        approve(session, first[0].schedule_version_id)
    _, replacement = import_document("b")
    assert protocol.current_version_id == original_version_id
    approve(session, replacement[0].schedule_version_id)
    if previously_approved:
        assert protocol.current_version_id == original_version_id
    else:
        assert protocol.current_version_id != original_version_id
        assert preview(session, trial, replacement[0])["schedule_version_id"] == str(replacement[0].schedule_version_id)


@pytest.mark.parametrize("pipeline", ["category", "source_first", "source_grounded", "source_generalization"])
def test_saved_live_extraction_can_be_reviewed_approved_and_dated(session, pipeline):
    """Replay the inspected real-provider response; no network or approval bypass."""
    from datetime import date
    from pathlib import Path
    from app.services.native_import import import_native_schedules
    from app.domain.schedule.projection import project_schedule
    captured = json.loads((Path(__file__).resolve().parents[2] / "docs/audits/uctsm-live-smoke.json").read_text())
    if pipeline == "source_first":
        captured = json.loads((Path(__file__).resolve().parents[2] / "docs/audits/source-first-final-smoke.json").read_text())["runs"][pipeline]["results"]
    elif pipeline == "source_grounded":
        captured = json.loads((Path(__file__).resolve().parents[2] / "docs/audits/source-grounding-live-smoke.json").read_text())["runs"]["source_first"]["results"]
    elif pipeline == "source_generalization":
        captured = json.loads((Path(__file__).resolve().parents[2] / "docs/audits/source-generalization-live-smoke.json").read_text())["runs"]["source_first"]["results"]
    assert len(captured) == 1 and captured[0]["issues"] == []
    trial, rows = import_native_schedules(
        session, organization_id=ORG, actor_id=REVIEWER, external_trial_id="live-smoke-replay",
        protocol_number="WF-001", study_title="Synthetic smoke", document_uri="private://synthetic-smoke.txt",
        document_name="synthetic-smoke.txt", document_hash="a" * 64,
        definitions=[{"id": "smoke-definition", "native_schedule": captured[0]["schedule"], "native_issues": []}],
    )
    row = rows[0]
    ScheduleReviewService(session).approve_reviewed_table(row.schedule_version_id,
        reviewer_id=REVIEWER, reason="Checked the source and all three table rows.")
    session.commit()
    result = preview(session, trial, row, anchor_dates={"LAST_DOSE": date(2026, 9, 30)})
    by_code = {event["event_code"]: event for event in result["visits"]}
    assert set(by_code) == {"C1D1", "W4", "FU"}
    assert by_code["C1D1"]["scheduled_date"] == date(2026, 9, 1)
    assert by_code["W4"]["scheduled_date"] == date(2026, 9, 29)
    assert by_code["W4"]["window_start"] == date(2026, 9, 26)
    assert by_code["W4"]["window_end"] == date(2026, 10, 2)
    assert by_code["FU"]["scheduled_date"] == date(2026, 10, 30)
    enrollment = OperationalBridgeService(session).enroll_patient(
        organization_id=ORG, actor_id=REVIEWER, canonical_trial_id=trial.id,
        schedule_definition_id=row.schedule_definition_id, expected_version_id=row.schedule_version_id,
        external_patient_id="smoke-patient", patient_code="SMOKE-001", baseline_date=BASELINE,
        anchor_dates={"LAST_DOSE": date(2026, 9, 30)}, horizon=HORIZON,
    )
    session.commit()
    assert {event.event_code: event.nominal_date for event in enrollment.events} == {
        code: event["scheduled_date"] for code, event in by_code.items()}
    schedule = ScheduleRepository(session).get(row.schedule_version_id)
    assert [[activity.display_name for activity in event.activities] for event in schedule.events] == [
        ["Vitals", "ECG"], ["Vitals", "ECG"], ["Adverse event review"]]
    projections = project_schedule(schedule)
    timing_claims = [claim for claim in schedule.claim_evidence if claim.claim_type == "TIMING"]
    for claim in timing_claims:
        assert str(claim.evidence_id) in next(item.evidence_refs for item in projections if item.event_id == str(claim.claim_entity_id))


def _footnote_issue(session, row, code="FOOTNOTE_NOT_INTERPRETED"):
    issue_id = uuid4()
    session.add(db.ValidationIssue(
        id=issue_id, schedule_version_id=row.schedule_version_id, validator_version="uctsm-validator.v1",
        issue_code=code, severity="ERROR", status="OPEN", blocking=True,
        message="Footnote * of Table 1 is not interpreted", details={"origin": "EXTRACTION"},
    ))
    session.commit()
    return issue_id


def test_a_footnote_finding_is_resolved_as_no_scheduling_effect_with_a_reason_and_the_type_is_recorded(session):
    _, row = imported(session)
    issue_id = _footnote_issue(session, row)
    ScheduleReviewService(session).resolve_extraction_issue(
        row.schedule_version_id, issue_id, reviewer_id=REVIEWER, resolution="NO_SCHEDULING_EFFECT",
        reason="Read page 23: investigator discretion for unscheduled assessments; no timing rule.")
    session.commit()
    decision = session.scalar(select(db.ReviewDecision).where(db.ReviewDecision.entity_id == issue_id))
    assert decision.new_value == {"status": "RESOLVED", "resolution": "NO_SCHEDULING_EFFECT"}
    assert "investigator discretion" in decision.reason


def test_the_no_effect_resolution_needs_a_reason_and_is_refused_for_any_other_finding(session):
    _, row = imported(session)
    service = ScheduleReviewService(session)
    footnote = _footnote_issue(session, row)
    with pytest.raises(ValueError):
        service.resolve_extraction_issue(row.schedule_version_id, footnote, reviewer_id=REVIEWER,
                                         resolution="NO_SCHEDULING_EFFECT", reason="  ")
    timing = _footnote_issue(session, row, code="AMBIGUOUS_TIMING")
    with pytest.raises(ValueError, match="not a resolution"):
        service.resolve_extraction_issue(row.schedule_version_id, timing, reviewer_id=REVIEWER,
                                         resolution="NO_SCHEDULING_EFFECT", reason="Looks harmless")
    session.rollback()
    assert session.get(db.ValidationIssue, timing).status == "OPEN"
