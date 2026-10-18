"""P6 over HTTP: the real FastAPI app, the real authorization guard and policy, and the
in-memory fake AND real MongoDB. Authentication is the only thing substituted."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _authz_fixtures import AllowAllAccess
from _p6_world import World
from app.api.uctsm_mongo import create_uctsm_mongo_router
from app.authorization.catalog import Capability as C
from app.authorization.grants import MemoryGrantStore
from app.authorization.guard import RouteAuthorizer

pytest_plugins = ["tests.contract.conftest"]

REVIEW = [C.SCHEDULE_READ, C.SCHEDULE_REVIEW]
PI = [C.PATIENT_IMPACT_PREVIEW, C.AMENDMENT_ADOPT, C.PATIENT_SCHEDULE_READ]


class DenyAccess:
    """No operational relationship to any trial or patient (another organization's staff)."""

    async def can_access_trial(self, user, external_trial_id):
        return False

    async def can_access_patient(self, user, external_patient_id):
        return False

    async def is_own_patient_record(self, user, external_patient_id):
        return False


def client_for(uow, org, capabilities, role="pi", user_id=None, access=None):
    user_id = user_id or uuid4()
    store = MemoryGrantStore()
    for capability in capabilities:
        store.grant_sync(str(user_id), str(org), capability.value)
    app = FastAPI()
    user = {"id": str(user_id), "organization_id": str(org), "role": role, "status": "Active"}
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow,
        authorizer=RouteAuthorizer(grants=store, access=access or AllowAllAccess())))
    client = TestClient(app)
    client.user_id, client.store = user_id, store
    return client


class Api:
    def __init__(self, uow, *, reviewed=False):
        self.world = World(uow, reviewed=reviewed)
        self.uow = uow
        self.org = self.world.org
        self.trial = self.world.trial_id
        self.external = self.world.patient(self.world.patient_ids[0]).external_patient_id
        self.reviewer = client_for(uow, self.org, [*REVIEW, C.SCHEDULE_AUTHOR], role="sponsor")
        self.pi = client_for(uow, self.org, PI)

    @property
    def trial_url(self):
        return f"/api/uctsm/mongo-plane/trials/{self.trial}/amendments"

    @property
    def patient_url(self):
        return f"/api/uctsm/mongo-plane/trials/{self.trial}/patients/{self.external}/amendments"

    def review_everything(self):
        pair = self.world.pair["pair_id"]
        view = self.reviewer.get(f"{self.trial_url}/pairs/{pair}").json()
        for row in view["mappings"]:
            if row["review_status"] == "PROPOSED":
                reply = self.reviewer.post(f"{self.trial_url}/pairs/{pair}/mappings", json={
                    "expected_revision": view["mapping_set_revision"], "action": "CONFIRM",
                    "mapping_id": row["id"], "reason": "same imaging visit, re-coded"})
                assert reply.status_code == 200, reply.text
                view = reply.json()
        return view


@pytest.fixture()
def api(uow):
    return Api(uow)


def test_the_whole_journey_over_http(api):
    # compare
    overview = api.reviewer.get(api.trial_url)
    assert overview.status_code == 200, overview.text
    assert [v["status"] for v in overview.json()["versions"]] == ["APPROVED", "APPROVED"]
    pair = api.reviewer.post(f"{api.trial_url}/pairs", json={
        "from_schedule_version_id": str(api.world.v1), "to_schedule_version_id": str(api.world.v2)})
    assert pair.status_code == 201, pair.text
    body = pair.json()
    assert body["from"]["verification"] == "VERIFIED" and not body["ready_for_adoption"]
    # an unreviewed mapping blocks the simulation, with a coded reason
    point = {"mode": "ADOPT_IMMEDIATELY"}
    blocked = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": point})
    assert blocked.status_code == 201 and blocked.json()["can_confirm"] is False
    assert {b["code"] for b in blocked.json()["blockers"]} == {"MAPPING_UNREVIEWED"}
    # review, then simulate and adopt
    assert api.review_everything()["ready_for_adoption"]
    simulation = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": point}).json()
    assert simulation["can_confirm"], simulation["blockers"]
    assert {row["category"] for row in simulation["visits"]} >= {"ADDED", "MOVED"}
    decision = api.pi.post(
        f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
        json={"decision": "ADOPT", "reason": "synthetic PI decision"},
        headers={"Idempotency-Key": "journey-1"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPLIED"
    # the committed Mongo evaluation is what the calendar shows, on every reload
    base = f"/api/uctsm/mongo-plane/trials/{api.trial}/patients/{api.external}"
    reader = client_for(api.uow, api.org, [C.PATIENT_SCHEDULE_READ])
    for _ in range(2):
        calendar = reader.get(f"{base}/calendar").json()
        assert calendar["schedule_version_id"] == str(api.world.v2)
        keys = {row["logical_key"] for row in calendar["calendar"]}
        assert "SAFETY_CALL:0" in keys and "IMAGING_BL:0" in keys and "IMAGING_BASELINE:0" not in keys
    status = api.pi.get(api.patient_url).json()
    assert status["current_version"]["schedule_version_id"] == str(api.world.v2)
    assert status["can_return"] is True and status["decisions"][0]["status"] == "APPLIED"


def test_the_client_cannot_supply_actor_grants_or_provenance(api):
    api.review_everything()
    pair = api.world.pair["pair_id"]
    for extra in ({"actor_id": str(uuid4())}, {"capabilities": ["amendment.adopt"]},
                  {"grants": []}, {"reviewed_by": str(uuid4())}, {"review_status": "AUTO_CONFIRMED"}):
        assert api.pi.post(f"{api.patient_url}/simulate", json={
            "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"},
            **extra}).status_code == 422
        assert api.reviewer.post(f"{api.trial_url}/pairs/{pair}/mappings", json={
            "expected_revision": 3, "action": "CONFIRM", "mapping_id": str(uuid4()),
            "reason": "x y z", **extra}).status_code == 422


def test_adoption_needs_its_own_grant_and_a_preview_grant_is_not_enough(api):
    api.review_everything()
    previewer = client_for(api.uow, api.org, [C.PATIENT_IMPACT_PREVIEW])
    simulation = previewer.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).json()
    denied = previewer.post(f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
                            json={"decision": "ADOPT", "reason": "not allowed"})
    assert denied.status_code == 403 and denied.headers["X-Authz-Capability"] == "amendment.adopt"
    assert api.world.patient().current_schedule_version_id == api.world.v1
    # an organisation administrator and a sponsor never hold it
    admin = client_for(api.uow, api.org, [], role="pi")
    assert admin.post(f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
                      json={"decision": "REMAIN", "reason": "no grant"}).status_code == 403
    sponsor = client_for(api.uow, api.org, [C.AMENDMENT_ADOPT, C.PATIENT_IMPACT_PREVIEW], role="sponsor")
    assert sponsor.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2),
        "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).status_code == 403


def test_a_return_needs_adopt_AND_the_high_impact_right(api):
    api.review_everything()
    first = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).json()
    assert api.pi.post(f"{api.patient_url}/simulations/{first['simulation_id']}/confirm",
                       json={"decision": "ADOPT", "reason": "adopt"}).status_code == 200
    back = api.pi.post(f"{api.patient_url}/simulate", json={
        "direction": "RETURN", "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}})
    assert back.status_code == 201 and back.json()["can_confirm"]
    sid = back.json()["simulation_id"]
    refused = api.pi.post(f"{api.patient_url}/simulations/{sid}/confirm",
                          json={"decision": "RETURN", "reason": "withdrawn"})
    assert refused.status_code == 403
    assert refused.headers["X-Authz-Capability"] == "patient.command.high_impact"
    api.pi.store.grant_sync(str(api.pi.user_id), str(api.org), C.PATIENT_COMMAND_HIGH_IMPACT.value)
    done = api.pi.post(f"{api.patient_url}/simulations/{sid}/confirm",
                       json={"decision": "RETURN", "reason": "withdrawn"})
    assert done.status_code == 200, done.text
    assert api.world.patient().current_schedule_version_id == api.world.v1


def test_authority_is_rechecked_on_a_replay(api):
    api.review_everything()
    simulation = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).json()
    url = f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm"
    body = {"decision": "ADOPT", "reason": "adopt"}
    first = api.pi.post(url, json=body, headers={"Idempotency-Key": "r-1"})
    assert first.status_code == 200
    again = api.pi.post(url, json=body, headers={"Idempotency-Key": "r-1"})
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["id"] == first.json()["id"]
    api.pi.store._rows.clear()                 # the grant is revoked between the two calls
    assert api.pi.post(url, json=body, headers={"Idempotency-Key": "r-1"}).status_code == 403
    api.pi.store.grant_sync(str(api.pi.user_id), str(api.org), C.AMENDMENT_ADOPT.value)
    different = api.pi.post(url, json={"decision": "REMAIN", "reason": "other"},
                            headers={"Idempotency-Key": "r-1"})
    assert different.status_code == 409          # a different decision under the key is not a replay


def test_review_is_a_separate_right_from_reading_and_a_stale_revision_conflicts(api):
    pair = api.world.pair["pair_id"]
    reader = client_for(api.uow, api.org, [C.SCHEDULE_READ], role="sponsor")
    assert reader.get(f"{api.trial_url}/pairs/{pair}").status_code == 200
    view = reader.get(f"{api.trial_url}/pairs/{pair}").json()
    rename = next(m for m in view["mappings"] if m["review_status"] == "PROPOSED")
    body = {"expected_revision": view["mapping_set_revision"], "action": "CONFIRM",
            "mapping_id": rename["id"], "reason": "same visit"}
    assert reader.post(f"{api.trial_url}/pairs/{pair}/mappings", json=body).status_code == 403
    assert api.reviewer.post(f"{api.trial_url}/pairs/{pair}/mappings", json=body).status_code == 200
    stale = api.reviewer.post(f"{api.trial_url}/pairs/{pair}/mappings", json={
        **body, "action": "REJECT"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "CONFLICT"


def test_another_tenant_or_patient_gets_not_found(api):
    api.review_everything()
    simulation = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).json()
    foreign = client_for(api.uow, uuid4(), PI, access=DenyAccess())
    assert foreign.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2),
        "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).status_code == 404
    assert foreign.post(f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
                        json={"decision": "ADOPT", "reason": "not mine"}).status_code == 404
    assert foreign.get(api.trial_url).status_code == 404
    # the other pilot patient cannot use this patient's simulation
    other_external = api.world.patient(api.world.patient_ids[1]).external_patient_id
    other_url = f"/api/uctsm/mongo-plane/trials/{api.trial}/patients/{other_external}/amendments"
    assert api.pi.post(f"{other_url}/simulations/{simulation['simulation_id']}/confirm",
                       json={"decision": "ADOPT", "reason": "wrong patient"}).status_code == 404
    pair_other_trial = f"/api/uctsm/mongo-plane/trials/{uuid4()}/amendments/pairs/{api.world.pair['pair_id']}"
    assert api.reviewer.get(pair_other_trial).status_code == 404


def test_deferred_decision_over_http(api):
    api.review_everything()
    simulation = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2),
        "adoption_point": {"mode": "ADOPT_ON_DATE", "on_date": "2099-01-01"}}).json()
    decision = api.pi.post(f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
                           json={"decision": "ADOPT", "reason": "adopt in 2099"})
    assert decision.status_code == 200 and decision.json()["status"] == "WAITING"
    status = api.pi.get(api.patient_url).json()
    assert status["open_decision"]["status"] == "WAITING"
    assert status["open_decision"]["trigger_state"]["due"] is False
    did = decision.json()["id"]
    executed = api.pi.post(f"{api.patient_url}/decisions/{did}/execute")
    assert executed.status_code == 200 and executed.json()["status"] == "WAITING"
    assert api.world.patient().current_schedule_version_id == api.world.v1
    cancelled = api.pi.post(f"{api.patient_url}/decisions/{did}/cancel", json={"reason": "not needed"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    # a decision of another patient is not addressable through this one
    other_external = api.world.patient(api.world.patient_ids[1]).external_patient_id
    other_url = f"/api/uctsm/mongo-plane/trials/{api.trial}/patients/{other_external}/amendments"
    assert api.pi.post(f"{other_url}/decisions/{did}/cancel", json={"reason": "wrong patient"}).status_code == 404


def test_a_blocked_move_is_a_structured_409(api):
    simulation = api.pi.post(f"{api.patient_url}/simulate", json={
        "to_schedule_version_id": str(api.world.v2), "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}}).json()
    refused = api.pi.post(f"{api.patient_url}/simulations/{simulation['simulation_id']}/confirm",
                          json={"decision": "ADOPT", "reason": "try anyway"})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "AMENDMENT_BLOCKED" and detail["blockers"][0]["code"] == "MAPPING_UNREVIEWED"
    assert api.world.patient().current_schedule_version_id == api.world.v1
