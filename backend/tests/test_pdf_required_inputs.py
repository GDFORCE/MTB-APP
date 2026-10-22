"""Required patient details: derived from the rules, extended by the reviewer, acknowledged before approval, entered at enrolment."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.authoring import TrialRecord
from app.application.operational_bridge import OperationalBridgeService
from app.application.patient_schedule import PatientScheduleService
from app.application.pilot import build_pilot_schedule
from app.application.required_inputs import RequiredInputsService
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.models import Anchor, ClaimEvidence, Event
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount, TimeUnit
from test_p6_approval_hash import Draft

pytest_plugins = ["tests.contract.conftest"]
IST = timezone(timedelta(hours=5, minutes=30))


def builder(protocol_id, name):
    schedule = build_pilot_schedule(protocol_id, name)
    evidence = schedule.evidence[0]
    for code, label in (("SURGERY", "Surgery date"), ("DOSE_TIME", "Dose time")):
        schedule.anchors.append(Anchor(code=code, display_name=label, anchor_type=code, status="RESOLVED"))
    for code, anchor, value, unit in (("FOLLOW_UP", "SURGERY", 30, TimeUnit.DAY), ("PK_SAMPLE", "DOSE_TIME", 90, TimeUnit.MINUTE)):
        event = Event(code=code, protocol_label=code, display_name=code.replace("_", " ").title(), event_type="VISIT",
                      timing=OffsetTiming(reference=AnchorReference(code=anchor), offset=TemporalAmount(value=value, unit=unit)),
                      evidence_refs=[evidence.id], track_scope="GLOBAL")
        schedule.events.append(event)
        for kind in ("EVENT_NAME", "TIMING"):
            schedule.claim_evidence.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=kind, claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="display_name" if kind == "EVENT_NAME" else "timing", confidence=1))
    return schedule


def setup(uow):
    trial = TrialRecord(organization_id=uuid4(), external_trial_id=f"inputs-{uuid4()}")
    uow.run(lambda tx: tx.authoring.add_trial(trial))
    draft = Draft(uow, org=trial.organization_id, trial=trial, builder=builder)
    return draft, RequiredInputsService(uow), uuid4()


def codes(view, key="effective"):
    return {row["code"]: row["kind"] for row in view[key]}


def approve(uow, draft, reviewer=None):
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=reviewer or uuid4(), reason="Reviewed")


def test_the_rules_alone_produce_the_list_with_time_of_day_only_where_needed(uow):
    draft, service, _ = setup(uow)
    view = service.view(draft.org, draft.trial.id, draft.version)
    assert codes(view, "derived") == {"ANCHOR:SURGERY": "DATE", "ANCHOR:DOSE_TIME": "DATETIME"}
    assert "BASELINE" not in str(view["derived"])                    # the origin date is always asked already
    assert view["needs_acknowledgement"] and not view["acknowledged"]


def test_approval_is_blocked_until_the_current_list_is_acknowledged_and_lapses_when_it_changes(uow):
    draft, service, reviewer = setup(uow)
    with pytest.raises(ValueError, match="acknowledge"):
        approve(uow, draft)
    service.acknowledge(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, reason="Reviewed the list")
    assert service.view(draft.org, draft.trial.id, draft.version)["acknowledged"]
    service.add_custom(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, code="weight_kg",
                       label="Weight (kg)", kind="NUMBER", reason="Dose is weight based")
    view = service.view(draft.org, draft.trial.id, draft.version)
    assert "CUSTOM:WEIGHT_KG" in codes(view) and not view["acknowledged"]       # the list changed
    with pytest.raises(ValueError, match="acknowledge"):
        approve(uow, draft)
    service.acknowledge(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, reason="Reviewed again")
    approve(uow, draft)
    assert draft.header().status == "APPROVED"


def test_custom_entries_are_validated_unique_removable_and_frozen_after_approval(uow):
    draft, service, reviewer = setup(uow)
    base = dict(reviewer_id=reviewer, label="Weight (kg)", kind="NUMBER", reason="Needed for dosing")
    for bad in ({"code": "x"}, {"code": "has space"}, {"code": "WEIGHT", "kind": "BLOB"},
                {"code": "WEIGHT", "label": "x"}, {"code": "WEIGHT", "reason": " "}):
        with pytest.raises(ValueError):
            service.add_custom(draft.org, draft.trial.id, draft.version, **{**base, **bad})
    service.add_custom(draft.org, draft.trial.id, draft.version, code="WEIGHT", **base)
    with pytest.raises(ValueError, match="already exists"):
        service.add_custom(draft.org, draft.trial.id, draft.version, code="weight", **base)
    service.remove_custom(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, code="CUSTOM:WEIGHT", reason="Not needed")
    assert "CUSTOM:WEIGHT" not in codes(service.view(draft.org, draft.trial.id, draft.version))
    with pytest.raises(KeyError):
        service.remove_custom(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, code="CUSTOM:NOPE", reason="none")
    service.acknowledge(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, reason="Reviewed the list")
    approve(uow, draft)
    with pytest.raises(ValueError, match="frozen"):
        service.add_custom(draft.org, draft.trial.id, draft.version, code="LATER", **base)
    with pytest.raises(KeyError):                                                  # another trial's id reveals nothing
        service.view(draft.org, uuid4(), draft.version)


def test_an_edit_by_the_reviewer_counts_for_maker_checker(uow):
    draft, service, reviewer = setup(uow)
    service.add_custom(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, code="WEIGHT",
                       label="Weight (kg)", kind="NUMBER", reason="Needed for dosing")
    assert draft.header().last_material_editor_id == reviewer


def enrol(uow, draft, **extra):
    definition = draft.header().schedule_definition_id
    args = dict(organization_id=draft.org, actor_id=uuid4(), canonical_trial_id=draft.trial.id,
                schedule_definition_id=definition, external_patient_id=f"p-{uuid4().hex[:8]}", patient_code=f"C{uuid4().hex[:5]}",
                baseline_date=date(2026, 3, 1), horizon=date(2026, 12, 31))
    args.update(extra)
    return OperationalBridgeService(uow).enroll_patient(**args)


def approved_with_list(uow):
    draft, service, reviewer = setup(uow)
    service.add_custom(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, code="WEIGHT",
                       label="Weight (kg)", kind="NUMBER", reason="Needed for dosing")
    service.acknowledge(draft.org, draft.trial.id, draft.version, reviewer_id=reviewer, reason="Reviewed the list")
    approve(uow, draft)
    return draft


GOOD = {"ANCHOR:SURGERY": "2026-03-20", "ANCHOR:DOSE_TIME": "2026-03-10T23:30:00+05:30", "CUSTOM:WEIGHT": "62.5"}


def test_enrolment_refuses_missing_unknown_and_malformed_details_and_writes_nothing(uow):
    draft = approved_with_list(uow)
    with pytest.raises(ValueError, match="Surgery date"):
        enrol(uow, draft, inputs={k: v for k, v in GOOD.items() if k != "ANCHOR:SURGERY"})
    with pytest.raises(ValueError, match="unknown details"):
        enrol(uow, draft, inputs={**GOOD, "CUSTOM:HEIGHT": "170"})
    with pytest.raises(ValueError, match="UTC offset"):
        enrol(uow, draft, inputs={**GOOD, "ANCHOR:DOSE_TIME": "2026-03-10T23:30:00"})
    with pytest.raises(ValueError, match="valid"):
        enrol(uow, draft, inputs={**GOOD, "ANCHOR:SURGERY": "20/03/2026"})
    with pytest.raises(ValueError, match="valid"):
        enrol(uow, draft, inputs={**GOOD, "CUSTOM:WEIGHT": "heavy"})
    with pytest.raises(ValueError, match="both"):
        enrol(uow, draft, inputs=GOOD, deferred_inputs=["CUSTOM:WEIGHT"])
    assert uow.read(lambda tx: tx.patients.list_for_trial(draft.org, draft.trial.id)) == []


def test_enrolment_stores_the_dose_time_with_its_offset_so_the_minute_rule_lands_on_the_right_day(uow):
    draft = approved_with_list(uow)
    result = enrol(uow, draft, inputs=GOOD)
    calendar = {row.event_code: row for row in PatientScheduleService(uow).current_calendar(
        result.patient_id, organization_id=draft.org)}
    assert calendar["PK_SAMPLE"].planned_start == date(2026, 3, 11)            # 23:30 + 90 min rolls past midnight
    assert calendar["FOLLOW_UP"].planned_start == date(2026, 4, 19)             # surgery 20 March + 30 days
    anchors = {row.anchor_code: row for row in uow.read(lambda tx: tx.facts.list_anchors(result.patient_id))}
    assert anchors["DOSE_TIME"].value_datetime == datetime(2026, 3, 10, 23, 30, tzinfo=IST)
    states = {row.state_code: row.state_value for row in uow.read(lambda tx: tx.facts.list_states(result.patient_id))}
    assert states == {"INPUT:CUSTOM:WEIGHT": 62.5}


def test_a_detail_can_be_deferred_explicitly_and_only_that_visit_waits(uow):
    draft = approved_with_list(uow)
    result = enrol(uow, draft, inputs={"ANCHOR:SURGERY": "2026-03-20", "CUSTOM:WEIGHT": "70"},
                   deferred_inputs=["ANCHOR:DOSE_TIME"])
    calendar = {row.event_code: row for row in PatientScheduleService(uow).current_calendar(
        result.patient_id, organization_id=draft.org)}
    assert calendar["PK_SAMPLE"].planned_start is None and calendar["PK_SAMPLE"].status.startswith("WAITING")
    assert calendar["FOLLOW_UP"].planned_start == date(2026, 4, 19)


def test_a_schedule_without_an_acknowledged_list_keeps_the_old_optional_behaviour_and_refuses_stray_inputs(uow):
    draft, _service, _ = setup(uow)
    uow.run(lambda tx: tx.schedules.transition(draft.version, to_status="IN_REVIEW") if False else None)
    # approve through the lowest boundary WITHOUT the list review is impossible now, so use a schedule that has none
    from test_p6_approval_hash import Draft as Plain
    plain = Plain(uow)
    ScheduleReviewService(uow).approve_reviewed_table(plain.version, reviewer_id=uuid4(), reason="Reviewed")
    result = enrol(uow, plain)
    assert result.patient_id
    with pytest.raises(ValueError, match="no acknowledged list"):
        enrol(uow, plain, inputs={"ANCHOR:X": "2026-01-01"})


from test_p6_amendment_api import C, client_for  # noqa: E402


def test_http_review_flow_needs_the_right_capability_and_the_enrolment_form_sees_only_an_acknowledged_list(uow):
    draft, _service, reviewer = setup(uow)
    base = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/schedule-versions/{draft.version}/required-inputs"
    definition = draft.header().schedule_definition_id
    enrol_url = f"/api/uctsm/mongo-plane/trials/{draft.trial.id}/enrolment-inputs"
    with client_for(uow, draft.org, [C.SCHEDULE_EVIDENCE_READ], role="sponsor") as reader:
        view = reader.get(base).json()
        assert set(codes(view, "derived")) == {"ANCHOR:SURGERY", "ANCHOR:DOSE_TIME"} and not view["acknowledged"]
        body = {"code": "WEIGHT", "label": "Weight (kg)", "kind": "NUMBER", "reason": "Needed for dosing"}
        assert reader.post(f"{base}/custom", json=body).status_code == 403
        assert reader.post(f"{base}/acknowledge", json={"reason": "Reviewed"}).status_code == 403
    with client_for(uow, draft.org, [C.SCHEDULE_CORRECT, C.SCHEDULE_REVIEW], role="sponsor", user_id=reviewer) as writer:
        assert writer.post(f"{base}/custom", json={**body, "actor": "x"}).status_code == 422
        assert writer.post(f"{base}/custom", json={**body, "kind": "BLOB"}).status_code == 422
        assert writer.post(f"{base}/custom", json=body).status_code == 200
        assert writer.post(f"{base}/custom", json=body).status_code == 422                      # duplicate
        wrong = base.replace(str(draft.trial.id), str(uuid4()))
        assert writer.post(f"{wrong}/acknowledge", json={"reason": "Reviewed"}).status_code == 404
        assert writer.post(f"{base}/acknowledge", json={"reason": "Reviewed the list"}).status_code == 200
        removed = writer.post(f"{base}/custom/CUSTOM:WEIGHT/remove", json={"reason": "Not needed after all"})
        assert removed.status_code == 200
        assert writer.post(f"{base}/custom/CUSTOM:NOPE/remove", json={"reason": "no such entry"}).status_code == 404
        assert writer.post(f"{base}/acknowledge", json={"reason": "Reviewed the final list"}).status_code == 200
    with client_for(uow, draft.org, [C.PATIENT_ENROL], role="crc") as clinic:
        before = clinic.get(enrol_url, params={"schedule_definition_id": str(definition)})
        assert before.status_code == 422                                  # nothing approved yet to enrol against
    approve(uow, draft)
    with client_for(uow, draft.org, [C.PATIENT_ENROL], role="crc") as clinic:
        form = clinic.get(enrol_url, params={"schedule_definition_id": str(definition)})
        assert form.status_code == 200, form.text
        entries = {row["code"]: row["kind"] for row in form.json()["entries"]}
        assert form.json()["enforced"] and entries == {"ANCHOR:SURGERY": "DATE", "ANCHOR:DOSE_TIME": "DATETIME"}
        assert clinic.get(enrol_url, params={"schedule_definition_id": str(uuid4())}).status_code == 422
