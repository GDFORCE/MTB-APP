"""P7-03: an unresolved reading is settled only by a reviewed, evidence-backed resolution."""
from uuid import uuid4

import pytest

from app.application.authoring import TrialRecord
from app.application.conflict_resolution import ConflictResolutionService
from app.application.pilot import build_pilot_schedule
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.models import InterpretationStatus
from app.domain.schedule.validator import ScheduleValidator
from test_p6_approval_hash import Draft
from test_p6_amendment_api import C, client_for

pytest_plugins = ["tests.contract.conftest"]


def builder(status=InterpretationStatus.CONFLICTING, *, strip_quote=False, drop_refs=False):
    def make(protocol_id, name):
        schedule = build_pilot_schedule(protocol_id, name)
        schedule.events[0].interpretation_status = status
        if strip_quote:
            for item in schedule.evidence:
                item.source_text = ""
        if drop_refs:
            schedule.events[0].evidence_refs = []
        return schedule
    return make


def setup(uow, **options):
    trial = TrialRecord(organization_id=uuid4(), external_trial_id=f"conflict-{uuid4()}")
    uow.run(lambda tx: tx.authoring.add_trial(trial))
    draft = Draft(uow, org=trial.organization_id, trial=trial, builder=builder(**options))
    event = draft.schedule().events[0]
    return draft, ConflictResolutionService(uow), event, uuid4()


def resolve(draft, service, event, reviewer, **extra):
    args = dict(confirmed_reading="Cycle 1 Day 1 is the baseline-relative start",
                reason="Source page and table row both state baseline-relative timing")
    args["chosen_evidence_ids"] = [event.evidence_refs[0]] if event.evidence_refs else [uuid4()]
    args.update(extra)
    return service.resolve_event(draft.org, draft.trial.id, draft.version, event.id, reviewer_id=reviewer, **args)


def test_an_unresolved_event_blocks_and_is_listed_with_its_competing_evidence(uow):
    draft, service, event, _ = setup(uow)
    assert "CONFLICTING_EVIDENCE" in {i.issue_code for i in ScheduleValidator().validate(draft.schedule()) if i.blocking}
    [row] = service.list_conflicts(draft.org, draft.trial.id, draft.version)
    assert row["event_code"] == event.code and row["status"] == "CONFLICTING" and row["resolvable"] is True
    assert row["evidence"] and {"page", "section", "quote", "evidence_id"} <= set(row["evidence"][0])


def test_resolution_confirms_only_with_supporting_evidence_a_reading_and_a_reason(uow):
    draft, service, event, reviewer = setup(uow)
    with pytest.raises(ValueError):
        resolve(draft, service, event, reviewer, reason="   ")
    with pytest.raises(ValueError):
        resolve(draft, service, event, reviewer, chosen_evidence_ids=[])
    with pytest.raises(ValueError, match="already linked"):
        resolve(draft, service, event, reviewer, chosen_evidence_ids=[uuid4()])
    with pytest.raises(KeyError):
        service.resolve_event(draft.org, draft.trial.id, draft.version, uuid4(), reviewer_id=reviewer,
                              chosen_evidence_ids=[event.evidence_refs[0]], confirmed_reading="x y z", reason="r s t")
    assert draft.schedule().events[0].interpretation_status == InterpretationStatus.CONFLICTING   # nothing changed
    result = resolve(draft, service, event, reviewer)
    assert result["status"] == "CONFIRMED" and "CONFLICTING_EVIDENCE" not in result["remaining_blocking"]
    assert service.list_conflicts(draft.org, draft.trial.id, draft.version) == []
    with pytest.raises(ValueError, match="no unresolved"):
        resolve(draft, service, event, reviewer)


def test_the_competing_claims_are_preserved_in_the_review_decision_and_the_edit_is_material(uow):
    draft, service, event, reviewer = setup(uow)
    resolve(draft, service, event, reviewer)
    decision = [d for d in uow.read(lambda tx: tx.schedules.list_decisions(draft.version))
                if d.decision == "RESOLVE_CONFLICT"][0]
    assert decision.previous_value["interpretation_status"] == "CONFLICTING"
    assert decision.previous_value["competing_evidence"] and decision.reviewer_id == reviewer
    assert decision.new_value["chosen_evidence_ids"] == [str(event.evidence_refs[0])]
    assert draft.header().last_material_editor_id == reviewer                 # maker-checker provenance
    audit = [a.action for a in uow.read(lambda tx: tx.audit.list_for_entity(draft.org, "EVENT", event.id))]
    assert "SCHEDULE_CONFLICT_RESOLVED" in audit


def test_an_event_without_quoted_evidence_cannot_be_confirmed(uow):
    draft, service, event, reviewer = setup(uow, strip_quote=True)
    assert service.list_conflicts(draft.org, draft.trial.id, draft.version)[0]["resolvable"] is False
    with pytest.raises(ValueError, match="quote"):
        resolve(draft, service, event, reviewer)
    bare, bare_service, bare_event, _ = setup(uow, drop_refs=True)
    assert bare_service.list_conflicts(bare.org, bare.trial.id, bare.version)[0]["resolvable"] is False
    with pytest.raises(ValueError):
        resolve(bare, bare_service, bare_event, reviewer, chosen_evidence_ids=[uuid4()])


def test_a_generic_event_correction_cannot_flip_an_unresolved_status(uow):
    draft, _service, event, reviewer = setup(uow)
    flipped = draft.schedule().events[0].model_copy(update={"interpretation_status": InterpretationStatus.CONFIRMED})
    with pytest.raises(ValueError, match="conflict resolution"):
        ScheduleReviewService(uow).correct_event(draft.version, flipped, reviewer_id=reviewer, reason="looks fine")
    assert draft.schedule().events[0].interpretation_status == InterpretationStatus.CONFLICTING
    other = draft.schedule().events[0].model_copy(update={"display_name": "Cycle 1 Day 1 (renamed)"})
    ScheduleReviewService(uow).correct_event(draft.version, other, reviewer_id=reviewer, reason="rename only")   # still allowed


def test_a_frozen_version_cannot_be_resolved(uow):
    draft, service, event, reviewer = setup(uow)
    uow.run(lambda tx: tx.schedules.transition(draft.version, to_status="REJECTED"))
    with pytest.raises(ValueError, match="frozen"):
        resolve(draft, service, event, reviewer)


def test_http_needs_the_right_capabilities_and_binds_the_version_to_the_url_trial(uow):
    draft, _service, event, reviewer = setup(uow)
    base = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/schedule-versions/{draft.version}/conflicts"
    body = {"chosen_evidence_ids": [str(event.evidence_refs[0])], "confirmed_reading": "Baseline-relative start",
            "reason": "Source page states baseline-relative timing"}
    with client_for(uow, draft.org, [C.SCHEDULE_EVIDENCE_READ], role="sponsor") as reader:
        listed = reader.get(base)
        assert listed.status_code == 200 and listed.json()["conflicts"][0]["event_code"] == event.code
        assert reader.post(f"{base}/{event.id}/resolve", json=body).status_code == 403
    with client_for(uow, draft.org, [], role="smo") as nobody:
        assert nobody.get(base).status_code in {403, 404}                # no evidence-read right for this role
    with client_for(uow, draft.org, [C.SCHEDULE_CORRECT], role="sponsor", user_id=reviewer) as writer:
        assert writer.post(f"{base}/{event.id}/resolve", json={**body, "actor_id": str(reviewer)}).status_code == 422
        wrong = base.replace(str(draft.trial.id), str(uuid4()))
        assert writer.post(f"{wrong}/{event.id}/resolve", json=body).status_code == 404
        done = writer.post(f"{base}/{event.id}/resolve", json=body)
        assert done.status_code == 200 and done.json()["status"] == "CONFIRMED"
        assert writer.post(f"{base}/{event.id}/resolve", json=body).status_code == 422     # nothing left to resolve
    assert draft.header().last_material_editor_id == reviewer
