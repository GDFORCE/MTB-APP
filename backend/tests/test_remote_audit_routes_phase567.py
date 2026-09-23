"""HTTP-level integration for Phases 5-7 (queries/CAPAs, virtual visits,
scoring and the final decision), through the real FastAPI app.

The exit criteria proven here are the ones that only exist at the API
boundary: separation of duties between the sponsor and site sides, the
step-up requirement on a final decision, and the fact that a decision cannot
be recorded for an audit nobody scored.
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

from app.services.remote_audit import capability_grants as grants_service  # noqa: E402
from app.services.remote_audit import virtual_visits as vv  # noqa: E402
from app.services.remote_audit.capabilities import RemoteAuditCapability  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


def key():
    return uuid.uuid4().hex


async def register(role: str, org: str):
    email = f"ra567-{RUN_ID}-{uuid.uuid4().hex[:6]}@example.com"
    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD, "full_name": f"{role.title()} {org}",
            "role": role, "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}, email


class World:
    def __init__(self, audit, approver, approver_headers, approver_email,
                 capturer, capturer_headers):
        self.audit = audit
        self.approver = approver
        self.approver_headers = approver_headers
        self.approver_email = approver_email
        self.capturer = capturer
        self.capturer_headers = capturer_headers


async def build_world(*, owner_role="approver"):
    sponsor_org = f"RA567-SPONSOR-{RUN_ID}-{uuid.uuid4().hex[:6]}"
    site_org = f"RA567-SITE-{RUN_ID}-{uuid.uuid4().hex[:6]}"
    approver, approver_headers, approver_email = await register("sponsor", sponsor_org)
    capturer, capturer_headers, _ = await register("site", site_org)

    async with client() as cli:
        audit_resp = await cli.post(
            "/api/remote-audit/audits", headers=approver_headers,
            json={"site_id": f"site-{uuid.uuid4().hex[:8]}",
                  "site_org_id": capturer["organization_id"]})
        assert audit_resp.status_code in (200, 201), audit_resp.text
        audit = audit_resp.json()

        for user, role, headers in ((approver, owner_role, approver_headers),
                                    (capturer, "capturer", capturer_headers)):
            resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/participants",
                headers={**headers, "Idempotency-Key": key()},
                json={"user_id": user["id"], "role_in_audit": role})
            assert resp.status_code in (200, 201), resp.text

    return World(audit, approver, approver_headers, approver_email, capturer, capturer_headers)


async def step_up(cli, headers, email, scope):
    resp = await cli.post("/api/remote-audit/step-up", headers=headers,
                          json={"action_scope": scope, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5 — separation of duties across the API
# ═══════════════════════════════════════════════════════════════════════════

def test_only_the_sponsor_side_can_raise_a_query():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            body = {"title": "Fridge log incomplete", "detail": "Three days missing.",
                    "criticality": "major"}
            ok = await cli.post(f"/api/remote-audit/audits/{world.audit['id']}/queries",
                                headers={**world.approver_headers, "Idempotency-Key": key()},
                                json=body)
            assert ok.status_code in (200, 201), ok.text

            # The site cannot raise a finding against itself.
            refused = await cli.post(f"/api/remote-audit/audits/{world.audit['id']}/queries",
                                     headers={**world.capturer_headers, "Idempotency-Key": key()},
                                     json=body)
            assert refused.status_code == 404

    run(flow())


def test_only_the_site_can_respond_and_only_the_sponsor_can_close():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            created = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/queries",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"title": "Fridge log incomplete", "criticality": "major"})
            query = created.json()

            # A reviewer answering their own query would remove the site from
            # the loop entirely.
            sponsor_answer = await cli.post(
                f"/api/remote-audit/queries/{query['id']}/responses",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"body": "I'll just answer this myself."})
            assert sponsor_answer.status_code == 404

            site_answer = await cli.post(
                f"/api/remote-audit/queries/{query['id']}/responses",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"body": "Logs attached; the gap was a printer fault."})
            assert site_answer.status_code in (200, 201), site_answer.text

            # And the site cannot then close it.
            site_close = await cli.post(
                f"/api/remote-audit/queries/{query['id']}/decision",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"action": "CLOSE"})
            assert site_close.status_code == 404

            sponsor_close = await cli.post(
                f"/api/remote-audit/queries/{query['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"action": "CLOSE", "reason_code": "SATISFACTORY"})
            assert sponsor_close.status_code == 200, sponsor_close.text
            assert sponsor_close.json()["status"] == "CLOSED"

    run(flow())


def test_a_capa_owner_cannot_be_someone_elses_work():
    """Only the accountable owner moves a CAPA along."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            created = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/capas",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"title": "Retrain staff", "owner_user_id": world.capturer["id"],
                      "due_date": "2027-06-01", "criticality": "critical"})
            assert created.status_code in (200, 201), created.text
            capa = created.json()

            not_owner = await cli.post(
                f"/api/remote-audit/capas/{capa['id']}/progress",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"action": "START"})
            assert not_owner.status_code == 404

            owner = await cli.post(
                f"/api/remote-audit/capas/{capa['id']}/progress",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"action": "START"})
            assert owner.status_code == 200, owner.text
            assert owner.json()["status"] == "IN_PROGRESS"

    run(flow())


def test_a_capa_cannot_be_certified_effective_by_whoever_completed_it():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            capa = (await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/capas",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"title": "Retrain staff", "owner_user_id": world.capturer["id"],
                      "due_date": "2027-06-01"})).json()

            await cli.post(f"/api/remote-audit/capas/{capa['id']}/progress",
                           headers={**world.capturer_headers, "Idempotency-Key": key()},
                           json={"action": "START"})
            await cli.post(f"/api/remote-audit/capas/{capa['id']}/progress",
                           headers={**world.capturer_headers, "Idempotency-Key": key()},
                           json={"action": "COMPLETE", "note": "Retraining delivered."})

            # The site cannot sign off its own work — it isn't an owner-side
            # role, and the service refuses self-review independently.
            self_review = await cli.post(
                f"/api/remote-audit/capas/{capa['id']}/effectiveness-review",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"outcome": "EFFECTIVE", "note": "Looks fine to me."})
            assert self_review.status_code == 404

            reviewed = await cli.post(
                f"/api/remote-audit/capas/{capa['id']}/effectiveness-review",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"outcome": "EFFECTIVE", "note": "Verified against the April logs."})
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["status"] == "CLOSED"

    run(flow())


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — virtual visits
# ═══════════════════════════════════════════════════════════════════════════

def test_a_session_is_scheduled_pending_consent_and_join_is_blocked():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            created = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/virtual-sessions",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"scheduled_start_utc": start, "privacy_mode": "SERVER_NO_RECORDING"})
            assert created.status_code in (200, 201), created.text
            session = created.json()
            assert session["consent_status"] == "PENDING"
            assert session["recording_enabled"] is False

            blocked = await cli.post(
                f"/api/remote-audit/virtual-sessions/{session['id']}/join",
                headers=world.capturer_headers, json={"role": "presenter"})
            assert blocked.status_code == 403
            assert blocked.json()["detail"]["code"] == "CONSENT_REQUIRED"

    run(flow())


def test_recording_is_refused_on_an_e2ee_session_through_the_api():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            session = (await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/virtual-sessions",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"scheduled_start_utc": start, "privacy_mode": "E2EE_NO_RECORDING",
                      "consent_required": False})).json()

            await cli.post(f"/api/remote-audit/virtual-sessions/{session['id']}/state",
                           headers=world.approver_headers, json={"action": "GO_LIVE"})

            refused = await cli.post(
                f"/api/remote-audit/virtual-sessions/{session['id']}/recording",
                headers=world.approver_headers)
            assert refused.status_code == 403
            assert refused.json()["detail"]["code"] == "RECORDING_NOT_PERMITTED"

    run(flow())


@pytest.mark.skipif(vv.livekit_configured(), reason="requires LiveKit NOT configured")
def test_video_being_unconfigured_does_not_break_the_rest_of_the_audit():
    """Correction #15 at the API boundary: joining reports 503, and queries
    and CAPAs on the same audit keep working."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            session = (await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/virtual-sessions",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"scheduled_start_utc": start, "consent_required": False})).json()

            join = await cli.post(
                f"/api/remote-audit/virtual-sessions/{session['id']}/join",
                headers=world.approver_headers, json={"role": "host"})
            assert join.status_code == 503
            assert join.json()["detail"]["code"] == "LIVEKIT_UNAVAILABLE"

            # Everything else on this audit still works.
            query = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/queries",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"title": "Unrelated finding"})
            assert query.status_code in (200, 201), query.text

    run(flow())


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7 — scoring and the final decision
# ═══════════════════════════════════════════════════════════════════════════

async def _active_template(cli, headers, user_id):
    """Grant template_admin explicitly (it is never implied), draft a template,
    and activate it with a step-up token."""
    await grants_service.grant_capability(
        server.db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN,
        granted_by="test-harness")

    draft = await cli.post("/api/remote-audit/templates", headers=headers, json={
        "scoring_policy_json": {"compliance": {"facilities": 60.0, "equipment": 40.0}},
        "requirement_items": [],
    })
    assert draft.status_code == 200, draft.text
    template = draft.json()

    token = await step_up(cli, headers, None, "template.activate")
    activated = await cli.post(f"/api/remote-audit/templates/{template['id']}/activate",
                               headers=headers, json={"step_up_token": token})
    assert activated.status_code == 200, activated.text
    return activated.json()


def test_scoring_reports_both_axes_separately():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            scored = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET",
                     "evidence_confidence": "FAILED"},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET",
                     "evidence_confidence": "FAILED"},
                ]})
            assert scored.status_code in (200, 201), scored.text
            run_doc = scored.json()

            # Full compliance on evidence nobody could verify: two numbers,
            # not one blended one.
            assert run_doc["compliance_percent"] == pytest.approx(100.0)
            assert run_doc["evidence_confidence_percent"] == pytest.approx(0.0)
            assert run_doc["proposed_decision"] != "APPROVED"
            assert any(b["code"] == "EVIDENCE_CONFIDENCE_TOO_LOW" for b in run_doc["blockers"])

    run(flow())


def test_scoring_requires_an_activated_template():
    """Activation is where weight validation happens. Scoring a draft would
    produce a number that isn't a percentage."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            await grants_service.grant_capability(
                server.db, user_id=world.approver["id"],
                capability=RemoteAuditCapability.TEMPLATE_ADMIN, granted_by="test-harness")
            draft = (await cli.post("/api/remote-audit/templates", headers=world.approver_headers,
                                    json={"scoring_policy_json": {"compliance": {"facilities": 100.0}},
                                          "requirement_items": []})).json()

            scored = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": draft["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET"}]})
            assert scored.status_code == 400
            assert scored.json()["detail"]["code"] == "TEMPLATE_NOT_ACTIVE"

    run(flow())


def test_a_final_decision_requires_a_step_up_token():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET"},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET"}]})

            bogus = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": "not-a-real-token"})
            assert bogus.status_code == 403
            assert bogus.json()["detail"]["code"] == "STEP_UP_REQUIRED"

            token = await step_up(cli, world.approver_headers, None, "audit.decision")
            signed = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token,
                      "signature_statement": "Reviewed and approved."})
            assert signed.status_code in (200, 201), signed.text
            assert signed.json()["decision"] == "APPROVED"
            assert signed.json()["overridden"] is False

    run(flow())


def test_a_step_up_token_is_single_use():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET"},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET"}]})
            token = await step_up(cli, world.approver_headers, None, "audit.decision")

            first = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token})
            assert first.status_code in (200, 201)

            # Replaying the same token must not sign a second decision.
            second = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "NOT_APPROVED", "step_up_token": token})
            assert second.status_code == 403

    run(flow())


def test_a_decision_cannot_be_recorded_for_an_unscored_audit():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            token = await step_up(cli, world.approver_headers, None, "audit.decision")
            resp = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token})
            assert resp.status_code == 400
            assert resp.json()["detail"]["code"] == "NOT_SCORED"

    run(flow())


def test_overriding_the_machine_requires_a_reason_and_keeps_its_verdict():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            scored = (await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "NOT_MET",
                     "is_critical": True},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET"}]})).json()
            assert scored["proposed_decision"] == "NOT_APPROVED"

            token = await step_up(cli, world.approver_headers, None, "audit.decision")
            no_reason = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token})
            assert no_reason.status_code == 400
            assert no_reason.json()["detail"]["code"] == "OVERRIDE_REASON_REQUIRED"

            token2 = await step_up(cli, world.approver_headers, None, "audit.decision")
            overridden = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token2,
                      "override_reason": "Regulator granted a written exemption on 2026-08-01."})
            assert overridden.status_code in (200, 201), overridden.text
            record = overridden.json()
            assert record["overridden"] is True
            # The machine's verdict and its reasoning survive beside the human's.
            assert record["machine_proposed"] == "NOT_APPROVED"
            assert any(b["code"] == "CRITICAL_REQUIREMENT_FAILED"
                       for b in record["machine_result"]["blockers"])

    run(flow())


def test_a_conditional_approval_must_link_a_real_capa():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET"},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "PARTIAL"}]})

            token = await step_up(cli, world.approver_headers, None, "audit.decision")
            no_conditions = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED_WITH_CONDITIONS", "step_up_token": token,
                      "override_reason": "Conditional on remediation."})
            assert no_conditions.status_code == 400
            assert no_conditions.json()["detail"]["code"] == "CONDITIONS_REQUIRED"

            capa = (await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/capas",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"title": "Replace the failed unit", "owner_user_id": world.capturer["id"],
                      "due_date": "2027-02-01"})).json()

            token2 = await step_up(cli, world.approver_headers, None, "audit.decision")
            with_conditions = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED_WITH_CONDITIONS", "step_up_token": token2,
                      "override_reason": "Conditional on remediation.",
                      "linked_capa_ids": [capa["id"]]})
            assert with_conditions.status_code in (200, 201), with_conditions.text
            assert with_conditions.json()["linked_capa_ids"] == [capa["id"]]

    run(flow())


def test_a_reviewer_without_the_approver_role_cannot_decide():
    """Separation of duties: `reviewer` examines, `approver` signs."""
    world = run(build_world(owner_role="reviewer"))

    async def flow():
        async with client() as cli:
            token = await step_up(cli, world.approver_headers, None, "audit.decision")
            resp = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/decision",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"decision": "APPROVED", "step_up_token": token})
            assert resp.status_code == 404

    run(flow())


def test_decisions_are_append_only():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/score",
                headers={**world.approver_headers, "Idempotency-Key": key()},
                json={"template_id": template["id"], "results": [
                    {"requirement_id": "f1", "section_id": "facilities", "outcome": "MET"},
                    {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET"}]})

            for decision, reason in (("APPROVED", None),
                                     ("NOT_APPROVED", "New information after sign-off.")):
                token = await step_up(cli, world.approver_headers, None, "audit.decision")
                resp = await cli.post(
                    f"/api/remote-audit/audits/{world.audit['id']}/decision",
                    headers={**world.approver_headers, "Idempotency-Key": key()},
                    json={"decision": decision, "step_up_token": token,
                          "override_reason": reason})
                assert resp.status_code in (200, 201), resp.text

            history = (await cli.get(f"/api/remote-audit/audits/{world.audit['id']}/decision",
                                     headers=world.approver_headers)).json()
            assert len(history["history"]) == 2
            # The superseded decision is still readable, and the chain is explicit.
            assert history["history"][0]["decision"] == "APPROVED"
            assert history["latest"]["decision"] == "NOT_APPROVED"
            assert history["latest"]["supersedes_decision_id"] == history["history"][0]["id"]

    run(flow())


def test_score_runs_are_append_only_and_snapshot_the_policy():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            template = await _active_template(cli, world.approver_headers, world.approver["id"])
            for outcome in ("NOT_MET", "MET"):
                resp = await cli.post(
                    f"/api/remote-audit/audits/{world.audit['id']}/score",
                    headers={**world.approver_headers, "Idempotency-Key": key()},
                    json={"template_id": template["id"], "results": [
                        {"requirement_id": "f1", "section_id": "facilities", "outcome": outcome},
                        {"requirement_id": "e1", "section_id": "equipment", "outcome": "MET"}]})
                assert resp.status_code in (200, 201), resp.text

            history = (await cli.get(f"/api/remote-audit/audits/{world.audit['id']}/score",
                                     headers=world.approver_headers)).json()
            assert len(history["history"]) == 2
            assert history["history"][0]["score_run_id"] == 1
            assert history["latest"]["score_run_id"] == 2
            assert history["latest"]["supersedes_score_id"] == history["history"][0]["id"]
            # The weights in force at the time are stored inline, so editing
            # the template later cannot rewrite what this run meant.
            assert history["latest"]["policy_snapshot"]["scoring_policy_json"] == \
                {"compliance": {"facilities": 60.0, "equipment": 40.0}}

    run(flow())


def test_an_outsider_cannot_read_a_score_or_decision():
    world = run(build_world())
    _outsider, outsider_headers, _ = run(register("sponsor", f"RA567-OUT-{RUN_ID}-{uuid.uuid4().hex[:6]}"))

    async def flow():
        async with client() as cli:
            for path in ("score", "decision", "queries", "capas", "virtual-sessions"):
                resp = await cli.get(f"/api/remote-audit/audits/{world.audit['id']}/{path}",
                                     headers=outsider_headers)
                assert resp.status_code == 404, f"{path} -> {resp.status_code}"

    run(flow())
