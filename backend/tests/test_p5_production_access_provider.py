"""R2 through the PRODUCTION operational-relationship provider (``server._OperationalAccess``)
backed by persisted ``patients`` documents - not ``FakeOperationalAccess``.

A cross-organization PI assigned through the persisted ``pi_id`` reaches the owner's
plane patient; an unassigned PI, a revoked assignment and a forged external id do not.
Isolated local Mongo only (``DB_NAME`` points at a test database).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

url = os.environ.get("MONGO_URL", "")
if "127.0.0.1" not in url and "localhost" not in url:       # never a shared cluster
    pytest.skip("production-provider test needs the explicit LOCAL Mongo settings",
                allow_module_level=True)

import server                                                  # noqa: E402
from _authz_fixtures import permissive_authorizer             # noqa: E402
from app.api.uctsm_mongo import create_uctsm_mongo_router     # noqa: E402
from test_uctsm_p1_api import body, make_env, url as plane_url  # noqa: E402
from test_uctsm_p1_occurrence_recalculation import ORG        # noqa: E402

pytest_plugins = ["tests.contract.conftest"]

OTHER_ORG_NAME = f"Participating Site {uuid4().hex[:6]}"


def test_the_production_provider_decides_cross_org_access_from_persisted_assignments(uow):
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient

    env = make_env(uow)
    pi = {"id": str(uuid4()), "organization_id": str(uuid4()), "role": "pi",
          "organization": OTHER_ORG_NAME, "status": "Active"}
    external = env.external_patient_id

    async def scenario():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"], uuidRepresentation="standard")
        server.db = client[os.environ.get("DB_NAME", "mtb_p5_test")]
        authorizer = permissive_authorizer(pi["id"], pi["organization_id"])
        authorizer.access = server._OperationalAccess()          # <- PRODUCTION provider
        app = FastAPI()
        app.include_router(create_uctsm_mongo_router(
            lambda: pi, uow_dependency=lambda: uow, authorizer=authorizer))

        async def seed(assigned_pi):
            await server.db.patients.delete_many({"id": external})
            await server.db.patients.insert_one({
                "id": external, "trial_id": "legacy-trial", "pi_id": assigned_pi,
                "created_by": "someone-else", "organization": "Owner Sponsor"})

        try:
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://plane") as http:
                await seed("a-different-pi")
                assert (await http.get(plane_url(env, "calendar"))).status_code == 404     # unassigned
                await seed(pi["id"])
                got = await http.get(plane_url(env, "calendar"))
                assert got.status_code == 200, (got.status_code, dict(got.headers), got.text)
                wrote = await http.post(plane_url(env, "actual-occurrences"), json=body())
                assert wrote.status_code == 201, wrote.text
                stored = uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))
                assert stored and stored[0].organization_id == ORG                         # owner's tenant
                await seed("a-different-pi")                                               # revoked
                assert (await http.get(plane_url(env, "calendar"))).status_code == 404
                forged = f"/api/uctsm/mongo-plane/trials/{env.trial_id}/patients/forged-id/calendar"
                assert (await http.get(forged)).status_code == 404
        finally:
            await server.db.patients.delete_many({"id": external})
            client.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()
