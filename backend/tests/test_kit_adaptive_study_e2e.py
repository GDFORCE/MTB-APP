"""End to end on a real database: workflow approval, enrolment through patient registration, inputs
recorded by site staff, confirmed study-wide events across three sites, and the calendars staff and
patients fetch (computed by the app's own engine and patient consumer, tests/_kit_calendar.py).

Runs the real ``server.app`` (real sign-in tokens, real trial and patient access checks, GridFS) on
the LOCAL MongoDB replica set only; it refuses a remote cluster. The model provider is scripted
(controlled responses); OTP delivery is captured instead of sent. Everything it creates is tagged
with this run's id and deleted at the end.

    set MONGO_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0 & set DB_NAME=mtb_kit_e2e
    python -m pytest -q -p no:cacheprovider tests/test_kit_adaptive_study_e2e.py

Scenarios: C7 (both operations from upload to the patient's own calendar) and B1-B9 on the
synthetic P16-style closeout (tests/fixtures/kit_synthetic/p16_study_closeout.raw.json, assumptions
written there). Expected dates and audiences below are worked out by hand from the fixture.

Synthetic participants (randomization R; Visit 6 = R+84, Visit 7 = R+91, Visit 8 = R+259):
  site A  A-101 extension    R 2026-03-02, V7 2026-06-01        -> early closeout
          A-102 finished     R 2025-11-03, V8 2026-07-20        -> excluded
          A-103 treatment    R 2026-08-03                       -> excluded
          A-104 version 1    R 2026-03-02, V7 2026-06-01, pinned to workflow v1 -> not matched
  site B  B-201 extension    R 2026-02-02, V7 2026-05-04        -> early closeout
          B-202 withdrawn    R 2026-03-02, withdrawal 2026-08-03 -> excluded
          B-203 safety       R 2026-06-15, V6 2026-09-07, V7 2026-09-16 (after the event) -> listed for review
          B-204 done         R 2026-01-05, V7 2026-04-06, V8 2026-09-21 (after the event, before processing) -> already completed
  site C  C-301 final        R 2026-06-22, V6 2026-09-14 (the event)  -> Visit 7 kept, Visit 8 superseded
          C-302 extension    R 2026-04-06, V7 2026-07-06        -> early closeout
          C-303 missing data R 2026-05-04, V6 2026-07-27, V7 unrecorded (window closed 2026-08-06) -> to resolve
Effective date of "final participant completed Visit 6": 2026-09-14, confirmed on the test day.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

import server  # noqa: I001 - server first: it imports kit_routes at its end
import kit_routes
from _kit_calendar import calendar

if not any(host in os.environ.get("MONGO_URL", "") for host in ("127.0.0.1", "localhost")):
    pytest.skip("runs only against the local MongoDB replica set (set MONGO_URL)", allow_module_level=True)

RUN = uuid.uuid4().hex[:8]
LOOP = asyncio.new_event_loop()
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kit_synthetic"
PASSWORD = "Password1!"
J: dict = {}
CREATED: dict[str, list] = {"users": [], "trials": [], "orgs": []}
OTP: list = []
EFFECTIVE = "2026-09-14"


def run(coro):
    return LOOP.run_until_complete(coro)


def call(method, path, who=None, **kwargs):
    headers = {"Authorization": f"Bearer {J[who]['token']}"} if who else {}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver") as cli:
            return await cli.request(method, path, headers=headers, timeout=120, **kwargs)
    return run(go())


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:1500]}"
    return response.json()


def raw(name):
    m = json.loads((FIXTURES / f"{name}.raw.json").read_text(encoding="utf-8"))
    m.pop("_fixture", None)
    return m


def p16(version: int):
    m = raw("p16_study_closeout")
    if version == 1:  # before the closeout section was added to the workflow
        m.pop("phases")
        m.pop("study_events")
    return m


def adapt_close():
    from test_kit_adaptive_study import adapt_close_model
    return adapt_close_model()


MODELS = {"P16-UPLOAD-V1": lambda: p16(1), "P16-UPLOAD-V2": lambda: p16(2), "SYN-ADAPT-CLOSE": adapt_close}


class ScriptedProvider:
    def __init__(self, *args, **kwargs):
        self.model, self.on_status = "scripted-provider", None

    def json(self, prompt, *, images=None, on_text=None):
        if prompt.startswith("You are a clinical trial protocol analyst. Below is text"):
            return MODELS[next(n for n in MODELS if n in prompt)]()
        return {"events": []} if "checking ONE thing" in prompt else {"corrections": [], "notes": []}


@pytest.fixture(scope="module", autouse=True)
def world():
    patch = pytest.MonkeyPatch()
    patch.setattr(kit_routes, "OpenAIKitClient", ScriptedProvider)
    patch.delenv("KIT_SAMPLE_MODEL", raising=False)
    patch.delenv("UCTSM_AUTHORITATIVE", raising=False)

    async def capture(channel, target, code, **_):
        OTP.append((target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    patch.setattr(server, "_deliver_otp", capture)
    patch.setattr(server, "_enforce_rate_limit", no_throttle)
    sponsor_org = f"E2E Sponsor {RUN}"
    for who, role, org in (("sponsor", "sponsor", sponsor_org), ("other_sponsor", "sponsor", f"E2E Other {RUN}"),
                           ("pi_a", "pi", f"E2E Site A {RUN}"), ("crc_a", "crc", f"E2E Site A {RUN}"),
                           ("pi_b", "pi", f"E2E Site B {RUN}"), ("crc_b", "crc", f"E2E Site B {RUN}"),
                           ("pi_c", "pi", f"E2E Site C {RUN}"), ("crc_c", "crc", f"E2E Site C {RUN}")):
        body = ok(call("POST", "/api/auth/register", json={
            "email": f"kit-e2e-{RUN}-{who.replace('_', '-')}@example.com", "password": PASSWORD,
            "full_name": f"E2E {who} {RUN}", "role": role, "organization": org}))
        J[who] = {"token": body["access_token"], "id": body["user"]["id"], "email": body["user"]["email"], "org": org}
        CREATED["users"].append(body["user"]["id"])
        CREATED["orgs"].append(org)

    async def trials():
        for key, protocol in (("p16", "SYN-P16-CLOSE"), ("adapt", "SYN-ADAPT-CLOSE")):
            tid = f"kit-e2e-{key}-{RUN}"
            await server.db.trials.insert_one({"id": tid, "title": f"E2E {key} {RUN}", "protocol_id": protocol,
                                               "created_by": J["sponsor"]["id"], "sponsor_name": sponsor_org, "created_at": server.now()})
            CREATED["trials"].append(tid)
            J[f"trial_{key}"] = tid
            # Each site's PI and CRC joined the trial team (the accepted trial invitation the team-invite flow stores).
            for who in ("pi_a", "crc_a", "pi_b", "crc_b", "pi_c", "crc_c"):
                await server.db.invitations.insert_one({"id": f"kit-e2e-team-{RUN}-{key}-{who}", "trial_id": tid, "status": "accepted",
                                                        "email": J[who]["email"], "role": who[:-2], "created_at": server.now()})
    run(trials())
    yield
    patch.undo()

    async def clean():
        trials = CREATED["trials"]
        patients = [p["id"] async for p in server.db.patients.find({"trial_id": {"$in": trials}}, {"_id": 0, "id": 1})]
        users = CREATED["users"] + [p["user_id"] async for p in server.db.patients.find({"trial_id": {"$in": trials}}, {"_id": 0, "user_id": 1})
                                    if p.get("user_id")]
        for name, query in (("patients", {"trial_id": {"$in": trials}}), ("invitations", {"trial_id": {"$in": trials}}),
                            ("trial_kit_models", {"trial_id": {"$in": trials}}), ("trial_kit_model_history", {"trial_id": {"$in": trials}}),
                            ("trial_kit_drafts", {"_id": {"$in": trials}}), ("kit_study_events", {"trial_id": {"$in": trials}}),
                            ("kit_study_effects", {"trial_id": {"$in": trials}}), ("notifications", {"trial_id": {"$in": trials}}),
                            ("trials", {"id": {"$in": trials}}), ("audit_logs", {"user_id": {"$in": users}}),
                            ("audit_logs", {"trial_id": {"$in": trials}}), ("refresh_tokens", {"user_id": {"$in": users}}),
                            ("users", {"id": {"$in": users}}), ("organizations", {"name": {"$in": CREATED["orgs"]}}),
                            ("kit_protocol_files.files", {"metadata.trial_id": {"$in": trials}})):
            if name == "kit_protocol_files.files":
                ids = [f["_id"] async for f in server.db[name].find(query, {"_id": 1})]
                await server.db["kit_protocol_files.chunks"].delete_many({"files_id": {"$in": ids}})
            await server.db[name].delete_many(query)
        del patients
    run(clean())


# ── helpers: the app's own routes, as each role uses them ─────────────────────────


def upload_and_approve(trial: str, marker: str, expected_version: int) -> dict:
    job = ok(call("POST", "/api/kit/analyze", "sponsor",
                  files={"file": ("protocol.txt", f"{marker} synthetic protocol text".encode(), "text/plain")}))
    for _ in range(300):
        state = ok(call("GET", f"/api/kit/jobs/{job['job_id']}", "sponsor"))
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    body = {"baseModel": state["result"]["baseModel"], "reviewAnswers": state["result"]["reviewAnswers"],
            "analysisJobId": job["job_id"], "fileSha256": state["result"]["fileSha256"], "fileName": "protocol.txt",
            "expectedVersion": expected_version}
    preview = ok(call("POST", "/api/kit/workflow-preview", "sponsor", json=body))
    assert not preview["problems"], preview["problems"]
    saved = ok(call("PUT", f"/api/trials/{trial}/kit-model", "sponsor", json={**body, "workflowAction": "approve",
                                                                          "workflowDigest": preview["digest"]}))
    assert saved["version"] == expected_version + 1
    return preview


def enrol(trial: str, pi: str, subject: str, version: int, start: str) -> dict:
    """Invite (PI) and register (the participant, through the OTP wizard): the record the app creates."""
    phone = f"+1202{int(uuid.uuid4().int % 10_000_000):07d}"
    invite = ok(call("POST", "/api/patients/invite", pi, json={
        "full_name": f"Synthetic {subject}", "phone": phone, "trial_id": trial, "subject_id": subject,
        "crc_id": J["crc_" + pi[-1]]["id"], "kit_model_version": version, "kit": {"startDate": start}}))
    OTP.clear()
    started = ok(call("POST", "/api/auth/register/start", json={
        "full_name": f"Synthetic {subject}", "role": "patient", "phone": phone, "invite_token": invite["token"],
        "profile": {"dob": "1960-01-01", "gender": "Female"}}))
    registration = started["registration_id"]
    [(target, code)] = OTP
    ok(call("POST", "/api/auth/register/verify", json={"registration_id": registration, "phone_otp": code}))
    ok(call("POST", "/api/auth/register/security-questions", json={"registration_id": registration, "security_questions": [
        {"question": "Question one?", "answer": "First answer"}, {"question": "Question two?", "answer": "Second answer"},
        {"question": "Question three?", "answer": "Third answer"}]}))
    session = ok(call("POST", "/api/auth/register/complete", json={"registration_id": registration, "password": PASSWORD}))
    patient = run(server.db.patients.find_one({"trial_id": trial, "subject_id": subject}, {"_id": 0}))
    assert patient and patient["kit"]["startDate"] == start and patient["kit_model_version"] == version
    J[f"patient_{subject}"] = {"token": session["access_token"], "id": patient["user_id"]}
    return patient


def update(pid: str, who: str, change) -> dict:
    current = ok(call("GET", f"/api/patients/{pid}/kit", who))
    record = copy.deepcopy(current["participant"])
    change(record)
    return ok(call("PUT", f"/api/patients/{pid}/kit?revision={current['revision']}", who, json=record))


def visits(**dates):
    return lambda r: r.setdefault("actuals", {}).update({f"{k}#1": v for k, v in dates.items()})


def staff_calendar(pid: str, who: str) -> dict:
    return calendar(ok(call("GET", f"/api/patients/{pid}/kit", who)))


def effects(view: dict) -> dict[str, dict]:
    return {e["subject_id"]: e for e in view["effects"]}


# ── C7: both operations from upload to the patient's own calendar ───────────────────


def test_c7_adaptive_interval_and_study_closure_end_to_end():
    trial = J["trial_adapt"]
    upload_and_approve(trial, "SYN-ADAPT-CLOSE", 0)
    patient = enrol(trial, "pi_a", "R-001", 1, "2026-01-05")
    pid = patient["id"]
    update(pid, "pi_a", lambda r: (r.setdefault("actuals", {}).update({
        "inj#1": "2026-01-05", "inj#2": "2026-02-02", "inj#3": "2026-03-02", "inj#4": "2026-04-13", "inj#5": "2026-06-08"}),
        r.setdefault("observations", {}).update({f"inj#{n}": {"activity_signs": {"value": 0}} for n in (3, 4)})))
    # The fifth finding is missing: the next injection waits, the fixed assessments stay dated (A7).
    waiting = staff_calendar(pid, "pi_a")["staff"]
    assert waiting["inj#6"]["status"] == "waiting" and waiting["inj#6"]["needs"][0]["roles"] == ["pi"]
    assert waiting["m12#1"]["planned"] == "2027-01-04"
    # A CRC may not confirm the finding; the investigator does, then the reading centre's recommendation arrives.
    finding = {"id": f"e2e-{RUN}-f5", "key": "inj#5", "input": "activity_signs", "value": 0, "confirmed_at": "2026-06-08T11:00:00Z"}
    assert call("POST", f"/api/patients/{pid}/kit/observations", "crc_a", json=finding).status_code == 403
    assert ok(call("POST", f"/api/patients/{pid}/kit/observations", "pi_a", json=finding))["status"] == "applied"
    assert ok(call("POST", f"/api/patients/{pid}/kit/observations", "pi_a", json=finding))["status"] == "replayed"
    ok(call("POST", f"/api/patients/{pid}/kit/observations", "crc_a", json={
        "id": f"e2e-{RUN}-r5", "key": "inj#5", "input": "crc_weeks", "value": 10, "confirmed_at": "2026-06-09T09:00:00Z"}))
    # Another site cannot touch this participant.
    assert call("GET", f"/api/patients/{pid}/kit", "pi_b").status_code == 403
    staff = staff_calendar(pid, "pi_a")
    assert (staff["staff"]["inj#6"]["planned"], staff["staff"]["inj#6"]["adaptive"]["intervalDays"]) == ("2026-08-17", 70)
    # The participant's own calendar, fetched with their own sign-in.
    mine = calendar(ok(call("GET", "/api/me/kit", "patient_R-001")))
    assert mine["patient"]["inj#6"]["target_date"] == "2026-08-17"
    assert mine["patient"]["m12#1"]["target_date"] == "2027-01-04"
    # The study team closes the study; a site PI cannot.
    event = {"id": f"e2e-{RUN}-close", "type": "sponsor_close", "effective_date": "2026-07-01"}
    assert call("POST", f"/api/trials/{trial}/kit-study-events", "pi_a", json=event).status_code == 403
    view = ok(call("POST", f"/api/trials/{trial}/kit-study-events", "sponsor", json=event))
    assert effects(view)["R-001"]["state"] == "applied"
    ok(call("PUT", f"/api/patients/{pid}/kit/study-effects/{effects(view)['R-001']['id']}/booking", "crc_a", json={"date": "2026-07-08"}))
    mine = calendar(ok(call("GET", "/api/me/kit", "patient_R-001")))
    task = f"se.{event['id']}.exit_visit#1"
    assert mine["patient"][task]["scheduled_date"] == "2026-07-08"
    assert not any(k in mine["patient"] for k in ("inj#6", "m12#1", "m24#1"))
    assert mine["patient"]["inj#5"]["completed_at"] == "2026-06-08"


# ── B: a study-wide closeout across three sites ─────────────────────────────────


def test_b_setup_two_workflow_versions_and_eleven_participants():
    trial = J["trial_p16"]
    upload_and_approve(trial, "P16-UPLOAD-V1", 0)
    old = enrol(trial, "pi_a", "A-104", 1, "2026-03-02")
    update(old["id"], "pi_a", visits(v3="2026-03-16", v4="2026-03-30", v5="2026-04-27", v6="2026-05-25", v7="2026-06-01"))
    upload_and_approve(trial, "P16-UPLOAD-V2", 1)
    people = {
        "A-101": ("pi_a", "2026-03-02", dict(v3="2026-03-16", v4="2026-03-30", v5="2026-04-27", v6="2026-05-25", v7="2026-06-01")),
        "A-102": ("pi_a", "2025-11-03", dict(v3="2025-11-17", v4="2025-12-01", v5="2025-12-29", v6="2026-01-26", v7="2026-02-02", v8="2026-07-20")),
        "A-103": ("pi_a", "2026-08-03", dict(v3="2026-08-17", v4="2026-08-31")),
        "B-201": ("pi_b", "2026-02-02", dict(v3="2026-02-16", v4="2026-03-02", v5="2026-03-30", v6="2026-04-27", v7="2026-05-04")),
        "B-202": ("pi_b", "2026-03-02", dict(v3="2026-03-16", v4="2026-03-30", v5="2026-04-27", v6="2026-05-25", v7="2026-06-01")),
        "B-203": ("pi_b", "2026-06-15", dict(v3="2026-06-29", v4="2026-07-13", v5="2026-08-10", v6="2026-09-07", v7="2026-09-16")),
        "B-204": ("pi_b", "2026-01-05", dict(v3="2026-01-19", v4="2026-02-02", v5="2026-03-02", v6="2026-03-30", v7="2026-04-06", v8="2026-09-21")),
        "C-301": ("pi_c", "2026-06-22", dict(v3="2026-07-06", v4="2026-07-20", v5="2026-08-17", v6="2026-09-14")),
        "C-302": ("pi_c", "2026-04-06", dict(v3="2026-04-20", v4="2026-05-04", v5="2026-06-01", v6="2026-06-29", v7="2026-07-06")),
        "C-303": ("pi_c", "2026-05-04", dict(v3="2026-05-18", v4="2026-06-01", v5="2026-06-29", v6="2026-07-27")),
    }
    for subject, (pi, start, dates) in people.items():
        patient = enrol(trial, pi, subject, 2, start)
        update(patient["id"], pi, visits(**dates))
        J[subject] = patient["id"]
    J["A-104"] = old["id"]
    update(J["B-202"], "pi_b", lambda r: r["events"].append({"key": f"wd-{RUN}", "type": "withdrawal", "date": "2026-08-03", "answers": {}}))
    # B6: a site device fetched C-302's calendar before the event, then went offline.
    J["offline_before"] = staff_calendar(J["C-302"], "pi_c")
    assert J["offline_before"]["staff"]["v8#1"]["planned"] == "2026-12-21"


def test_b7_only_the_study_team_confirms():
    event = {"id": f"e2e-{RUN}-final", "type": "final_v6", "effective_date": EFFECTIVE, "fields": {"final_subject": "C-301"}}
    for who in ("pi_c", "crc_a"):
        assert call("POST", f"/api/trials/{J['trial_p16']}/kit-study-events", who, json=event).status_code == 403
    assert call("POST", f"/api/trials/{J['trial_p16']}/kit-study-events", "other_sponsor", json=event).status_code == 403
    assert call("POST", f"/api/trials/{J['trial_p16']}/kit-study-events", "sponsor",
                json={**event, "fields": {"final_subject": "Z-999"}}).status_code == 422
    assert call("POST", f"/api/trials/{J['trial_p16']}/kit-study-events", "sponsor",
                json={**event, "effective_date": "2099-01-01"}).status_code == 422
    assert run(server.db.kit_study_events.find_one({"_id": event["id"]})) is None


def test_b1_b5_confirm_with_a_partial_failure_retry_and_replay(monkeypatch):
    trial = J["trial_p16"]
    event = {"id": f"e2e-{RUN}-final", "type": "final_v6", "effective_date": EFFECTIVE, "fields": {"final_subject": "C-301"}}
    real = kit_routes.kit_study.desired_effect

    def flaky(definition, model, patient, ev):
        if patient.get("subject_id") == "B-201":
            raise RuntimeError("simulated failure while processing B-201")
        return real(definition, model, patient, ev)
    monkeypatch.setattr(kit_routes.kit_study, "desired_effect", flaky)
    partial = ok(call("POST", f"/api/trials/{trial}/kit-study-events", "sponsor", json=event))
    assert partial["status"] == "partial" and [f["subject_id"] for f in partial["failed"]] == ["B-201"]
    assert "B-201" not in effects(partial)
    monkeypatch.setattr(kit_routes.kit_study, "desired_effect", real)
    done = ok(call("POST", f"/api/trials/{trial}/kit-study-events/{event['id']}/process", "sponsor"))
    assert done["status"] == "applied" and done["failed"] == []
    by = effects(done)
    states = {s: e["state"] for s, e in by.items()}
    assert states == {
        "A-101": "applied", "B-201": "applied", "C-302": "applied", "C-301": "applied",
        "A-102": "excluded", "A-103": "excluded", "B-202": "excluded",
        "B-203": "to_review", "B-204": "already_completed", "C-303": "unresolved", "A-104": "unmatched"}
    # One closeout task per eligible participant, across all three sites; the final participant gets none.
    tasks = {s: e["task_key"] for s, e in by.items() if e["task_key"]}
    assert tasks == {s: f"se.{event['id']}.early_eos#1" for s in ("A-101", "B-201", "C-302")}
    assert {by[s]["site"] for s in tasks} == {J["pi_a"]["org"], J["pi_b"]["org"], J["pi_c"]["org"]}
    assert by["C-301"]["action"] == "final_participant" and by["C-301"]["changes"] == [{"visit": "v8", "change": "superseded"}]
    assert "V7 has no recorded date" in by["C-303"]["reason"] and "2026-08-06" in by["C-303"]["reason"]
    assert "version has no action" in by["A-104"]["reason"]
    stored = run(server.db.kit_study_events.find_one({"_id": event["id"]}))
    assert stored["confirmed_by"] == J["sponsor"]["id"] and stored["effective_date"] == EFFECTIVE
    assert stored["confirmed_at"].date().isoformat() != EFFECTIVE, "confirmation time is kept apart from when it happened"
    reminders = run(server.db.notifications.count_documents({"trial_id": trial, "id": {"$regex": f"^kit-study:{event['id']}:"}}))
    assert reminders == 6, "one reminder for each site PI and CRC of the three closeouts"
    # B5: replaying the confirmation, and processing again, changes nothing.
    replay = ok(call("POST", f"/api/trials/{trial}/kit-study-events", "sponsor", json=event))
    assert {s: e["state"] for s, e in effects(replay).items()} == states
    ok(call("POST", f"/api/trials/{trial}/kit-study-events/{event['id']}/process", "sponsor"))
    assert run(server.db.kit_study_effects.count_documents({"event_id": event["id"]})) == 11
    assert run(server.db.notifications.count_documents({"trial_id": trial, "id": {"$regex": f"^kit-study:{event['id']}:"}})) == 6
    assert call("POST", f"/api/trials/{trial}/kit-study-events", "sponsor",
                json={**event, "fields": {"final_subject": "A-101"}}).status_code == 409
    J["event"] = event
    J["effects"] = by


def test_b2_b3_calendars_after_the_event():
    task = f"se.{J['event']['id']}.early_eos#1"
    final = staff_calendar(J["C-301"], "pi_c")["staff"]
    assert final["v7#1"]["planned"] == "2026-09-21" and final["v7#1"]["status"] == "scheduled"
    assert "v8#1" not in final and task not in final, "Visit 7, then exit: no extension, no closeout"
    finished = staff_calendar(J["A-102"], "pi_a")["staff"]
    assert finished["v8#1"]["actual"] == "2026-07-20" and task not in finished
    later = staff_calendar(J["B-203"], "pi_b")["staff"]
    assert later["v8#1"]["planned"] == "2027-03-01" and task not in later, "in extension now, but not on the effective date"
    done = staff_calendar(J["B-204"], "pi_b")["staff"]
    assert done["v8#1"]["actual"] == "2026-09-21" and task not in done, "completed before processing: kept, nothing added"
    withdrawn = staff_calendar(J["B-202"], "pi_b")["staff"]
    assert task not in withdrawn
    old = staff_calendar(J["A-104"], "pi_a")["staff"]
    assert old["v8#1"]["planned"] == "2026-11-16", "workflow v1 has no closeout action: unchanged"


def test_b4_b6_site_books_the_pending_closeout_and_the_offline_device_sees_it():
    task = f"se.{J['event']['id']}.early_eos#1"
    after = staff_calendar(J["C-302"], "pi_c")
    assert "v8#1" not in after["staff"]
    assert after["staff"][task]["status"] == "waiting" and after["staff"][task]["needs"][0]["input"] == "booking"
    assert task not in after["patient"], "the participant is not shown an undated visit"
    effect = J["effects"]["A-101"]["id"]
    route = f"/api/patients/{J['A-101']}/kit/study-effects/{effect}/booking"
    assert call("PUT", route, "crc_b", json={"date": "2026-11-25"}).status_code == 403, "another site cannot book it"
    assert call("PUT", route, "crc_a", json={"date": "2026-09-01"}).status_code == 422, "not before the event"
    ok(call("PUT", route, "crc_a", json={"date": "2026-11-25"}))
    booked = staff_calendar(J["A-101"], "pi_a")["staff"][task]
    assert (booked["status"], booked["planned"], booked["booked"]) == ("scheduled", "2026-11-25", "2026-11-25")
    mine = calendar(ok(call("GET", "/api/me/kit", "patient_A-101")))
    assert mine["patient"][task]["scheduled_date"] == "2026-11-25" and "v8#1" not in mine["patient"]
    # B6: the device that was offline fetches again and receives the stored effect.
    assert J["offline_before"]["staff"]["v8#1"]["planned"] == "2026-12-21" and task not in J["offline_before"]["staff"]
    # Site staff list only their own participants' effects.
    seen = {e["subject_id"] for e in ok(call("GET", f"/api/trials/{J['trial_p16']}/kit-study-events", "crc_b"))["events"][0]["effects"]}
    assert seen == {"B-201", "B-202", "B-203", "B-204"}


def test_b9_correction_previews_booked_visits_and_keeps_completed_ones():
    trial, event = J["trial_p16"], J["event"]
    task = f"se.{event['id']}.early_eos#1"
    ok(call("PUT", f"/api/patients/{J['C-302']}/kit/study-effects/{J['effects']['C-302']['id']}/booking", "pi_c", json={"date": "2026-11-27"}))
    ok(call("PUT", f"/api/patients/{J['B-201']}/kit/study-effects/{J['effects']['B-201']['id']}/booking", "pi_b", json={"date": "2026-11-18"}))
    update(J["B-201"], "pi_b", lambda r: r["actuals"].update({task: "2026-11-18"}))
    # The event actually happened on 1 July: C-302 entered extension on 6 July, so its booked closeout no longer applies.
    corrected = {"effective_date": "2026-07-01", "fields": {"final_subject": "C-301"}, "revision": 1}
    preview = ok(call("PUT", f"/api/trials/{trial}/kit-study-events/{event['id']}", "sponsor", json={**corrected, "preview": True}))["preview"]
    assert [x["subject_id"] for x in preview["booked_affected"]] == ["C-302"] and preview["booked_affected"][0]["booked"] == "2026-11-27"
    assert [x["subject_id"] for x in preview["completed_kept"]] == ["B-201"]
    refused = call("PUT", f"/api/trials/{trial}/kit-study-events/{event['id']}", "sponsor", json=corrected)
    assert refused.status_code == 409 and "acknowledge" in refused.text
    view = ok(call("PUT", f"/api/trials/{trial}/kit-study-events/{event['id']}", "sponsor", json={
        **corrected, "acknowledge_booked": [J["effects"]["C-302"]["id"]]}))
    states = {s: e["state"] for s, e in effects(view).items()}
    assert states == {
        "A-101": "applied", "B-201": "applied", "C-301": "applied", "C-302": "review",
        "A-102": "already_completed", "B-204": "already_completed", "B-202": "ended",
        "A-103": "excluded", "B-203": "excluded", "C-303": "excluded", "A-104": "unmatched"}
    assert effects(view)["A-101"]["booked"] == "2026-11-25", "a closeout still applying keeps its booking"
    completed = staff_calendar(J["B-201"], "pi_b")["staff"][task]
    assert completed["actual"] == "2026-11-18" and completed["status"] == "done"
    flagged = staff_calendar(J["C-302"], "pi_c")["staff"]
    assert flagged["v8#1"]["planned"] == "2026-12-21", "Visit 8 is back"
    assert flagged[task]["booked"] == "2026-11-27" and any("Review this booking" in n for n in flagged[task]["notes"])
    # The site reviews it and clears the booking: the closeout is withdrawn.
    ok(call("PUT", f"/api/patients/{J['C-302']}/kit/study-effects/{J['effects']['C-302']['id']}/booking", "pi_c", json={"date": None}))
    cleared = staff_calendar(J["C-302"], "pi_c")["staff"]
    assert task not in cleared and cleared["v8#1"]["planned"] == "2026-12-21"
    stored = run(server.db.kit_study_events.find_one({"_id": event["id"]}))
    assert stored["revision"] == 2 and stored["corrections"][0]["effective_date"] == EFFECTIVE
    audit = run(server.db.audit_logs.count_documents({"trial_id": trial, "action": "trial.kit_study_event_correct"}))
    assert audit == 1
