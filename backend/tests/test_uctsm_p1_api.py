"""P1-04/P1-05: the HTTP route for actual occurrences on the Mongo patient plane.

A real FastAPI app and TestClient, the real ``PatientScheduleService`` command and
the in-memory AND real-Mongo unit of work. Only authentication is substituted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.uctsm_mongo import create_uctsm_mongo_router, get_plane_uow
from _authz_fixtures import permissive_authorizer
from app.application.patients import PatientAnchorRecord
from app.domain.schedule.timing import TemporalBasis
from test_uctsm_p1_occurrence_recalculation import (
    ORG, Env, after, from_baseline, schedule_of,
)

pytest_plugins = ["tests.contract.conftest"]

ACTOR = uuid4()


def client_for(uow, *, organization: UUID = ORG) -> TestClient:
    app = FastAPI()
    user = {"id": str(ACTOR), "organization_id": str(organization),
            "role": "pi", "status": "Active"}
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow,
        authorizer=permissive_authorizer(str(ACTOR), str(organization))))
    return TestClient(app)


def make_env(uow) -> Env:
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT),
        from_baseline("IMAGING", 30, 3)))
    env.evaluate()
    return env


def url(env: Env, tail: str) -> str:
    return (f"/api/uctsm/mongo-plane/trials/{env.trial_id}"
            f"/patients/{env.external_patient_id}/{tail}")


def body(**changes) -> dict:
    payload = {"logical_key": "V2:0", "kind": "PERFORMED", "visit_date": "2026-10-13",
               "reason": "Visit attended", "horizon": "2027-12-31"}
    payload.update(changes)
    return payload


def entries(payload: dict) -> dict[str, dict]:
    return {item["logical_key"]: item for item in payload["calendar"]}


def test_headline_through_the_api_27_october_and_imaging_unchanged(uow):
    env = make_env(uow)
    client = client_for(uow)
    before = client.get(url(env, "calendar")).json()
    assert entries(before)["V3:0"]["planned_start"] is None
    assert entries(before)["V3:0"]["status"] == "BLOCKED"
    imaging = entries(before)["IMAGING:0"]["planned_start"]

    response = client.post(url(env, "actual-occurrences"), json=body(),
                           headers={"Idempotency-Key": "api-1"})
    assert response.status_code == 201, response.text
    payload = response.json()
    calendar = entries(payload)
    assert calendar["V3:0"]["planned_start"] == "2026-10-27"             # 13 Oct + 14 days
    assert calendar["IMAGING:0"]["planned_start"] == imaging == "2026-10-31"
    assert calendar["V2:0"]["completed"] and calendar["V2:0"]["actual_visit_date"] == "2026-10-13"
    assert calendar["V2:0"]["original_planned"] == "2026-10-10"
    outcome = payload["outcome"]
    assert outcome["reevaluated"] is True and outcome["replayed"] is False
    assert outcome["schedule_version_id"] == str(env.version_id) == payload["schedule_version_id"]
    changed = {c["event_code"]: c for c in outcome["changes"] if c["changed"]}
    assert set(changed) == {"V2", "V3"}
    assert changed["V3"]["old_target"] is None and changed["V3"]["new_target"] == "2026-10-27"
    assert "actual visit" in changed["V3"]["reason"]
    unchanged = {c["event_code"]: c for c in outcome["changes"] if not c["changed"]}
    assert unchanged["IMAGING"]["reason"] == "does not depend on V2"


def test_the_planned_relative_rule_stays_on_24_october_through_the_api(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.CURRENT_PLANNED)))
    env.evaluate()
    response = client_for(uow).post(url(env, "actual-occurrences"), json=body())
    assert response.status_code == 201, response.text
    assert entries(response.json())["V3:0"]["planned_start"] == "2026-10-24"


def test_replay_returns_the_first_result_and_a_changed_body_conflicts(uow):
    env = make_env(uow)
    client = client_for(uow)
    headers = {"Idempotency-Key": "replay-1"}
    first = client.post(url(env, "actual-occurrences"), json=body(), headers=headers).json()
    second = client.post(url(env, "actual-occurrences"), json=body(), headers=headers)
    assert second.status_code == 201
    assert second.json()["outcome"]["replayed"] is True
    assert second.json()["outcome"]["occurrence_id"] == first["outcome"]["occurrence_id"]
    assert second.json()["outcome"]["evaluation_id"] == first["outcome"]["evaluation_id"]

    changed = client.post(url(env, "actual-occurrences"), headers=headers,
                          json=body(visit_date="2026-10-14"))
    assert changed.status_code == 409
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 1


def test_a_version_the_patient_has_left_is_a_409_with_no_write(uow):
    env = make_env(uow)
    response = client_for(uow).post(
        url(env, "actual-occurrences"),
        json=body(expected_schedule_version_id=str(uuid4())))
    assert response.status_code == 409
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_validation_unknown_visit_and_extra_fields_are_4xx_with_no_write(uow):
    env = make_env(uow)
    client = client_for(uow)
    future = client.post(url(env, "actual-occurrences"), json=body(visit_date="2030-01-01"))
    assert future.status_code == 422 and "future" in future.json()["detail"]
    missing = client.post(url(env, "actual-occurrences"), json=body(logical_key="NOPE:0"))
    assert missing.status_code == 404
    extra = client.post(url(env, "actual-occurrences"), json={**body(), "organization_id": "x"})
    assert extra.status_code == 422                       # client-supplied scope is refused
    no_date = client.post(url(env, "actual-occurrences"), json=body(visit_date=None))
    assert no_date.status_code == 422
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_another_tenant_and_an_unknown_patient_are_404(uow):
    """Tenant isolation rests on the OPERATIONAL relationship (a stranger has none),
    then on the persisted owner - not on the caller's organization id alone."""
    from test_uctsm_authz_http import FakeOperationalAccess

    env = make_env(uow)
    organization = uuid4()
    user = {"id": str(ACTOR), "organization_id": str(organization), "role": "pi", "status": "Active"}
    authorizer = permissive_authorizer(str(ACTOR), str(organization))
    authorizer.access = FakeOperationalAccess()               # no assignment at all
    app = FastAPI()
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow, authorizer=authorizer))
    stranger = TestClient(app)
    assert stranger.get(url(env, "calendar")).status_code == 404
    assert stranger.post(url(env, "actual-occurrences"), json=body()).status_code == 404
    unknown = client_for(uow).get(
        f"/api/uctsm/mongo-plane/trials/{env.trial_id}/patients/not-enrolled/calendar")
    assert unknown.status_code == 404
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_treatment_withheld_is_a_visit_without_a_treatment_date(uow):
    env = Env(uow, schedule_of(
        from_baseline("DOSE", 9, 1, event_type="TREATMENT"),
        after("AFTER_VISIT", "DOSE", 7, 2, TemporalBasis.ACTUAL_VISIT),
        after("AFTER_TREATMENT", "DOSE", 14, 3, TemporalBasis.ACTUAL_TREATMENT)))
    env.evaluate()
    response = client_for(uow).post(
        url(env, "actual-occurrences"), json=body(logical_key="DOSE:0"))
    calendar = entries(response.json())
    assert calendar["AFTER_VISIT:0"]["planned_start"] == "2026-10-20"
    assert calendar["AFTER_TREATMENT:0"]["planned_start"] is None
    assert calendar["AFTER_TREATMENT:0"]["status"] == "BLOCKED"
    assert calendar["DOSE:0"]["actual_treatment_date"] is None


def test_a_pending_anchor_candidate_is_reported_and_never_confirmed(uow):
    env = make_env(uow)
    candidate = PatientAnchorRecord(
        organization_id=ORG, patient_id=env.patient_id, anchor_definition_id=env.anchor_id,
        anchor_code="BASELINE", value_date=date(2026, 10, 3), status="PENDING_CONFIRMATION",
        source_type="PATIENT_EVENT_OCCURRENCE")
    uow.run(lambda tx: tx.facts.add_anchor(candidate))
    response = client_for(uow).post(url(env, "actual-occurrences"), json=body())
    assert response.status_code == 201
    pending = response.json()["pending_anchor_candidates"]
    assert [item["id"] for item in pending] == [str(candidate.id)]
    stored = uow.read(lambda tx: tx.facts.get_anchor(candidate.id))
    assert stored.status == "PENDING_CONFIRMATION"
    # the baseline the evaluation used is still the enrolment anchor, not the candidate
    assert entries(response.json())["V2:0"]["original_planned"] == "2026-10-10"


def test_unconfigured_plane_answers_503_without_falling_back(monkeypatch):
    for name in ("UCTSM_MONGO_URL", "UCTSM_MONGO_DB"):
        monkeypatch.delenv(name, raising=False)
    import app.api.uctsm_mongo as module
    monkeypatch.setattr(module, "_unit_of_work", None)
    app = FastAPI()
    app.include_router(module.create_uctsm_mongo_router(
        lambda: {"id": str(ACTOR), "organization_id": str(ORG), "role": "pi", "status": "Active"},
        authorizer=permissive_authorizer(str(ACTOR), str(ORG))))
    response = TestClient(app).get(
        f"/api/uctsm/mongo-plane/trials/{uuid4()}/patients/p-1/calendar")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    assert get_plane_uow is module.get_plane_uow
