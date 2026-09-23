"""HTTP-level integration tests for Phase 3 (Sites, Facilities, Equipment,
Geofence Map) — exercised through the real FastAPI app, following this
repo's existing convention (see test_sponsor_dashboard.py / test_remote_audit_routes.py).

Covers the plan's explicit Phase 3 exit criteria: the geofence
approve/version/supersede flow under the corrected MFA/capability rules, a
site user being unable to approve their own site's geofence without an
explicit grant, and a platform admin being unable to activate a template
without an explicit template_admin grant.
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

from app.services.remote_audit import geofence_onboarding, vision_extraction  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


async def _login(email: str):
    async with client() as cli:
        response = await cli.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


async def register(role: str, org: str):
    email = f"ra3-{RUN_ID}-{uuid.uuid4().hex[:6]}@example.com"
    if role == "admin":
        # Admins cannot self-register (server.py rejects role=admin at
        # /api/auth/register) -- insert directly, matching test_admin_api.py's
        # own established pattern for this exact situation.
        doc = {
            "id": str(uuid.uuid4()), "email": email, "full_name": f"Admin {org}", "role": "admin",
            "organization": org, "phone": "", "hashed_password": server.pwd_ctx.hash(PASSWORD),
            "security_question": "", "security_answer_hash": "", "avatar_initials": "AD",
            "created_at": server.now(), "is_online": False,
        }
        await server.db.users.insert_one(doc)
        return await _login(email)

    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD, "full_name": f"{role.title()} {org}",
            "role": role, "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


@pytest.fixture
def site_org():
    return f"RA3-SITE-{RUN_ID}-{uuid.uuid4().hex[:6]}"


@pytest.fixture(autouse=True)
def mock_osm_lookup(monkeypatch):
    """Every test in this file uses the circle fallback deterministically --
    OSM lookup is exercised separately in test_remote_audit_geofence_onboarding.py."""
    async def no_match(lat, lon, **kwargs):
        return None
    monkeypatch.setattr(geofence_onboarding, "fetch_osm_boundary", no_match)


def test_full_geofence_propose_approve_flow_with_explicit_approver_grant(site_org):
    admin_user, admin_headers = run(register("admin", f"RA3-ADMIN-{RUN_ID}"))
    site_admin_user, site_admin_headers = run(register("site", site_org))
    approver_user, approver_headers = run(register("sponsor", f"RA3-SPONSOR-{RUN_ID}-{uuid.uuid4().hex[:6]}"))

    async def flow():
        async with client() as cli:
            site_resp = await cli.post(
                "/api/remote-audit/sites", headers=site_admin_headers,
                json={"official_name": "Test Hospital", "address": {}, "timezone": "Asia/Kolkata"},
            )
            assert site_resp.status_code == 200, site_resp.text
            site = site_resp.json()

            propose_resp = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions",
                headers=site_admin_headers, json={"lat": 28.6139, "lon": 77.2090},
            )
            assert propose_resp.status_code == 200, propose_resp.text
            proposal = propose_resp.json()
            assert proposal["source"] == "circle"
            geofence_version_id = proposal["geofence_version"]["id"]

            # site's own admin cannot approve without an explicit site-approver grant
            no_grant_approve = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions/{geofence_version_id}/approve",
                headers=site_admin_headers, json={"step_up_token": "irrelevant-not-reached"},
            )
            assert no_grant_approve.status_code == 404

            # platform admin grants approver rights to the sponsor user
            grant_resp = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/approvers",
                headers=admin_headers, json={"user_id": approver_user["id"]},
            )
            assert grant_resp.status_code == 200, grant_resp.text

            # approver mints a step-up token (dev/pilot password fallback)
            stepup_resp = await cli.post(
                "/api/remote-audit/step-up", headers=approver_headers,
                json={"action_scope": "geofence.approve", "password": PASSWORD},
            )
            assert stepup_resp.status_code == 200, stepup_resp.text
            token = stepup_resp.json()["token"]

            approve_resp = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions/{geofence_version_id}/approve",
                headers=approver_headers, json={"step_up_token": token},
            )
            assert approve_resp.status_code == 200, approve_resp.text
            assert approve_resp.json()["status"] == "approved"

            # the same step-up token cannot be reused
            replay_approve = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions/{geofence_version_id}/approve",
                headers=approver_headers, json={"step_up_token": token},
            )
            assert replay_approve.status_code == 403

            # supersession: a second proposal + approval retires v1 without deleting it
            propose2 = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions",
                headers=site_admin_headers, json={"lat": 28.6139, "lon": 77.2090, "default_radius_m": 200.0},
            )
            v2_id = propose2.json()["geofence_version"]["id"]
            stepup2 = (await cli.post(
                "/api/remote-audit/step-up", headers=approver_headers,
                json={"action_scope": "geofence.approve", "password": PASSWORD},
            )).json()["token"]
            approve2 = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions/{v2_id}/approve",
                headers=approver_headers, json={"step_up_token": stepup2},
            )
            assert approve2.status_code == 200

            versions_resp = await cli.get(f"/api/remote-audit/sites/{site['id']}/geofence-versions", headers=site_admin_headers)
            versions = versions_resp.json()
            assert len(versions) == 2
            v1_after = next(v for v in versions if v["id"] == geofence_version_id)
            assert v1_after["status"] == "superseded"

    run(flow())


def test_missing_step_up_token_blocks_geofence_approval(site_org):
    admin_user, admin_headers = run(register("admin", f"RA3-ADMIN-{RUN_ID}-{uuid.uuid4().hex[:6]}"))
    site_admin_user, site_admin_headers = run(register("site", site_org))

    async def flow():
        async with client() as cli:
            site = (await cli.post(
                "/api/remote-audit/sites", headers=site_admin_headers,
                json={"official_name": "H2", "address": {}, "timezone": "UTC"},
            )).json()
            proposal = (await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions",
                headers=site_admin_headers, json={"lat": 28.0, "lon": 77.0},
            )).json()
            await cli.post(f"/api/remote-audit/sites/{site['id']}/approvers",
                           headers=admin_headers, json={"user_id": site_admin_user["id"]})
            response = await cli.post(
                f"/api/remote-audit/sites/{site['id']}/geofence-versions/{proposal['geofence_version']['id']}/approve",
                headers=site_admin_headers, json={"step_up_token": "not-a-real-token"},
            )
            assert response.status_code == 403

    run(flow())


def test_platform_admin_cannot_activate_template_without_explicit_grant():
    admin_user, admin_headers = run(register("admin", f"RA3-ADMIN2-{RUN_ID}"))

    async def flow():
        async with client() as cli:
            create_resp = await cli.post(
                "/api/remote-audit/templates", headers=admin_headers,
                json={"jurisdiction": "IN", "scoring_policy_json": {
                    "ComplianceScore": {"A": 60, "B": 40},
                }},
            )
            # v1.3 correction #8: admin role alone never grants template_admin
            assert create_resp.status_code == 404

    run(flow())


def test_template_admin_explicit_grant_enables_create_and_activate():
    admin_user, admin_headers = run(register("admin", f"RA3-ADMIN3-{RUN_ID}"))
    sponsor_user, sponsor_headers = run(register("sponsor", f"RA3-SPONSOR3-{RUN_ID}"))

    async def flow():
        async with client() as cli:
            grant_resp = await cli.post(
                "/api/remote-audit/capability-grants", headers=admin_headers,
                json={"user_id": sponsor_user["id"], "capability": "remote_audit.template_admin"},
            )
            assert grant_resp.status_code == 200, grant_resp.text

            create_resp = await cli.post(
                "/api/remote-audit/templates", headers=sponsor_headers,
                json={"jurisdiction": "IN", "scoring_policy_json": {
                    "ComplianceScore": {"A": 60, "B": 40},
                    "EvidenceConfidence": {"X": 100},
                }},
            )
            assert create_resp.status_code == 200, create_resp.text
            template = create_resp.json()

            stepup_resp = await cli.post(
                "/api/remote-audit/step-up", headers=sponsor_headers,
                json={"action_scope": "template.activate", "password": PASSWORD},
            )
            token = stepup_resp.json()["token"]

            activate_resp = await cli.post(
                f"/api/remote-audit/templates/{template['id']}/activate",
                headers=sponsor_headers, json={"step_up_token": token},
            )
            assert activate_resp.status_code == 200, activate_resp.text
            assert activate_resp.json()["status"] == "active"

    run(flow())


def test_bad_weight_total_blocks_activation_even_with_valid_grant_and_stepup():
    admin_user, admin_headers = run(register("admin", f"RA3-ADMIN4-{RUN_ID}"))
    sponsor_user, sponsor_headers = run(register("sponsor", f"RA3-SPONSOR4-{RUN_ID}"))

    async def flow():
        async with client() as cli:
            await cli.post(
                "/api/remote-audit/capability-grants", headers=admin_headers,
                json={"user_id": sponsor_user["id"], "capability": "remote_audit.template_admin"},
            )
            template = (await cli.post(
                "/api/remote-audit/templates", headers=sponsor_headers,
                json={"jurisdiction": "IN", "scoring_policy_json": {"ComplianceScore": {"A": 60, "B": 60}}},
            )).json()
            token = (await cli.post(
                "/api/remote-audit/step-up", headers=sponsor_headers,
                json={"action_scope": "template.activate", "password": PASSWORD},
            )).json()["token"]
            response = await cli.post(
                f"/api/remote-audit/templates/{template['id']}/activate",
                headers=sponsor_headers, json={"step_up_token": token},
            )
            assert response.status_code == 400

    run(flow())


def test_facility_equipment_ai_suggest_and_confirm_flow(monkeypatch, site_org):
    sponsor_org = f"RA3-SPONSOR5-{RUN_ID}"
    sponsor_user, sponsor_headers = run(register("sponsor", sponsor_org))
    site_user, site_headers = run(register("site", site_org))

    async def fake_analyze(*, image_bytes, media_type):
        from app.services.remote_audit.vision_extraction import VisionExtractionResult
        return VisionExtractionResult(
            fields={"name": "Infusion Pump", "manufacturer": "Acme", "model": "IP-500", "serial_number": None},
            confidence="medium", confidence_notes="serial obscured", provider="anthropic", model="claude-opus-5",
            raw_response="{}",
        )

    monkeypatch.setattr(vision_extraction, "analyze_equipment_photo", fake_analyze)

    async def flow():
        async with client() as cli:
            audit_resp = await cli.post(
                "/api/remote-audit/audits", headers=sponsor_headers,
                json={"site_id": str(uuid.uuid4()), "site_org_id": site_user["organization_id"]},
            )
            audit = audit_resp.json()
            await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/participants",
                headers=site_headers, json={"user_id": site_user["id"], "role_in_audit": "capturer"},
            )

            facility_resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/facilities",
                headers=site_headers, json={"facility_type": "ICU", "name": "ICU-1"},
            )
            assert facility_resp.status_code == 200, facility_resp.text
            facility = facility_resp.json()

            equipment_resp = await cli.post(
                f"/api/remote-audit/facilities/{facility['id']}/equipment",
                headers=site_headers, json={"category": "infusion_pump"},
            )
            assert equipment_resp.status_code == 200, equipment_resp.text
            equipment = equipment_resp.json()
            assert equipment["confirmed"] is None

            ai_resp = await cli.post(
                f"/api/remote-audit/equipment/{equipment['id']}/ai-suggest",
                headers=site_headers, files={"file": ("eq.jpg", b"\xff\xd8\xfffakejpeg", "image/jpeg")},
            )
            assert ai_resp.status_code == 200, ai_resp.text
            assert ai_resp.json()["fields"]["manufacturer"] == "Acme"

            confirm_resp = await cli.post(
                f"/api/remote-audit/equipment/{equipment['id']}/confirm",
                headers=site_headers, json={"fields": {"name": "Infusion Pump", "manufacturer": "Acme",
                                                        "model": "IP-500", "serial_number": "SN-999"}},
            )
            assert confirm_resp.status_code == 200, confirm_resp.text
            assert confirm_resp.json()["confirmed"]["serial_number"] == "SN-999"

    run(flow())
