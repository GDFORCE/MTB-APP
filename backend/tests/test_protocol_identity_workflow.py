"""P2-02..P2-05 through the LIVE upload routes of ``server.py``.

Real FastAPI app, real MongoDB for the legacy collections AND the identity store,
scripted model client (no network, no key). Full-schedule model calls are counted
separately from everything else: a quarantined or reused upload must make ZERO.

Fixtures are SYNTHETIC text reproducing the PERIGON identity conflict (a file
submitted as NCT02088541 that contains the NCT02088554 protocol). They are not
the real protocol PDFs; verification against the real corpus is pending.

Run against a LOCAL replica set only::

    MONGO_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0 DB_NAME=mtb_p2_test \\
        python -m pytest tests/test_protocol_identity_workflow.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

_URL = os.environ.get("MONGO_URL", "")
_HOST = (urlparse(_URL).netloc.rsplit("@", 1)[-1].split(",")[0].rsplit(":", 1)[0]
         if _URL else "")
if _HOST not in {"localhost", "127.0.0.1", "::1"}:
    pytest.skip(
        "set MONGO_URL to a LOCAL MongoDB replica set to run the live-route identity "
        "tests (never the shared cluster)", allow_module_level=True)

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import protocol_identity_runtime as identity_runtime  # noqa: E402
import server  # noqa: E402
from app.extraction import documents, runner  # noqa: E402
from app.extraction.contracts import DocumentPage  # noqa: E402
from app.persistence.mongodb.client import build_client, get_database  # noqa: E402
from app.persistence.mongodb.identity_repository import MongoIdentityStore  # noqa: E402
from app.persistence.mongodb.migrate import run_migrations  # noqa: E402
from app.persistence.mongodb.settings import MongoSettings  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG = f"P2-IDENTITY-{RUN_ID}"
OTHER_ORG = f"P2-OTHER-{RUN_ID}"
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()
USER_IDS: list[str] = []
TRIAL_IDS: list[str] = []

METADATA = {
    "trial": {"ctri_number": "", "title": "PERIGON", "phase": "Phase III", "indications": [],
              "drug": "Aprocitentan", "duration": "", "target_enrollment": None,
              "total_visits": None},
    "schedule_options": [],
    "day_numbering": {"day_1_anchor": "Registration", "counting_convention": "CLINICAL_DAY",
                      "evidence": [{"page": 3, "quote": "Day 1 is the day of registration"}]},
    "visit_names": ["Screening labs", "Visit 3"], "anchor_names": ["Registration"],
}
FACTS = [
    {"what": "Registration", "kind": "anchor", "anchor": {"type": "RECORDED"},
     "evidence": [{"page": 3, "quote": "Day 1 is the day of registration"}]},
    {"what": "Screening labs", "kind": "visit",
     "timing": {"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"},
     "evidence": [{"page": 2, "quote": "CBC within 28 days before registration"}]},
    {"what": "Visit 3", "kind": "visit",
     "timing": {"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"},
     "window": {"before": "3 DAY", "after": "3 DAY"},
     "evidence": [{"page": 3, "quote": "Visit 3: 4 weeks after registration +/- 3 days"}]},
]


class Scripted:
    """Counts schedule-fact reads separately from the metadata read."""

    def __init__(self):
        self.metadata_calls = 0
        self.fact_calls = 0

    def complete(self, *, system, prompt, max_tokens=0):
        if "return one JSON object" in prompt:
            self.metadata_calls += 1
            return json.dumps(METADATA)
        self.fact_calls += 1
        return json.dumps({"facts": FACTS})


class MemoryStorage:
    def __init__(self):
        self.saved: dict[str, tuple[bytes, str]] = {}

    async def save(self, key, data, content_type):
        self.saved[key] = (data, content_type)

    async def open(self, key):
        data, content_type = self.saved[key]
        return data, content_type

    async def delete(self, key):
        self.saved.pop(key, None)


class Ids:
    """Identifiers unique to one test, so accepted documents of other tests are
    never "the same protocol" (a shared protocol number would be, correctly)."""

    def __init__(self):
        serial = int(uuid.uuid4().int % 90_000_000) + 10_000_000
        self.right = f"NCT{serial:08d}"
        self.wrong = f"NCT{(serial + 1) % 100_000_000:08d}"
        self.proto = f"PGN-{uuid.uuid4().hex[:8].upper()}"


def perigon(ids=None, *, nct=None, version="2.0", date="12 March 2015", extra="",
            reflow=False, ref=None) -> list[str]:
    ids = ids or Ids()
    nct = nct or ids.right
    ref = ref or ids.wrong
    pages = [
        f"PROTOCOL\nTitle: PERIGON - a study of aprocitentan\nSponsor: Example Pharma\n"
        f"Protocol Number: {ids.proto}\nClinicalTrials.gov Identifier: {nct}\n"
        f"Version {version}\nDate: {date}\nThis protocol describes the PERIGON study.",
        "Section 3.1 Eligibility. Pre-treatment evaluation: CBC within 28 days before registration."
        + extra,
        "Section 7. Schedule of Assessments. Day 1 is the day of registration. "
        "Visit 3: 4 weeks after registration +/- 3 days.",
        f"References\n1. Earlier hypertension trial, see {ref} [4].",
    ]
    if reflow:
        pages = [page.replace(" ", "  ").replace("\n", "\n\n") for page in pages]
    return pages


DOCS: dict[bytes, list[DocumentPage]] = {}


def make_pdf(pages: list[str], *, scanned: bool = False) -> bytes:
    data = b"%PDF-1.7 " + uuid.uuid4().hex.encode()
    DOCS[data] = [
        DocumentPage(page_number=i + 1, text="" if scanned else text,
                     text_status="image_or_empty" if scanned else "text")
        for i, text in enumerate(pages)]
    return data


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                             base_url="http://testserver", timeout=60)


async def register(cli, org, tag):
    response = await cli.post("/api/auth/register", json={
        "email": f"p2-{tag}-{RUN_ID}@example.com", "password": PASSWORD,
        "full_name": f"P2 {tag}", "role": "sponsor", "organization": org})
    assert response.status_code == 200, response.text
    USER_IDS.append(response.json()["user"]["id"])
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, response.json()["user"]["id"]


@pytest.fixture(scope="module")
def actors():
    async def build():
        async with client() as cli:
            admin, admin_id = await register(cli, ORG, "admin")
            stranger, stranger_id = await register(cli, OTHER_ORG, "stranger")
        return {"admin": admin, "admin_id": admin_id, "stranger": stranger,
                "stranger_id": stranger_id}
    built = run(build())
    # Selected policy: identity review is an EXPLICIT grant, never an org-admin default.
    _grant_review(built["admin_id"], _org_uuid(ORG))
    # the stranger is a legitimately designated reviewer of ANOTHER organization, so
    # the cross-tenant test exercises scope (404), not a missing capability (403)
    _grant_review(built["stranger_id"], _org_uuid(OTHER_ORG))
    return built


@pytest.fixture(scope="module")
def real_store():
    name = f"uctsm_p2_{RUN_ID}"
    settings = MongoSettings(url=_URL, database=name)
    mongo = build_client(settings, server_selection_timeout_ms=4000)
    database = get_database(mongo, settings)
    run_migrations(database)
    store = MongoIdentityStore(mongo, database)
    yield store
    mongo.drop_database(name)
    mongo.close()


@pytest.fixture(autouse=True)
def world(monkeypatch, real_store):
    """A configured provider with a scripted client, in-memory file storage and
    the REAL Mongo identity store (replacing the suite's in-memory default)."""
    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    scripted = Scripted()
    monkeypatch.setattr(runner, "build_client", lambda **_: scripted)
    storage = MemoryStorage()
    monkeypatch.setattr(server.file_storage, "get_storage", lambda: storage)
    monkeypatch.setattr(documents, "pages_from_pdf", lambda data: DOCS[data])
    identity_runtime.configure(store=real_store)
    yield scripted, storage
    identity_runtime.configure(store=None)


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.schedule_definitions.delete_many({"trial_id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": {"$in": [ORG, OTHER_ORG]}})
        await server.db.audit_logs.delete_many({"user_id": {"$in": USER_IDS}})
        await server.db.protocol_extractions.delete_many({"user_id": {"$in": USER_IDS}})
    run(clean())
    LOOP.close()


def upload(cli, headers, data, *, name="protocol.pdf", expected=None, path="/api/protocols/extract"):
    fields = {"expected_protocol_id": expected} if expected else None
    return cli.post(path, headers=headers, data=fields,
                    files={"file": (name, data, "application/pdf")})


def post(coro_factory):
    async def go():
        async with client() as cli:
            return await coro_factory(cli)
    return run(go())


def drafts_for(user_id):
    return run(server.db.protocol_extractions.count_documents({"user_id": user_id}))


# ------------------------------------------------------------------ P2-02 / P2-05
def test_perigon_file_submitted_as_the_wrong_nct_is_quarantined_with_zero_calls(actors, world):
    """The PDF's scenario with the literal identifiers: NCT02088541 submitted,
    NCT02088554 inside. (Synthetic text, not the real PDF.)"""
    scripted, storage = world
    ids = Ids()
    pages = perigon(ids, nct="NCT02088554", ref="NCT02088541")
    before = drafts_for(actors["admin_id"])
    response = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(pages), name="NCT02088541.pdf", expected="NCT02088541"))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "PROTOCOL_IDENTITY_BLOCKED" and detail["workflow_state"] == "QUARANTINED"
    identity = detail["identity"]
    assert identity["classification"] == "MISMATCH" and identity["review_status"] == "PENDING_REVIEW"
    assert "EXPECTED_IDENTIFIER_CONFLICT" in identity["issue_codes"]
    assert identity["suggested_correction"]["expected_identifier"] == "NCT02088554"
    evidence = [i for i in identity["document_identifiers"] if i["normalized"] == "NCT02088554"][0]
    assert evidence["page"] == 1 and evidence["authoritative"]
    cited = [i for i in identity["document_identifiers"] if i["normalized"] == "NCT02088541"][0]
    assert cited["page"] == 4 and not cited["authoritative"]      # reference only
    assert scripted.fact_calls == 0 and scripted.metadata_calls == 0
    assert drafts_for(actors["admin_id"]) == before               # nothing consumable
    assert not any(key.startswith("protocol-uploads/") for key in storage.saved)
    assert any(key.startswith("protocol-identity/") for key in storage.saved)
    assert "storage_key" not in identity and "pdf_storage_key" not in json.dumps(identity)


def test_quarantine_works_without_a_configured_provider_and_is_not_a_503(actors, world, monkeypatch):
    monkeypatch.delenv("UCTSM_EXTRACTION_PROVIDER", raising=False)
    ids = Ids()
    wrong = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.wrong))
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["identity"]["classification"] == "MISMATCH"
    ok = post(lambda cli: upload(cli, actors["admin"], make_pdf(perigon(ids))))
    assert ok.status_code == 503          # provider availability reported separately


def test_correct_upload_extracts_then_same_text_different_bytes_is_reused_with_no_new_calls(
        actors, world):
    scripted, _ = world
    ids = Ids()
    first = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.right))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["identity"]["classification"] == "MATCH" and body["identity"]["permitted"]
    assert body["identity"]["reused_extraction"] is False
    assert scripted.fact_calls >= 1
    calls = (scripted.fact_calls, scripted.metadata_calls)

    reupload = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids, reflow=True)), name="copy-of-protocol.pdf"))
    assert reupload.status_code == 200, reupload.text
    again = reupload.json()
    assert again["identity"]["classification"] == "DUPLICATE"
    assert again["identity"]["reused_extraction"] is True
    assert again["identity"]["duplicate_of_id"] == body["identity"]["id"]
    assert (scripted.fact_calls, scripted.metadata_calls) == calls     # zero further model calls
    assert again["extraction_id"] != body["extraction_id"]            # a fresh, own draft
    assert again["preview"]["schedule"]["events"]


def test_conflict_beats_hash_equality_for_an_already_accepted_document(actors, world):
    ids = Ids()
    pages = perigon(ids)
    ok = post(lambda cli: upload(cli, actors["admin"], make_pdf(pages), expected=ids.right))
    assert ok.status_code == 200
    wrong = post(lambda cli: upload(cli, actors["admin"], make_pdf(pages), expected=ids.wrong))
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["identity"]["classification"] == "MISMATCH"


def test_amended_version_gets_new_lineage_and_leaves_the_old_identity_unchanged(actors, world):
    scripted, _ = world
    ids = Ids()
    original = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.right)).json()
    original_id = original["identity"]["id"]
    before = run(_get(actors["admin"], f"/api/protocol-identities/{original_id}"))
    calls = scripted.fact_calls
    amended = post(lambda cli: upload(cli, actors["admin"], make_pdf(perigon(
        ids, version="3.0", date="5 June 2016", extra=" Dose changed to 80 mg."))))
    assert amended.status_code == 200, amended.text
    view = amended.json()["identity"]
    assert view["classification"] == "NEW_VERSION" and view["predecessor_id"] == original_id
    assert view["reused_extraction"] is False and scripted.fact_calls > calls
    after = run(_get(actors["admin"], f"/api/protocol-identities/{original_id}"))
    assert after["classification"] == before["classification"] == "MATCH"
    assert after["revision"] == before["revision"]


async def _get(headers, path):
    async with client() as cli:
        response = await cli.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("kind,code", [
    ("no_identifier", "NO_AUTHORITATIVE_IDENTIFIER"),
    ("scanned", "NO_USABLE_TEXT"),
    ("competing", "COMPETING_IDENTIFIERS"),
])
def test_unsafe_documents_are_quarantined_not_extracted(actors, world, kind, code):
    scripted, _ = world
    ids = Ids()
    scanned = False
    if kind == "no_identifier":
        pages = ["A study of a drug. " * 30, "More text. " * 30]
    elif kind == "scanned":
        pages, scanned = [f"PROTOCOL\nClinicalTrials.gov Identifier: {ids.right}\n" + "x " * 100], True
    else:
        pages = [f"PROTOCOL\nClinicalTrials.gov Identifier: {ids.right}\n"
                 f"ClinicalTrials.gov Identifier: {ids.wrong}\n" + "Study text. " * 30]
    response = post(lambda cli: upload(cli, actors["admin"], make_pdf(pages, scanned=scanned)))
    assert response.status_code == 409, response.text
    assert code in response.json()["detail"]["identity"]["issue_codes"]
    assert scripted.fact_calls == 0 and scripted.metadata_calls == 0


def test_invalid_mime_is_rejected_before_any_identity_work(actors, world):
    response = post(lambda cli: cli.post(
        "/api/protocols/extract", headers=actors["admin"],
        files={"file": ("x.png", b"\x89PNG", "image/png")}))
    assert response.status_code == 400


def test_extract_details_is_gated_too(actors, world):
    scripted, _ = world
    ids = Ids()
    response = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.wrong,
        path="/api/protocols/extract-details"))
    assert response.status_code == 409
    assert scripted.metadata_calls == 0


# ------------------------------------------------------------------- existing trial
ACTORS: dict = {}


def _create_trial(protocol_id):
    async def go(cli):
        response = await cli.post("/api/trials", headers=ACTORS["admin"], json={
            "title": f"P2 trial {protocol_id}", "protocol_id": protocol_id, "phase": "Phase III",
            "condition": "Hypertension", "indications": ["Hypertension"], "drug": "Aprocitentan"})
        assert response.status_code in (200, 201), response.text
        trial_id = response.json()["id"]
        TRIAL_IDS.append(trial_id)
        return trial_id
    return post(go)


def test_existing_trial_expected_identity_comes_from_the_persisted_record(actors, world):
    ACTORS.update(actors)
    scripted, _ = world
    ids = Ids()
    trial_id = _create_trial(ids.wrong)          # the trial is recorded as the OTHER protocol
    path = f"/api/trials/{trial_id}/extract-schedule"
    response = post(lambda cli: upload(cli, actors["admin"], make_pdf(perigon(ids)), path=path))
    assert response.status_code == 409, response.text
    identity = response.json()["detail"]["identity"]
    assert identity["classification"] == "MISMATCH" and identity["trial_id"] == trial_id
    assert scripted.fact_calls == 0
    # a client-supplied identifier cannot override the record
    override = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.right, path=path))
    assert override.status_code == 422
    assert override.json()["detail"]["code"] == "EXPECTED_IDENTITY_CONFLICT"
    assert scripted.fact_calls == 0


def test_consumption_rechecks_identity_even_with_a_forged_or_legacy_draft(actors, world):
    ACTORS.update(actors)
    ids = Ids()
    trial_id = _create_trial(ids.right)
    quarantined = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids)), expected=ids.wrong)).json()["detail"]
    stamp = server.now()
    run(server.db.protocol_extractions.insert_many([
        {"id": f"legacy-{RUN_ID}", "user_id": actors["admin_id"], "file_name": "x.pdf",
         "details": {}, "native_schedule": {"events": []}, "created_at": stamp,
         "expires_at": stamp + server.timedelta(hours=1)},
        {"id": f"forged-{RUN_ID}", "user_id": actors["admin_id"], "file_name": "x.pdf",
         "details": {}, "native_schedule": {"events": []}, "created_at": stamp,
         "identity_id": quarantined["identity"]["id"],
         "expires_at": stamp + server.timedelta(hours=1)}]))
    legacy = post(lambda cli: cli.post(
        f"/api/trials/{trial_id}/protocol-extractions/legacy-{RUN_ID}/consume",
        headers=actors["admin"]))
    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "PROTOCOL_IDENTITY_UNVERIFIED"
    forged = post(lambda cli: cli.post(
        f"/api/trials/{trial_id}/protocol-extractions/forged-{RUN_ID}/consume",
        headers=actors["admin"]))
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == "PROTOCOL_IDENTITY_BLOCKED"


def test_a_verified_draft_is_consumed_into_the_trial_without_another_model_call(actors, world):
    ACTORS.update(actors)
    scripted, _ = world
    ids = Ids()
    trial_id = _create_trial(ids.right)
    uploaded = post(lambda cli: upload(cli, actors["admin"], make_pdf(perigon(ids)))).json()
    calls = scripted.fact_calls
    consumed = post(lambda cli: cli.post(
        f"/api/trials/{trial_id}/protocol-extractions/{uploaded['extraction_id']}/consume",
        headers=actors["admin"]))
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()["schema_version"] == "uctsm.v1"
    assert scripted.fact_calls == calls


# --------------------------------------------------------------------- review
def _quarantine(actors, ids, expected=None, **doc):
    response = post(lambda cli: upload(
        cli, actors["admin"], make_pdf(perigon(ids, **doc)), expected=expected or ids.wrong))
    assert response.status_code == 409, response.text
    return response.json()["detail"]["identity"]


def _resolve(actors, identity, *, headers=None, **body):
    payload = {"expected_revision": identity["revision"], **body}
    return post(lambda cli: cli.post(
        f"/api/protocol-identities/{identity['id']}/resolve",
        headers=headers or actors["admin"], json=payload))


def test_review_requires_permission_reason_revision_and_appends_history(actors, world):
    scripted, _ = world
    identity = _quarantine(actors, Ids())
    _delete_grants(actors["admin_id"])           # an org admin without the grant may not review
    try:
        denied = _resolve(actors, identity, action="REJECT", reason="no")
        assert denied.status_code == 403
        assert run(_get(actors["admin"], "/api/protocol-identities"))["can_review"] is False
    finally:
        _grant_review(actors["admin_id"], _org_uuid(ORG))

    assert _resolve(actors, identity, action="REJECT", reason="  ").status_code == 422
    stale = _resolve(actors, {**identity, "revision": identity["revision"] + 5},
                     action="KEEP_QUARANTINED", reason="Looking at it")
    assert stale.status_code == 409
    kept = _resolve(actors, identity, action="KEEP_QUARANTINED", reason="Waiting for the sponsor")
    assert kept.status_code == 200 and kept.json()["resume"] is None
    revision = kept.json()["identity"]["revision"]
    rejected = _resolve(actors, {**identity, "revision": revision}, action="REJECT",
                        reason="Wrong protocol file")
    assert rejected.status_code == 200
    assert rejected.json()["identity"]["review_status"] == "REJECTED"
    assert rejected.json()["resume"] is None
    again = _resolve(actors, {**identity, "revision": revision + 1}, action="REJECT",
                     reason="Different reason")
    assert again.status_code == 409                                   # already resolved
    history = run(_get(actors["admin"], f"/api/protocol-identities/{identity['id']}"))["reviews"]
    assert [item["action"] for item in history] == ["KEEP_QUARANTINED", "REJECT"]
    assert scripted.fact_calls == 0                                   # never resumed
    blocked = post(lambda cli: cli.get(
        f"/api/protocol-identities/{identity['id']}/extraction", headers=actors["admin"]))
    assert blocked.status_code == 409


def test_cross_tenant_reviewer_cannot_see_or_change_the_record(actors, world):
    identity = _quarantine(actors, Ids())
    seen = post(lambda cli: cli.get(
        f"/api/protocol-identities/{identity['id']}", headers=actors["stranger"]))
    assert seen.status_code == 404
    changed = _resolve(actors, identity, headers=actors["stranger"], action="REJECT",
                       reason="not yours")
    assert changed.status_code == 404
    assert run(_get(actors["stranger"], "/api/protocol-identities"))["items"] == []
    assert run(_get(actors["admin"], f"/api/protocol-identities/{identity['id']}"))[
        "review_status"] == "PENDING_REVIEW"


def test_approving_a_correction_resumes_extraction_exactly_once_and_the_uploader_collects_it(
        actors, world):
    scripted, _ = world
    ids = Ids()
    identity = _quarantine(actors, ids)                     # submitted as the other id
    assert identity["classification"] == "MISMATCH" and scripted.fact_calls == 0
    resolved = _resolve(actors, identity, action="CORRECT_EXPECTED_IDENTITY",
                        reason="Sponsor typed the wrong registry id",
                        corrected_identifier=ids.right)
    assert resolved.status_code == 200, resolved.text
    outcome = resolved.json()
    assert outcome["identity"]["review_status"] == "APPROVED"
    assert outcome["resume"]["status"] == "COMPLETED"
    calls = scripted.fact_calls
    assert calls >= 1

    replay = _resolve(actors, identity, action="CORRECT_EXPECTED_IDENTITY",
                      reason="Sponsor typed the wrong registry id", corrected_identifier=ids.right)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    resume = post(lambda cli: cli.post(
        f"/api/protocol-identities/{identity['id']}/resume", headers=actors["admin"]))
    assert resume.status_code == 200
    assert resume.json()["resume"]["status"] == "ALREADY_HANDLED"
    assert scripted.fact_calls == calls

    prepared = run(_get(actors["admin"], f"/api/protocol-identities/{identity['id']}/extraction"))
    assert prepared["extractions"] and prepared["extractions"][0]["preview"]["schedule"]["events"]
    assert scripted.fact_calls == calls                              # drafts from the stored result


def test_a_correction_the_document_does_not_support_is_refused_without_state_change(actors, world):
    identity = _quarantine(actors, Ids())
    refused = _resolve(actors, identity, action="CORRECT_EXPECTED_IDENTITY",
                       reason="trying", corrected_identifier="NCT00000001")
    assert refused.status_code == 422
    after = run(_get(actors["admin"], f"/api/protocol-identities/{identity['id']}"))
    assert after["review_status"] == "PENDING_REVIEW" and after["reviews"] == []
    assert after["revision"] == identity["revision"]


def test_confirming_an_unreadable_document_resumes_extraction_once(actors, world):
    scripted, _ = world
    ids = Ids()
    # an uncertain scan: no readable text; a reviewer who read the pages confirms it
    data = make_pdf(perigon(ids), scanned=True)
    response = post(lambda cli: upload(cli, actors["admin"], data, expected=ids.right))
    assert response.status_code == 409
    identity = response.json()["detail"]["identity"]
    assert "NO_USABLE_TEXT" in identity["issue_codes"] and scripted.fact_calls == 0
    resolved = _resolve(actors, identity, action="CONFIRM_DOCUMENT_IDENTITY",
                        reason="Read the cover page: it is the PERIGON protocol",
                        corrected_identifier=ids.right)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["identity"]["review_status"] == "APPROVED"


# ------------------------------------------------------------------ P3 integration
def _org_uuid(org_name):
    organization = run(server.find_organization_by_name(org_name))
    return str(organization["id"])


def _delete_grants(user_id):
    run(server.db.uctsm_capability_grants.delete_many({"user_id": user_id}))


def _grant_review(user_id, org):
    run(server.uctsm_grant_store.add(
        organization_id=org, user_id=user_id, capability="protocol_identity.review",
        trial_id=None, granted_by=str(uuid.uuid4()), reason="designated identity reviewer (test)"))


def test_identity_review_is_the_catalogued_capability_not_a_user_field(actors, world):
    """P3: ``protocol_identity.review`` comes from a persisted grant (or the
    documented org-admin compatibility default). A ``permissions`` list on the
    user document is no longer an authorization source."""
    identity = _quarantine(actors, Ids())
    admin_id, org = actors["admin_id"], _org_uuid(ORG)
    _delete_grants(admin_id)
    run(server.db.users.update_one(
        {"id": admin_id}, {"$set": {"org_admin": False, "permissions": ["protocol_identity.review"]}}))
    try:
        by_user_field = _resolve(actors, identity, action="KEEP_QUARANTINED", reason="x")
        assert by_user_field.status_code == 403            # the user field grants nothing

        grant = run(server.uctsm_grant_store.add(
            organization_id=org, user_id=admin_id, capability="protocol_identity.review",
            trial_id=None, granted_by=admin_id, reason="test grant"))
        kept = _resolve(actors, identity, action="KEEP_QUARANTINED", reason="held for the sponsor")
        assert kept.status_code == 200, kept.text

        run(server.uctsm_grant_store.revoke(grant.id, organization_id=org, revoked_by=admin_id))
        revoked = _resolve(
            actors, {**identity, "revision": kept.json()["identity"]["revision"]},
            action="REJECT", reason="after revocation")
        assert revoked.status_code == 403                  # effective on the very next call
        assert run(_get(actors["admin"], "/api/protocol-identities"))["can_review"] is False
    finally:
        _delete_grants(admin_id)
        _grant_review(admin_id, org)
        run(server.db.users.update_one(
            {"id": admin_id}, {"$set": {"org_admin": True}, "$unset": {"permissions": ""}}))


def test_grant_api_is_administration_not_a_clinical_right(actors, world):
    admin, admin_id, org = actors["admin"], actors["admin_id"], _org_uuid(ORG)
    colleague = f"p3-colleague-{RUN_ID}"
    run(server.db.users.insert_one({
        "id": colleague, "organization": ORG, "role": "pi", "status": "Active",
        "full_name": "P3 Colleague", "email": f"{colleague}@example.com"}))
    USER_IDS.append(colleague)
    try:
        # an organization admin may grant a colleague a clinical capability ...
        created = post(lambda cli: cli.post("/api/uctsm/authorization/grants", headers=admin, json={
            "user_id": colleague, "capability": "schedule.approve", "reason": "Designated approver"}))
        assert created.status_code == 201, created.text
        grant_id = created.json()["id"]
        # ... but may not grant one to themselves, nor to someone outside the organization
        selfie = post(lambda cli: cli.post("/api/uctsm/authorization/grants", headers=admin, json={
            "user_id": admin_id, "capability": "schedule.approve", "reason": "me"}))
        assert selfie.status_code == 403
        assert selfie.headers["x-authz-reason"] == "SELF_GRANT_OF_CLINICAL_CAPABILITY"
        foreign = post(lambda cli: cli.post("/api/uctsm/authorization/grants", headers=admin, json={
            "user_id": actors["stranger_id"], "capability": "schedule.approve", "reason": "x"}))
        assert foreign.status_code == 404
        unknown = post(lambda cli: cli.post("/api/uctsm/authorization/grants", headers=admin, json={
            "user_id": colleague, "capability": "schedule.everything", "reason": "x"}))
        assert unknown.status_code == 422
        forged = post(lambda cli: cli.post("/api/uctsm/authorization/grants", headers=admin, json={
            "user_id": colleague, "capability": "schedule.approve", "reason": "x",
            "organization_id": "other"}))
        assert forged.status_code == 422               # unknown fields refused

        listed = post(lambda cli: cli.get("/api/uctsm/authorization/grants", headers=admin)).json()
        assert listed["matrix_status"] == "SELECTED_UNDER_OWNER_DELEGATION"
        assert any(item["id"] == grant_id and item["active"] for item in listed["items"])
        # another organization's admin sees none of it and cannot revoke it
        theirs = post(lambda cli: cli.get(
            "/api/uctsm/authorization/grants", headers=actors["stranger"])).json()["items"]
        assert all(item["id"] != grant_id for item in theirs)       # only their own org's grants
        assert post(lambda cli: cli.delete(
            f"/api/uctsm/authorization/grants/{grant_id}", headers=actors["stranger"])).status_code == 404

        revoked = post(lambda cli: cli.delete(
            f"/api/uctsm/authorization/grants/{grant_id}", headers=admin))
        assert revoked.status_code == 200 and revoked.json()["active"] is False
        assert run(server.uctsm_grant_store.active_for_user(colleague, org)) == ()
    finally:
        _delete_grants(colleague)
        run(server.db.users.delete_many({"id": colleague}))


def test_a_non_admin_cannot_manage_grants_and_the_denial_is_audited(actors, world):
    admin_id = actors["admin_id"]
    run(server.db.users.update_one({"id": admin_id}, {"$set": {"org_admin": False}}))
    try:
        denied = post(lambda cli: cli.get("/api/uctsm/authorization/grants", headers=actors["admin"]))
        assert denied.status_code == 403
        assert denied.headers["x-authz-reason"] == "CAPABILITY_MISSING"
        row = run(server.db.audit_logs.find_one(
            {"user_id": admin_id, "action": "authorization.denied",
             "capability": "authorization.grants.manage"}, {"_id": 0}))
        assert row and row["reason_code"] == "CAPABILITY_MISSING" and row["status"] == "denied"
        assert row["policy_version"] == "uctsm-authz.v2"
    finally:
        run(server.db.users.update_one({"id": admin_id}, {"$set": {"org_admin": True}}))


def test_my_capabilities_reflects_policy_for_the_ui(actors, world):
    mine = post(lambda cli: cli.get("/api/uctsm/authorization/me", headers=actors["admin"])).json()
    assert mine["policy_version"] == "uctsm-authz.v2"
    held = mine["capabilities"]
    assert "authorization.grants.manage" in held and "protocol_identity.review" in held
    assert "schedule.approve" not in held and "schedule.correct" not in held
