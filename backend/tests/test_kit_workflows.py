"""Exercise the app's workflow routes with replayed extraction and synthetic participants."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

import server
import kit_routes
from app.kit.normalize import normalize
from app.kit.workflow import build_workflow, compile_workflow
from test_kit_patterns import Coll, _matches


class Collection(Coll):
    async def replace_one(self, query, doc, upsert=False):
        matches = sum(_matches(d, query) for d in self.docs)
        await super().replace_one(query, doc, upsert)
        return SimpleNamespace(matched_count=matches)

    async def update_one(self, query, update, upsert=False):
        matches = sum(_matches(d, query) for d in self.docs)
        await super().update_one(query, update)
        if not matches and upsert:
            await self.insert_one({**query, **update["$setOnInsert"]})
        return SimpleNamespace(matched_count=matches)

    async def delete_one(self, query):
        self.docs = [d for d in self.docs if not _matches(d, query)]


def benzoyl_timing():
    """Source timing subset only. Physical PDF pages 46–48 fix all visits to Visit 1."""
    return {
        "protocol": {"number": "NCS-CT-006-AL-Benz", "version": "Final", "date": "2016-06-29"},
        "anchors": [{"id": "baseline", "label": "Actual Day 0", "kind": "start", "is_day1": True, "day_zero": True}],
        "questions": [{"id": "wocbp", "label": "Childbearing potential", "text": "Is pregnancy testing applicable?",
                       "kind": "boolean", "scope": "participant"}],
        "visits": [
            {"id": f"v{i}", "code": f"V{i}", "name": f"Day {day}", "offset_days": day,
             "anchor": "baseline", "window_before": window, "window_after": window, "source": f"PDF page {page}",
             "procedures": [{"name": "Lesion count", "source": f"PDF page {page}"},
                            {"name": "Pregnancy test", "if": [{"question": "wocbp", "in": ["yes"]}]}]}
            for i, (day, window, page) in enumerate([(0, 0, 46), (28, 4, 46), (56, 4, 47), (70, 4, 48)], 1)
        ],
    }


def crd09_timing():
    """The confirmed interval and one timed sample per period from PDF pages 39 and 42–43."""
    return {
        "protocol": {"number": "CRD/09", "version": "01", "date": "2016-08-12"},
        "anchors": [
            {"id": "first_dose", "label": "Period I actual first dose", "kind": "start", "is_day1": True},
            {"id": "next_booking", "label": "Period II confirmed booking", "kind": "ask",
             "question": "What date is the Period II dose booked?", "date_basis": "confirmed",
             "range": {"relative_to": "first_dose", "min_days": 21, "max_days": None},
             "source": "PDF page 39"},
        ],
        "visits": [
            {"id": f"dose{i}", "code": f"P{i}", "name": f"Period {i} dose", "anchor": anchor,
             "offset_days": 0, "window_stated": False, "source": "PDF page 39",
             "timepoints": [{"label": "24-hour PK sample", "offset_min": 1440, "window_after_min": 2,
                             "source": "PDF pages 42–43", "procedures": [{"name": "PK sample"}]}]}
            for i, anchor in [(1, "first_dose"), (2, "next_booking")]
        ],
    }


@pytest.fixture()
def app_client(monkeypatch):
    db = SimpleNamespace(**{name: Collection() for name in (
        "trial_kit_models", "trial_kit_model_history", "trial_kit_drafts", "trial_kit_shared_dates",
        "patients", "invitations", "trials", "users", "kit_study_effects", "kit_study_events", "notifications",
    )})
    db.trials.docs.append({"id": "trial", "title": "Synthetic test trial", "protocol_id": "NCS-CT-006-AL-Benz"})
    db.trials.docs.append({"id": "crd09", "title": "CRD/09 timing replay", "protocol_id": "CRD/09"})
    db.users.docs.append({"id": "pi", "role": "pi"})
    log = []
    files = {}

    def current_user(x_role: str = Header("sponsor")):
        return {"id": x_role, "role": x_role}

    async def can_access(*args):
        return True

    async def audit(user, action, detail, **context):
        log.append((user["id"], action, detail))

    async def require_patient(user, patient_id):
        return await db.patients.find_one({"id": patient_id})

    async def store_file(sha, raw, filename, trial_id):
        files[sha] = raw

    async def read_file(sha):
        return files[sha], {"content_type": "text/plain", "file_name": "schedule.txt"}

    def analyze(job, data, file_name):
        kit_routes._prepare(job, data, file_name)
        if b"CRD/09" in data:
            base = normalize(crd09_timing())
            return {"baseModel": base, "model": base}
        base = normalize(benzoyl_timing())
        if b"amendment" in data:
            base["protocol"]["version"] = "synthetic amendment"
            base["visits"][3]["offset_days"] = 77
        elif b"shared-date" in data:
            base["anchors"].append({"id": "close", "label": "Shared study end", "kind": "ask", "scope": "study"})
            base["visits"].append({"id": "closeout", "name": "Final contact", "anchor": "close", "offset_days": 0})
            base = normalize(base)
        else:
            base["open_points"] = [{
                "id": "window", "kind": "ambiguous", "text": "Verify the Day 28 window", "why": "",
                "quote": "Day 28 ± 4 days from the date of Visit 1", "page": 46, "affects": ["v2"],
            }]
        return {"baseModel": base, "model": base}

    for module in (server, kit_routes):
        monkeypatch.setattr(module, "db", db)
        monkeypatch.setattr(module, "_can_access_trial", can_access)
        monkeypatch.setattr(module, "write_audit", audit)
    monkeypatch.setattr(kit_routes, "_require_patient", require_patient)
    monkeypatch.setattr(kit_routes, "_store_protocol_file", store_file)
    monkeypatch.setattr(kit_routes, "_read_protocol_file", read_file)
    monkeypatch.setattr(kit_routes, "_run", analyze)
    monkeypatch.delenv("UCTSM_AUTHORITATIVE", raising=False)
    app = FastAPI()
    app.include_router(kit_routes.router)
    enrollment = APIRouter()
    enrollment.routes.extend([r for r in server.api.routes if r.path == "/api/patients/invite"])
    app.include_router(enrollment)
    app.dependency_overrides[server.current_user] = current_user
    with TestClient(app) as client:
        yield client, db, log


def upload(client, raw, current_version):
    response = client.post("/api/kit/analyze", files={"file": ("schedule.txt", raw, "text/plain")})
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/api/kit/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "done", job
    result = job["result"]
    return {"baseModel": result["baseModel"], "analysisJobId": job_id, "fileSha256": result["fileSha256"],
            "fileName": "schedule.txt", "expectedVersion": current_version}


def post_preview(client, body):
    response = client.post("/api/kit/workflow-preview", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def calculate(tmp_path, records):
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    out = {}
    for name, record in records.items():
        source = tmp_path / "input.json"
        target = tmp_path / "output.json"
        source.write_text(json.dumps({"model": record["model"], "site": record["site_answers"],
                                      "records": {name: record["participant"]}}), encoding="utf-8")
        subprocess.run(["node", "--experimental-strip-types", "--import", "./tests/kit-resolve-hook.mjs",
                        "scripts/kit-schedule-cli.ts", str(source), str(target)],
                       cwd=frontend, check=True, capture_output=True, timeout=20)
        out[name] = {r["key"]: r for r in json.loads(target.read_text(encoding="utf-8"))[name]["rows"]}
    return out


def test_uploaded_blocks_approve_and_pin_the_patient_calendar(app_client, tmp_path):
    client, db, log = app_client
    route = "/api/trials/trial/kit-model"
    body = upload(client, b"Schedule excerpt replay", 0)
    preview = post_preview(client, body)
    [issue] = preview["workflow"]["issues"]
    assert issue["blocks"] == ["visit:v2"] and issue["page"] == 46
    assert client.put(route, json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]}).status_code == 422
    assert client.put(route, json={**body, "workflowAction": "draft"}).json()["status"] == "draft"
    assert client.get(route).status_code == 404
    assert client.get("/api/trials/trial/kit-draft").json()["workflow"]["version"] == 1
    body["resolutions"] = {"window": "Physical page 46 supports four days before and after Day 28."}
    body["blockEdits"] = {"visit:v2": {"id": "v2", "anchor": "invalid"}}
    assert client.post("/api/kit/workflow-preview", json=body).status_code == 422
    body.pop("blockEdits")
    body.pop("analysisJobId")
    preview = post_preview(client, body)
    assert not preview["problems"]
    approved = client.put(route, json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert approved.status_code == 200, approved.text
    assert client.get(f"{route}/file").content == b"Schedule excerpt replay"

    pi = {"X-Role": "pi"}
    invite = {"full_name": "Synthetic participant", "phone": "+12025550102", "trial_id": "trial",
              "subject_id": "T-01", "kit_model_version": 1, "kit": {"startDate": "2026-01-01"}}
    response = client.post("/api/patients/invite", json=invite, headers=pi)
    assert response.status_code == 200, response.text
    # Registration materializes the already-pinned invitation data. Use the enrollment write helper
    # boundary here to keep OTP and outbound messaging out of the scheduling test.
    materialized = {**db.invitations.docs[0]["patient_data"], "id": "patient"}
    db.patients.docs.append(materialized)
    second_invite = {**invite, "phone": "+12025550103", "subject_id": "T-02",
                     "kit": {"startDate": "2028-02-20", "answers": {"wocbp": "yes"}}}
    second = client.post("/api/patients/invite", json=second_invite, headers=pi)
    assert second.status_code == 200, second.text
    db.patients.docs.append({**db.invitations.docs[1]["patient_data"], "id": "second"})
    patient_route = "/api/patients/patient/kit"
    before = client.get(patient_route, headers=pi).json()
    assert before["workflow_version"] == 1
    assert client.get(patient_route).status_code == 403
    update = deepcopy(before["participant"])
    update.update(bookings={"v2#1": "2026-02-01"}, actuals={"v2#1": "2026-02-02"})
    for _ in range(2):
        response = client.put(f"{patient_route}?revision=0", headers=pi, json=update)
        assert response.status_code == 200, response.text
    after = response.json()
    assert after["revision"] == 1
    assert [action for _, action, _ in log].count("patient.kit_update") == 1
    second_record = client.get("/api/patients/second/kit", headers=pi).json()
    calendars = calculate(tmp_path, {"before": before, "after": after, "second": second_record})
    for visit, target, lower, upper in [
        ("v2#1", "2026-01-29", "2026-01-25", "2026-02-02"),
        ("v3#1", "2026-02-26", "2026-02-22", "2026-03-02"),
        ("v4#1", "2026-03-12", "2026-03-08", "2026-03-16"),
    ]:
        for rows in (calendars["before"], calendars["after"]):
            row = rows[visit]
            assert (row["planned"], row["windowStart"], row["windowEnd"]) == (target, lower, upper)
    assert calendars["after"]["v2#1"]["actual"] == "2026-02-02"
    assert calendars["after"]["v2#1"]["booked"] == "2026-02-01"
    second_v2 = calendars["second"]["v2#1"]
    assert (second_v2["planned"], second_v2["windowStart"], second_v2["windowEnd"]) == ("2028-03-19", "2028-03-15", "2028-03-23")
    assert any(p["name"] == "Pregnancy test" and not p["conditional"] for p in second_v2["procedures"])

    amend = upload(client, b"synthetic amendment", 1)
    preview = post_preview(client, amend)
    response = client.put(route, json={**amend, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert response.status_code == 200, response.text
    pinned = client.get(patient_route, headers=pi).json()
    assert pinned["workflow_version"] == 1 and pinned["latest"]["version"] == 2
    assert client.post(f"{patient_route}/move-to-latest?version=1&revision=1", headers=pi).status_code == 409
    adopted = client.post(f"{patient_route}/move-to-latest?version=2&revision=1", headers=pi)
    assert adopted.status_code == 200, adopted.text
    adopted = adopted.json()
    assert adopted["workflow_version"] == 2 and adopted["actual_versions"] == {"v2#1": 1}
    assert adopted["participant"]["actuals"] == update["actuals"]
    moved = calculate(tmp_path, {"adopted": adopted})["adopted"]
    assert moved["v4#1"]["planned"] == "2026-03-19"
    assert moved["v2#1"]["actual"] == "2026-02-02"
    assert len(db.trial_kit_model_history.docs) == 1


def test_workflow_compile_preserves_operations_and_event_identity():
    model = benzoyl_timing()
    model["events"] = [{"id": "progression", "label": "Progression", "visits": [
        {"id": "follow", "name": "Follow-up", "offset_days": 10},
    ]}]
    model = normalize(model)
    workflow = build_workflow(model, 1)
    assert compile_workflow(workflow) == model
    event = next(b for b in workflow["blocks"] if b["kind"] == "event")
    assert event["paths"][0]["block"] == "event_visit:progression:follow"
    factual = {"key": "log-1", "type": "progression", "date": "2026-01-21"}
    clean = kit_routes.clean_participant(model, {"startDate": "2026-01-01", "events": [factual, factual]}, workflow=True)
    assert len(clean["events"]) == 1


def test_investigator_only_inputs_are_checked_on_the_server():
    model = benzoyl_timing()
    model["questions"][0]["answer_roles"] = ["pi"]
    model = normalize(model)
    workflow = build_workflow(model, 1)
    question = next(b for b in workflow["blocks"] if b["id"] == "question:wocbp")
    assert question["prompts"][0]["roles"] == ["pi"]
    answer = {"answers": {"wocbp": "yes"}}
    with pytest.raises(HTTPException) as error:
        kit_routes.check_workflow_input_roles(model, {}, answer, "crc")
    assert error.value.status_code == 403
    kit_routes.check_workflow_input_roles(model, {}, answer, "pi")
    kit_routes.check_workflow_input_roles(model, answer, answer, "crc")


def test_correcting_a_branch_leaves_completed_attendance_visible(tmp_path):
    raw = benzoyl_timing()
    for visit in raw["visits"]:
        visit["procedures"] = []
    raw["visits"][2]["include_if"] = [{"question": "wocbp", "in": ["yes"]}]
    model = normalize(raw)
    before = {"model": model, "site_answers": {}, "participant": {
        "startAnchor": "baseline", "startDate": "2026-01-01", "answers": {"wocbp": "yes"},
        "actuals": {"v3#1": "2026-02-26"},
    }}
    after = deepcopy(before)
    after["participant"]["answers"]["wocbp"] = "no"
    rows = calculate(tmp_path, {"before": before, "after": after})
    assert rows["before"]["v3#1"]["actual"] == rows["after"]["v3#1"]["actual"] == "2026-02-26"
    assert rows["after"]["v3#1"]["status"] == "done"
    assert rows["before"]["v4#1"] == rows["after"]["v4#1"]


def test_shared_date_releases_only_its_dependent_visits(app_client, tmp_path):
    client, db, log = app_client
    body = upload(client, b"synthetic shared-date", 0)
    preview = post_preview(client, body)
    route = "/api/trials/trial/kit-model"
    approval = client.put(route, json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert approval.status_code == 200, approval.text
    db.patients.docs.append({"id": "patient", "trial_id": "trial", "kit_model_version": 1,
                             "kit": {"startAnchor": "baseline", "startDate": "2026-01-01", "answers": {}}})
    pi = {"X-Role": "pi"}
    patient = "/api/patients/patient/kit"
    before = client.get(patient, headers=pi).json()
    shared = f"{route}/1/study-dates/close"
    payload = {"date": "2026-04-01", "revision": 0}
    assert client.put(shared, json=payload, headers={"X-Role": "crc"}).status_code == 403
    for _ in range(2):
        response = client.put(shared, json=payload, headers=pi)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 1
    assert client.put(shared, json={**payload, "date": "2026-04-02"}, headers=pi).status_code == 409
    after = client.get(patient, headers=pi).json()
    rows = calculate(tmp_path, {"before": before, "after": after})
    assert rows["before"]["closeout#1"]["status"] == "waiting"
    assert rows["after"]["closeout#1"]["planned"] == "2026-04-01"
    assert rows["before"]["v4#1"] == rows["after"]["v4#1"]
    assert client.get(route).json()["workflow"] == preview["workflow"]
    assert [action for _, action, _ in log].count("trial.kit_study_date") == 1


def test_confirmed_booking_releases_second_period_and_preserves_sample_clocks(app_client, tmp_path):
    client, db, _ = app_client
    body = upload(client, b"CRD/09 timing excerpt replay", 0)
    preview = post_preview(client, body)
    route = "/api/trials/crd09/kit-model"
    approval = client.put(route, json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert approval.status_code == 200, approval.text
    pi = {"X-Role": "pi"}
    invite = client.post("/api/patients/invite", headers=pi, json={
        "full_name": "Synthetic crossover", "phone": "+12025550104", "trial_id": "crd09", "subject_id": "CRD-001",
        "kit_model_version": 1, "kit": {"startDate": "2026-01-01", "doseTimes": {"dose1#1": "08:10"}},
    })
    assert invite.status_code == 200, invite.text
    db.patients.docs.append({**db.invitations.docs[0]["patient_data"], "id": "crossover"})
    details = "/api/patients/crossover/kit"
    before = client.get(details, headers=pi).json()
    part = deepcopy(before["participant"])
    part["answers"]["@next_booking"] = "2026-01-21"
    assert client.put(details + "?revision=0", json=part, headers=pi).status_code == 422
    part["answers"]["@next_booking"] = "2026-01-22"
    part["doseTimes"]["dose2#1"] = "11:25"
    response = client.put(details + "?revision=0", json=part, headers=pi)
    assert response.status_code == 200, response.text
    rows = calculate(tmp_path, {"before": before, "after": response.json()})
    assert rows["before"]["dose2#1"]["status"] == "waiting"
    assert rows["before"]["dose1#1"] == rows["after"]["dose1#1"]
    [first] = rows["after"]["dose1#1"]["timepoints"]
    [second] = rows["after"]["dose2#1"]["timepoints"]
    assert (first["date"], first["at"], first["window"]) == ("2026-01-02", "08:10 (+1 d)", "08:10 (+1 d) \u2013 08:12 (+1 d)")
    assert (second["date"], second["at"], second["window"]) == ("2026-01-23", "11:25 (+1 d)", "11:25 (+1 d) \u2013 11:27 (+1 d)")
