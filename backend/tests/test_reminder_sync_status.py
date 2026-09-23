"""AD-12: a per-patient reminder-sync failure must become patient-visible
delivery state, not just a server log line - and a fixed patient must clear
on the next successful pass rather than staying flagged forever.

Same harness as test_visit_instances.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, module teardown cleanup.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
ORG_SPONSOR = f"REMINDER-SYNC-{RUN_ID} Pharma"

LOOP = asyncio.new_event_loop()
_trial_ids: list[str] = []
_patient_ids: list[str] = []
_user_ids: list[str] = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


async def _register(role, org=None):
    email = f"reminder-sync-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com"
    async with make_client() as cli:
        r = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD,
            "full_name": f"Test {role.upper()} {RUN_ID}",
            "role": role, "organization": org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    _user_ids.append(j["user"]["id"])
    return j["user"], {"Authorization": f"Bearer {j['access_token']}"}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.patients.delete_many({"id": {"$in": _patient_ids}})
        await server.db.trials.delete_many({"id": {"$in": _trial_ids}})
        await server.db.users.delete_many({"id": {"$in": _user_ids}})
        await server.db.organizations.delete_many({"name": ORG_SPONSOR})
        await server.db.worker_status.delete_one({"id": "reminder_worker"})
    run(clean())
    LOOP.close()


def test_a_worker_failure_is_patient_visible_and_clears_on_success():
    sponsor, headers = run(_register("sponsor", org=ORG_SPONSOR))

    async def exercise():
        async with make_client() as cli:
            created = await cli.post("/api/trials", headers=headers, json={
                "title": f"Reminder Sync Trial {RUN_ID}",
                "protocol_id": f"RSYNC-{RUN_ID}",
                "phase": "Phase II", "condition": "Testing",
                "sponsor_name": ORG_SPONSOR,
            })
        assert created.status_code == 200, created.text
        trial_id = created.json()["id"]
        _trial_ids.append(trial_id)

        patient_id = str(uuid.uuid4())
        _patient_ids.append(patient_id)
        # A minimal linked patient - the worker's failure-visibility mechanism
        # does not require a real canonical schedule, only that the patient is
        # linked (uctsm_patient_id) and has somewhere to be notified.
        await server.db.patients.insert_one({
            "id": patient_id, "trial_id": trial_id, "created_by": sponsor["id"],
            "uctsm_patient_id": str(uuid.uuid4()), "user_id": sponsor["id"],
            "full_name": f"Reminder Sync Patient {RUN_ID}",
        })

        def boom(_uctsm_patient_id, _today):
            raise RuntimeError("simulated projection failure")

        server_patch = server._compute_patient_projection_sync
        try:
            server._compute_patient_projection_sync = boom
            await server.sync_all_patient_reminders()
        finally:
            server._compute_patient_projection_sync = server_patch

        patient_doc = await server.db.patients.find_one({"id": patient_id}, {"_id": 0})
        assert patient_doc["uctsm_reminder_sync"]["status"] == "FAILED"
        assert "simulated projection failure" in patient_doc["uctsm_reminder_sync"]["last_error"]

        async with make_client() as cli:
            status = await cli.get("/api/patients/reminder-sync-status", headers=headers)
        assert status.status_code == 200, status.text
        failed_ids = {item["id"] for item in status.json()["patients"]}
        assert patient_id in failed_ids
        assert status.json()["worker_last_run_at"] is not None

        # A patient whose next pass succeeds must clear, not stay flagged.
        def ok(_uctsm_patient_id, _today):
            return None  # not linked to an evaluated schedule - a normal, non-error return

        server._compute_patient_projection_sync = ok
        try:
            await server.sync_all_patient_reminders()
        finally:
            server._compute_patient_projection_sync = server_patch

        cleared_doc = await server.db.patients.find_one({"id": patient_id}, {"_id": 0})
        assert cleared_doc["uctsm_reminder_sync"]["status"] == "OK"

        async with make_client() as cli:
            status_after = await cli.get("/api/patients/reminder-sync-status", headers=headers)
        assert patient_id not in {item["id"] for item in status_after.json()["patients"]}
    run(exercise())
