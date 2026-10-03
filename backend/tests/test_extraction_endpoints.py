"""The restored protocol extraction endpoints, with a scripted model client.

/protocols/extract must return the exact response contract Add Trial and
ProtocolUploadReview read ({details, extraction_id, schedule_visit_count,
needs_schedule_selection, verification, preview} or {details, extractions}),
cache a native draft the consume endpoint can reuse, and say 503 clearly when
no provider is configured. No network call is made: the model client and file
storage are replaced.
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402
from app.extraction import documents, runner  # noqa: E402
from app.extraction.contracts import DocumentPage  # noqa: E402
from app.extraction.llm_client import ExtractionModelError  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG = f"EXTRACT-ENDPOINTS-{RUN_ID}"
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()
USER_IDS: list[str] = []
TRIAL_IDS: list[str] = []

PROTOCOL_TEXT = "\f".join([
    "PROTOCOL EXT-1. A Phase II study of Drug Y. CTRI/2026/02/000002.",
    "Section 3.1 Eligibility. Pre-treatment evaluation: CBC within 28 days before registration.",
    "Section 7. Schedule of Assessments. Day 1 is the day of registration. "
    "Visit 3: 4 weeks after registration +/- 3 days.",
])

METADATA = {
    "trial": {"ctri_number": "CTRI/2026/02/000002", "title": "A Phase II study of Drug Y",
              "phase": "Phase II", "indications": ["Oncology"], "drug": "Drug Y", "duration": "",
              "target_enrollment": None, "total_visits": None},
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


class ScriptedClient:
    def __init__(self, *, metadata=None, facts=None, error=None):
        self.metadata = metadata or METADATA
        self.facts = facts if facts is not None else FACTS
        self.error = error
        self.calls = 0

    def complete(self, *, system, prompt, max_tokens=0):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if "return one JSON object" in prompt:
            return json.dumps(self.metadata)
        return json.dumps({"facts": self.facts})


class MemoryStorage:
    def __init__(self):
        self.saved: dict[str, tuple[bytes, str]] = {}

    async def save(self, key, data, content_type):
        self.saved[key] = (data, content_type)


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


@pytest.fixture(scope="module")
def headers():
    async def build():
        async with client() as cli:
            response = await cli.post("/api/auth/register", json={
                "email": f"extract-endpoints-{RUN_ID}@example.com", "password": PASSWORD,
                "full_name": "Extraction Sponsor", "role": "sponsor", "organization": ORG,
            })
        assert response.status_code == 200, response.text
        USER_IDS.append(response.json()["user"]["id"])
        return {"Authorization": f"Bearer {response.json()['access_token']}"}
    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.schedule_definitions.delete_many({"trial_id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": ORG})
        await server.db.audit_logs.delete_many({"actor_id": {"$in": USER_IDS}})
        await server.db.protocol_extractions.delete_many({"user_id": {"$in": USER_IDS}})
    run(clean())
    LOOP.close()


@pytest.fixture()
def model(monkeypatch):
    """A configured provider whose client is scripted, plus in-memory storage."""
    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    scripted = ScriptedClient()
    monkeypatch.setattr(runner, "build_client", lambda **_: scripted)
    storage = MemoryStorage()
    monkeypatch.setattr(server.file_storage, "get_storage", lambda: storage)
    # A PDF upload is read into pages without needing a real PDF renderer.
    monkeypatch.setattr(documents, "pages_from_pdf", lambda data: [
        DocumentPage(page_number=index + 1, text=text)
        for index, text in enumerate(PROTOCOL_TEXT.split("\f"))])
    return scripted, storage


def upload_text():
    return {"file": ("protocol.txt", PROTOCOL_TEXT.encode(), "text/plain")}


def upload_pdf():
    return {"file": ("protocol.pdf", b"%PDF-1.7 test", "application/pdf")}


# --- not configured ------------------------------------------------------------------

def test_every_extraction_endpoint_says_503_when_no_provider_is_configured(headers, monkeypatch):
    monkeypatch.delenv("UCTSM_EXTRACTION_PROVIDER", raising=False)

    async def exercise():
        async with client() as cli:
            extract = await cli.post("/api/protocols/extract", headers=headers, files=upload_pdf())
            details = await cli.post("/api/protocols/extract-details", headers=headers, files=upload_pdf())
        for response in (extract, details):
            assert response.status_code == 503, response.text
            assert "manually" in response.json()["detail"]
    run(exercise())


def test_upload_validation_still_runs_first(headers, model):
    async def exercise():
        async with client() as cli:
            wrong = await cli.post("/api/protocols/extract", headers=headers,
                                   files={"file": ("protocol.docx", b"x", "application/msword")})
            empty = await cli.post("/api/protocols/extract", headers=headers,
                                   files={"file": ("protocol.pdf", b"", "application/pdf")})
        assert wrong.status_code == 400
        assert empty.status_code == 400
    run(exercise())
    assert model[0].calls == 0


# --- Add Trial extraction --------------------------------------------------------------

def test_extract_returns_the_add_trial_contract_and_caches_a_native_draft(headers, model):
    scripted, storage = model

    async def exercise():
        async with client() as cli:
            response = await cli.post("/api/protocols/extract", headers=headers, files=upload_text())
        assert response.status_code == 200, response.text
        return response.json()
    body = run(exercise())

    assert set(body) == {"details", "extraction_id", "schedule_visit_count", "needs_schedule_selection",
                         "verification", "preview"}
    assert body["details"]["ctri_number"] == "CTRI/2026/02/000002"
    assert body["details"]["status"] == "active"
    assert body["needs_schedule_selection"] is False
    assert body["schedule_visit_count"] == 2
    # WP1 (IN-03/IR-05): the preview also carries the accountability ledgers.
    assert set(body["preview"]) == {"schedule", "projections", "issues", "accountability"}
    ledger = body["preview"]["accountability"]
    assert ledger["schema"] == "uctsm-extraction-accountability.v1"
    assert ledger["coverage"]["status"] == "COMPLETE"
    assert {item["disposition"] for item in ledger["statements"]} == {"COMPILED"}
    events = {item["code"]: item for item in body["preview"]["schedule"]["events"]}
    assert events["SCREENING_LABS"]["timing"]["type"] == "WITHIN"
    assert events["VISIT_3"]["timing"]["type"] == "OFFSET" and events["VISIT_3"]["window"]["after"]["value"] == 3
    assert body["verification"]["status"] in {"ok", "needs_review"}
    assert scripted.calls == 2  # one overview call + one page batch

    async def stored():
        return await server.db.protocol_extractions.find_one({"id": body["extraction_id"]}, {"_id": 0})
    draft = run(stored())
    assert draft["native_schedule"]["events"]
    assert draft["pdf_storage_key"] in storage.saved
    assert storage.saved[draft["pdf_storage_key"]][1] == "text/plain"
    assert len(draft["pdf_document_hash"]) == 64
    assert draft["option_id"] == ""
    lifetime = draft["expires_at"] - draft["created_at"]
    assert lifetime.total_seconds() == 2 * 3600


def test_multiple_schedules_come_back_as_extractions(headers, model):
    scripted, _ = model
    scripted.metadata = {**METADATA, "schedule_options": [
        {"id": "A", "label": "Substudy A"}, {"id": "B", "label": "Substudy B"}]}
    scripted.facts = FACTS[:1] + [
        {**FACTS[1], "schedule_option": "A"}, {**FACTS[2], "schedule_option": "B"}]

    async def exercise():
        async with client() as cli:
            response = await cli.post("/api/protocols/extract", headers=headers, files=upload_text())
        assert response.status_code == 200, response.text
        return response.json()
    body = run(exercise())

    assert set(body) == {"details", "extractions", "needs_schedule_selection"}
    assert [item["option_label"] for item in body["extractions"]] == ["Substudy A", "Substudy B"]
    assert [item["schedule_visit_count"] for item in body["extractions"]] == [1, 1]
    for item in body["extractions"]:
        assert set(item) >= {"extraction_id", "option_id", "preview", "verification"}


def test_a_model_failure_is_a_502_not_an_empty_schedule(headers, model):
    scripted, _ = model
    scripted.error = ExtractionModelError("model request failed: overloaded")

    async def exercise():
        async with client() as cli:
            return await cli.post("/api/protocols/extract", headers=headers, files=upload_text())
    response = run(exercise())

    assert response.status_code == 502
    assert "overloaded" in response.json()["detail"]


def test_extract_details_returns_only_trial_details(headers, model):
    scripted, _ = model

    async def exercise():
        async with client() as cli:
            return await cli.post("/api/protocols/extract-details", headers=headers, files=upload_pdf())
    response = run(exercise())

    assert response.status_code == 200, response.text
    assert response.json() == {"details": {
        "ctri_number": "CTRI/2026/02/000002", "title": "A Phase II study of Drug Y",
        "phase": "Phase II", "indications": ["Oncology"], "drug": "Drug Y", "duration": "",
        "target_enrollment": None, "total_visits": None, "status": "active"}}
    assert scripted.calls == 1


# --- consuming the draft, and extraction for an existing trial ----------------------------

def create_trial(cli, headers):
    return cli.post("/api/trials", headers=headers, json={
        "title": "Extraction endpoint trial", "protocol_id": f"EXT-{uuid.uuid4().hex[:6]}",
        "phase": "Phase II", "condition": "Oncology", "indications": ["Oncology"], "drug": "Drug Y",
    })


def test_the_cached_draft_is_consumed_by_the_new_trial_without_another_model_call(headers, model):
    scripted, _ = model

    async def exercise():
        async with client() as cli:
            extracted = (await cli.post("/api/protocols/extract", headers=headers, files=upload_text())).json()
            calls_after_extract = scripted.calls
            trial = await create_trial(cli, headers)
            assert trial.status_code == 200, trial.text
            TRIAL_IDS.append(trial.json()["id"])
            consumed = await cli.post(
                f"/api/trials/{trial.json()['id']}/protocol-extractions/{extracted['extraction_id']}/consume",
                headers=headers)
        assert consumed.status_code == 200, consumed.text
        assert scripted.calls == calls_after_extract
        definition = await server.db.schedule_definitions.find_one(
            {"id": consumed.json()["schedule_definition_id"]}, {"_id": 0})
        return consumed.json(), definition
    body, definition = run(exercise())

    assert body["schema_version"] == "uctsm.v1"
    assert definition["native_schedule"]["events"]


def test_extract_schedule_for_an_existing_trial_stores_a_draft_definition(headers, model):
    async def exercise():
        async with client() as cli:
            trial = await create_trial(cli, headers)
            assert trial.status_code == 200, trial.text
            trial_id = trial.json()["id"]
            TRIAL_IDS.append(trial_id)
            response = await cli.post(f"/api/trials/{trial_id}/extract-schedule",
                                      headers=headers, files=upload_pdf())
            not_pdf = await cli.post(f"/api/trials/{trial_id}/extract-schedule", headers=headers,
                                     files={"file": ("x.pdf", b"hello", "application/pdf")})
        assert response.status_code == 200, response.text
        assert not_pdf.status_code == 400
        stored = await server.db.trials.find_one({"id": trial_id}, {"_id": 0})
        return trial_id, response.json(), stored
    trial_id, body, stored = run(exercise())

    assert body["schema_version"] == "uctsm.v1"
    assert body["needs_schedule_selection"] is False
    assert body["schedule_visit_count"] == 2
    assert stored["current_schedule_definition_id"] == body["schedule_definition_id"]


def test_a_read_that_yields_no_visit_is_a_502_not_an_empty_success(headers, model):
    """AT-20: "no visits" must never look like a successful draft."""
    scripted, _ = model
    scripted.facts = []

    async def exercise():
        async with client() as cli:
            return await cli.post("/api/protocols/extract", headers=headers, files=upload_text())
    response = run(exercise())

    assert response.status_code == 502
    assert "upload the same file again to resume" in response.json()["detail"]
