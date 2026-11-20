"""History-based intervals and study-wide events through clean-up, workflow review, the app's routes
and the calendar engine, with an in-memory database and a controlled model provider.

Synthetic fixtures (assumptions written in each file): tests/fixtures/kit_synthetic/
  rival_treat_extend.raw.json   treat-and-extend modelled on NCT02130024 (RIVAL)
  p16_study_closeout.raw.json   P16-style extension closeout
The real-database end-to-end run (sites, registration, permissions, scenarios B1-B9) is
tests/test_kit_adaptive_study_e2e.py.

Scenarios here: C1 (extraction pathway to calendar), C2 (unsupported/conflicting logic stays
blocked with its evidence), C3 (fixed grid and rebooking), C4 (progression), C5 (confirmed
booking and sampling clock), C6 (version pinning with recorded inputs), A9/A12 at the server
(corrections, replays, late and out-of-order inputs).
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Header
from fastapi.testclient import TestClient

import server  # noqa: I001 - server first: it imports kit_routes at its end
import kit_routes
from app.kit import analyze as kit_analyze
from app.kit.normalize import normalize
from app.kit.workflow import build_workflow, compile_workflow
from test_kit_workflows import Collection, benzoyl_timing, crd09_timing
from _kit_calendar import calendar

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kit_synthetic"
BACKEND = Path(__file__).resolve().parents[1]


def raw(name: str) -> dict:
    model = json.loads((FIXTURES / f"{name}.raw.json").read_text(encoding="utf-8"))
    model.pop("_fixture", None)
    return model


def adapt_close_model() -> dict:
    """SYN-ADAPT-CLOSE: the treat-and-extend fixture plus a study-wide early closure (synthetic:
    'if the sponsor closes the study, participants still receiving injections attend one exit
    visit on the site's next available date; remaining injections and assessments do not happen')."""
    m = raw("rival_treat_extend")
    m["protocol"] = {**m["protocol"], "number": "SYN-ADAPT-CLOSE"}
    m["events"] = [{"id": "discontinuation", "label": "Study discontinuation", "ends_schedule": True, "repeatable": False,
                    "questions": [], "visits": []}]
    m["phases"] = [{"id": "on_injections", "label": "On injections", "starts": {"visit": "inj"},
                    "ends": [{"visit": "m24"}, {"event": "discontinuation"}]}]
    m["study_events"] = [{
        "id": "sponsor_close", "label": "Sponsor closed the study early", "confirm_roles": ["sponsor", "cro"], "fields": [],
        "actions": [{"id": "exit", "label": "Exit visit for participants still on injections",
                     "audience": {"phase": "on_injections"}, "supersede_visits": ["inj", "m12", "m24"],
                     "add_visit": {"id": "exit_visit", "code": "EXIT", "name": "Early exit visit", "date": "site_booking",
                                   "window_before": 0, "window_after": 0,
                                   "procedures": [{"name": "BCVA"}, {"name": "OCT"}, {"name": "Multimodal imaging for the reading centre"}]},
                     "effect": "Remaining injections and assessments are replaced by one exit visit."}],
        "quote": "participants still receiving injections attend one exit visit", "page": 1}]
    return m


def conflicting_model() -> dict:
    """SYN-C2-CONFLICT: the reading centre recommendation's role is not stated (no precedence), an
    unsupported rule shape, and a study event the protocol says is detected automatically."""
    m = raw("rival_treat_extend")
    m["protocol"] = {**m["protocol"], "number": "SYN-C2-CONFLICT"}
    del m["visits"][0]["repeat"]["adaptive"]["precedence"]
    m["visits"][0]["repeat"]["adaptive"]["quote"] = "The reading centre recommendation and the investigator assessment determine the interval."
    m["visits"][0]["repeat"]["adaptive"]["page"] = 2
    m["pattern_rules"] = [{"id": "pr1", "pattern": "CONSECUTIVE_MISSED_VISITS_WITHDRAW", "label": "Two missed injections end treatment",
                           "visits": ["inj"], "params": {"missed": 2}, "quote": "Two consecutive missed injections lead to withdrawal.",
                           "page": 3}]
    m["phases"] = [{"id": "on_injections", "label": "On injections", "starts": {"visit": "inj"}, "ends": [{"visit": "m24"}]}]
    m["study_events"] = [{"id": "auto_close", "label": "Enrolment target reached", "detect": "when the 280th participant is randomised",
                          "actions": [{"id": "stop", "audience": {"phase": "on_injections"}, "supersede_visits": ["m24"]}],
                          "quote": "When the 280th participant is randomised the study closes.", "page": 4}]
    return m


PROTOCOL_TEXT = {
    "SYN-ADAPT-CLOSE": "SYN-ADAPT-CLOSE synthetic protocol. participants still receiving injections attend one exit visit",
    "SYN-C2-CONFLICT": ("SYN-C2-CONFLICT synthetic protocol. The reading centre recommendation and the investigator assessment "
                        "determine the interval. Two consecutive missed injections lead to withdrawal. "
                        "When the 280th participant is randomised the study closes."),
}
MODELS = {"SYN-ADAPT-CLOSE": adapt_close_model, "SYN-C2-CONFLICT": conflicting_model}


class ScriptedProvider:
    """Stands in for the model provider: the model build returns the fixture for the protocol in the
    prompt; the cross-check finds nothing to correct; the events check reports nothing."""
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        self.model = "scripted-provider"
        self.on_status = None

    def json(self, prompt: str, *, images=None, on_text=None):
        if prompt.startswith("You are a clinical trial protocol analyst. Below is text"):
            name = next(n for n in MODELS if n in prompt)
            ScriptedProvider.calls.append(f"model:{name}")
            return MODELS[name]()
        if "checking ONE thing" in prompt:
            ScriptedProvider.calls.append("events")
            return {"events": []}
        ScriptedProvider.calls.append("cross-check")
        return {"corrections": [], "notes": []}


@pytest.fixture()
def app(monkeypatch):
    db = SimpleNamespace(**{name: Collection() for name in (
        "trial_kit_models", "trial_kit_model_history", "trial_kit_drafts", "trial_kit_shared_dates", "patients",
        "invitations", "trials", "users", "kit_study_effects", "kit_study_events", "notifications",
    )})
    for trial, protocol in (("adapt", "SYN-ADAPT-CLOSE"), ("conflict", "SYN-C2-CONFLICT"), ("benz", "NCS-CT-006-AL-Benz"),
                            ("rival", "SYN-RIVAL-TE")):
        db.trials.docs.append({"id": trial, "title": f"Synthetic {trial}", "protocol_id": protocol})
    db.users.docs.extend([{"id": "pi-a", "role": "pi"}, {"id": "crc-a", "role": "crc"}])
    log, files = [], {}

    def current_user(x_role: str = Header("sponsor"), x_user: str = Header("")):
        return {"id": x_user or x_role, "role": x_role}

    async def allowed(*args):
        return True

    async def audit(user, action, detail, **context):
        log.append((user["id"], action, detail))

    async def require_patient(user, patient_id):
        return await db.patients.find_one({"id": patient_id})

    async def store_file(sha, data, file_name, trial_id):
        files[sha] = data

    async def read_file(sha):
        return files[sha], {"content_type": "text/plain", "file_name": "protocol.txt"}

    for module in (server, kit_routes):
        monkeypatch.setattr(module, "db", db)
        monkeypatch.setattr(module, "_can_access_trial", allowed)
        monkeypatch.setattr(module, "write_audit", audit)
    monkeypatch.setattr(kit_routes, "_require_patient", require_patient)
    monkeypatch.setattr(kit_routes, "_store_protocol_file", store_file)
    monkeypatch.setattr(kit_routes, "_read_protocol_file", read_file)
    monkeypatch.setattr(kit_routes, "OpenAIKitClient", ScriptedProvider)
    monkeypatch.delenv("KIT_SAMPLE_MODEL", raising=False)
    monkeypatch.delenv("UCTSM_AUTHORITATIVE", raising=False)
    ScriptedProvider.calls = []
    api = FastAPI()
    api.include_router(kit_routes.router)
    enrollment = APIRouter()
    enrollment.routes.extend([r for r in server.api.routes if r.path == "/api/patients/invite"])
    api.include_router(enrollment)
    api.dependency_overrides[server.current_user] = current_user
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, db=db, log=log)


SPONSOR, PI, CRC = {"X-Role": "sponsor"}, {"X-Role": "pi", "X-User": "pi-a"}, {"X-Role": "crc", "X-User": "crc-a"}


def analyze(client, text: str) -> dict:
    response = client.post("/api/kit/analyze", files={"file": ("protocol.txt", text.encode(), "text/plain")}, headers=SPONSOR)
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/kit/jobs/{job_id}", headers=SPONSOR).json()
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    result = job["result"]
    return {"baseModel": result["baseModel"], "reviewAnswers": result["reviewAnswers"], "analysisJobId": job_id,
            "fileSha256": result["fileSha256"], "fileName": "protocol.txt", "expectedVersion": 0}


def approve(client, trial: str, body: dict) -> dict:
    preview = client.post("/api/kit/workflow-preview", json=body, headers=SPONSOR)
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    assert not preview["problems"], preview["problems"]
    saved = client.put(f"/api/trials/{trial}/kit-model", json={**body, "workflowAction": "approve",
                                                               "workflowDigest": preview["digest"]}, headers=SPONSOR)
    assert saved.status_code == 200, saved.text
    return preview


def enrol(app, trial: str, subject: str, version: int, kit: dict) -> str:
    invite = app.client.post("/api/patients/invite", headers=PI, json={
        "full_name": f"Synthetic {subject}", "phone": f"+1202555{abs(hash(subject)) % 10000:04d}", "trial_id": trial,
        "subject_id": subject, "kit_model_version": version, "kit": kit})
    assert invite.status_code == 200, invite.text
    stored = next(i for i in app.db.invitations.docs if i["patient_data"]["subject_id"] == subject)
    # Registration (OTP) copies the pinned invitation data into the patient record; the real-database
    # end-to-end test runs that step through the registration routes.
    app.db.patients.docs.append({**stored["patient_data"], "id": subject.lower(), "trial_id": trial, "pi_id": "pi-a", "crc_id": "crc-a"})
    return subject.lower()


def put_kit(app, pid: str, change, headers=PI):
    current = app.client.get(f"/api/patients/{pid}/kit", headers=headers).json()
    record = copy.deepcopy(current["participant"])
    change(record)
    return app.client.put(f"/api/patients/{pid}/kit?revision={current['revision']}", json=record, headers=headers)


def history(record: dict, steps: list[tuple[int, str, int | None]]) -> None:
    for n, actual, finding in steps:
        record.setdefault("actuals", {})[f"inj#{n}"] = actual
        if finding is not None:
            record.setdefault("observations", {}).setdefault(f"inj#{n}", {})["activity_signs"] = {"value": finding}


# ── clean-up and the workflow the study team reviews ─────────────────────────────


def test_synthetic_fixture_models_are_current():
    done = subprocess.run([sys.executable, "scripts/kit_synthetic_fixtures.py", "--check"], cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_clean_up_keeps_stated_operations_and_blocks_unstated_ones():
    model = normalize(raw("rival_treat_extend"))
    adaptive = model["visits"][0]["repeat"]["adaptive"]
    assert "blocked" not in adaptive and adaptive["precedence"] == "calculated"
    assert model["visits"][0]["combine_with"] == [{"visit": "m12", "date": "adaptive"}, {"visit": "m24", "date": "adaptive"}]
    # Each missing part is named; none is filled in.
    broken = raw("rival_treat_extend")
    a = broken["visits"][0]["repeat"]["adaptive"]
    for key in ("precedence", "basis", "max_days"):
        a.pop(key)
    a["breakpoint"] = {"after_extensions": 3}
    m = normalize(broken)
    blocked = m["visits"][0]["repeat"]["adaptive"]["blocked"]
    for words in ("previous visit's actual date", "shortest and longest", "cap's offset", "recommendation decides the booking"):
        assert words in blocked
    point = next(p for p in m["open_points"] if p["id"] == "adaptive_inj")
    assert point["affects"] == ["inj"] and point["quote"].startswith("If none of the signs")
    from app.kit.patterns import blockers, enrol_problem
    assert [b["pattern"] for b in blockers(m)] == ["ADAPTIVE_INTERVAL"]
    assert "ADAPTIVE_INTERVAL" in enrol_problem(blockers(m))
    # A combined visit must be a fixed one and say whose date is used.
    loose = raw("rival_treat_extend")
    loose["visits"][0]["combine_with"] = [{"visit": "m12"}, {"visit": "inj", "date": "fixed"}, {"visit": "nope", "date": "fixed"}]
    assert "combine_with" not in normalize(loose)["visits"][0]


def test_study_event_clean_up():
    model = normalize(raw("p16_study_closeout"))
    [event] = model["study_events"]
    assert "blocked" not in event and [a["id"] for a in event["actions"]] == ["final_participant", "early_close"]
    assert event["actions"][1]["add_visit"]["date"] == "site_booking"
    assert [p["id"] for p in model["phases"]] == ["treatment", "safety_follow_up", "extension"]
    bad = raw("p16_study_closeout")
    bad["study_events"][0]["actions"][1]["audience"] = {"phase": "nowhere"}
    bad["study_events"][0]["actions"][0]["supersede_visits"] = ["v8", "v99"]
    bad["study_events"][0]["actions"].append({"id": "x", "audience": {"field": "final_subject"},
                                              "add_visit": {"id": "x1", "code": "X", "name": "X", "date": "planned"}})
    m = normalize(bad)
    blocked = m["study_events"][0]["blocked"]
    assert "nowhere" not in blocked and "names no phase" in blocked and "v99" in blocked and "site's booking" in blocked
    assert any(p["id"] == "study_event_final_v6" and p["page"] == 58 for p in m["open_points"])


def test_workflow_blocks_show_condition_inputs_timing_outputs_and_branches():
    model = normalize(adapt_close_model())
    workflow = build_workflow(model, 1)
    assert compile_workflow(workflow) == model
    assert not workflow["errors"]
    inj = next(b for b in workflow["blocks"] if b["id"] == "visit:inj")
    summary = "\n".join(inj["summary"])
    for words in ("Condition: from occurrence 3", "Inputs: Signs of disease activity (pi)", "Reading centre recommended interval",
                  "Timing: next target = previous visit's actual date + interval", "Branch: finding = 0 -> extend by 2 weeks",
                  "Branch: finding >= 2 -> set to 4 weeks", "Breakpoint: after 3 extension attempts",
                  "Precedence: the calculated interval books the visit", "Outputs:"):
        assert words in summary, words
    assert [p["roles"] for p in inj["prompts"] if "disease activity" in p["text"]] == [["pi"]]
    assert {"block": "visit:m12", "relation": "Combined when inside its window (adaptive date)"} in inj["paths"]
    study = next(b for b in workflow["blocks"] if b["id"] == "study_event:sponsor_close")
    assert study["scope"] == "study" and study["prompts"][0]["roles"] == ["sponsor", "cro"]
    assert {"block": "phase:on_injections", "relation": "Audience on the effective date", "basis": "actual"} in study["inputs"]
    assert any(line.startswith("Branch: participants in on injections -> supersede pending inj, m12, m24 and add Early exit visit")
               for line in study["summary"])


# ── C1: one synthetic protocol through extraction, review, approval, enrolment and the calendar ──


def test_c1_extraction_to_calendar_for_both_operations(app):
    body = analyze(app.client, PROTOCOL_TEXT["SYN-ADAPT-CLOSE"])
    assert ScriptedProvider.calls == ["model:SYN-ADAPT-CLOSE", "cross-check", "events"]
    base = body["baseModel"]
    assert base["visits"][0]["repeat"]["adaptive"]["breakpoint"] == {"after_extensions": 3, "cap_offset_days": -14,
                                                                        "count_activity_at": "any_interval"}
    assert base["study_events"][0]["actions"][0]["audience"] == {"phase": "on_injections"}
    # Draft review keeps both, then approval.
    draft = app.client.put("/api/trials/adapt/kit-model", json={**body, "workflowAction": "draft"}, headers=SPONSOR)
    assert draft.status_code == 200, draft.text
    saved_draft = app.client.get("/api/trials/adapt/kit-draft", headers=SPONSOR).json()
    kinds = {b["id"]: b["kind"] for b in saved_draft["workflow"]["blocks"]}
    assert kinds["visit:inj"] == "visit" and kinds["study_event:sponsor_close"] == "study_event"
    approve(app.client, "adapt", body)
    pid = enrol(app, "adapt", "AC-001", 1, {"startDate": "2026-01-05"})
    # The investigator confirms findings; the CRC records the reading-centre recommendation.
    response = put_kit(app, pid, lambda r: history(r, [(1, "2026-01-05", None), (2, "2026-02-02", None), (3, "2026-03-02", 0),
                                                       (4, "2026-04-13", 0), (5, "2026-06-08", 0)]))
    assert response.status_code == 200, response.text
    assert put_kit(app, pid, lambda r: r["observations"]["inj#5"].update(activity_signs={"value": 1}), headers=CRC).status_code == 403
    rec = app.client.post(f"/api/patients/{pid}/kit/observations", headers=CRC, json={
        "id": "crc-inj5-0001", "key": "inj#5", "input": "crc_weeks", "value": 12, "confirmed_at": "2026-06-09T10:00:00Z"})
    assert rec.status_code == 200 and rec.json()["status"] == "applied", rec.text
    staff = app.client.get(f"/api/patients/{pid}/kit", headers=PI).json()
    stamped = staff["participant"]["observations"]["inj#5"]
    assert stamped["activity_signs"]["by"] == "pi-a" and stamped["crc_weeks"]["by"] == "crc-a"
    cal = calendar(staff)
    nxt = cal["staff"]["inj#6"]
    assert (nxt["planned"], nxt["adaptive"]["intervalDays"], nxt["adaptive"]["recommendedDays"]) == ("2026-08-17", 70, 84)
    assert cal["patient"]["inj#6"]["target_date"] == "2026-08-17"
    assert cal["staff"]["m12#1"]["planned"] == "2027-01-04"
    # The study-wide trigger, confirmed by the sponsor, reaches the same calendar.
    confirmed = app.client.post("/api/trials/adapt/kit-study-events", headers=SPONSOR, json={
        "id": "close-0001", "type": "sponsor_close", "effective_date": "2026-07-01"})
    assert confirmed.status_code == 200, confirmed.text
    [effect] = confirmed.json()["effects"]
    assert (effect["state"], effect["subject_id"], effect["task_key"]) == ("applied", "AC-001", "se.close-0001.exit_visit#1")
    after = calendar(app.client.get(f"/api/patients/{pid}/kit", headers=PI).json())
    assert "inj#6" not in after["staff"] and "m12#1" not in after["staff"] and "m24#1" not in after["staff"]
    assert after["staff"]["inj#5"]["actual"] == "2026-06-08"
    exit_row = after["staff"]["se.close-0001.exit_visit#1"]
    assert (exit_row["status"], exit_row["needs"][0]["input"]) == ("waiting", "booking")
    booked = app.client.put(f"/api/patients/{pid}/kit/study-effects/{effect['id']}/booking", json={"date": "2026-07-08"}, headers=CRC)
    assert booked.status_code == 200, booked.text
    final = calendar(app.client.get(f"/api/patients/{pid}/kit", headers=PI).json())
    assert final["staff"]["se.close-0001.exit_visit#1"]["planned"] == "2026-07-08"
    assert final["patient"]["se.close-0001.exit_visit#1"]["scheduled_date"] == "2026-07-08"


# ── C2: what cannot run keeps its evidence and cannot drive a calendar ───────────────


def test_c2_unsupported_and_conflicting_logic_stays_blocked_with_its_evidence(app):
    body = analyze(app.client, PROTOCOL_TEXT["SYN-C2-CONFLICT"])
    preview = app.client.post("/api/kit/workflow-preview", json=body, headers=SPONSOR).json()
    problems = "\n".join(preview["problems"])
    assert "ADAPTIVE_INTERVAL" not in problems  # blockers are shown by label, with their reason
    assert "History-based interval for INJ" in problems and "recommendation decides the booking" in problems
    assert "Two missed injections end treatment" in problems
    issues = {i["id"]: i for i in preview["workflow"]["issues"]}
    assert issues["adaptive_inj"]["quote"].startswith("The reading centre recommendation") and issues["adaptive_inj"]["page"] == 2
    assert issues["adaptive_inj"]["blocks"] == ["visit:inj"]
    assert issues["study_event_detect_auto_close"]["quote"].startswith("When the 280th participant")
    rule = next(b for b in preview["workflow"]["blocks"] if b["id"] == "pattern:pr1")
    assert rule["source"]["quote"] == "Two consecutive missed injections lead to withdrawal."
    assert rule["operation"]["quote_found"] is True  # looked up word by word in the uploaded text
    refused = app.client.put("/api/trials/conflict/kit-model", json={**body, "workflowAction": "approve",
                                                                   "workflowDigest": preview["digest"]}, headers=SPONSOR)
    assert refused.status_code == 422
    # Saved as a draft, the evidence survives a reload.
    assert app.client.put("/api/trials/conflict/kit-model", json={**body, "workflowAction": "draft"}, headers=SPONSOR).status_code == 200
    draft = app.client.get("/api/trials/conflict/kit-draft", headers=SPONSOR).json()
    assert {i["id"] for i in draft["workflow"]["issues"]} >= {"adaptive_inj", "study_event_detect_auto_close"}
    # No approved workflow: nobody can be enrolled on it.
    invite = app.client.post("/api/patients/invite", headers=PI, json={
        "full_name": "Nobody", "phone": "+12025559999", "trial_id": "conflict", "subject_id": "X-1",
        "kit_model_version": 1, "kit": {"startDate": "2026-01-05"}})
    assert invite.status_code == 409
    # The reviewer completes the interval rule in the block: the interval runs, the unsupported rule still blocks.
    block = next(b for b in preview["workflow"]["blocks"] if b["id"] == "visit:inj")
    repeat = {**block["operation"]["repeat"], "adaptive": {**block["operation"]["repeat"]["adaptive"], "precedence": "review"}}
    repeat["adaptive"].pop("blocked")
    fixed = app.client.post("/api/kit/workflow-preview", headers=SPONSOR, json={
        **body, "blockEdits": {"visit:inj": {"id": "inj", "repeat": repeat}}}).json()
    assert "recommendation decides the booking" not in "\n".join(fixed["workflow"]["errors"])
    assert any("Two missed injections" in e for e in fixed["workflow"]["errors"])
    # The engine never dates what a blocked operation governs.
    model = normalize(conflicting_model())
    record = {"startAnchor": "baseline", "startDate": "2026-01-05", "answers": {}, "events": [], "today": "2026-06-10",
              "actuals": {"inj#1": "2026-01-05", "inj#2": "2026-02-02", "inj#3": "2026-03-02"},
              "observations": {"inj#3": {"activity_signs": {"value": 0}, "crc_weeks": {"value": 6}}}}
    rows = calendar({"model": model, "site_answers": {}, "participant": record, "workflow_version": 1,
                     "patient": {"id": "x", "trial_id": "conflict"}, "trial": {}})["staff"]
    assert rows["inj#4"]["planned"] is None and "not approved" in rows["inj#4"]["relative"]
    assert rows["m12#1"]["planned"] == "2027-01-04"


# ── server-side inputs: corrections, replays, late and out-of-order delivery (A9, A12) ──


def test_inputs_replay_late_delivery_and_correction(app):
    body = analyze(app.client, PROTOCOL_TEXT["SYN-ADAPT-CLOSE"])
    approve(app.client, "adapt", body)
    pid = enrol(app, "adapt", "AC-002", 1, {"startDate": "2026-01-05"})
    assert put_kit(app, pid, lambda r: history(r, [(1, "2026-01-05", None), (2, "2026-02-02", None), (3, "2026-03-02", 0),
                                                   (4, "2026-04-13", 0), (5, "2026-06-08", None)])).status_code == 200
    route = f"/api/patients/{pid}/kit/observations"
    newer = {"id": "feed-0002", "key": "inj#5", "input": "activity_signs", "value": 0, "confirmed_at": "2026-06-08T11:00:00+00:00"}
    first = app.client.post(route, json=newer, headers=PI)
    assert first.json()["status"] == "applied", first.text
    revision = first.json()["revision"]
    replay = app.client.post(route, json=newer, headers=PI)
    assert replay.json() == {"status": "replayed", "observation": first.json()["observation"], "revision": revision}
    assert app.client.post(route, json={**newer, "value": 2}, headers=PI).status_code == 409
    # A reading confirmed earlier but delivered later never replaces the newer one.
    older = {**newer, "id": "feed-0001", "value": 2, "confirmed_at": "2026-06-08T09:00:00Z"}
    late = app.client.post(route, json=older, headers=PI).json()
    assert late["status"] == "ignored_older" and late["observation"]["id"] == "feed-0002"
    assert app.client.post(route, json={**newer, "id": "feed-crc", "confirmed_at": "2026-06-08T12:00:00Z"}, headers=CRC).status_code == 403
    staff = app.client.get(f"/api/patients/{pid}/kit", headers=PI).json()
    assert staff["revision"] == revision
    assert [a for _, a, _ in app.log].count("patient.kit_observation") == 1
    before = calendar(staff)
    assert before["staff"]["inj#6"]["planned"] == "2026-08-17"
    keys = [r["key"] for r in before["schedule"]["rows"] if r["type"] == "visit"]
    assert len(keys) == len(set(keys)), "a replayed input never duplicates a visit"
    # A correction confirmed later replaces the finding; recorded visits stay, the next target moves (A9).
    corrected = app.client.post(route, json={**newer, "id": "feed-0003", "value": 1, "confirmed_at": "2026-06-10T08:00:00Z"}, headers=PI)
    assert corrected.json()["status"] == "applied"
    after = calendar(app.client.get(f"/api/patients/{pid}/kit", headers=PI).json())
    assert after["staff"]["inj#6"]["planned"] == "2026-07-20"
    assert [after["staff"][f"inj#{n}"]["actual"] for n in range(1, 6)] == [before["staff"][f"inj#{n}"]["actual"] for n in range(1, 6)]
    # An input for an earlier visit arriving out of order is applied there and the calendar is recomputed.
    early = app.client.post(route, json={"id": "feed-0000", "key": "inj#4", "input": "activity_signs", "value": 1,
                                         "confirmed_at": "2026-04-13T10:00:00Z"}, headers=PI)
    assert early.json()["status"] == "ignored_older"  # inj#4 already has a later confirmation from the staff record
    again = calendar(app.client.get(f"/api/patients/{pid}/kit", headers=PI).json())
    assert again["staff"] == after["staff"], "nothing changed: the result is deterministic"


# ── C3, C4, C5, C6: fixed grids, progression, confirmed bookings and versions still hold ──


def _plain(model: dict, part: dict) -> dict:
    return calendar({"model": model, "site_answers": {}, "participant": part, "workflow_version": 1,
                     "patient": {"id": "x", "trial_id": "t"}, "trial": {}})["staff"]


def test_c3_fixed_grid_targets_do_not_move_when_day_28_is_rebooked():
    model = normalize(benzoyl_timing())
    base = {"startAnchor": "baseline", "startDate": "2026-01-01", "answers": {"wocbp": "no"}, "events": [], "today": "2026-01-01"}
    expected = {"v2#1": ("2026-01-29", "2026-01-25", "2026-02-02"), "v3#1": ("2026-02-26", "2026-02-22", "2026-03-02"),
                "v4#1": ("2026-03-12", "2026-03-08", "2026-03-16")}
    for change in ({}, {"bookings": {"v2#1": "2026-02-01"}}, {"bookings": {"v2#1": "2026-02-05"}},
                   {"bookings": {"v2#1": "2026-02-05"}, "actuals": {"v2#1": "2026-02-05"}}):
        rows = _plain(model, {**base, **change})
        assert {k: (rows[k]["planned"], rows[k]["windowStart"], rows[k]["windowEnd"]) for k in expected} == expected
    rows = _plain(model, {**base, "bookings": {"v2#1": "2026-02-05"}, "actuals": {"v2#1": "2026-02-05"}})
    assert (rows["v2#1"]["bookingStatus"], rows["v2#1"]["deviation"]) == ("outside_window", "outside_window")


def test_c4_progression_stops_future_treatment_and_keeps_follow_up():
    model = normalize({
        "anchors": [{"id": "c1", "label": "Cycle 1 Day 1", "kind": "start", "is_day1": True}],
        "visits": [
            {"id": "tx", "code": "C", "name": "Treatment cycle", "anchor": "c1", "offset_days": 0, "window_before": 0, "window_after": 3,
             "repeat": {"every_days": 21, "count": 8}, "procedures": [{"name": "Infusion"}]},
            {"id": "fu", "code": "FU", "name": "Survival follow-up", "anchor": "c1", "offset_days": 168, "window_before": 7,
             "window_after": 7, "repeat": {"every_days": 84, "count": 3}, "procedures": [{"name": "Survival status"}]}],
        "events": [{"id": "pd", "label": "Progression", "repeatable": False, "questions": [], "visits": []}],
        "rules": [{"id": "r_pd", "label": "Progression stops treatment", "when": {"event": "pd"}, "stop_visits": ["tx"],
                   "effect": "No further cycles."}],
    })
    part = {"startAnchor": "c1", "startDate": "2026-01-05", "answers": {}, "today": "2026-03-01",
            "actuals": {"tx#1": "2026-01-05", "tx#2": "2026-01-26", "tx#3": "2026-02-16"},
            "events": [{"key": "pd-1", "type": "pd", "date": "2026-02-20", "answers": {}}]}
    rows = _plain(model, part)
    assert [k for k in rows if k.startswith("tx#")] == ["tx#1", "tx#2", "tx#3"]
    assert [rows[f"fu#{n}"]["planned"] for n in (1, 2, 3)] == ["2026-06-22", "2026-09-14", "2026-12-07"]


def test_c5_dependent_dose_waits_for_the_confirmed_booking_and_samples_for_the_dose_time():
    model = normalize(crd09_timing())
    part = {"startAnchor": "first_dose", "startDate": "2026-01-01", "answers": {}, "events": [], "today": "2026-01-02"}
    rows = _plain(model, part)
    assert rows["dose2#1"]["status"] == "waiting" and rows["dose2#1"]["planned"] is None
    assert "date" not in rows["dose1#1"]["timepoints"][0], "no clock until the dose time is recorded"
    early = _plain(model, {**part, "answers": {"@next_booking": "2026-01-21"}})
    assert early["dose2#1"]["status"] == "waiting", "a booking before the 21-day minimum does not release Period II"
    rows = _plain(model, {**part, "answers": {"@next_booking": "2026-01-22"}, "doseTimes": {"dose1#1": "08:10", "dose2#1": "11:25"}})
    assert rows["dose2#1"]["planned"] == "2026-01-22"
    assert [rows[k]["timepoints"][0]["date"] for k in ("dose1#1", "dose2#1")] == ["2026-01-02", "2026-01-23"]


def test_c6_patients_stay_pinned_with_their_recorded_inputs_until_moved(app):
    body = analyze(app.client, PROTOCOL_TEXT["SYN-ADAPT-CLOSE"])
    approve(app.client, "adapt", body)
    pid = enrol(app, "adapt", "AC-003", 1, {"startDate": "2026-01-05"})
    assert put_kit(app, pid, lambda r: history(r, [(1, "2026-01-05", None), (2, "2026-02-02", None), (3, "2026-03-02", 0),
                                                   (4, "2026-04-13", 0), (5, "2026-06-08", 0)])).status_code == 200
    # Amendment: the longest interval becomes 8 weeks.
    amended = copy.deepcopy(body)
    amended["baseModel"]["visits"][0]["repeat"]["adaptive"]["max_days"] = 56
    amended["baseModel"]["protocol"]["version"] = "synthetic amendment"
    amended["expectedVersion"] = 1
    amended.pop("analysisJobId")
    approve(app.client, "adapt", amended)
    pinned = app.client.get(f"/api/patients/{pid}/kit", headers=PI).json()
    assert (pinned["workflow_version"], pinned["latest"]["version"], pinned["latest"]["problem"]) == (1, 2, None)
    assert calendar(pinned)["staff"]["inj#6"]["planned"] == "2026-08-17"
    preview = calendar({**pinned, "model": pinned["latest"]["model"]})["staff"]
    assert preview["inj#6"]["planned"] == "2026-08-03", "the amended limit, previewed before moving"
    moved = app.client.post(f"/api/patients/{pid}/kit/move-to-latest?version=2&revision={pinned['revision']}", headers=PI)
    assert moved.status_code == 200, moved.text
    moved = moved.json()
    assert moved["participant"]["observations"] == pinned["participant"]["observations"]
    assert moved["actual_versions"]["inj#5"] == 1
    assert calendar(moved)["staff"]["inj#6"]["planned"] == "2026-08-03"


def test_models_without_the_new_operations_compute_as_before():
    """Saved workflows from before this change carry none of the new fields; their calendars match."""
    model = normalize(benzoyl_timing())
    assert not {"phases", "study_events"} & set(model) and "adaptive" not in json.dumps(model)
    rows = _plain(model, {"startAnchor": "baseline", "startDate": "2026-01-01", "answers": {}, "events": [], "today": "2026-01-01"})
    assert all("needs" not in r and "adaptive" not in r and "studyEffect" not in r for r in rows.values())


def test_analysis_result_carries_the_operations_unchanged_through_clean_up():
    """The cross-check and events check run clean-up twice more; the operations survive unchanged."""
    first = kit_analyze.clean(adapt_close_model(), raw=True)
    again = kit_analyze.clean(copy.deepcopy(first))
    assert again["visits"][0]["repeat"]["adaptive"] == first["visits"][0]["repeat"]["adaptive"]
    assert again["study_events"] == first["study_events"]
    assert not [p for p in first.get("open_points") or [] if p["kind"] == "cleanup"]
