"""HTTP-level integration tests for the Remote Audit API (remote_audit_routes.py)
— exercised through the real FastAPI app (server.app), following this repo's
existing convention (see test_sponsor_dashboard.py) rather than pytest-asyncio,
since these tests share server.py's global `db` across one session event loop.

Covers the full capture-session -> upload-intent -> upload-bytes -> finalize
HTTP flow, universal idempotency (v1.3 correction #1), and the cross-org /
participant-boundary authorization tests the plan calls for explicitly.
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
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


async def register(role: str, org: str):
    email = f"ra-{RUN_ID}-{uuid.uuid4().hex[:6]}@example.com"
    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD, "full_name": f"{role.title()} {org}",
            "role": role, "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


async def create_audit(sponsor_headers, site_org_id: str) -> dict:
    async with client() as cli:
        response = await cli.post(
            "/api/remote-audit/audits",
            headers=sponsor_headers,
            json={"site_id": f"site-{uuid.uuid4().hex[:6]}", "site_org_id": site_org_id},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def add_participant(audit_id, admin_headers, user_id, role_in_audit) -> dict:
    async with client() as cli:
        response = await cli.post(
            f"/api/remote-audit/audits/{audit_id}/participants",
            headers=admin_headers, json={"user_id": user_id, "role_in_audit": role_in_audit},
        )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def sponsor_org():
    # Unique per test: server.py's register() only grants org_admin to the
    # FIRST registrant under a given org name -- a shared/module-scoped org
    # name would make every test after the first register a non-admin
    # member instead, silently breaking participant-assignment checks.
    return f"RA-SPONSOR-{RUN_ID}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def site_org():
    return f"RA-SITE-{RUN_ID}-{uuid.uuid4().hex[:6]}"


def test_full_capture_to_finalize_http_flow(sponsor_org, site_org):
    sponsor_user, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))

    audit = run(create_audit(sponsor_headers, site_org_id=site_user["organization_id"]))
    run(add_participant(audit["id"], site_headers, site_user["id"], "capturer"))

    async def flow():
        async with client() as cli:
            session_resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/capture-sessions",
                headers={**site_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "linked_entity_type": "facility", "linked_entity_id": "fac-1",
                    "requested_media_type": "image/jpeg", "capture_mode": "LIVE_IN_APP",
                },
            )
            assert session_resp.status_code == 200, session_resp.text
            session = session_resp.json()
            assert session["nonce"]

            intent_resp = await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/upload-intent",
                headers=site_headers,
                json={"file_name": "photo.jpg", "mime_claimed": "image/jpeg", "size_claimed": 1024},
            )
            assert intent_resp.status_code == 200, intent_resp.text
            intent = intent_resp.json()
            assert intent["server_mediated"] is True  # local storage in this test env

            upload_resp = await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/upload-bytes",
                headers=site_headers, files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0fakejpegbytes", "image/jpeg")},
            )
            assert upload_resp.status_code == 200, upload_resp.text

            finalize_resp = await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/finalize",
                headers={**site_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "object_key": intent["object_key"], "sha256_client": "irrelevant-for-this-http-test",
                    "capture_manifest": {"lon": 77.0, "lat": 28.0, "accuracy_m": 10.0, "location_age_ms": 1000},
                },
            )
            assert finalize_resp.status_code == 200, finalize_resp.text
            evidence = finalize_resp.json()
            assert evidence["status"] == "QUARANTINED"  # worker isn't running in this test process

            get_resp = await cli.get(f"/api/remote-audit/evidence/{evidence['id']}", headers=site_headers)
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == evidence["id"]

    run(flow())


def test_finalize_idempotency_replays_the_same_evidence_not_a_duplicate(sponsor_org, site_org):
    sponsor_user, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))
    audit = run(create_audit(sponsor_headers, site_org_id=site_user["organization_id"]))
    run(add_participant(audit["id"], site_headers, site_user["id"], "capturer"))

    async def flow():
        async with client() as cli:
            session_resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/capture-sessions",
                headers={**site_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"linked_entity_type": "facility", "linked_entity_id": "fac-1",
                      "requested_media_type": "image/jpeg", "capture_mode": "LIVE_IN_APP"},
            )
            session = session_resp.json()
            intent = (await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/upload-intent",
                headers=site_headers,
                json={"file_name": "p.jpg", "mime_claimed": "image/jpeg", "size_claimed": 100},
            )).json()
            await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/upload-bytes",
                headers=site_headers, files={"file": ("p.jpg", b"\xff\xd8\xffdata", "image/jpeg")},
            )
            key = str(uuid.uuid4())
            body = {"object_key": intent["object_key"], "sha256_client": "x", "capture_manifest": {}}
            first = await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/finalize",
                headers={**site_headers, "Idempotency-Key": key}, json=body,
            )
            second = await cli.post(
                f"/api/remote-audit/capture-sessions/{session['id']}/finalize",
                headers={**site_headers, "Idempotency-Key": key}, json=body,
            )
            assert first.status_code == 200 and second.status_code == 200
            assert first.json()["id"] == second.json()["id"]

    run(flow())


def test_missing_idempotency_key_is_rejected(sponsor_org, site_org):
    sponsor_user, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))
    audit = run(create_audit(sponsor_headers, site_org_id=site_user["organization_id"]))
    run(add_participant(audit["id"], site_headers, site_user["id"], "capturer"))

    async def flow():
        async with client() as cli:
            response = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/capture-sessions",
                headers=site_headers,  # no Idempotency-Key
                json={"linked_entity_type": "facility", "linked_entity_id": "fac-1",
                      "requested_media_type": "image/jpeg", "capture_mode": "LIVE_IN_APP"},
            )
            assert response.status_code == 400

    run(flow())


def test_cross_org_stranger_cannot_see_or_act_on_audit(sponsor_org, site_org):
    _, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))
    audit = run(create_audit(sponsor_headers, site_org_id=site_user["organization_id"]))

    stranger_org = f"RA-STRANGER-{RUN_ID}"
    _, stranger_headers = run(register("sponsor", stranger_org))

    async def flow():
        async with client() as cli:
            get_resp = await cli.get(f"/api/remote-audit/audits/{audit['id']}", headers=stranger_headers)
            assert get_resp.status_code == 404

            create_resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/capture-sessions",
                headers={**stranger_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"linked_entity_type": "facility", "linked_entity_id": "fac-1",
                      "requested_media_type": "image/jpeg", "capture_mode": "LIVE_IN_APP"},
            )
            assert create_resp.status_code == 404

    run(flow())


def test_org_member_without_participant_grant_cannot_capture(sponsor_org, site_org):
    """v1.3 correction #7's key boundary: being in the site's org is not
    enough -- an explicit participant grant is required to act."""
    _, sponsor_headers = run(register("sponsor", sponsor_org))
    site_admin_user, site_admin_headers = run(register("site", site_org))
    audit = run(create_audit(sponsor_headers, site_org_id=site_admin_user["organization_id"]))

    # A second site-org user who is never added as a participant.
    unassigned_user, unassigned_headers = run(register("site", site_org))

    async def flow():
        async with client() as cli:
            response = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/capture-sessions",
                headers={**unassigned_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"linked_entity_type": "facility", "linked_entity_id": "fac-1",
                      "requested_media_type": "image/jpeg", "capture_mode": "LIVE_IN_APP"},
            )
            assert response.status_code == 404

    run(flow())


def test_owner_org_reviewer_gets_minimal_list_only_without_participant_grant(sponsor_org, site_org):
    sponsor_user, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))
    audit = run(create_audit(sponsor_headers, site_org_id=site_user["organization_id"]))

    # A second sponsor-org user, never assigned as a participant on this audit.
    _, other_sponsor_headers = run(register("sponsor", sponsor_org))

    async def flow():
        async with client() as cli:
            response = await cli.get(f"/api/remote-audit/audits/{audit['id']}", headers=other_sponsor_headers)
            assert response.status_code == 200
            body = response.json()
            # Minimal-list shape only -- no jurisdiction/template/etc detail fields.
            assert set(body.keys()) == {"id", "state", "site_id"}

    run(flow())
