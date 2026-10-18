"""P3-03 / P3-05: the Mongo patient-plane routes are guarded by the same policy.

Real guard, real route, real ``PatientScheduleService`` over the memory AND real
MongoDB units of work. Authentication and the operational relationship lookup
are the only doubles.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.uctsm_mongo import create_uctsm_mongo_router
from app.authorization.catalog import Capability
from app.authorization.grants import MemoryGrantStore
from app.authorization.guard import RouteAuthorizer
from test_uctsm_authz_http import FakeOperationalAccess
from test_uctsm_p1_api import body, make_env, url
from test_uctsm_p1_occurrence_recalculation import ORG

pytest_plugins = ["tests.contract.conftest"]


def person(role: str, **extra) -> dict:
    return {"id": str(uuid4()), "role": role, "organization_id": str(ORG),
            "status": "Active", **extra}


def build(uow, user: dict, access: FakeOperationalAccess, grants: MemoryGrantStore | None = None):
    sink: list[dict] = []

    async def record(entry):
        sink.append(entry)

    authorizer = RouteAuthorizer(grants=grants or MemoryGrantStore(), access=access,
                                 denial_sink=record)
    opened = {"count": 0}

    def plane():
        opened["count"] += 1
        return uow

    app = FastAPI()
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=plane, authorizer=authorizer))
    return TestClient(app), sink, opened


def test_assigned_clinician_records_and_reads(uow):
    env = make_env(uow)
    pi = person("pi")
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, sink, _ = build(uow, pi, access)
    assert client.get(url(env, "calendar")).status_code == 200
    assert client.post(url(env, "actual-occurrences"), json=body()).status_code == 201
    assert sink == []


def test_a_clinician_with_no_tie_to_the_patient_is_refused_before_the_plane_is_opened(uow):
    env = make_env(uow)
    pi = person("pi")
    client, sink, opened = build(uow, pi, FakeOperationalAccess())      # no relationship
    for response in (client.get(url(env, "calendar")),
                     client.post(url(env, "actual-occurrences"), json=body())):
        assert response.status_code == 404
    assert opened["count"] == 0, "no unit of work may be requested for a denied caller"
    assert [entry["reason_code"] for entry in sink] == ["NO_RELATIONSHIP", "NO_RELATIONSHIP"]
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_sponsor_side_may_read_the_calendar_but_not_record_actuals(uow):
    env = make_env(uow)
    sponsor = person("sponsor")
    access = FakeOperationalAccess()
    access.link(sponsor, patient=env.external_patient_id)
    client, sink, _ = build(uow, sponsor, access)
    assert client.get(url(env, "calendar")).status_code == 200
    denied = client.post(url(env, "actual-occurrences"), json=body())
    assert denied.status_code == 403
    assert denied.headers["x-authz-reason"] == "CAPABILITY_MISSING"
    assert sink[-1]["capability"] == "patient.occurrence.record"
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_a_patient_reads_only_their_own_record_and_never_writes(uow):
    env = make_env(uow)
    patient = person("patient")
    access = FakeOperationalAccess()
    access.link(patient, own=env.external_patient_id)
    client, _, _ = build(uow, patient, access)
    assert client.get(url(env, "calendar")).status_code == 200
    assert client.post(url(env, "actual-occurrences"), json=body()).status_code == 403

    stranger = person("patient")
    other_client, _, _ = build(uow, stranger, FakeOperationalAccess())
    assert other_client.get(url(env, "calendar")).status_code == 404


def test_a_grant_cannot_make_a_patient_a_clinician(uow):
    env = make_env(uow)
    patient = person("patient")
    grants = MemoryGrantStore()
    grants.grant_sync(patient["id"], str(ORG), Capability.PATIENT_OCCURRENCE_RECORD.value)
    access = FakeOperationalAccess()
    access.link(patient, own=env.external_patient_id)
    client, _, _ = build(uow, patient, access, grants)
    assert client.post(url(env, "actual-occurrences"), json=body()).status_code == 403


def test_revoked_assignment_and_suspension_deny_on_the_next_request(uow):
    env = make_env(uow)
    pi = person("pi")
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, _, _ = build(uow, pi, access)
    assert client.get(url(env, "calendar")).status_code == 200
    access.patients[pi["id"]].clear()
    assert client.get(url(env, "calendar")).status_code == 404

    suspended = person("pi", status="Suspended")
    access.link(suspended, patient=env.external_patient_id)
    other, _, _ = build(uow, suspended, access)
    response = other.get(url(env, "calendar"))
    assert response.status_code == 403
    assert response.headers["x-authz-reason"] == "ACCOUNT_INACTIVE"


def test_a_foreign_trial_id_cannot_ride_an_allowed_patient(uow):
    env = make_env(uow)
    pi = person("pi")
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, _, _ = build(uow, pi, access)
    wrong_trial = (f"/api/uctsm/mongo-plane/trials/{UUID(int=7)}"
                   f"/patients/{env.external_patient_id}/calendar")
    assert client.get(wrong_trial).status_code == 404     # plane lookup is trial-bound


def test_a_stored_replay_receipt_does_not_outlive_the_callers_access(uow):
    """An idempotency receipt is not authorization: after revocation the same
    request, with the same key, is refused instead of replaying the old result."""
    env = make_env(uow)
    pi = person("pi")
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, _, _ = build(uow, pi, access)
    headers = {"Idempotency-Key": "revocation-replay"}
    first = client.post(url(env, "actual-occurrences"), json=body(), headers=headers)
    assert first.status_code == 201
    access.patients[pi["id"]].clear()                      # assignment revoked
    replay = client.post(url(env, "actual-occurrences"), json=body(), headers=headers)
    assert replay.status_code == 404
    assert "outcome" not in replay.json()


# ============ R2: the owner is the persisted canonical patient's organization

OTHER_ORG = UUID("99999999-9999-4999-8999-999999999999")


def test_an_assigned_investigator_from_another_organization_is_served_in_the_owners_tenant(uow):
    env = make_env(uow)
    pi = person("pi", organization_id=str(OTHER_ORG))
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, sink, _ = build(uow, pi, access)
    assert client.get(url(env, "calendar")).status_code == 200
    recorded = client.post(url(env, "actual-occurrences"), json=body())
    assert recorded.status_code == 201, recorded.text
    stored = uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))
    assert len(stored) == 1 and stored[0].organization_id == ORG        # the OWNER's tenant
    assert sink == []


def test_an_unassigned_or_revoked_investigator_from_another_organization_is_refused(uow):
    env = make_env(uow)
    pi = person("pi", organization_id=str(OTHER_ORG))
    access = FakeOperationalAccess()
    client, _, _ = build(uow, pi, access)                      # never assigned
    assert client.get(url(env, "calendar")).status_code == 404
    access.link(pi, patient=env.external_patient_id)
    assert client.get(url(env, "calendar")).status_code == 200
    access.patients[pi["id"]].discard(env.external_patient_id)            # assignment revoked
    assert client.get(url(env, "calendar")).status_code == 404
    assert client.post(url(env, "actual-occurrences"), json=body()).status_code == 404
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_a_forged_external_id_or_foreign_trial_never_resolves(uow):
    env = make_env(uow)
    pi = person("pi", organization_id=str(OTHER_ORG))
    access = FakeOperationalAccess()
    access.link(pi, patient="SOME-OTHER-PATIENT")
    client, _, _ = build(uow, pi, access)
    forged = f"/api/uctsm/mongo-plane/trials/{env.trial_id}/patients/SOME-OTHER-PATIENT/calendar"
    assert client.get(forged).status_code == 404
    foreign_trial = f"/api/uctsm/mongo-plane/trials/{uuid4()}/patients/{env.external_patient_id}/calendar"
    assert client.get(foreign_trial).status_code == 404


def test_cross_organization_investigator_uses_the_same_owner_for_preview_confirm_and_history(uow):
    env = make_env(uow)
    pi = person("pi", organization_id=str(OTHER_ORG))
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, _, _ = build(uow, pi, access)
    base = f"/api/uctsm/mongo-plane/trials/{env.trial_id}/patients/{env.external_patient_id}"
    preview = client.post(f"{base}/commands/preview", json={
        "type": "RECORD_ACTUAL", "logical_key": "V2:0", "visit_date": "2026-10-13",
        "effective_at": "2026-10-13", "reason": "attended", "horizon": "2027-12-31"})
    assert preview.status_code == 200, preview.text
    confirm = client.post(f"{base}/commands/{preview.json()['proposal_id']}/confirm", json={})
    assert confirm.status_code == 200, confirm.text
    history = client.get(f"{base}/commands")
    assert history.status_code == 200
    ledger = uow.read(lambda tx: tx.ledger.list_for_patient(ORG, env.patient_id))
    assert len(ledger) == 1 and ledger[0].organization_id == ORG        # owner's tenant
    assert uow.read(lambda tx: tx.ledger.list_for_patient(OTHER_ORG, env.patient_id)) == []
