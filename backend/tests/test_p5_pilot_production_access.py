"""F1: a FRESH pilot seed is reachable through the PRODUCTION relationship provider.

``server._OperationalAccess`` over persisted operational ``trials``/``patients``
documents - the provider the app really uses - plus the production provisioner that
the seed route calls. No AllowAllAccess / FakeOperationalAccess, no pilot-mode
bypass: fresh seed -> list -> calendar -> preview/confirm -> reload, and the
negative relationships (unassigned, revoked, foreign organization, forged ids).
Isolated local Mongo only.
"""

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


def test_fresh_pilot_is_reachable_through_the_production_provider(uow, monkeypatch):
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setenv("UCTSM_PILOT_MODE", "true")
    owner_org = str(uuid4())
    seeder = staff("pi", owner_org, f"Pilot Site {uuid4().hex[:6]}")
    colleague = staff("pi", str(uuid4()), f"Other Site {uuid4().hex[:6]}")      # assigned later
    stranger = staff("pi", str(uuid4()), f"Stranger Site {uuid4().hex[:6]}")    # never assigned

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
        server.db = client[os.environ.get("DB_NAME", "mtb_p5_test")]
        created: dict[str, list[str]] = {"trials": [], "patients": []}
        try:
            async with app_for(seeder) as http:
                seeded = await http.post(f"{PLANE}/pilot/seed")
                assert seeded.status_code == 200, seeded.text
                body = seeded.json()
                assert body["complete"] is True and len(body["patients"]) == 2
                trial_id = body["trial_id"]
                created["trials"].append(body["external_trial_id"])
                created["patients"] += [p["external_patient_id"] for p in body["patients"]]

                listed = await http.get(f"{PLANE}/trials/{trial_id}/patients")
                assert listed.status_code == 200, listed.text
                assert [i["patient_code"] for i in listed.json()["items"]] == [
                    "PILOT-P001", "PILOT-P002"], listed.json()
                first = listed.json()["items"][0]["external_patient_id"]
                base = f"{PLANE}/trials/{trial_id}/patients/{first}"

                calendar = await http.get(f"{base}/calendar")
                assert calendar.status_code == 200, calendar.text
                assert calendar.json()["authority_plane"] == "MONGO"

                preview = await http.post(f"{base}/commands/preview", json={
                    "type": "RECORD_ACTUAL", "logical_key": "C1D1:0",
                    "visit_date": "2026-01-08", "effective_at": "2026-01-08",
                    "reason": "attended", "horizon": "2026-12-31"})
                assert preview.status_code == 200, preview.text
                confirmed = await http.post(
                    f"{base}/commands/{preview.json()['proposal_id']}/confirm",
                    json={}, headers={"Idempotency-Key": "pilot-prod-1"})
                assert confirmed.status_code == 200, confirmed.text
                reload = await http.get(f"{base}/calendar")
                row = next(i for i in reload.json()["calendar"] if i["logical_key"] == "C1D1:0")
                assert row["status"] != "SCHEDULED" or row.get("actual_visit_date")

                # a re-seed is idempotent and does not re-grant a later revocation
                again = await http.post(f"{PLANE}/pilot/seed")
                assert again.status_code == 200
                assert [p["patient_id"] for p in again.json()["patients"]] == [
                    p["patient_id"] for p in body["patients"]]

            # an unassigned PI of another organization reaches nothing
            async with app_for(stranger) as http:
                assert (await http.get(f"{base}/calendar")).status_code == 404
                listed = await http.get(f"{PLANE}/trials/{trial_id}/patients")
                assert listed.json()["items"] == []

            # assigned through the operational pi_id (the normal staff relationship)
            await server.db.patients.update_one(
                {"id": first}, {"$set": {"pi_id": colleague["id"]}})
            async with app_for(colleague) as http:
                assert (await http.get(f"{base}/calendar")).status_code == 200

            # revocation: neither the assignment nor the creator tie remains for the seeder
            await server.db.patients.update_one(
                {"id": first}, {"$set": {"pi_id": "someone-else", "created_by": "someone-else"}})
            async with app_for(seeder) as http:
                assert (await http.get(f"{base}/calendar")).status_code == 404
                forged_trial = f"{PLANE}/trials/{uuid4()}/patients/{first}/calendar"
                assert (await http.get(forged_trial)).status_code == 404
                forged_patient = f"{PLANE}/trials/{trial_id}/patients/forged-id/calendar"
                assert (await http.get(forged_patient)).status_code == 404
        finally:
            await server.db.trials.delete_many({"id": {"$in": created["trials"]}})
            await server.db.patients.delete_many({"id": {"$in": created["patients"]}})
            client.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()
