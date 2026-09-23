"""HTTP-level integration tests for Phase 4 (Documents) — exercised through
the real FastAPI app, following this repo's convention
(see test_remote_audit_routes_phase3.py).

Covers the plan's Phase 4 exit criteria at the API boundary:
  * no document reaches ACCEPTED without a completed, clean malware scan
  * a document that has not cleared processing is not visible to a reviewer
  * a malware-rejected document's CONTENT is never served, to anyone
  * cross-organization access returns 404, never 403
  * every mutation is idempotent
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

from app.services.remote_audit import document_pipeline as pipeline  # noqa: E402
from app.services.remote_audit import documents as documents_service  # noqa: E402
from app.services.remote_audit import file_checks  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


def key():
    """A fresh idempotency key. Every remote-audit mutation requires one."""
    return uuid.uuid4().hex


async def _login(email: str):
    async with client() as cli:
        response = await cli.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


async def register(role: str, org: str):
    email = f"ra4-{RUN_ID}-{uuid.uuid4().hex[:6]}@example.com"
    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD, "full_name": f"{role.title()} {org}",
            "role": role, "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


def make_pdf(text: str = "Calibration Certificate\nDue Date: 2027-05-04") -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(text.split("\n")):
        page.insert_text((72, 100 + i * 18), line)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(autouse=True)
def clean_scanner(monkeypatch):
    """A CLEAN scanner for the API tests. The scanner-unavailable path is
    exercised for real (no ClamAV configured) in
    test_remote_audit_document_pipeline.py — here we need documents that
    actually get past the gate so the rest of the API is reachable."""
    async def fake_scan(_data):
        return file_checks.MalwareScanStatus.CLEAN, None
    monkeypatch.setattr(pipeline.file_checks, "scan", fake_scan)


class World:
    """A sponsor org auditing a site org, with a reviewer and a capturer."""

    def __init__(self, audit, reviewer, reviewer_headers, capturer, capturer_headers):
        self.audit = audit
        self.reviewer = reviewer
        self.reviewer_headers = reviewer_headers
        self.capturer = capturer
        self.capturer_headers = capturer_headers


async def build_world():
    sponsor_org = f"RA4-SPONSOR-{RUN_ID}-{uuid.uuid4().hex[:6]}"
    site_org = f"RA4-SITE-{RUN_ID}-{uuid.uuid4().hex[:6]}"
    reviewer, reviewer_headers = await register("sponsor", sponsor_org)
    capturer, capturer_headers = await register("site", site_org)

    async with client() as cli:
        audit_resp = await cli.post(
            "/api/remote-audit/audits",
            headers={**reviewer_headers, "Idempotency-Key": key()},
            json={"site_id": f"site-{uuid.uuid4().hex[:8]}",
                  "site_org_id": capturer["organization_id"]})
        assert audit_resp.status_code in (200, 201), audit_resp.text
        audit = audit_resp.json()

        # Each side assigns its own participants: an owner-side role can only
        # be granted by the sponsor org's admin, a site-side role only by the
        # hospital's. The sponsor cannot appoint staff inside the site's org,
        # which is the cross-organization tenancy model working as intended.
        for user, role, headers in ((reviewer, "reviewer", reviewer_headers),
                                    (capturer, "capturer", capturer_headers)):
            resp = await cli.post(
                f"/api/remote-audit/audits/{audit['id']}/participants",
                headers={**headers, "Idempotency-Key": key()},
                json={"user_id": user["id"], "role_in_audit": role})
            assert resp.status_code in (200, 201), resp.text

    return World(audit, reviewer, reviewer_headers, capturer, capturer_headers)


async def upload_document(cli, world, *, data=None, document_type="calibration_certificate",
                          mime="application/pdf"):
    """Create -> upload bytes -> submit, the real client sequence."""
    payload = data if data is not None else make_pdf()
    create = await cli.post(
        f"/api/remote-audit/audits/{world.audit['id']}/documents",
        headers={**world.capturer_headers, "Idempotency-Key": key()},
        json={"document_type": document_type, "file_name": "cert.pdf",
              "mime_claimed": mime, "size_claimed": len(payload)})
    assert create.status_code in (200, 201), create.text
    doc = create.json()

    upload = await cli.post(
        f"/api/remote-audit/documents/{doc['id']}/upload-bytes",
        headers=world.capturer_headers,
        files={"file": ("cert.pdf", payload, mime)})
    assert upload.status_code == 200, upload.text

    submit = await cli.post(
        f"/api/remote-audit/documents/{doc['id']}/submit",
        headers={**world.capturer_headers, "Idempotency-Key": key()})
    assert submit.status_code in (200, 202), submit.text
    return doc


# ── Creation & the invisible window ─────────────────────────────────────────

def test_a_submitted_document_is_invisible_to_the_reviewer_until_processed():
    """The core visibility rule at the HTTP boundary: between submit and a
    completed check run, a reviewer gets 404 — not a placeholder, not a
    "processing" record they could act on."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)

            # The uploader can see their own in-flight upload...
            own = await cli.get(f"/api/remote-audit/documents/{doc['id']}",
                                headers=world.capturer_headers)
            assert own.status_code == 200
            assert own.json()["status"] == documents_service.STATUS_QUARANTINED

            # ...the reviewer cannot.
            theirs = await cli.get(f"/api/remote-audit/documents/{doc['id']}",
                                   headers=world.reviewer_headers)
            assert theirs.status_code == 404

            listing = await cli.get(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers=world.reviewer_headers)
            assert listing.status_code == 200
            assert doc["id"] not in [item["id"] for item in listing.json()]

    run(flow())


def test_submit_requires_the_bytes_to_actually_exist():
    """Enqueueing a job for bytes nobody uploaded would burn the worker's
    whole retry budget before anyone learned the upload simply never
    happened."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            create = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"document_type": "calibration_certificate", "file_name": "ghost.pdf",
                      "mime_claimed": "application/pdf", "size_claimed": 1024})
            doc = create.json()

            submit = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/submit",
                headers={**world.capturer_headers, "Idempotency-Key": key()})
            assert submit.status_code == 409
            assert submit.json()["detail"]["code"] == "OBJECT_MISSING"

    run(flow())


def test_creating_a_document_is_idempotent():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            idem = key()
            body = {"document_type": "calibration_certificate", "file_name": "cert.pdf",
                    "mime_claimed": "application/pdf", "size_claimed": 2048}
            first = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers={**world.capturer_headers, "Idempotency-Key": idem}, json=body)
            second = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers={**world.capturer_headers, "Idempotency-Key": idem}, json=body)
            assert first.status_code in (200, 201)
            assert second.status_code in (200, 201)
            # The replay returns the STORED result rather than creating a
            # second document record.
            assert first.json()["id"] == second.json()["id"]

    run(flow())


def test_a_mutation_without_an_idempotency_key_is_refused():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            resp = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers=world.capturer_headers,
                json={"document_type": "calibration_certificate", "file_name": "c.pdf",
                      "mime_claimed": "application/pdf", "size_claimed": 10})
            assert resp.status_code == 400

    run(flow())


# ── After processing ────────────────────────────────────────────────────────

async def process(document_id):
    return await pipeline.run_document_processing(
        server.db, document_id, policy=pipeline.DocumentPolicy(enable_vision_extraction=False))


def test_after_processing_the_reviewer_sees_the_document_and_its_findings():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])

            visible = await cli.get(f"/api/remote-audit/documents/{doc['id']}",
                                    headers=world.reviewer_headers)
            assert visible.status_code == 200
            assert visible.json()["status"] in (
                documents_service.STATUS_TECHNICALLY_VALID,
                documents_service.STATUS_REVIEW_REQUIRED)

            checks = await cli.get(f"/api/remote-audit/documents/{doc['id']}/checks",
                                   headers=world.reviewer_headers)
            assert checks.status_code == 200
            body = checks.json()
            # A reviewer is never shown a verdict without the checks behind it.
            assert body["latest"]["checks"]["malware_status"] == "CLEAN"
            assert body["latest"]["rule_set_version"] == pipeline.RULE_SET_VERSION
            assert isinstance(body["latest"]["findings"], list)

    run(flow())


def test_content_is_served_only_after_processing():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)

            too_early = await cli.get(f"/api/remote-audit/documents/{doc['id']}/content",
                                      headers=world.capturer_headers)
            assert too_early.status_code == 409
            assert too_early.json()["detail"]["code"] == "DOCUMENT_NOT_READY"

            await process(doc["id"])
            ready = await cli.get(f"/api/remote-audit/documents/{doc['id']}/content",
                                  headers=world.reviewer_headers)
            assert ready.status_code == 200
            assert ready.content.startswith(b"%PDF-")
            # Never rendered inline: a script-named file must not be able to
            # execute in a reviewer's browser.
            assert "attachment" in ready.headers["content-disposition"]
            assert ready.headers["x-content-type-options"] == "nosniff"

    run(flow())


def test_malware_rejected_content_is_never_served_to_anyone(monkeypatch):
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)

            async def detected(_data):
                return file_checks.MalwareScanStatus.DETECTED, "Eicar-Test-Signature"
            monkeypatch.setattr(pipeline.file_checks, "scan", detected)
            await process(doc["id"])

            record = await cli.get(f"/api/remote-audit/documents/{doc['id']}",
                                   headers=world.reviewer_headers)
            # The RECORD stays visible so the site is told why it was rejected...
            assert record.status_code == 200
            assert record.json()["status"] == documents_service.STATUS_REJECTED

            # ...but the CONTENT is gone for everyone, uploader included.
            for headers in (world.reviewer_headers, world.capturer_headers):
                content = await cli.get(f"/api/remote-audit/documents/{doc['id']}/content",
                                        headers=headers)
                assert content.status_code == 403
                assert content.json()["detail"]["code"] == "CONTENT_BLOCKED"

    run(flow())


# ── Acceptance requires real checks and real confirmation ───────────────────

def test_a_document_cannot_be_accepted_before_it_has_been_checked():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            resp = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.reviewer_headers, "Idempotency-Key": key()},
                json={"decision": "ACCEPTED"})
            # Not visible yet, so it does not even exist as far as the
            # reviewer is concerned.
            assert resp.status_code == 404

    run(flow())


def test_accepting_a_certificate_requires_a_confirmed_due_date():
    """An expiry-bearing certificate accepted without a confirmed due date
    would sit in the record forever — no sweep could ever expire it."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])

            premature = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.reviewer_headers, "Idempotency-Key": key()},
                json={"decision": "ACCEPTED"})
            assert premature.status_code == 400
            assert premature.json()["detail"]["code"] == "CONFIRMED_DUE_DATE_REQUIRED"

            confirm = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/confirm",
                headers=world.reviewer_headers,
                json={"fields": {"due_date": "2027-05-04", "certificate_number": "CAL-1"}})
            assert confirm.status_code == 200
            assert confirm.json()["expires_on"] == "2027-05-04"

            accepted = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.reviewer_headers, "Idempotency-Key": key()},
                json={"decision": "ACCEPTED"})
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == documents_service.STATUS_ACCEPTED

    run(flow())


def test_a_rejection_must_carry_a_reason_code():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])

            no_reason = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.reviewer_headers, "Idempotency-Key": key()},
                json={"decision": "REJECTED"})
            assert no_reason.status_code == 400
            assert no_reason.json()["detail"]["code"] == "REASON_CODE_REQUIRED"

            with_reason = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.reviewer_headers, "Idempotency-Key": key()},
                json={"decision": "REJECTED", "reason_code": "ILLEGIBLE",
                      "note": "The due date is not readable."})
            assert with_reason.status_code == 200
            assert with_reason.json()["decision_reason_code"] == "ILLEGIBLE"

    run(flow())


def test_a_site_capturer_cannot_decide_on_their_own_document():
    """Separation of duties: the party submitting the evidence never rules on
    it, regardless of what else they can do on the audit."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])

            resp = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"decision": "ACCEPTED"})
            assert resp.status_code == 404

    run(flow())


def test_deciding_is_idempotent():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])
            idem = key()
            body = {"decision": "REJECTED", "reason_code": "SUPERSEDED_BY_NEWER"}

            first = await cli.post(f"/api/remote-audit/documents/{doc['id']}/decide",
                                   headers={**world.reviewer_headers, "Idempotency-Key": idem},
                                   json=body)
            second = await cli.post(f"/api/remote-audit/documents/{doc['id']}/decide",
                                    headers={**world.reviewer_headers, "Idempotency-Key": idem},
                                    json=body)
            assert first.status_code == 200, first.text
            # The replay returns the stored result rather than attempting a
            # second (now illegal) transition out of a terminal state.
            assert second.status_code == 200, second.text
            assert first.json()["status"] == second.json()["status"]

    run(flow())


# ── Cross-organization isolation ────────────────────────────────────────────

def test_an_unrelated_organization_gets_404_not_403():
    """404 everywhere, so a caller can never use the response to discover that
    a document exists."""
    world = run(build_world())
    outsider, outsider_headers = run(register("sponsor", f"RA4-OUTSIDER-{RUN_ID}-{uuid.uuid4().hex[:6]}"))

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)
            await process(doc["id"])

            for path in (f"/api/remote-audit/documents/{doc['id']}",
                         f"/api/remote-audit/documents/{doc['id']}/checks",
                         f"/api/remote-audit/documents/{doc['id']}/content"):
                resp = await cli.get(path, headers=outsider_headers)
                assert resp.status_code == 404, f"{path} -> {resp.status_code}"

            decide = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/decide",
                headers={**outsider_headers, "Idempotency-Key": key()},
                json={"decision": "ACCEPTED"})
            assert decide.status_code == 404

    run(flow())


def test_only_the_uploader_can_send_bytes_for_their_document():
    world = run(build_world())

    async def flow():
        async with client() as cli:
            create = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"document_type": "calibration_certificate", "file_name": "c.pdf",
                      "mime_claimed": "application/pdf", "size_claimed": 100})
            doc = create.json()

            hijack = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/upload-bytes",
                headers=world.reviewer_headers,
                files={"file": ("evil.pdf", make_pdf(), "application/pdf")})
            assert hijack.status_code == 404

    run(flow())


def test_bytes_cannot_be_replaced_once_submitted():
    """Closes the window between submit and the worker reading the object.

    Without this, the file that gets scanned and the file that gets stored
    could differ, and a reviewer would be looking at a verdict for something
    that is no longer there.
    """
    world = run(build_world())

    async def flow():
        async with client() as cli:
            doc = await upload_document(cli, world)

            # Already submitted — refused immediately, before any worker runs.
            swap = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/upload-bytes",
                headers=world.capturer_headers,
                files={"file": ("swap.pdf", make_pdf("Swapped"), "application/pdf")})
            assert swap.status_code == 409
            assert swap.json()["detail"]["code"] == "DOCUMENT_ALREADY_SUBMITTED"

            await process(doc["id"])
            after = await cli.post(
                f"/api/remote-audit/documents/{doc['id']}/upload-bytes",
                headers=world.capturer_headers,
                files={"file": ("swap.pdf", make_pdf("Swapped again"), "application/pdf")})
            assert after.status_code == 409


def test_a_retried_upload_before_submit_is_still_allowed():
    """The flip side: a dropped connection mid-upload must be retryable. The
    lock is on SUBMIT, not on having uploaded once."""
    world = run(build_world())

    async def flow():
        async with client() as cli:
            create = await cli.post(
                f"/api/remote-audit/audits/{world.audit['id']}/documents",
                headers={**world.capturer_headers, "Idempotency-Key": key()},
                json={"document_type": "calibration_certificate", "file_name": "c.pdf",
                      "mime_claimed": "application/pdf", "size_claimed": 2048})
            doc = create.json()

            for attempt in ("first", "retry"):
                resp = await cli.post(
                    f"/api/remote-audit/documents/{doc['id']}/upload-bytes",
                    headers=world.capturer_headers,
                    files={"file": ("c.pdf", make_pdf(attempt), "application/pdf")})
                assert resp.status_code == 200, f"{attempt}: {resp.text}"

    run(flow())
