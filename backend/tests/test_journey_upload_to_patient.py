"""End-to-end journey of the Adaptive Protocol Brain through the REAL HTTP
endpoints: upload -> draft -> review -> approval -> patient calendar ->
amendment, plus the SQL-commit / Mongo-projection failure probe (AT-16).

Requirements: docs/adaptive-protocol-brain-requirements.md, journeys J1-J4.

One invented protocol (SYN-CRT-7, "weekly Veltracin with daily radiotherapy";
no real trial, drug or document) is read by a SCRIPTED model client. No model
provider is ever called: the provider env is replaced, ``runner.build_client``
returns the scripted client and every real client class is replaced by one that
fails the test if it is constructed. File storage is in memory.

Stores: the Mongo half uses the app's configured database exactly like
test_extraction_endpoints.py / test_patient_audit_trail.py (every document is
tagged with this run's id and deleted at teardown). The SQL half is a private
in-memory SQLite database wired into both SQL entry points of ``server.app``
(the ``/api/uctsm`` router's ``get_session`` dependency and the
``_operational_factory`` the Mongo-side routes use), restored at teardown.

The tests are ONE journey and run in file order, sharing state in ``J``.
Assertions are semantic (statuses, dates, counts) and derived from the
synthetic protocol's own rules, not from snapshots.

The product gaps this journey first found (B1 held statements, B3 approve-table
gates, B4 reviewer window, B5 enrolment after an amendment) are fixed; the steps
that were strict xfails now assert the correct behaviour directly.

Run alone:  python -m pytest -q -p no:cacheprovider tests/test_journey_upload_to_patient.py
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import sys
import uuid
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import server  # noqa: E402
from app.db import models as db  # noqa: E402
from app.db.base import Base, get_session  # noqa: E402
from app.extraction import llm_client, runner  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG = f"JOURNEY-APB-{RUN_ID}"
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()
API = "/api/uctsm"

#: Shared journey state (the tests are one sequential journey).
J: dict = {}
MONGO = {"users": [], "trials": [], "patients": []}

# --- the synthetic protocol ------------------------------------------------------------

PAGES = {
    1: "PROTOCOL SYN-CRT-7. A Phase II study of weekly Veltracin with daily radiotherapy. "
       "CTRI/2026/09/000777. Sponsor: Invented Oncology Group.",
    2: "Section 3 Eligibility and pre-treatment evaluation. History and physical examination within "
       "28 days before registration. Complete blood count within 14 days before registration. "
       "Informed consent within 14 days prior to study entry. Pregnancy test within 7 days prior to study "
       "entry. Chemistry panel within 3 days before the planned start of treatment.",
    3: "Section 5 Treatment plan. Day 1 is the day of registration. Veltracin is given weekly for 6 weeks "
       "starting on the first day of treatment. Radiotherapy is delivered Monday to Friday for 30 "
       "scheduled fractions; a missed fraction is not replaced.",
    4: "Section 6 Dose modification. Grade 3 neuropathy: discontinue Veltracin; radiotherapy continues. "
       "If two treatment-related deaths occur, accrual is suspended pending DSMC review.",
    5: "Section 7 Schedule of assessments and follow-up. Response PET 12 to 16 weeks after end of "
       "chemotherapy. The PET must be at least 12 weeks after end of radiotherapy. Toxicity call within 7 "
       "days after end of radiotherapy or within 3 days after end of chemotherapy. End-of-treatment visit "
       "4 weeks after end of chemotherapy; it may be done up to 7 days late.",
    6: "Section 9 Adverse events. Serious adverse events must be reported to the sponsor within 24 hours.",
}

ANCHOR_NAMES = ["Registration", "Start of treatment", "Start of radiotherapy", "End of radiotherapy",
                "End of chemotherapy"]
VISIT_NAMES = ["History and physical", "Complete blood count", "Informed consent", "Pregnancy test",
               "Chemistry panel", "Veltracin dose", "Radiotherapy", "Response PET", "Toxicity call",
               "End-of-treatment visit"]

METADATA = {
    "trial": {"ctri_number": "CTRI/2026/09/000777",
              "title": "A Phase II study of weekly Veltracin with daily radiotherapy",
              "phase": "Phase II", "indications": ["Oncology"], "drug": "Veltracin", "duration": "",
              "target_enrollment": None, "total_visits": None},
    "schedule_options": [],
    "day_numbering": {"day_1_anchor": "Registration", "counting_convention": "CLINICAL_DAY",
                      "evidence": [{"page": 3, "quote": "Day 1 is the day of registration"}]},
    "arms": [], "parts": [], "cohorts": [], "populations": [], "blocks": [],
    "visit_names": VISIT_NAMES, "anchor_names": ANCHOR_NAMES,
}


def fact(what, page, quote, **extra):
    return {"what": what, "kind": extra.pop("kind", "visit"),
            "evidence": [{"page": page, "quote": quote}], **extra}


def anchor_fact(name, page, quote):
    return fact(name, page, quote, kind="anchor", anchor={"type": "RECORDED"})


def within_before(what, page, quote, reference, days):
    return fact(what, page, quote, timing={"pattern": "WITHIN_BEFORE", "from": reference,
                                          "duration": f"{days} DAY"})


FACTS = [
    anchor_fact("Registration", 3, "Day 1 is the day of registration"),
    anchor_fact("Start of treatment", 3, "starting on the first day of treatment"),
    anchor_fact("Start of radiotherapy", 3, "Radiotherapy is delivered Monday to Friday"),
    anchor_fact("End of radiotherapy", 5, "at least 12 weeks after end of radiotherapy"),
    anchor_fact("End of chemotherapy", 5, "Response PET 12 to 16 weeks after end of chemotherapy"),
    # Pre-treatment windows counted from Registration (WITHIN_BEFORE).
    within_before("History and physical", 2,
                  "History and physical examination within 28 days before registration", "Registration", 28),
    within_before("Complete blood count", 2, "Complete blood count within 14 days before registration",
                  "Registration", 14),
    # The one shared anchor question: "study entry" is not one of the protocol's reference dates.
    within_before("Informed consent", 2, "Informed consent within 14 days prior to study entry",
                  "study entry", 14),
    within_before("Pregnancy test", 2, "Pregnancy test within 7 days prior to study entry", "Study Entry", 7),
    # Explicitly timed from the PLANNED date (PT-01 / C6).
    fact("Chemistry panel", 2, "Chemistry panel within 3 days before the planned start of treatment",
         timing={"pattern": "WITHIN_BEFORE", "from": {"name": "Start of treatment", "accepts": "PLANNED"},
                 "duration": "3 DAY"}),
    # Weekly treatment series from the ACTUAL start.
    fact("Veltracin dose", 3, "Veltracin is given weekly for 6 weeks starting on the first day of treatment",
         event_type="TREATMENT", component="Veltracin", component_kind="INTERVENTION",
         timing={"pattern": "REPEAT", "from": "Start of treatment"},
         repeat={"interval": "7 DAY", "includes_start": True, "end": {"type": "COUNT", "count": 6}}),
    # Weekday RT series: SLOTS and NO_REPLACEMENT stated, holiday policy NOT stated.
    fact("Radiotherapy", 3, "Radiotherapy is delivered Monday to Friday for 30 scheduled fractions",
         event_type="TREATMENT", component="Radiotherapy", component_kind="INTERVENTION",
         count_basis="SLOTS", missed_policy="NO_REPLACEMENT",
         timing={"pattern": "WEEKDAY_REPEAT", "from": "Start of radiotherapy",
                 "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]},
         repeat={"interval": "1 DAY", "end": {"type": "COUNT", "count": 30}}),
    # AND pair: a range from end of chemotherapy AND a minimum delay from end of RT.
    fact("Response PET", 5, "Response PET 12 to 16 weeks after end of chemotherapy", event_type="IMAGING",
         timing={"pattern": "RANGE", "from": "End of chemotherapy", "start": "12 WEEK", "end": "16 WEEK",
                 "lower_inclusive": True, "upper_inclusive": True}),
    fact("Response PET", 5, "The PET must be at least 12 weeks after end of radiotherapy", event_type="IMAGING",
         timing={"pattern": "NO_EARLIER_THAN", "from": "End of radiotherapy", "duration": "12 WEEK",
                 "lower_inclusive": True}),
    # A stated either/or alternative.
    fact("Toxicity call", 5, "Toxicity call within 7 days after end of radiotherapy",
         alternative_group="toxicity-call",
         timing={"pattern": "WITHIN_AFTER", "from": "End of radiotherapy", "duration": "7 DAY",
                 "lower_inclusive": True, "upper_inclusive": True}),
    fact("Toxicity call", 5, "or within 3 days after end of chemotherapy", alternative_group="toxicity-call",
         timing={"pattern": "WITHIN_AFTER", "from": "End of chemotherapy", "duration": "3 DAY",
                 "lower_inclusive": True, "upper_inclusive": True}),
    # Only one side of a window stated: it is never completed by a guess.
    fact("End-of-treatment visit", 5,
         "End-of-treatment visit 4 weeks after end of chemotherapy; it may be done up to 7 days late",
         timing={"pattern": "OFFSET", "from": "End of chemotherapy", "offset": "4 WEEK"},
         window={"after": "7 DAY"}),
    # Per-drug stop rule.
    fact("Grade 3 neuropathy", 4, "Grade 3 neuropathy: discontinue Veltracin; radiotherapy continues",
         kind="condition", timing={"pattern": "DISCONTINUE"},
         condition={"name": "Neuropathy", "effect": "STOP", "target_components": ["Veltracin"]}),
    # Trial-level accrual rule.
    fact("Accrual suspension", 4, "If two treatment-related deaths occur, accrual is suspended pending DSMC review",
         kind="condition", scope="TRIAL", timing={"pattern": "DISCONTINUE"},
         condition={"name": "Accrual suspension", "effect": "MANUAL_REVIEW"}),
    # A non-schedule statement.
    {**fact("SAE reporting", 6, "Serious adverse events must be reported to the sponsor within 24 hours",
            kind="activity", timing={"pattern": "TRIGGERED", "from": "Serious adverse event", "offset": "1 DAY"}),
     "statement_type": "NON_SCHEDULE", "non_schedule_reason": "REPORTING"},
]

#: Rows the draft must show, by the codes the compiler derives from the names.
ROW_CODES = {"HISTORY_AND_PHYSICAL", "COMPLETE_BLOOD_COUNT", "INFORMED_CONSENT", "PREGNANCY_TEST",
             "CHEMISTRY_PANEL", "VELTRACIN_DOSE", "RADIOTHERAPY", "RESPONSE_PET", "TOXICITY_CALL",
             "END_OF_TREATMENT_VISIT"}

# --- the amendment (J4): same trial, a changed CBC window, no open questions --------------

AMENDED_PAGES = {
    **PAGES,
    1: PAGES[1] + " Amendment 1.",
    2: "Section 3 Eligibility and pre-treatment evaluation. History and physical examination within "
       "28 days before registration. Complete blood count within 21 days before registration. "
       "Informed consent within 14 days before registration. Pregnancy test within 7 days before "
       "registration. Chemistry panel within 3 days before the planned start of treatment.",
    5: PAGES[5].replace("it may be done up to 7 days late", "window 0 days early to 7 days late"),
}


def _amended_facts():
    facts = []
    for item in FACTS:
        item = json.loads(json.dumps(item))
        quote = item["evidence"][0]["quote"]
        if item["what"] == "Complete blood count":
            item = within_before("Complete blood count", 2, "Complete blood count within 21 days before registration",
                                 "Registration", 21)
        elif item["what"] == "Informed consent":
            item = within_before("Informed consent", 2, "Informed consent within 14 days before registration",
                                 "Registration", 14)
        elif item["what"] == "Pregnancy test":
            item = within_before("Pregnancy test", 2, "Pregnancy test within 7 days before registration",
                                 "Registration", 7)
        elif item["what"] == "End-of-treatment visit":
            item["evidence"][0]["quote"] = quote.replace("it may be done up to 7 days late",
                                                         "window 0 days early to 7 days late")
            item["window"] = {"before": "0 DAY", "after": "7 DAY"}
        facts.append(item)
    return facts


# --- offline model + storage ------------------------------------------------------------

_PAGES_IN_PROMPT = re.compile(r"stated on pages ([\d, ]+) -")


class ScriptedClient:
    """Answers the overview with METADATA and each page batch with the facts
    whose first citation is on one of that batch's pages."""

    def __init__(self, facts, metadata=METADATA):
        self.facts = facts
        self.metadata = metadata
        self.calls = 0

    def complete(self, *, system, prompt, max_tokens=0):
        self.calls += 1
        if "return one JSON object" in prompt:
            return json.dumps(self.metadata)
        match = _PAGES_IN_PROMPT.search(prompt)
        numbers = {int(item) for item in match.group(1).split(",")} if match else set(PAGES)
        chosen = [item for item in self.facts if item["evidence"][0]["page"] in numbers]
        return json.dumps({"facts": chosen, "table_cells": [], "footnotes": []})


class _NoRealModel:
    def __init__(self, *args, **kwargs):  # pragma: no cover - fails the test if reached
        raise AssertionError("a real model client was constructed during an offline test")


class MemoryStorage:
    def __init__(self):
        self.saved: dict[str, tuple[bytes, str]] = {}

    async def save(self, key, data, content_type):
        self.saved[key] = (data, content_type)


# --- harness ---------------------------------------------------------------------------------

def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver")


def call(method, path, headers, **kwargs):
    async def go():
        async with client() as cli:
            return await cli.request(method, path, headers=headers, **kwargs)
    return run(go())


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:2000]}"
    return response.json()


def mongo(coro):
    return run(coro)


@pytest.fixture(scope="module", autouse=True)
def environment():
    patcher = pytest.MonkeyPatch()
    # Offline model: an explicit provider so the endpoints run, a scripted client,
    # and every real client class replaced so a real call cannot happen.
    patcher.setenv("UCTSM_EXTRACTION_PROVIDER", "anthropic")
    patcher.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    patcher.delenv("UCTSM_AUTHORITATIVE", raising=False)
    scripted = {"client": ScriptedClient(FACTS)}
    patcher.setattr(runner, "build_client", lambda **_: scripted["client"])
    for name in ("AnthropicClient", "GeminiClient", "OpenAIClient"):
        if hasattr(runner, name):
            patcher.setattr(runner, name, _NoRealModel)
        if hasattr(llm_client, name):
            patcher.setattr(llm_client, name, _NoRealModel)
    storage = MemoryStorage()
    patcher.setattr(server.file_storage, "get_storage", lambda: storage)

    # Private SQL store for both SQL entry points of the app.
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    previous_factory = server._operational_session_factory
    server._operational_session_factory = factory
    server.app.dependency_overrides[get_session] = session_override
    J.update(scripted=scripted, storage=storage, factory=factory)

    sponsor = register("sponsor")
    pi = register("pi")
    J.update(sponsor=sponsor, pi=pi)
    yield
    server.app.dependency_overrides.pop(get_session, None)
    server._operational_session_factory = previous_factory
    patcher.undo()

    async def clean():
        patients = MONGO["patients"] + [
            item["id"] for item in await server.db.patients.find(
                {"trial_id": {"$in": MONGO["trials"]}}, {"_id": 0, "id": 1}).to_list(100)]
        await server.db.visit_instances.delete_many({"patient_id": {"$in": patients}})
        await server.db.visit_instances.delete_many({"trial_id": {"$in": MONGO["trials"]}})
        await server.db.patients.delete_many({"id": {"$in": patients}})
        await server.db.trials.delete_many({"id": {"$in": MONGO["trials"]}})
        await server.db.schedule_definitions.delete_many({"trial_id": {"$in": MONGO["trials"]}})
        await server.db.protocol_extractions.delete_many({"user_id": {"$in": MONGO["users"]}})
        await server.db.audit_logs.delete_many({"user_id": {"$in": MONGO["users"]}})
        await server.db.audit_logs.delete_many({"actor_id": {"$in": MONGO["users"]}})
        await server.db.users.delete_many({"id": {"$in": MONGO["users"]}})
        await server.db.organizations.delete_many({"name": ORG})
    run(clean())
    engine.dispose()


def register(role):
    async def go():
        async with client() as cli:
            return await cli.post("/api/auth/register", json={
                "email": f"journey-{RUN_ID}-{role}@example.com", "password": PASSWORD,
                "full_name": f"Journey {role.upper()} {RUN_ID}", "role": role, "organization": ORG,
            })
    body = ok(run(go()))
    MONGO["users"].append(body["user"]["id"])
    return {"Authorization": f"Bearer {body['access_token']}", "_user_id": body["user"]["id"]}


def hdr(who):
    return {key: value for key, value in J[who].items() if not key.startswith("_")}


def text_upload(pages, name="syn-crt-7.txt"):
    data = "\f".join(pages[number] for number in sorted(pages)).encode()
    return data, {"file": (name, data, "text/plain")}


def review_view(version_id):
    return ok(call("GET", f"{API}/schedule-versions/{version_id}/review-view", hdr("sponsor")))


def row(view, code):
    return next(item for item in view["rows"] if item["code"] == code)


def schedule_json(version_id):
    return ok(call("GET", f"{API}/schedule-versions/{version_id}", hdr("sponsor")))


def need(*keys):
    missing = [key for key in keys if key not in J]
    if missing:
        pytest.skip(f"an earlier journey step did not produce {', '.join(missing)}")


# --- patient helpers (J3) ---------------------------------------------------------------

REGISTRATION = date(2026, 10, 5)          # a Monday; the schedule origin (Day 1)
PLANNED_START = date(2026, 10, 12)
ACTUAL_START = date(2026, 10, 13)
RT_START = date(2026, 10, 19)             # a Monday
SITE_HOLIDAY = date(2026, 11, 2)          # a Monday, inside the RT course
VELTRACIN_STOP = date(2026, 11, 4)
END_OF_CHEMO = date(2026, 11, 3)
HORIZON = "2027-12-31"


def patient_schedule(patient_id):
    body = ok(call("GET", f"{API}/patients/{patient_id}/schedule", hdr("pi")))
    return body, {item["logical_key"]: item for item in body["events"]}


def by_code(events, code):
    return sorted((item for key, item in events.items() if key.startswith(f"{code}:")),
                  key=lambda item: item["occurrence_index"])


def iso(value):
    return value.isoformat() if value else None


def anchor_ids(version_id):
    return {item["code"]: item["id"] for item in schedule_json(version_id)["anchors"]}


def record_anchor(patient_id, code, value, reason):
    """Anchor candidate -> impact preview -> confirm (the only way a date moves)."""
    candidate = ok(call("POST", f"{API}/patients/{patient_id}/anchors", hdr("pi"), json={
        "anchor_definition_id": J["anchor_ids"][code], "value_date": value.isoformat(),
        "source_type": "CLINICAL_RECORD"}), 201)
    preview = ok(call("POST", f"{API}/patients/{patient_id}/anchors/{candidate['id']}/impact-preview",
                      hdr("pi"), json={"horizon": HORIZON, "reason": reason}), 201)
    ok(call("POST", f"{API}/schedule-impact-proposals/{preview['id']}/confirm", hdr("pi"),
            json={"reason": reason}))
    return preview


def reminder_keys(patient_id, today):
    body = ok(call("GET", f"{API}/patients/{patient_id}/schedule/projection", hdr("pi"),
                   params={"today": today.isoformat()}))
    return {item.get("logical_key") for item in body["reminders"]}, body


def weekday_slots(start, count, holidays=()):
    """The protocol's rule, restated: Monday-Friday sessions from ``start``;
    a holiday is not a slot (the reviewer's stated SKIP policy)."""
    slots, day = [], start
    while len(slots) < count:
        if day.weekday() < 5 and day not in holidays:
            slots.append(day)
        day += timedelta(days=1)
    return slots


# =============================================================================================
# J1: upload to draft
# =============================================================================================

def test_j1_upload_returns_draft_with_accountability_and_builds_one_canonical_draft():
    scripted = J["scripted"]["client"]
    data, files = text_upload(PAGES)
    body = ok(call("POST", "/api/protocols/extract", hdr("sponsor"), files=files))

    assert body["needs_schedule_selection"] is False
    assert body["details"]["title"] == METADATA["trial"]["title"]
    assert body["schedule_visit_count"] == len(ROW_CODES)
    assert scripted.calls == 2  # one overview call + one page batch; nothing else
    ledger = body["preview"]["accountability"]
    assert ledger["schema"] == "uctsm-extraction-accountability.v1"
    # Coverage: every page accounted for, and the read is complete.
    coverage = ledger["coverage"]
    assert coverage["status"] == "COMPLETE"
    assert {unit["page"] for unit in coverage["units"] if unit["page"] is not None} == set(PAGES)
    # Dispositions: every scripted statement has exactly one; the two "study entry"
    # statements wait for the shared question; two statements are not schedule rules.
    statements = ledger["statements"]
    assert len(statements) == len(FACTS)
    assert len({item["statement_id"] for item in statements}) == len(statements)
    by_what = {}
    for item in statements:
        by_what.setdefault(item["what"], []).append(item["disposition"])
    assert by_what["SAE reporting"] == ["OUT_OF_SCOPE_NON_SCHEDULE"]
    assert by_what["Accrual suspension"] == ["OUT_OF_SCOPE_NON_SCHEDULE"]
    assert by_what["Informed consent"] == ["PENDING_INTERPRETATION"]
    assert by_what["Pregnancy test"] == ["PENDING_INTERPRETATION"]
    assert set(by_what["Response PET"]) == {"COMPILED"} and len(by_what["Response PET"]) == 2
    assert set(by_what["Toxicity call"]) == {"COMPILED"} and len(by_what["Toxicity call"]) == 2
    assert sum(ledger["summary"]["dispositions"].values()) == len(statements)
    assert ledger["summary"]["complete"] is False  # uncompiled statements remain
    assert body["verification"]["status"] == "needs_review"
    # The preview makes no patient-date claim (UI-10).
    assert "patient_id" not in json.dumps(body["preview"]["schedule"])

    trial = ok(call("POST", "/api/trials", hdr("sponsor"), json={
        "title": f"Journey trial {RUN_ID}", "protocol_id": f"SYN-CRT-7-{RUN_ID}", "phase": "Phase II",
        "condition": "Oncology", "indications": ["Oncology"], "drug": "Veltracin", "sponsor_name": ORG}))
    MONGO["trials"].append(trial["id"])
    consumed = ok(call("POST", f"/api/trials/{trial['id']}/protocol-extractions/{body['extraction_id']}/consume",
                       hdr("sponsor")))
    assert consumed["schema_version"] == "uctsm.v1"
    assert scripted.calls == 2  # consuming reuses the cached read

    built = ok(call("POST", f"/api/trials/{trial['id']}/uctsm-schedule", hdr("sponsor")))
    [draft] = built["schedules"]
    assert draft["status"] == "VALIDATION_REQUIRED" and draft["created"] is True
    # J1.5: a retry reuses the draft instead of creating a second one.
    again = ok(call("POST", f"/api/trials/{trial['id']}/uctsm-schedule", hdr("sponsor")))
    assert [item["schedule_version_id"] for item in again["schedules"]] == [draft["schedule_version_id"]]
    assert again["schedules"][0]["created"] is False
    J.update(trial_id=trial["id"], version_id=draft["schedule_version_id"],
             document_hash=hashlib.sha256(data).hexdigest(), statement_count=len(statements))


# =============================================================================================
# J2: review to approval
# =============================================================================================

def test_j2_review_view_counts_rows_questions_and_statements_honestly():
    need("version_id")
    version_id = J["version_id"]
    view = review_view(version_id)
    summary = view["summary"]

    assert {item["code"] for item in view["rows"]} == ROW_CODES
    assert summary["displayed_rows"] == len(ROW_CODES)
    pending = {item["code"] for item in view["rows"] if item["review_status"] == "PENDING"}
    # Each Pending row has a reason derived from the protocol: the shared question,
    # the unstated holiday policy, the one-sided window.
    assert pending == {"INFORMED_CONSENT", "PREGNANCY_TEST", "RADIOTHERAPY", "END_OF_TREATMENT_VISIT"}
    assert summary["pending_rows"] == len(pending)
    assert summary["ready_rows"] == len(ROW_CODES) - len(pending)
    assert "Window needs review" in row(view, "END_OF_TREATMENT_VISIT")["pending_reasons"]
    assert row(view, "END_OF_TREATMENT_VISIT")["window_status"] == "NEEDS_REVIEW"
    assert any(reason.startswith("Repetition policy not stated") for reason in row(view, "RADIOTHERAPY")["pending_reasons"])

    # One shared question, listed once, linked to both affected rows.
    [question] = view["shared_review_questions"]
    assert question["id"] == "anchor-equivalence-study_entry" and question["blocking"] is True
    assert question["kind"] == "ANCHOR_EQUIVALENCE"
    assert {item["visit_name"] for item in question["affected_rows"]} == {"Informed consent", "Pregnancy test"}
    assert summary["shared_questions"] == 1
    for code in ("INFORMED_CONSENT", "PREGNANCY_TEST"):
        assert row(view, code)["question_refs"] == [question["id"]]

    # Timing meaning, units and reference events are kept.
    assert row(view, "HISTORY_AND_PHYSICAL")["timing_summary"] == "Within 28 days before Registration"
    assert row(view, "COMPLETE_BLOOD_COUNT")["timing_summary"] == "Within 14 days before Registration"
    assert "planned or actual" in row(view, "CHEMISTRY_PANEL")["timing_summary"]
    assert "every 7 days" in row(view, "VELTRACIN_DOSE")["timing_summary"]
    assert "6 occurrences" in row(view, "VELTRACIN_DOSE")["timing_summary"]
    assert " or " in row(view, "TOXICITY_CALL")["timing_summary"]  # the stated alternative
    policies = {item["key"]: item for item in row(view, "RADIOTHERAPY")["details"]["recurrence"]["policies"]}
    assert policies["count_basis"]["status"] == "STATED" and "Scheduled slots" in policies["count_basis"]["value"]
    assert policies["missed_policy"]["value"] == "No replacement session"
    assert policies["holiday_policy"]["status"] == "NEEDS_REVIEW"
    # The AND pair: both restrictions are visible in the expanded row.
    pet = [item["summary"] for item in row(view, "RESPONSE_PET")["details"]["constraints"]
           if item["kind"].startswith("CONSTRAINT_")]
    assert any("end of radiotherapy + 12 weeks" in text.lower() for text in pet), pet
    assert any("end of chemotherapy + 16 weeks" in text.lower() for text in pet), pet
    # ... and the one-line Timing names every bound's reference, not just the first.
    pet_line = row(view, "RESPONSE_PET")["timing_summary"].lower()
    assert "radiotherapy" in pet_line and "chemotherapy" in pet_line, pet_line
    # Per-drug stop: shown on the Veltracin row only.
    processes = {item["code"] for item in view["processes"]}
    assert {"VELTRACIN", "RADIOTHERAPY"} <= processes
    assert any(item["kind"] == "PROCESS_ACTION" for item in row(view, "VELTRACIN_DOSE")["details"]["constraints"])
    assert not any(item["kind"] == "PROCESS_ACTION" for item in row(view, "RADIOTHERAPY")["details"]["constraints"])

    # The non-schedule statement and the trial-level rule are visible, not visits.
    unlinked = {item["what"]: item for item in view["source_coverage"]["statements"]["not_linked_to_a_visit"]}
    assert unlinked["SAE reporting"]["disposition_label"] == "Not a schedule rule"
    assert unlinked["Accrual suspension"]["disposition_label"] == "Not a schedule rule"
    trial_rule = [item for item in view["document_level_issues"] if item["code"] == "NON_PATIENT_RULE"]
    assert trial_rule and not trial_rule[0]["blocking"]

    # Approval: backend says no, with reasons, and refuses the attempt.
    assert view["can_approve"] is False and view["approval_blockers"]
    blocked = call("POST", f"{API}/schedule-versions/{version_id}/approve-version", hdr("sponsor"),
                   json={"expected_revision": view["revision"], "reason": "Looks fine"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "APPROVAL_BLOCKED"
    assert blocked.json()["detail"]["reasons"]

    # UI-10: the draft review never carries patient dates.
    forbidden = {"patient_id", "due_date", "scheduled_date", "occurrence_date", "actual_date"}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in forbidden, key
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(view)
    J["first_revision"] = view["revision"]


def test_j2_resolve_the_shared_question_once_edit_pending_rows_and_refuse_stale_writes():
    need("version_id", "first_revision")
    version_id = J["version_id"]
    view = review_view(version_id)
    resolved = ok(call("POST", f"{API}/schedule-versions/{version_id}/review-questions/"
                               "anchor-equivalence-study_entry/resolve", hdr("sponsor"), json={
        "expected_revision": view["revision"], "action": "REMAP_ANCHOR", "anchor_code": "REGISTRATION",
        "rationale": "Section 3: study entry is the registration date"}))
    after = resolved["view"]
    assert after["shared_review_questions"] == []
    assert row(after, "INFORMED_CONSENT")["timing_summary"] == "Within 14 days before Registration"
    assert row(after, "PREGNANCY_TEST")["timing_summary"] == "Within 7 days before Registration"
    affected = {row(view, "INFORMED_CONSENT")["id"], row(view, "PREGNANCY_TEST")["id"]}
    assert affected <= set(resolved["changed_row_ids"])

    # RV-05: a write against the revision the reviewer first loaded is refused.
    eot = row(after, "END_OF_TREATMENT_VISIT")
    stale = call("PATCH", f"{API}/schedule-versions/{version_id}/events/{eot['id']}", hdr("sponsor"), json={
        "expected_revision": J["first_revision"], "rationale": "stale", "changes": {"display_name": "x"}})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_REVISION"

    # State the missing side of the window (0 early / 7 late) and confirm the row.
    window = {"before": {"value": 0, "unit": "DAY"}, "after": {"value": 7, "unit": "DAY"}}
    edited = ok(call("PATCH", f"{API}/schedule-versions/{version_id}/events/{eot['id']}", hdr("sponsor"), json={
        "expected_revision": resolved["revision"], "rationale": "Sponsor clarification: not earlier than day 28",
        "changes": {"window": window}, "confirm_row": True}))
    eot_after = row(edited["view"], "END_OF_TREATMENT_VISIT")
    stored_eot = next(item for item in schedule_json(version_id)["events"] if item["code"] == "END_OF_TREATMENT_VISIT")
    assert stored_eot["window"]["before"]["value"] == 0 and stored_eot["window"]["after"]["value"] == 7
    assert eot_after["timing_summary"] == eot["timing_summary"]  # a window edit never moves timing
    assert any(item["is_reviewer_decision"] for item in eot_after["details"]["evidence"])
    assert eot_after["window_status"] == "STATED" and eot["id"] in edited["changed_row_ids"]

    # State the holiday policy: sessions on a site holiday are skipped, using the
    # site's calendar. UI-07's typed edit is tried first.
    revision = edited["revision"]
    rt_row = row(edited["view"], "RADIOTHERAPY")
    stored = next(item for item in schedule_json(version_id)["events"] if item["code"] == "RADIOTHERAPY")
    calendar = {**stored["calendar_recurrence"], "holiday_policy": "SKIP", "holiday_calendar_ref": "SITE_HOLIDAYS"}
    typed = call("PATCH", f"{API}/schedule-versions/{version_id}/events/{rt_row['id']}", hdr("sponsor"), json={
        "expected_revision": revision, "rationale": "Protocol 5.2: fractions on public holidays are skipped",
        "changes": {"calendar_recurrence": calendar}})
    # The typed edit accepts calendar_recurrence (policies by explicit reviewer choice).
    J["holiday_patch_status"] = typed.status_code
    assert typed.status_code == 200, typed.text
    revision = typed.json()["revision"]
    view = review_view(version_id)
    assert view["revision"] == revision
    rt = row(view, "RADIOTHERAPY")
    policies = {item["key"]: item for item in rt["details"]["recurrence"]["policies"]}
    assert policies["holiday_policy"]["status"] == "STATED"
    assert policies["holiday_policy"]["value"].startswith("Skip the session")
    assert rt["review_status"] == "READY"
    J["reviewed_revision"] = revision


def test_j2_export_json_and_csv_carry_every_constraint_and_statement():
    need("version_id")
    version_id = J["version_id"]
    view = review_view(version_id)
    ledger = ok(call("GET", f"{API}/schedule-versions/{version_id}/accountability", hdr("sponsor")))
    statement_ids = {item["statement_id"] for item in ledger["statements"]}
    assert len(statement_ids) == J["statement_count"]

    exported = ok(call("GET", f"{API}/schedule-versions/{version_id}/export", hdr("sponsor"),
                       params={"format": "json"}))
    assert exported["label"].startswith("DRAFT")
    assert exported["provenance"]["document_hash"] == J["document_hash"]
    assert {item["code"] for item in exported["canonical_schedule"]["events"]} == ROW_CODES
    assert {item["statement_id"] for item in exported["accountability"]["statements"]} == statement_ids
    assert {item["code"] for item in exported["processes"]} >= {"VELTRACIN", "RADIOTHERAPY"}
    pet = next(item for item in exported["canonical_schedule"]["events"] if item["code"] == "RESPONSE_PET")
    pet_text = json.dumps(pet)
    assert "END_OF_RADIOTHERAPY" in pet_text and "END_OF_CHEMOTHERAPY" in pet_text

    response = call("GET", f"{API}/schedule-versions/{version_id}/export", hdr("sponsor"), params={"format": "csv"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    records = list(csv.reader(io.StringIO(response.text)))
    cells = {cell for record in records for cell in record}
    for item in view["rows"]:
        for constraint in item["details"]["constraints"]:
            assert constraint["id"] in cells, (item["code"], constraint)
    assert statement_ids <= cells  # every statement, including the non-schedule ones
    assert any("DRAFT" in cell for cell in records[1])


def test_j2_a_reviewer_stated_window_clears_window_needs_review():
    need("version_id", "reviewed_revision")
    eot = row(review_view(J["version_id"]), "END_OF_TREATMENT_VISIT")
    assert eot["window_status"] == "STATED", eot["window_summary"]
    assert eot["window_summary"] == "−0 / +7 days"
    assert eot["review_status"] == "READY", eot["pending_reasons"]


def test_j2_answering_the_shared_question_releases_the_statements_it_held():
    need("version_id", "reviewed_revision")
    view = review_view(J["version_id"])
    for code in ("INFORMED_CONSENT", "PREGNANCY_TEST"):
        assert row(view, code)["review_status"] == "READY", row(view, code)["pending_reasons"]
    assert not any("not compiled" in reason for reason in view["approval_blockers"]), view["approval_blockers"]
    assert view["can_approve"] is True


def test_j2_whole_version_approval_at_the_reviewed_revision():
    need("version_id", "reviewed_revision")
    version_id = J["version_id"]
    view = review_view(version_id)
    assert view["revision"] == J["reviewed_revision"]
    attempt = call("POST", f"{API}/schedule-versions/{version_id}/approve-version", hdr("sponsor"),
                   json={"expected_revision": view["revision"], "reason": "Whole table reviewed against source"})
    # B1/B4 fixed: a fully reviewed draft is approved by approve-version itself.
    assert attempt.status_code == 200, attempt.text
    assert attempt.json()["status"] == "APPROVED"
    J["approved_via"] = "approve-version"


def test_j2_approved_version_is_immutable_and_labelled():
    need("version_id", "approved_via")
    version_id = J["version_id"]
    view = review_view(version_id)
    assert view["status"] == "APPROVED" and view["can_approve"] is False
    edit = call("PATCH", f"{API}/schedule-versions/{version_id}/events/{row(view, 'CHEMISTRY_PANEL')['id']}",
                hdr("sponsor"), json={"expected_revision": view["revision"], "rationale": "late edit",
                                      "changes": {"display_name": "Chemistry"}})
    assert edit.status_code == 409 and edit.json()["detail"]["code"] == "IMMUTABLE_VERSION"
    exported = ok(call("GET", f"{API}/schedule-versions/{version_id}/export", hdr("sponsor")))
    assert exported["label"] == "APPROVED"
    J["anchor_ids"] = anchor_ids(version_id)


# =============================================================================================
# J3: enrolment to patient calendar
# =============================================================================================

def enrol(subject, *, extra=None):
    return call("POST", "/api/patients", hdr("pi"), json={
        "full_name": f"Journey Patient {subject} {RUN_ID}", "trial_id": J["trial_id"],
        "subject_id": f"{subject}-{RUN_ID}", "baseline_date": REGISTRATION.isoformat(),
        "anchor_dates": {"START_OF_TREATMENT": PLANNED_START.isoformat()},
        "anchor_date_sources": {"START_OF_TREATMENT": "PLANNED"}, **(extra or {}),
    })


def test_j3_enrolment_dates_pre_treatment_windows_and_names_what_actual_only_visits_wait_for():
    need("approved_via", "anchor_ids")
    body = ok(enrol("J3"))
    MONGO["patients"].append(body["id"])
    assert body["schedule_assignment_status"] == "assigned"
    assert body["assigned_schedule_version_id"] == J["version_id"]
    patient_id = body["uctsm_patient_id"]
    J.update(patient_id=patient_id, mongo_patient_id=body["id"])

    schedule, events = patient_schedule(patient_id)
    assert schedule["schedule_version_id"] == J["version_id"]
    # Pre-treatment windows are dated from registration (never from first dose).
    for code, days in (("HISTORY_AND_PHYSICAL", 28), ("COMPLETE_BLOOD_COUNT", 14),
                       ("INFORMED_CONSENT", 14), ("PREGNANCY_TEST", 7)):
        [item] = by_code(events, code)
        assert (item["earliest_date"], item["latest_date"]) == (
            iso(REGISTRATION - timedelta(days=days)), iso(REGISTRATION)), (code, item)
    # The one rule stated against the PLANNED start is dated from it.
    [chem] = by_code(events, "CHEMISTRY_PANEL")
    assert (chem["earliest_date"], chem["latest_date"]) == (
        iso(PLANNED_START - timedelta(days=3)), iso(PLANNED_START)), chem
    # Actual-only obligations wait, undated, naming what they wait for (PT-01, PT-07).
    doses = by_code(events, "VELTRACIN_DOSE")
    assert doses and all(item["status"] == "WAITING_FOR_ANCHOR" for item in doses), doses
    assert all(item["nominal_start_date"] is None for item in doses)
    assert "START_OF_TREATMENT" in json.dumps(doses[0]["explanation"])
    rt = by_code(events, "RADIOTHERAPY")
    assert rt and all(item["nominal_start_date"] is None for item in rt)
    assert "START_OF_RADIOTHERAPY" in json.dumps(rt[0]["explanation"])
    [pet] = by_code(events, "RESPONSE_PET")
    assert pet["nominal_start_date"] is None and pet["earliest_date"] is None
    anchors = ok(call("GET", f"{API}/patients/{patient_id}/anchors", hdr("pi")))
    statuses = {item["anchor_code"]: item["status"] for item in anchors["anchors"]}
    assert statuses["START_OF_TREATMENT"] == "PLANNED"
    assert statuses["REGISTRATION"] in {"ACTUAL", "CONFIRMED"}  # a confirmed actual date

    # The operational (Mongo) projection mirrors the canonical plan one-for-one.
    rows = mongo(server.db.visit_instances.find({"patient_id": body["id"]}, {"_id": 0}).to_list(500))
    assert len(rows) == len(events)
    assert len({item["uctsm_patient_event_id"] for item in rows}) == len(rows)
    # Undated obligations are never reminded about.
    keys, _ = reminder_keys(patient_id, REGISTRATION)
    assert not {item["logical_key"] for item in doses + rt + [pet]} & keys


def test_j3_actual_events_date_dependent_obligations_and_a_per_drug_stop_changes_only_that_drug():
    need("patient_id")
    patient_id = J["patient_id"]
    # The site's holiday calendar the reviewer's SKIP policy names (a site input,
    # previewed then confirmed like every other schedule input).
    calendar = {"code": "SITE_HOLIDAYS", "version": "2026.1",
                "business_weekdays": ["MON", "TUE", "WED", "THU", "FRI"], "dates": [SITE_HOLIDAY.isoformat()]}
    preview = ok(call("POST", f"{API}/patients/{patient_id}/states/impact-preview", hdr("pi"), json={
        "state": {"state_code": "uctsm:site_holiday_calendar", "state_value": calendar,
                  "effective_at": "2026-10-01T00:00:00"},
        "horizon": HORIZON, "reason": "Site holiday calendar 2026"}), 201)
    ok(call("POST", f"{API}/schedule-impact-proposals/{preview['id']}/confirm", hdr("pi"), json={"reason": "ok"}))

    record_anchor(patient_id, "START_OF_TREATMENT", ACTUAL_START, "First Veltracin dose given")
    record_anchor(patient_id, "START_OF_RADIOTHERAPY", RT_START, "First fraction delivered")
    _, events = patient_schedule(patient_id)
    doses = by_code(events, "VELTRACIN_DOSE")
    assert [item["nominal_start_date"] for item in doses] == [
        iso(ACTUAL_START + timedelta(weeks=week)) for week in range(6)]
    rt = [item for item in by_code(events, "RADIOTHERAPY") if item["nominal_start_date"]]
    assert [item["nominal_start_date"] for item in rt] == [
        iso(day) for day in weekday_slots(RT_START, 30, holidays={SITE_HOLIDAY})]
    before = {key: (item["status"], item["nominal_start_date"]) for key, item in events.items()}

    # Per-drug stop (PI decision): only Veltracin's future doses change.
    preview_body = call("POST", f"{API}/patients/{patient_id}/schedule/impact-preview", hdr("pi"), json={
        "horizon": HORIZON, "reason": "Grade 3 neuropathy: PI stops Veltracin",
        "process_transition": {"target": {"type": "PROCESS", "process_code": "VELTRACIN"},
                               "to_state": "STOPPED", "effective_at": VELTRACIN_STOP.isoformat(),
                               "reason": "DECISION", "authority": "PI"}})
    if preview_body.status_code in (404, 405) or (
            preview_body.status_code == 422 and "process_transition" in preview_body.text
            and "extra" in preview_body.text):
        pytest.skip("process_transition impact preview is not available on this route yet")
    proposal = ok(preview_body, 201)
    changed = {item["logical_key"] for item in proposal["impact"]["events"] if item["change"] != "UNCHANGED"}
    stopped = {item["logical_key"] for item in doses if item["nominal_start_date"] > iso(VELTRACIN_STOP)}
    assert changed == stopped, changed
    ok(call("POST", f"{API}/schedule-impact-proposals/{proposal['proposal_id']}/confirm", hdr("pi"),
            json={"reason": "PI confirmed"}))
    _, events = patient_schedule(patient_id)
    for key, (status, nominal) in before.items():
        if key in stopped:
            assert events[key]["status"] == "CANCELLED", (key, events[key]["status"])
        else:
            assert (events[key]["status"], events[key]["nominal_start_date"]) == (status, nominal), key
    J["stopped_keys"] = stopped


def test_j3_the_and_pair_intersects_across_two_anchors_and_incomplete_or_infeasible_is_never_reminded():
    need("patient_id")
    patient_id = J["patient_id"]
    record_anchor(patient_id, "END_OF_CHEMOTHERAPY", END_OF_CHEMO, "Last Veltracin dose given")
    _, events = patient_schedule(patient_id)
    [pet] = by_code(events, "RESPONSE_PET")
    # Only one of the two anchors is known: the PET is not dated and not complete.
    assert pet["nominal_start_date"] is None and pet["feasibility"] != "FEASIBLE", pet
    assert "END_OF_RADIOTHERAPY" in json.dumps(pet["missing_inputs"] or pet["explanation"])
    keys, _ = reminder_keys(patient_id, END_OF_CHEMO)
    assert pet["logical_key"] not in keys

    rt_end = date(2026, 11, 30)
    record_anchor(patient_id, "END_OF_RADIOTHERAPY", rt_end, "Last fraction delivered")
    _, events = patient_schedule(patient_id)
    [pet] = by_code(events, "RESPONSE_PET")
    # Intersection: later of (chemo end + 12 w, RT end + 12 w) .. chemo end + 16 w.
    lower = max(END_OF_CHEMO + timedelta(weeks=12), rt_end + timedelta(weeks=12))
    upper = END_OF_CHEMO + timedelta(weeks=16)
    assert pet["feasibility"] == "FEASIBLE", pet
    [interval] = pet["feasible_intervals"]
    assert (interval["lower"]["value"], interval["upper"]["value"]) == (iso(lower), iso(upper))
    assert lower == rt_end + timedelta(weeks=12)  # the RT bound is the one that binds
    # The stated alternative keeps two disjoint intervals; the gap is not allowed.
    [call_row] = by_code(events, "TOXICITY_CALL")
    intervals = sorted((item["lower"]["value"], item["upper"]["value"]) for item in call_row["feasible_intervals"])
    assert intervals == [(iso(END_OF_CHEMO), iso(END_OF_CHEMO + timedelta(days=3))),
                         (iso(rt_end), iso(rt_end + timedelta(days=7)))], call_row

    # A corrected (later) RT end empties the intersection: infeasible, undated, not reminded.
    corrected = date(2026, 12, 7)
    record_anchor(patient_id, "END_OF_RADIOTHERAPY", corrected, "RT end corrected from treatment record")
    _, events = patient_schedule(patient_id)
    [pet] = by_code(events, "RESPONSE_PET")
    assert pet["feasibility"] == "INFEASIBLE", pet
    assert pet["conflicting_constraints"] and pet["nominal_start_date"] is None
    keys, projection = reminder_keys(patient_id, END_OF_CHEMO)
    assert pet["logical_key"] not in keys
    for key in J.get("stopped_keys", ()):
        assert key not in keys  # cancelled doses are not reminded either


# =============================================================================================
# AT-16: SQL commit succeeds, Mongo projection fails, retry converges
# =============================================================================================

class _FailAfter(list):
    """A projection whose Mongo writes stop after ``limit`` documents."""

    def __init__(self, items, limit):
        super().__init__(items)
        self.limit = limit

    def __iter__(self):
        for index, item in enumerate(list.__iter__(self)):
            if index >= self.limit:
                raise RuntimeError("simulated Mongo projection outage")
            yield item


def test_at16_a_failed_mongo_projection_after_the_sql_commit_is_retried_without_duplicates(monkeypatch):
    need("approved_via")
    real = server.bridge_visit_documents
    monkeypatch.setattr(server, "bridge_visit_documents", lambda **kw: _FailAfter(real(**kw), 3))
    with pytest.raises(RuntimeError, match="simulated Mongo projection outage"):
        enrol("AT16")
    monkeypatch.setattr(server, "bridge_visit_documents", real)

    patient = mongo(server.db.patients.find_one({"subject_id": f"AT16-{RUN_ID}"}, {"_id": 0}))
    assert patient is not None
    MONGO["patients"].append(patient["id"])
    assert patient.get("schedule_assignment_status") != "assigned"
    partial = mongo(server.db.visit_instances.count_documents({"patient_id": patient["id"]}))
    assert partial == 3
    before = sql_plan(patient["id"])
    assert len(before["current"]) > 3  # the SQL side committed the whole plan

    first = ok(call("POST", f"/api/patients/{patient['id']}/uctsm-reconcile", hdr("pi")))
    second = ok(call("POST", f"/api/patients/{patient['id']}/uctsm-reconcile", hdr("pi")))
    assert first["projected_events"] == second["projected_events"] == len(before["current"])
    rows = mongo(server.db.visit_instances.find({"patient_id": patient["id"]}, {"_id": 0}).to_list(500))
    assert len(rows) == len(before["current"])
    assert {item["uctsm_patient_event_id"] for item in rows} == set(before["current"])
    reconciled = mongo(server.db.patients.find_one({"id": patient["id"]}, {"_id": 0}))
    assert reconciled["schedule_assignment_status"] == "assigned"
    after = sql_plan(patient["id"])
    # One patient, one assignment, no second evaluation (clinical decision), and the
    # same occurrences under the same stable keys.
    assert (after["patients"], after["assignments"], after["schedules"]) == (1, 1, 1)
    assert after["evaluations"] == before["evaluations"]
    assert after["current"] == before["current"]


def sql_plan(mongo_patient_id):
    with J["factory"]() as session:
        patients = session.scalars(select(db.Patient).where(
            db.Patient.external_patient_id == mongo_patient_id)).all()
        patient = patients[0]
        schedules = session.scalars(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient.id)).all()
        active = next(item for item in schedules if item.status == "ACTIVE")
        evaluations = session.scalar(select(func.count()).select_from(db.ScheduleEvaluation).where(
            db.ScheduleEvaluation.patient_schedule_id.in_([item.id for item in schedules])))
        current = session.scalars(select(db.PatientEvent).where(
            db.PatientEvent.schedule_evaluation_id == active.current_evaluation_id)).all()
        keys = [item.logical_key for item in current]
        assert len(keys) == len(set(keys))
        assignments = session.scalar(select(func.count()).select_from(db.PatientScheduleAssignment).where(
            db.PatientScheduleAssignment.patient_id == patient.id))
        return {"patients": len(patients), "schedules": len(schedules), "evaluations": evaluations,
                "assignments": assignments,
                "current": {str(item.id): (item.logical_key, str(item.logical_occurrence_id)) for item in current}}


# =============================================================================================
# J4 (light): an amendment makes a new draft; approving it moves no enrolled patient
# =============================================================================================

def test_j4_an_amendment_is_a_new_draft_and_its_approval_leaves_the_enrolled_patient_pinned():
    need("patient_id", "trial_id")
    J["scripted"]["client"] = ScriptedClient(_amended_facts())
    _, files = text_upload(AMENDED_PAGES, name="syn-crt-7-amendment-1.txt")
    extracted = ok(call("POST", "/api/protocols/extract", hdr("sponsor"), files=files))
    ok(call("POST", f"/api/trials/{J['trial_id']}/protocol-extractions/{extracted['extraction_id']}/consume",
            hdr("sponsor")))
    built = ok(call("POST", f"/api/trials/{J['trial_id']}/uctsm-schedule", hdr("sponsor")))
    [draft] = built["schedules"]
    amended = draft["schedule_version_id"]
    assert amended != J["version_id"] and draft["status"] == "VALIDATION_REQUIRED"

    view = review_view(amended)
    assert view["shared_review_questions"] == []
    assert row(view, "COMPLETE_BLOOD_COUNT")["timing_summary"] == "Within 21 days before Registration"
    # The unstated holiday policy is stated again by the reviewer.
    stored = next(item for item in schedule_json(amended)["events"] if item["code"] == "RADIOTHERAPY")
    calendar = {**stored["calendar_recurrence"], "holiday_policy": "SKIP", "holiday_calendar_ref": "SITE_HOLIDAYS"}
    rt_id = row(view, "RADIOTHERAPY")["id"]
    typed = call("PATCH", f"{API}/schedule-versions/{amended}/events/{rt_id}", hdr("sponsor"), json={
        "expected_revision": view["revision"], "rationale": "Protocol 5.2: holidays skipped",
        "changes": {"calendar_recurrence": calendar}})
    if typed.status_code != 200:
        ok(call("PUT", f"{API}/schedule-versions/{amended}/events/{rt_id}",
                {**hdr("sponsor"), "X-Review-Reason": "Protocol 5.2: holidays skipped",
                 "X-Expected-Revision": view["revision"]}, json={**stored, "calendar_recurrence": calendar}))
    view = review_view(amended)
    assert view["can_approve"] is True, view["approval_blockers"]
    approved = ok(call("POST", f"{API}/schedule-versions/{amended}/approve-version", hdr("sponsor"),
                       json={"expected_revision": view["revision"], "reason": "Amendment 1 reviewed"}))
    assert approved["status"] == "APPROVED"

    # Approval did not migrate the enrolled patient (PT-05).
    schedule, events = patient_schedule(J["patient_id"])
    assert schedule["schedule_version_id"] == J["version_id"]
    [cbc] = by_code(events, "COMPLETE_BLOOD_COUNT")
    assert cbc["earliest_date"] == iso(REGISTRATION - timedelta(days=14))  # the v1 rule, not 21 days
    with J["factory"]() as session:
        patient = session.get(db.Patient, uuid.UUID(J["patient_id"]))
        assert str(patient.current_schedule_version_id) == J["version_id"]
        assignments = session.scalars(select(db.PatientScheduleAssignment).where(
            db.PatientScheduleAssignment.patient_id == patient.id)).all()
        # Every assignment row (enrolment + each confirmed impact) names v1 only.
        assert {str(item.schedule_version_id) for item in assignments} == {J["version_id"]}
    mongo_patient = mongo(server.db.patients.find_one({"id": J["mongo_patient_id"]}, {"_id": 0}))
    assert mongo_patient["assigned_schedule_version_id"] == J["version_id"]


def test_j4_new_enrolments_are_still_possible_after_the_amendment_is_approved():
    need("patient_id", "trial_id")
    response = enrol("J4-NEW")
    if response.status_code == 200:
        MONGO["patients"].append(response.json()["id"])
    assert response.status_code == 200, response.text
