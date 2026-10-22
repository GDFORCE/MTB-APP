from datetime import date, datetime, timezone
import asyncio
from uuid import uuid4

import pytest

from app.application.manual_rules import ManualRuleService, verify_manual_decisions
from app.application.authoring import TrialRecord
from app.application.pilot import build_pilot_schedule
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.manual_handling import KEY
from app.domain.schedule.models import PatientContext, ScheduleStatus
from app.domain.schedule.timing import UnresolvedTiming
from app.domain.schedule.validator import ScheduleValidator
from test_p6_approval_hash import Draft
from test_p6_amendment_api import C, client_for

pytest_plugins = ["tests.contract.conftest"]


def builder(protocol_id, name):
    schedule = build_pilot_schedule(protocol_id, name)
    schedule.events[0].timing = UnresolvedTiming(reason="Synthetic unsupported timing, needs manual scheduling")
    return schedule


def setup(uow):
    # PI HTTP access must be tied to an actual operational trial identifier.
    trial = TrialRecord(organization_id=uuid4(), external_trial_id=f"manual-test-{uuid4()}")
    uow.run(lambda tx: tx.authoring.add_trial(trial))
    draft = Draft(uow, org=trial.organization_id, trial=trial, builder=builder)
    return draft, ManualRuleService(uow), uuid4(), uuid4()


def declare(draft, service, actor):
    return service.decide(organization_id=draft.org, trial_id=draft.trial.id, version_id=draft.version,
        event_code="C1D1", actor_id=actor, expected_revision=draft.header().revision, action="DECLARE",
        reason="Review this synthetic rule manually", kind="MANUAL_ONLY",
        evidence_refs=[draft.schedule().evidence[0].id], policy_reference="Synthetic source page 1")


def approve(draft, service, actor):
    return service.decide(organization_id=draft.org, trial_id=draft.trial.id, version_id=draft.version,
        event_code="C1D1", actor_id=actor, expected_revision=draft.header().revision,
        action="APPROVE", reason="Independently reviewed this synthetic manual workflow")


def test_pending_handling_blocks_and_same_actor_cannot_approve(uow):
    draft, service, editor, reviewer = setup(uow)
    declare(draft, service, editor)
    assert "MANUAL_REVIEW_REQUIRED" in {row.issue_code for row in ScheduleValidator().validate(draft.schedule())}
    with pytest.raises(ValueError, match="another approver"):
        approve(draft, service, editor)
    assert approve(draft, service, reviewer)["handling"]["status"] == "APPROVED"


def test_independent_manual_decision_can_approve_but_never_generates_a_target(uow):
    draft, service, editor, reviewer = setup(uow)
    declare(draft, service, editor); approve(draft, service, reviewer)
    uow.read(lambda tx: verify_manual_decisions(tx, draft.schedule()))
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=reviewer,
                                                      reason="Reviewed synthetic manual-only schedule")
    schedule = draft.schedule()
    result = ScheduleEvaluator().evaluate(schedule, PatientContext(
        patient_id=uuid4(), schedule_version_id=draft.version, anchors={"BASELINE": date(2026, 1, 1)}),
        horizon=date(2026, 2, 28), today=date(2026, 1, 1))
    manual = next(row for row in result.events if row.event_code == "C1D1")
    assert manual.timing is None and manual.status.value == "BLOCKED"
    assert manual.explanation["rule_handling"] == "MANUAL_ONLY"


def test_material_edit_invalidates_the_manual_decision(uow):
    draft, service, editor, reviewer = setup(uow)
    declare(draft, service, editor); approve(draft, service, reviewer)
    event = draft.schedule().events[0].model_copy(deep=True)
    event.display_name = "Changed clinical meaning"
    uow.run(lambda tx: tx.schedules.replace_event(draft.version, event))
    assert "MANUAL_REVIEW_REQUIRED" in {row.issue_code for row in ScheduleValidator().validate(draft.schedule())}


def test_metadata_alone_cannot_forge_a_manual_approval(uow):
    draft, service, editor, reviewer = setup(uow)
    declare(draft, service, editor); approve(draft, service, reviewer)
    event = draft.schedule().events[0].model_copy(deep=True)
    event.metadata[KEY]["approval_decision_id"] = str(uuid4())
    uow.run(lambda tx: tx.schedules.replace_event(draft.version, event))
    with pytest.raises(ValueError, match="authenticated"):
        uow.read(lambda tx: verify_manual_decisions(tx, draft.schedule()))


def test_http_requires_capability_and_binds_versions_to_the_url_trial(uow):
    draft, service, editor, reviewer = setup(uow)
    base = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/amendments/rule-handling"
    # Sponsor relation without an operational mapping is established by ownership.
    with client_for(uow, draft.org, [C.SCHEDULE_EVIDENCE_READ], role="sponsor") as reader:
        assert reader.get(base, params={"schedule_version_id": str(draft.version)}).status_code == 200
        body = {"schedule_version_id": str(draft.version), "event_code": "C1D1",
            "expected_revision": draft.header().revision, "kind": "MANUAL_ONLY", "reason": "explicit test",
            "evidence_refs": [str(draft.schedule().evidence[0].id)], "policy_reference": "Synthetic page 1",
            "accepts_responsibility": True}
        assert reader.post(base, json=body).status_code == 403
    with client_for(uow, draft.org, [C.SCHEDULE_CORRECT], role="sponsor", user_id=editor) as writer:
        assert writer.post(base, json={**body, "approved_by": str(reviewer)}).status_code == 422
        assert writer.post(base, json=body).status_code == 200
        assert writer.post(base.replace(str(draft.trial.id), str(uuid4())), json=body).status_code == 404


def test_manual_rule_and_whole_table_are_reviewed_and_approved_in_the_same_plane(uow, monkeypatch):
    monkeypatch.setenv("UCTSM_MAKER_CHECKER", "on")
    draft, service, editor, reviewer = setup(uow)
    declare(draft, service, editor)
    base = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/schedule-versions/{draft.version}"
    rights = [C.SCHEDULE_EVIDENCE_READ, C.SCHEDULE_APPROVE]
    def body():
        return {"expected_revision": draft.header().revision, "reviewed_entire_table": True,
                "reason": "Independently reviewed every synthetic visit and source"}
    with client_for(uow, draft.org, rights, user_id=reviewer) as client:
        view = client.get(base + "/review-view")
        assert view.status_code == 200
        assert not view.json()["view"]["can_approve"]
        assert client.post(base + "/approve-table", json=body()).status_code == 422
    assert draft.header().content_hash is None
    approve(draft, service, reviewer)
    with client_for(uow, draft.org, rights, user_id=editor) as client:
        denied = client.post(base + "/approve-table", json=body())
        assert denied.status_code == 403, denied.text
        assert denied.headers["X-Authz-Code"] == "MAKER_CHECKER"
    assert draft.header().status == "IN_REVIEW" and draft.header().content_hash is None
    denials = uow.read(lambda tx: tx.audit.list_for_entity(draft.org, "SCHEDULE_VERSION", draft.version))
    assert any(row.action == "SCHEDULE_APPROVAL_DENIED_MAKER_CHECKER" for row in denials)
    with client_for(uow, draft.org, rights, user_id=reviewer) as client:
        loaded = client.get(base + "/review-view").json()
        assert loaded["view"]["can_approve"]
        assert any(row["code"] == "C1D1" for row in loaded["view"]["rows"])
        response = client.post(base + "/approve-table", json=body())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "APPROVED" and response.json()["content_hash"]
        assert "view" not in response.json()
    assert draft.header().approved_by == reviewer
    assert draft.header().last_material_editor_id == editor


def test_native_approval_refuses_stale_foreign_forged_and_revoked_requests(uow):
    draft, _service, _editor, reviewer = setup(uow)
    base = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/schedule-versions/{draft.version}"
    body = {"expected_revision": draft.header().revision, "reviewed_entire_table": True,
            "reason": "Synthetic explicit review"}
    with client_for(uow, draft.org, [C.SCHEDULE_EVIDENCE_READ], user_id=reviewer) as client:
        assert client.post(base + "/approve-table", json=body).status_code == 403
    with client_for(uow, draft.org, [C.SCHEDULE_APPROVE, C.SCHEDULE_EVIDENCE_READ], user_id=reviewer) as client:
        assert client.post(base + "/approve-table", json={**body, "actor_id": str(reviewer)}).status_code == 422
        assert client.post(base + "/approve-table", json={**body,
            "expected_revision": body["expected_revision"] + 1}).status_code == 409
        foreign = Draft(uow, org=draft.org)
        wrong_path = base.replace(str(draft.version), str(foreign.version))
        assert client.get(wrong_path + "/review-view").status_code == 404
        assert client.post(wrong_path + "/approve-table", json=body).status_code == 404
        grant = next(row for row in asyncio.run(client.store.list_for_organization(str(draft.org)))
                     if row.capability == C.SCHEDULE_APPROVE.value)
        asyncio.run(client.store.revoke(grant.id, organization_id=str(draft.org), revoked_by=str(uuid4())))
        assert client.post(base + "/approve-table", json=body).status_code == 403
    assert draft.header().content_hash is None


def test_generic_decisions_cannot_masquerade_as_authenticated_manual_approval(uow):
    draft, _service, _editor, reviewer = setup(uow)
    with pytest.raises(ValueError, match="dedicated authenticated"):
        ScheduleReviewService(uow).record_decision(draft.version, reviewer_id=reviewer,
                                                 decision="MANUAL_APPROVE")
    from app.services.schedule_service import ScheduleReviewService as SqlReview
    with pytest.raises(ValueError, match="dedicated authenticated"):
        SqlReview(None).record_decision(draft.version, reviewer_id=reviewer, decision="MANUAL_APPROVE")


@pytest.mark.parametrize("field", ["metadata", "evidence_refs"])
def test_whole_event_correction_records_metadata_and_evidence_provenance(uow, field):
    draft = Draft(uow)
    editor = uuid4()
    event = draft.schedule().events[0].model_copy(deep=True)
    if field == "metadata":
        event.metadata["clinical_review_reference"] = "Synthetic reviewed source interpretation"
    else:
        event.evidence_refs = []
    ScheduleReviewService(uow).correct_event(draft.version, event, reviewer_id=editor,
                                           reason="Synthetic whole-event source correction")
    assert draft.header().last_material_editor_id == editor
