"""GET /patients/{id}/audit-trail: the Consistency Conventions require that
neither audit log (canonical uctsm_audit_events, operational Mongo
audit_logs) is read alone and presented as complete. This is the combined
view - one entry per row from either log, tagged by source, time-ordered.

Same harness as test_reminder_sync_status.py: in-process ASGITransport
against the real Atlas DB for the Mongo half, plus a direct SQL session
(server._operational_factory) for the canonical half.
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402
from app.db import models as db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
ORG_SPONSOR = f"AUDIT-TRAIL-{RUN_ID} Pharma"

LOOP = asyncio.new_event_loop()
_trial_ids: list[str] = []
_patient_ids: list[str] = []
_user_ids: list[str] = []
_sql_trial_ids: list = []
_sql_patient_ids: list = []
_sql_proposal_ids: list = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


async def _register(role, org=None):
    email = f"audit-trail-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com"
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
        await server.db.audit_logs.delete_many({"target_id": {"$in": _patient_ids}})
    run(clean())
    with server._operational_factory()() as session:
        for proposal_id in _sql_proposal_ids:
            row = session.get(db.ScheduleImpactProposal, proposal_id)
            if row is not None:
                session.query(db.AuditEvent).filter(
                    db.AuditEvent.entity_id == proposal_id).delete()
                session.delete(row)
        for patient_id in _sql_patient_ids:
            row = session.get(db.Patient, patient_id)
            if row is not None:
                session.query(db.AuditEvent).filter(
                    db.AuditEvent.entity_id == patient_id).delete()
                session.delete(row)
        for trial_id in _sql_trial_ids:
            row = session.get(db.Trial, trial_id)
            if row is not None:
                session.delete(row)
        session.commit()
    LOOP.close()


def test_the_audit_trail_merges_both_logs_tagged_by_source():
    sponsor, headers = run(_register("sponsor", org=ORG_SPONSOR))

    async def exercise():
        async with make_client() as cli:
            created = await cli.post("/api/trials", headers=headers, json={
                "title": f"Audit Trail Trial {RUN_ID}",
                "protocol_id": f"AUDIT-{RUN_ID}",
                "phase": "Phase II", "condition": "Testing",
                "sponsor_name": ORG_SPONSOR,
            })
        assert created.status_code == 200, created.text
        trial_id = created.json()["id"]
        _trial_ids.append(trial_id)

        patient_id = str(uuid.uuid4())
        _patient_ids.append(patient_id)
        await server.db.patients.insert_one({
            "id": patient_id, "trial_id": trial_id, "created_by": sponsor["id"],
            "full_name": f"Audit Trail Patient {RUN_ID}",
        })
        await server.db.audit_logs.insert_one({
            "id": str(uuid.uuid4()), "target_id": patient_id,
            "action": "visit_instance.patch", "detail": "Operational-side test entry",
            "user_id": sponsor["id"], "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })

        org_id = uuid.uuid4()
        sql_trial_id = uuid.uuid4()
        sql_patient_id = uuid.uuid4()
        _sql_trial_ids.append(sql_trial_id)
        _sql_patient_ids.append(sql_patient_id)
        with server._operational_factory()() as session:
            session.add(db.Trial(
                id=sql_trial_id, organization_id=org_id, external_trial_id=trial_id,
            ))
            session.add(db.Patient(
                id=sql_patient_id, organization_id=org_id, trial_id=sql_trial_id,
                patient_code="P-AUDIT", external_patient_id=patient_id,
            ))
            session.flush()
            session.add(db.AuditEvent(
                organization_id=org_id, actor_id=None,
                action="PATIENT_SCHEDULE_EVALUATED", entity_type="PATIENT",
                entity_id=sql_patient_id, before=None, after={"event_count": 0},
                reason="Canonical-side test entry",
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            ))
            # impact_service.py's preview/confirm/cancel audit trail keys on
            # the PROPOSAL's own id, not the patient's - this is the gap the
            # user asked to close: without explicitly walking
            # ScheduleImpactProposal, this row would be invisible here even
            # though it plainly belongs to this patient.
            proposal_id = uuid.uuid4()
            _sql_proposal_ids.append(proposal_id)
            schedule_version_id = uuid.uuid4()
            session.add(db.ScheduleImpactProposal(
                id=proposal_id, patient_id=sql_patient_id,
                from_schedule_version_id=schedule_version_id,
                to_schedule_version_id=schedule_version_id,
                horizon=datetime(2027, 1, 1).date(), input_hash="h" * 64,
                impact={}, status="PENDING", created_by=uuid.uuid4(),
                expires_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            ))
            session.add(db.AuditEvent(
                organization_id=org_id, actor_id=None,
                action="PATIENT_SCHEDULE_IMPACT_PREVIEWED",
                entity_type="SCHEDULE_IMPACT_PROPOSAL", entity_id=proposal_id,
                before=None, after={"summary": {}},
                reason="Proposal-side test entry",
                created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            ))
            session.commit()

        async with make_client() as cli:
            response = await cli.get(
                f"/api/patients/{patient_id}/audit-trail", headers=headers)
        assert response.status_code == 200, response.text
        entries = response.json()["entries"]
        sources = {item["source"] for item in entries}
        assert sources == {"operational", "canonical"}

        operational_entry = next(item for item in entries if item["source"] == "operational")
        assert operational_entry["reason"] == "Operational-side test entry"
        canonical_reasons = {
            item["reason"] for item in entries if item["source"] == "canonical"}
        assert canonical_reasons == {"Canonical-side test entry", "Proposal-side test entry"}
        proposal_entry = next(
            item for item in entries if item["reason"] == "Proposal-side test entry")
        assert proposal_entry["action"] == "PATIENT_SCHEDULE_IMPACT_PREVIEWED"

        # Time-ordered across both logs, not grouped by source.
        assert [item["reason"] for item in entries] == [
            "Operational-side test entry", "Proposal-side test entry",
            "Canonical-side test entry",
        ]
    run(exercise())
