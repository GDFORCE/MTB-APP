"""Upload the real NCT02130024 (RIVAL) protocol PDF through the app with NO model provider.

Offline mode (``KIT_OFFLINE=1``, app/kit/offline.py): the PDF is read by the app's own reader; the
three model requests are answered from app/kit/offline_answers/nct02130024.model.json, which the
coding assistant wrote from the PDF (pages 8, 19, 24-37). Any attempt to build a provider client
fails the test. Real server.app, local MongoDB replica set, real sign-in and OTP registration.

Needs D:/NCT02130024__Prot_000.pdf (not in the repository) and the local replica set; skips otherwise.
Expected dates are worked out by hand from the protocol's rules (section 5.5.4.1, Table 6-1).
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

import server  # noqa: I001 - server first: it imports kit_routes at its end
import kit_routes
from _kit_calendar import calendar, calendars

PDF = Path("D:/NCT02130024__Prot_000.pdf")
if not PDF.exists():
    pytest.skip("the NCT02130024 protocol PDF is not on this machine", allow_module_level=True)
if not any(host in os.environ.get("MONGO_URL", "") for host in ("127.0.0.1", "localhost")):
    pytest.skip("runs only against the local MongoDB replica set (set MONGO_URL)", allow_module_level=True)

RUN = uuid.uuid4().hex[:8]
LOOP = asyncio.new_event_loop()
PASSWORD = "Password1!"
J: dict = {}
OTP: list = []
RESOLUTIONS = {  # the reviewer's confirmed interpretations of the seven points the analysis raised
    "loading_windows": "Accepted: staff keep Visits 3 and 4 within +/-3 days.",
    "breakpoint_reading": "Confirmed with the medical monitor: only activity at intervals reached by an extension counts.",
    "crc_recommendation": "Confirmed: book on the calculated interval, replace it with the reading centre's interval when it arrives.",
    "interval_basis": "Accepted: count from the recorded visit date.",
    "m12_combined": "Confirmed: a due injection is given at the Month 12 visit.",
    "vegf_visits": "Accepted for this pilot: VEGF visits on Days 35 and 63 +/-3.",
    "dose_withheld": "Accepted: withheld doses are noted by staff.",
}


def run(coro):
    return LOOP.run_until_complete(coro)


def call(method, path, who=None, **kwargs):
    headers = {"Authorization": f"Bearer {J[who]['token']}"} if who else {}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver") as cli:
            return await cli.request(method, path, headers=headers, timeout=300, **kwargs)
    return run(go())


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:1500]}"
    return response.json()


class NoProvider:
    def __init__(self, *args, **kwargs):
        raise AssertionError("offline mode must never build a model provider client")


@pytest.fixture(scope="module", autouse=True)
def world():
    patch = pytest.MonkeyPatch()
    patch.setenv("KIT_OFFLINE", "1")
    patch.delenv("KIT_OFFLINE_DIR", raising=False)
    patch.delenv("KIT_SAMPLE_MODEL", raising=False)
    patch.delenv("UCTSM_AUTHORITATIVE", raising=False)
    patch.setattr(kit_routes, "OpenAIKitClient", NoProvider)

    async def capture(channel, target, code, **_):
        OTP.append(code)

    async def no_throttle(*_a, **_k):
        return None
    patch.setattr(server, "_deliver_otp", capture)
    patch.setattr(server, "_enforce_rate_limit", no_throttle)
    orgs = []
    for who, role, org in (("sponsor", "sponsor", f"RIVAL Sponsor {RUN}"), ("pi", "pi", f"RIVAL Site {RUN}"),
                           ("crc", "crc", f"RIVAL Site {RUN}")):
        body = ok(call("POST", "/api/auth/register", json={
            "email": f"rival-{RUN}-{who}@example.com", "password": PASSWORD, "full_name": f"RIVAL {who} {RUN}",
            "role": role, "organization": org}))
        J[who] = {"token": body["access_token"], "id": body["user"]["id"], "email": body["user"]["email"]}
        orgs.append(org)
    J["trial"] = f"rival-offline-{RUN}"

    async def trial():
        await server.db.trials.insert_one({"id": J["trial"], "title": f"RIVAL offline {RUN}", "protocol_id": "NCT02130024",
                                           "created_by": J["sponsor"]["id"], "sponsor_name": orgs[0], "created_at": server.now()})
        for who in ("pi", "crc"):
            await server.db.invitations.insert_one({"id": f"rival-team-{RUN}-{who}", "trial_id": J["trial"], "status": "accepted",
                                                    "email": J[who]["email"], "role": who, "created_at": server.now()})
    run(trial())
    yield
    patch.undo()

    async def clean():
        t = [J["trial"]]
        users = [J[w]["id"] for w in ("sponsor", "pi", "crc")] + [
            p["user_id"] async for p in server.db.patients.find({"trial_id": {"$in": t}}, {"_id": 0, "user_id": 1}) if p.get("user_id")]
        files = [f["_id"] async for f in server.db["kit_protocol_files.files"].find({"metadata.trial_id": {"$in": t}}, {"_id": 1})]
        await server.db["kit_protocol_files.chunks"].delete_many({"files_id": {"$in": files}})
        for name, query in (("kit_protocol_files.files", {"metadata.trial_id": {"$in": t}}), ("patients", {"trial_id": {"$in": t}}),
                            ("invitations", {"trial_id": {"$in": t}}), ("trial_kit_models", {"trial_id": {"$in": t}}),
                            ("trial_kit_model_history", {"trial_id": {"$in": t}}), ("trial_kit_drafts", {"_id": {"$in": t}}),
                            ("kit_study_events", {"trial_id": {"$in": t}}), ("kit_study_effects", {"trial_id": {"$in": t}}),
                            ("notifications", {"trial_id": {"$in": t}}), ("trials", {"id": {"$in": t}}),
                            ("audit_logs", {"user_id": {"$in": users}}), ("audit_logs", {"trial_id": {"$in": t}}),
                            ("refresh_tokens", {"user_id": {"$in": users}}), ("users", {"id": {"$in": users}}),
                            ("organizations", {"name": {"$in": orgs}})):
            await server.db[name].delete_many(query)
    run(clean())


def test_upload_the_real_pdf_offline_and_review_what_it_found():
    job = ok(call("POST", "/api/kit/analyze", "sponsor", files={"file": (PDF.name, PDF.read_bytes(), "application/pdf")}))
    for _ in range(600):
        state = ok(call("GET", f"/api/kit/jobs/{job['job_id']}", "sponsor"))
        if state["status"] != "running":
            break
        time.sleep(0.1)
    assert state["status"] == "done", state
    assert state["model_name"] == "offline:NCT02130024"
    stages = {s["id"]: s["detail"] for s in state["stages"]}
    assert stages["read"].startswith("51 pages") and stages["locate"] == "tables on p. 32"
    result = state["result"]
    assert result["details"]["protocol_number"] == "CRFB002AAU17"
    base = result["baseModel"]
    adaptive = next(v for v in base["visits"] if v["id"] == "inj")["repeat"]["adaptive"]
    assert "blocked" not in adaptive and adaptive["precedence"] == "recommendation_if_given"
    assert base["study_events"][0]["id"] == "study_terminated" and "blocked" not in base["study_events"][0]
    points = {p["id"]: p for p in base["open_points"]}
    assert set(points) == set(RESOLUTIONS)
    assert all(p["found"] for p in points.values()), "every quoted passage is in the PDF text"
    body = {"baseModel": base, "reviewAnswers": result["reviewAnswers"], "analysisJobId": job["job_id"],
            "fileSha256": result["fileSha256"], "fileName": PDF.name, "expectedVersion": 0}
    preview = ok(call("POST", "/api/kit/workflow-preview", "sponsor", json=body))
    assert len(preview["problems"]) == 7 and not preview["workflow"]["errors"]
    refused = call("PUT", f"/api/trials/{J['trial']}/kit-model", "sponsor",
                   json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert refused.status_code == 422, "the interpretations must be recorded before approval"
    body["resolutions"] = RESOLUTIONS
    preview = ok(call("POST", "/api/kit/workflow-preview", "sponsor", json=body))
    assert not preview["problems"]
    inj = next(b for b in preview["workflow"]["blocks"] if b["id"] == "visit:inj")
    assert any("reached by an extension" in line for line in inj["summary"])
    saved = ok(call("PUT", f"/api/trials/{J['trial']}/kit-model", "sponsor",
                    json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]}))
    assert saved["version"] == 1 and saved["file_stored"] and saved["blockers"] == []
    downloaded = call("GET", f"/api/trials/{J['trial']}/kit-model/file", "sponsor")
    assert downloaded.content == PDF.read_bytes()


def test_enrol_record_findings_and_follow_the_inject_and_extend_rules():
    phone = f"+1202{int(uuid.uuid4().int % 10_000_000):07d}"
    invite = ok(call("POST", "/api/patients/invite", "pi", json={
        "full_name": "Synthetic RIVAL participant", "phone": phone, "trial_id": J["trial"], "subject_id": "0001-00001",
        "crc_id": J["crc"]["id"], "kit_model_version": 1,
        "kit": {"startDate": "2026-01-05", "answers": {"wocbp": "no", "@screening": "2025-12-29"}}}))
    OTP.clear()
    reg = ok(call("POST", "/api/auth/register/start", json={"full_name": "Synthetic RIVAL participant", "role": "patient", "phone": phone,
                                                         "invite_token": invite["token"], "profile": {"dob": "1950-01-01", "gender": "Male"}}))
    ok(call("POST", "/api/auth/register/verify", json={"registration_id": reg["registration_id"], "phone_otp": OTP[-1]}))
    ok(call("POST", "/api/auth/register/security-questions", json={"registration_id": reg["registration_id"], "security_questions": [
        {"question": "Q1?", "answer": "A one"}, {"question": "Q2?", "answer": "A two"}, {"question": "Q3?", "answer": "A three"}]}))
    session = ok(call("POST", "/api/auth/register/complete", json={"registration_id": reg["registration_id"], "password": PASSWORD}))
    J["patient"] = {"token": session["access_token"]}
    pid = run(server.db.patients.find_one({"trial_id": J["trial"], "subject_id": "0001-00001"}, {"_id": 0}))["id"]
    J["pid"] = pid
    fresh = calendar(ok(call("GET", f"/api/patients/{pid}/kit", "pi")))["staff"]
    # Table 6-1 grid from Baseline Monday 5 Jan 2026: Weeks 4 and 8, the VEGF visits on Days 35 and 63, Months 12 and 24 (+/-6).
    assert [fresh[k]["planned"] for k in ("inj#1", "inj#2", "inj#3", "vegf_w5#1", "vegf_w9#1")] == \
        ["2026-01-05", "2026-02-02", "2026-03-02", "2026-02-09", "2026-03-09"]
    assert (fresh["m12#1"]["planned"], fresh["m12#1"]["windowStart"], fresh["m12#1"]["windowEnd"]) == ("2027-01-04", "2026-12-29", "2027-01-10")
    assert (fresh["m24#1"]["planned"], fresh["m24#1"]["windowStart"], fresh["m24#1"]["windowEnd"]) == ("2028-01-03", "2027-12-28", "2028-01-09")
    assert fresh["inj#4"]["status"] == "waiting" and fresh["inj#4"]["needs"][0]["input"] == "activity_signs"

    current = ok(call("GET", f"/api/patients/{pid}/kit", "pi"))
    record = copy.deepcopy(current["participant"])
    record["actuals"].update({"inj#1": "2026-01-05", "inj#2": "2026-02-02", "vegf_w5#1": "2026-02-09", "inj#3": "2026-03-02",
                              "vegf_w9#1": "2026-03-09", "inj#4": "2026-04-13", "inj#5": "2026-06-08"})
    record["observations"] = {"inj#3": {"activity_signs": {"value": 0}}, "inj#4": {"activity_signs": {"value": 0}},
                              "inj#5": {"activity_signs": {"value": 0}}}
    ok(call("PUT", f"/api/patients/{pid}/kit?revision={current['revision']}", "pi", json=record))
    staff = calendar(ok(call("GET", f"/api/patients/{pid}/kit", "pi")))["staff"]
    # No sign at Week 8: 6 weeks; no sign again: 8 weeks; no sign at 8 weeks: 10 weeks, booked provisionally.
    assert [staff[f"inj#{n}"]["adaptive"]["intervalDays"] for n in (4, 5, 6)] == [42, 56, 70]
    assert staff["inj#6"]["planned"] == "2026-08-17" and staff["inj#6"]["adaptive"].get("provisional") is True
    # The reading centre recommends 8 weeks: the protocol says the investigator adjusts to it.
    ok(call("POST", f"/api/patients/{pid}/kit/observations", "crc", json={
        "id": f"crc-{RUN}-tv5", "key": "inj#5", "input": "crc_weeks", "value": 8, "confirmed_at": "2026-06-12T09:00:00Z"}))
    staff = calendar(ok(call("GET", f"/api/patients/{pid}/kit", "pi")))["staff"]
    assert (staff["inj#6"]["planned"], staff["inj#6"]["adaptive"]["intervalDays"]) == ("2026-08-03", 56)
    assert "provisional" not in staff["inj#6"]["adaptive"]
    mine = calendar(ok(call("GET", "/api/me/kit", "patient")))["patient"]
    assert mine["inj#6"]["target_date"] == "2026-08-03" and mine["m12#1"]["target_date"] == "2027-01-04"
    J["record"] = ok(call("GET", f"/api/patients/{pid}/kit", "pi"))


def _what_if(steps: dict[int, tuple[str, int | None]]) -> dict:
    """The approved model as served, with a hypothetical injection history (dates, findings)."""
    payload = copy.deepcopy(J["record"])
    p = payload["participant"]
    p["actuals"] = {f"inj#{n}": d for n, (d, _) in steps.items()}
    p["observations"] = {f"inj#{n}": {"activity_signs": {"value": f}} for n, (_, f) in steps.items() if f is not None}
    return payload


def test_the_break_point_and_month_12_on_the_approved_model():
    loading = {1: ("2026-01-05", None), 2: ("2026-02-02", None)}
    cases = calendars({
        # Activity at 10 weeks (an extended interval), then the 4th extension: capped at 8 weeks (page 27).
        "capped": _what_if({**loading, 3: ("2026-03-02", 0), 4: ("2026-04-13", 0), 5: ("2026-06-08", 0), 6: ("2026-08-17", 1),
                            7: ("2026-10-12", 0), 8: ("2026-12-07", 2)}),
        # Activity only during the 4-weekly loading phase: under the confirmed reading it is not a break point.
        "loading_activity": _what_if({**loading, 3: ("2026-03-02", 1), 4: ("2026-03-30", 0), 5: ("2026-05-11", 0),
                                      6: ("2026-07-06", 0), 7: ("2026-09-14", 0)}),
    })
    capped = cases["capped"]["staff"]
    assert capped["inj#7"]["adaptive"]["intervalDays"] == 56, "one sign at 10 weeks: back to 8"
    assert (capped["inj#8"]["adaptive"]["calculatedDays"], capped["inj#8"]["adaptive"]["capDays"]) == (56, 56)
    # Two signs at TV8: back to 4-weekly, due 4 Jan 2027 = inside the Month 12 window, so given at Month 12.
    combined = capped["inj#9"]
    assert combined["planned"] == "2027-01-04" and [c["key"] for c in combined["combined"]] == ["m12#1"]
    assert "m12#1" not in capped
    names = [p["name"] for p in combined["procedures"]]
    assert "Intravitreal injection (study eye)" in names and "Nerve fibre analysis" in names and len(names) == len(set(names))
    loading_activity = cases["loading_activity"]["staff"]
    assert loading_activity["inj#8"]["adaptive"]["intervalDays"] == 84
    assert loading_activity["inj#8"]["adaptive"]["capDays"] is None


def test_novartis_terminates_the_study_and_the_site_books_the_early_discontinuation():
    pid = J["pid"]
    assert call("POST", f"/api/trials/{J['trial']}/kit-study-events", "pi",
                json={"id": f"term-{RUN}", "type": "study_terminated", "effective_date": "2026-07-20"}).status_code == 403
    view = ok(call("POST", f"/api/trials/{J['trial']}/kit-study-events", "sponsor",
                   json={"id": f"term-{RUN}", "type": "study_terminated", "effective_date": "2026-07-20"}))
    [effect] = view["effects"]
    assert effect["state"] == "applied" and effect["phase"] == "on_study"
    staff = calendar(ok(call("GET", f"/api/patients/{pid}/kit", "pi")))["staff"]
    assert not any(k in staff for k in ("inj#6", "m12#1", "m24#1"))
    assert staff["inj#5"]["actual"] == "2026-06-08" and staff["vegf_w9#1"]["actual"] == "2026-03-09"
    task = f"se.term-{RUN}.ed_term#1"
    assert staff[task]["status"] == "waiting"
    ok(call("PUT", f"/api/patients/{pid}/kit/study-effects/{effect['id']}/booking", "crc", json={"date": "2026-07-27"}))
    mine = calendar(ok(call("GET", "/api/me/kit", "patient")))["patient"]
    assert mine[task]["scheduled_date"] == "2026-07-27"
    assert "Fluorescein angiography (both eyes)" in [p["label"] for p in mine[task]["procedures"]]
