"""P6 reachability through the PRODUCTION relationship provider (F1 prerequisite).

``server._OperationalAccess`` over persisted operational ``trials``/``patients`` documents,
plus the production provisioner the pilot seed route calls - no AllowAllAccess, no pilot-mode
bypass. A freshly seeded pilot and its seeded amendment must be reachable by the assigned
staff, and by nobody else. Isolated local Mongo only."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

url = os.environ.get("MONGO_URL", "")
if "127.0.0.1" not in url and "localhost" not in url:       # never a shared cluster
    pytest.skip("production-provider test needs the explicit LOCAL Mongo settings",
                allow_module_level=True)

import server                                                  # noqa: E402
from _authz_fixtures import permissive_authorizer             # noqa: E402
from app.api.uctsm_mongo import create_uctsm_mongo_router     # noqa: E402

pytest_plugins = ["tests.contract.conftest"]

PLANE = "/api/uctsm/mongo-plane"


def staff(role: str, organization: str, organization_name: str) -> dict:
    return {"id": str(uuid4()), "organization_id": organization, "role": role,
            "organization": organization_name, "status": "Active"}


def test_a_seeded_amendment_is_reachable_through_the_production_provider(uow, monkeypatch):
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setenv("UCTSM_PILOT_MODE", "true")
    owner_org = str(uuid4())
    seeder = staff("pi", owner_org, f"Pilot Site {uuid4().hex[:6]}")
    stranger = staff("pi", str(uuid4()), f"Stranger Site {uuid4().hex[:6]}")   # never assigned

    def app_for(user: dict) -> httpx.AsyncClient:
        authorizer = permissive_authorizer(user["id"], user["organization_id"])
        authorizer.access = server._OperationalAccess()                          # PRODUCTION
        app = FastAPI()
        app.include_router(create_uctsm_mongo_router(
            lambda: user, uow_dependency=lambda: uow, authorizer=authorizer,
            operational_provisioner=server._provision_pilot_operational))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://plane")

    async def scenario():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"], uuidRepresentation="standard")
        server.db = client[os.environ.get("DB_NAME", "mtb_p6_test")]
        created: dict[str, list[str]] = {"trials": [], "patients": []}
        try:
            async with app_for(seeder) as http:
                seeded = await http.post(f"{PLANE}/pilot/seed")
                assert seeded.status_code == 200, seeded.text
                body = seeded.json()
                trial_id = body["trial_id"]
                created["trials"].append(body["external_trial_id"])
                created["patients"] += [p["external_patient_id"] for p in body["patients"]]
                first = body["patients"][0]["external_patient_id"]

                amended = await http.post(f"{PLANE}/pilot/seed-amendment")
                assert amended.status_code == 200, amended.text
                v1, v2 = amended.json()["from_schedule_version_id"], amended.json()["to_schedule_version_id"]
                again = await http.post(f"{PLANE}/pilot/seed-amendment")
                assert again.json()["to_schedule_version_id"] == v2          # idempotent

                trial_url = f"{PLANE}/trials/{trial_id}/amendments"
                patient_url = f"{PLANE}/trials/{trial_id}/patients/{first}/amendments"
                overview = await http.get(trial_url)
                assert overview.status_code == 200, overview.text
                assert [v["status"] for v in overview.json()["versions"]] == ["APPROVED", "APPROVED"]
                pair = await http.post(f"{trial_url}/pairs", json={
                    "from_schedule_version_id": v1, "to_schedule_version_id": v2})
                assert pair.status_code == 201, pair.text
                view = pair.json()
                for row in [m for m in view["mappings"] if m["review_status"] == "PROPOSED"]:
                    reviewed = await http.post(f"{trial_url}/pairs/{view['pair_id']}/mappings", json={
                        "expected_revision": view["mapping_set_revision"], "action": "CONFIRM",
                        "mapping_id": row["id"], "reason": "same visit, re-coded"})
                    assert reviewed.status_code == 200, reviewed.text
                    view = reviewed.json()
                assert view["ready_for_adoption"]

                status = await http.get(patient_url)
                assert status.status_code == 200, status.text
                assert status.json()["available"][0]["ready_for_adoption"] is True
                simulation = await http.post(f"{patient_url}/simulate", json={
                    "to_schedule_version_id": v2, "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}})
                assert simulation.status_code == 201 and simulation.json()["can_confirm"], simulation.text
                decision = await http.post(
                    f"{patient_url}/simulations/{simulation.json()['simulation_id']}/confirm",
                    json={"decision": "ADOPT", "reason": "synthetic production-provider decision"})
                assert decision.status_code == 200, decision.text
                calendar = await http.get(f"{PLANE}/trials/{trial_id}/patients/{first}/calendar")
                assert calendar.json()["schedule_version_id"] == v2

            # an unassigned PI of another organization reaches nothing
            async with app_for(stranger) as http:
                assert (await http.get(patient_url)).status_code == 404
                assert (await http.post(f"{patient_url}/simulate", json={
                    "to_schedule_version_id": v2,
                    "adoption_point": {"mode": "ADOPT_IMMEDIATELY"}})).status_code == 404
                assert (await http.get(trial_url)).status_code == 404

            # revocation: neither the assignment nor the creator tie remains for the seeder
            await server.db.patients.update_one({"id": first}, {"$set": {"created_by": "someone-else"},
                                                                 "$unset": {"pi_id": ""}})
            async with app_for(seeder) as http:
                assert (await http.get(patient_url)).status_code == 404
        finally:
            await server.db.trials.delete_many({"id": {"$in": created["trials"]}})
            await server.db.patients.delete_many({"id": {"$in": created["patients"]}})
            client.close()

    asyncio.new_event_loop().run_until_complete(scenario())


def test_the_deferred_worker_rechecks_the_decider_through_production_grants_and_assignments():
    """``server._amendment_decider_still_authorised`` over persisted users, organizations,
    grants and operational patients: authorised only while the person who decided is active,
    holds ``amendment.adopt`` (a grant - it has no role default) and is assigned to the patient."""
    from types import SimpleNamespace
    from motor.motor_asyncio import AsyncIOMotorClient

    async def scenario():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"], uuidRepresentation="standard")
        server.db = client[os.environ.get("DB_NAME", "mtb_p6_test")]
        org_id, org_name = str(uuid4()), f"Worker Site {uuid4().hex[:6]}"
        user_id, external = str(uuid4()), f"worker-patient-{uuid4().hex[:8]}"
        await server.db.organizations.insert_one({
            "id": org_id, "name": org_name, "name_key": server.organization_name_key(org_name)})
        await server.db.users.insert_one({
            "id": user_id, "role": "pi", "organization": org_name, "status": "Active",
            "email": f"{user_id}@worker.example.com"})
        await server.db.patients.insert_one({"id": external, "pi_id": user_id, "synthetic": True})
        decision = SimpleNamespace(actor_id=user_id, organization_id=org_id, trial_id=uuid4())
        patient = SimpleNamespace(external_patient_id=external)
        check = server._amendment_decider_still_authorised
        try:
            assert await check(decision, patient) is False                      # assigned, but no grant
            grant = await server.uctsm_grant_store.add(
                organization_id=org_id, user_id=user_id, capability="amendment.adopt", trial_id=None,
                granted_by="admin", reason="synthetic")
            assert await check(decision, patient) is True
            await server.uctsm_grant_store.revoke(grant.id, organization_id=org_id, revoked_by="admin")
            assert await check(decision, patient) is False                      # revoked
            grant = await server.uctsm_grant_store.add(
                organization_id=org_id, user_id=user_id, capability="amendment.adopt", trial_id=None,
                granted_by="admin", reason="again")
            assert await check(decision, patient) is True
            await server.db.users.update_one({"id": user_id}, {"$set": {"status": "Suspended"}})
            assert await check(decision, patient) is False                      # suspended
            await server.db.users.update_one({"id": user_id}, {"$set": {"status": "Active"}})
            await server.db.patients.update_one({"id": external}, {"$unset": {"pi_id": ""}})
            await server.db.organizations.update_one({"id": org_id}, {"$set": {"name": "Moved Away"}})
            # no assignment and a different site now: the relationship is gone
            assert await check(decision, patient) is False
            assert await check(SimpleNamespace(actor_id=str(uuid4()), organization_id=org_id,
                                               trial_id=uuid4()), patient) is False   # unknown user
        finally:
            await server.db.organizations.delete_many({"id": org_id})
            await server.db.users.delete_many({"id": user_id})
            await server.db.patients.delete_many({"id": external})
            await server.db.uctsm_capability_grants.delete_many({"user_id": user_id})
            client.close()

    asyncio.new_event_loop().run_until_complete(scenario())


def test_the_worker_is_wired_into_server_startup_and_off_without_the_plane():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "amendment_worker_loop(" in source and "_amendment_decider_still_authorised" in source
    import os as _os
    saved = {key: _os.environ.pop(key, None) for key in ("UCTSM_MONGO_URL", "UCTSM_MONGO_DB")}
    try:
        assert server._amendment_worker_interval() == 0                           # no plane: no worker
        _os.environ["UCTSM_MONGO_URL"], _os.environ["UCTSM_MONGO_DB"] = "mongodb://127.0.0.1", "x"
        assert server._amendment_worker_interval() == 300
        _os.environ["UCTSM_AMENDMENT_WORKER_INTERVAL_SEC"] = "0"
        assert server._amendment_worker_interval() == 0                           # explicitly off
        _os.environ["UCTSM_AMENDMENT_WORKER_INTERVAL_SEC"] = "soon"
        assert server._amendment_worker_interval() == 0                           # unreadable: off
    finally:
        _os.environ.pop("UCTSM_AMENDMENT_WORKER_INTERVAL_SEC", None)
        for key, value in saved.items():
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value
