"""HTTP API for the kit extraction path (Add Trial protocol upload).

Imported at the bottom of server.py like org_routes.py / remote_audit_routes.py:
it imports ``db`` / ``current_user`` back from server.

  POST /api/kit/analyze                 multipart PDF -> {job_id}; analysis runs in the background
  GET  /api/kit/jobs/{job_id}           stage progress, then the result
  POST /api/kit/models/patch            base model + review answers -> patched model
  PUT  /api/trials/{id}/kit-model       store the reviewed model on a trial
  GET  /api/trials/{id}/kit-model       read it back
  GET  /api/patients/{id}/kit           a patient's kit record + the trial model (staff, or the patient)
  PUT  /api/patients/{id}/kit           replace it after recording visits/events (PI, CRC)
  GET  /api/me/kit                      the signed-in patient's own record

This path is independent of the older UCTSM extraction: it does not call the
reader/compiler, the identity gate or the canonical schedule build.
"""
from __future__ import annotations

import asyncio
import copy
import os
import re
import time
import uuid
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server import _can_access_trial, _require_patient, current_user, db, now, require_roles, write_audit

from app.kit import analyze as kit_analyze
from app.kit.llm import AnalysisError, OpenAIKitClient
from app.kit.normalize import build_template_model, default_review_answers, normalize
from app.kit.pdf_input import PdfReadError, prepare_protocol_input

router = APIRouter(prefix="/api")

MAX_BYTES = 25 * 1024 * 1024
JOB_TTL_SECONDS = 2 * 60 * 60
STAGES = [
    ("read", "Reading the protocol"),
    ("locate", "Locating the schedule tables"),
    ("model", "Building the protocol model"),
    ("verify", "Cross-checking against the schedule tables"),
    ("check", "Sorting the questions for review"),
]
_jobs: Dict[str, Dict[str, Any]] = {}

_ERROR_STATUS = {
    "no_credits": 402, "model_busy": 503, "rate_limited": 429, "prompt_too_large": 413, "invalid_json": 502, "refused": 422,
    "bad_model": 422, "no_api_key": 503, "auth": 503,
}


def _new_job(user_id: str, file_name: str) -> Dict[str, Any]:
    cutoff = time.time() - JOB_TTL_SECONDS
    for key in [k for k, j in _jobs.items() if j["created"] < cutoff]:
        _jobs.pop(key, None)
    job = {
        "id": str(uuid.uuid4()), "user_id": user_id, "file_name": file_name, "created": time.time(),
        "status": "running", "stages": [{"id": i, "label": l, "status": "wait", "detail": ""} for i, l in STAGES],
        "result": None, "error": None,
    }
    _jobs[job["id"]] = job
    return job


# Stages that appear only when they are needed, shown just before "model".
OPTIONAL_STAGES = {
    "tables": "Reading tables without cell borders",
    "summarize": "Summarising the large protocol",
}


def _set_stage(job: Dict[str, Any], stage_id: str, status: str, detail: Any = "") -> None:
    if stage_id in OPTIONAL_STAGES and not any(s["id"] == stage_id for s in job["stages"]):
        index = next((i for i, s in enumerate(job["stages"]) if s["id"] == "model"), len(job["stages"]))
        job["stages"].insert(index, {
            "id": stage_id, "label": OPTIONAL_STAGES[stage_id],
            "status": "wait", "detail": "",
        })
    for stage in job["stages"]:
        if stage["id"] == stage_id:
            stage["status"] = status
            if isinstance(detail, str):
                stage["detail"] = detail
            elif isinstance(detail, dict):
                stage["detail"] = (f"{detail.get('review', 0)} for you · {detail.get('participant', 0)} per participant · "
                                   f"{detail.get('decisions', 0)} decisions · {detail.get('events', 0)} events")


def _prepare(job: Dict[str, Any], data: bytes, file_name: str) -> Dict[str, Any]:
    if file_name.lower().endswith(".txt"):
        text = data.decode("utf-8-sig")
        _set_stage(job, "read", "done", "text file")
        _set_stage(job, "locate", "done", "text file")
        return {"pageCount": 1, "tablePages": [], "picked": [0], "scanned": False,
                "text": f"[[Page 1]]\n{text}", "tableText": f"[[Page 1]]\n{text}",
                "fullText": True, "pageTexts": [f"[[Page 1]]\n{text}"],
                "tableImages": [], "scanImages": []}
    _set_stage(job, "read", "run", "opening")

    def progress(page: int, total: int) -> None:
        _set_stage(job, "read", "run", f"page {page} of {total}")

    prepared = prepare_protocol_input(data, on_progress=progress)
    sent = "all pages" if prepared["fullText"] else f"{len(prepared['picked'])} selected pages"
    _set_stage(job, "read", "done", f"{prepared['pageCount']} pages · {round(len(prepared['text']) / 1000)}k characters · {sent}")
    if prepared["scanned"]:
        _set_stage(job, "locate", "done", f"no text layer; {len(prepared['scanImages'])} page images")
    elif prepared["tablePages"]:
        borderless = [t["page"] for t in prepared.get("alignedTables") or []]
        how = f" ({len(borderless)} without cell borders)" if borderless else ""
        _set_stage(job, "locate", "done", "tables on p. " + ", ".join(str(p) for p in prepared["tablePages"]) + how)
    else:
        _set_stage(job, "locate", "done", f"{len(prepared['picked'])} relevant pages")
    return prepared


def _run(job: Dict[str, Any], data: bytes, file_name: str) -> Dict[str, Any]:
    active_stage = "model"

    def on_stage(stage_id: str, status: str, detail: Any = "") -> None:
        nonlocal active_stage
        if status == "run":
            active_stage = stage_id
        _set_stage(job, stage_id, status, detail)
    sample = (os.getenv("KIT_SAMPLE_MODEL") or "").strip()
    if sample:
        _set_stage(job, "read", "done", "sample mode")
        _set_stage(job, "locate", "done", "sample mode")
        return kit_analyze.sample_analysis(sample, on_stage)
    prepared = _prepare(job, data, file_name)
    client = OpenAIKitClient()
    job["model"] = client.model

    def on_text(written: int) -> None:
        _set_stage(job, "model", "run", f"{round(written / 1000)}k characters")

    # A busy model is retried automatically; show it instead of looking stuck.
    client.on_status = lambda message: _set_stage(job, active_stage, "run", message)

    return kit_analyze.analyze_protocol(prepared, client, on_stage=on_stage, on_text=on_text)


async def _execute(job: Dict[str, Any], data: bytes, file_name: str) -> None:
    try:
        job["result"] = await run_in_threadpool(_run, job, data, file_name)
        job["status"] = "done"
    except AnalysisError as error:
        job["status"], job["error"] = "failed", {"code": error.code, "message": str(error)}
    except PdfReadError as error:
        job["status"], job["error"] = "failed", {"code": "bad_pdf", "message": str(error)}
    except Exception as error:  # noqa: BLE001 - recorded on the job, never raised into the request
        job["status"], job["error"] = "failed", {"code": "internal", "message": f"Analysis failed: {error}"}
    for stage in job["stages"]:
        if stage["status"] == "run":
            stage["status"] = "wait" if job["status"] == "failed" else "done"


def _details(model: Dict[str, Any]) -> Dict[str, Any]:
    p = model.get("protocol") or {}
    return {
        "protocol_number": p.get("number") or "", "title": p.get("title") or "", "phase": p.get("phase") or "",
        "indications": [p["indication"]] if p.get("indication") else [], "sponsor": p.get("sponsor") or "",
        "version": p.get("version") or "", "design": p.get("design") or "",
    }


@router.post("/kit/analyze", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def start_analysis(file: UploadFile = File(...), expected_protocol_id: Optional[str] = Form(None),
                         user=Depends(current_user)):
    name = file.filename or "protocol.pdf"
    is_text = (file.content_type or "").lower() == "text/plain" or name.lower().endswith(".txt")
    if (file.content_type or "").lower() != "application/pdf" and not name.lower().endswith(".pdf") and not is_text:
        raise HTTPException(400, "Upload a PDF or UTF-8 text protocol document")
    data = await file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(400, "The uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Protocol is too large (maximum 25 MB)")
    if is_text:
        try:
            if not data.decode("utf-8-sig").strip():
                raise HTTPException(400, "The uploaded text is empty")
        except UnicodeDecodeError as error:
            raise HTTPException(400, "Text protocols must use UTF-8 encoding") from error
    job = _new_job(user["id"], name)
    job["expected_protocol_id"] = (expected_protocol_id or "").strip() or None
    asyncio.create_task(_execute(job, data, name))
    await write_audit(user, "trial.kit_analyze", f"Started protocol analysis of {name}")
    return {"job_id": job["id"], "stages": job["stages"]}


@router.get("/kit/jobs/{job_id}", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def get_job(job_id: str, user=Depends(current_user)):
    job = _jobs.get(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Analysis not found")
    out: Dict[str, Any] = {"job_id": job["id"], "status": job["status"], "stages": job["stages"],
                           "file_name": job["file_name"], "model_name": job.get("model")}
    if job["status"] == "failed":
        out["error"] = job["error"]
        out["http_status"] = _ERROR_STATUS.get(job["error"]["code"], 502)
    if job["status"] == "done":
        result = job["result"]
        out["result"] = {**result, "details": _details(result["model"])}
    return out


class PatchIn(BaseModel):
    baseModel: Dict[str, Any]
    reviewAnswers: Dict[str, str] = Field(default_factory=dict)


@router.post("/kit/models/patch", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def patch_model(body: PatchIn):
    try:
        base = normalize(copy.deepcopy(body.baseModel))
        return {"model": build_template_model(base, body.reviewAnswers)}
    except ValueError as error:
        raise HTTPException(422, "The protocol model is not valid") from error


class KitModelIn(BaseModel):
    baseModel: Dict[str, Any]
    reviewAnswers: Dict[str, str] = Field(default_factory=dict)
    siteAnswers: Dict[str, str] = Field(default_factory=dict)
    fileName: Optional[str] = None


async def _trial_for(user: dict, trial_id: str) -> dict:
    trial = await db.trials.find_one({"id": trial_id}, {"_id": 0})
    if not trial:
        raise HTTPException(404, "Trial not found")
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, "You do not have access to this trial")
    return trial


def _checked(q: dict, value: Any) -> bool:
    if value is None or value == "":
        return False
    if q["kind"] == "boolean":
        return value in ("yes", "no")
    if q["kind"] == "choice":
        return any(o["value"] == str(value) for o in q["options"])
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


@router.put("/trials/{trial_id}/kit-model", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def save_kit_model(trial_id: str, body: KitModelIn, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    try:
        base = normalize(copy.deepcopy(body.baseModel))
        model = build_template_model(base, body.reviewAnswers or default_review_answers(base))
    except ValueError as error:
        raise HTTPException(422, "The protocol model is not valid") from error
    missing = [q["id"] for q in model["questions"]
               if q["scope"] == "study" and not _checked(q, body.siteAnswers.get(q["id"]))]
    if missing:
        raise HTTPException(400, {"message": "Answer every site setting.", "fields": missing})
    doc = {
        "trial_id": trial_id, "base_model": base, "review_answers": body.reviewAnswers,
        "site_answers": body.siteAnswers, "model": model, "file_name": body.fileName,
        "saved_by": user["id"], "saved_at": now(),
    }
    await db.trial_kit_models.replace_one({"trial_id": trial_id}, doc, upsert=True)
    await write_audit(user, "trial.kit_model_save", f"Saved the reviewed protocol model ({len(model['visits'])} visits)",
                      target_id=trial_id, trial_id=trial_id)
    return {"trial_id": trial_id, "visits": len(model["visits"]), "saved_at": doc["saved_at"]}


@router.get("/trials/{trial_id}/kit-model")
async def read_kit_model(trial_id: str, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    doc = await db.trial_kit_models.find_one({"trial_id": trial_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "No reviewed protocol model has been saved for this trial")
    return doc


# ── Patients on the kit schedule ────────────────────────────────────────────
# A patient's schedule is not stored as dated rows. Like the kit, the patient
# record keeps only what the engine needs (start date, answers, recorded visit
# dates, logged events) under `kit`; every client computes the dates from that
# plus the trial's reviewed model, so a recorded visit moves its followers.

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _iso(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        if not _ISO.match(text):
            raise ValueError
        date.fromisoformat(text)
    except ValueError:
        raise HTTPException(422, f"{field}: enter a date as YYYY-MM-DD") from None
    return text


def _answer(q: dict, value: Any, where: str) -> str:
    text = str(value).strip()
    if not _checked(q, text):
        raise HTTPException(422, f"{where}: “{text}” is not an answer to “{q['label']}”")
    return text


def clean_participant(model: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """Check one patient's kit record against the trial's reviewed model.

    Raises 422 naming the first problem. Participant questions must all be
    answered (they are known at enrolment by definition); decisions, ask-anchor
    dates, events and recorded visits are optional and filled in over time.
    """
    if not isinstance(raw, dict):
        raise HTTPException(422, "The schedule details are missing")
    start = next(a for a in model["anchors"] if a["kind"] == "start")
    anchors = {a["id"]: a for a in model["anchors"]}
    questions = {q["id"]: q for q in model["questions"] if q["scope"] in ("participant", "decision")}

    answers: Dict[str, str] = {}
    for key, value in (raw.get("answers") or {}).items():
        if value in (None, ""):
            continue
        key = str(key)
        if key.startswith("@"):
            anchor = anchors.get(key[1:])
            if not anchor or anchor["kind"] != "ask":
                raise HTTPException(422, f"Unknown protocol date “{key[1:]}”")
            answers[key] = _iso(value, anchor["label"])
        elif key in questions:
            answers[key] = _answer(questions[key], value, "Answer")
        else:
            raise HTTPException(422, f"Unknown question “{key}”")
    missing = [q["label"] for q in questions.values() if q["scope"] == "participant" and not answers.get(q["id"])]
    if missing:
        raise HTTPException(422, "Answer: " + ", ".join(missing))

    events_by_id = {e["id"]: e for e in model["events"]}
    events = []
    for item in raw.get("events") or []:
        if not isinstance(item, dict) or item.get("type") not in events_by_id:
            raise HTTPException(422, "Unknown event")
        definition = events_by_id[item["type"]]
        if not definition["repeatable"] and any(e["type"] == definition["id"] for e in events):
            raise HTTPException(422, f"“{definition['label']}” can only be logged once")
        event_questions = {q["id"]: q for q in definition["questions"]}
        event_answers = {}
        for key, value in (item.get("answers") or {}).items():
            if value in (None, ""):
                continue
            if key not in event_questions:
                raise HTTPException(422, f"Unknown question “{key}” on {definition['label']}")
            event_answers[key] = _answer(event_questions[key], value, definition["label"])
        events.append({
            "key": str(item.get("key") or uuid.uuid4().hex[:8]), "type": definition["id"],
            "date": _iso(item.get("date"), definition["label"]), "answers": event_answers,
        })

    visit_ids = {v["id"] for v in model["visits"]} | {v["id"] for e in model["events"] for v in e["visits"]}

    def occurrence(key: Any) -> str:
        text = str(key)
        visit_id, _, n = text.rpartition("#")
        if visit_id not in visit_ids or not n.lstrip("-").isdigit():
            raise HTTPException(422, f"Unknown visit “{text}”")
        return text

    actuals = {occurrence(k): _iso(v, "Visit date") for k, v in (raw.get("actuals") or {}).items() if v}
    dose_times = {}
    for key, value in (raw.get("doseTimes") or {}).items():
        if not value:
            continue
        if not _TIME.match(str(value)):
            raise HTTPException(422, "Dose time: enter HH:MM")
        dose_times[occurrence(key)] = str(value)

    return {
        "startAnchor": start["id"], "startDate": _iso(raw.get("startDate"), start["label"]),
        "answers": answers, "events": events, "actuals": actuals, "doseTimes": dose_times,
    }


async def _kit_payload(patient: dict, user: dict) -> dict:
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    saved = await db.trial_kit_models.find_one({"trial_id": patient.get("trial_id")}, {"_id": 0})
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    trial = await db.trials.find_one({"id": patient.get("trial_id")}, {"_id": 0, "id": 1, "title": 1, "protocol_id": 1}) or {}
    return {
        # Sponsors and CROs see subjects de-identified, as everywhere else.
        "patient": {key: patient.get(key) for key in ("id", "subject_id", "full_name", "avatar_initials", "trial_id")
                    if not (key == "full_name" and user["role"] in ("sponsor", "cro"))},
        "trial": trial, "model": saved["model"], "site_answers": saved.get("site_answers") or {},
        "participant": patient["kit"], "updated_at": patient.get("kit_updated_at"),
    }


@router.get("/patients/{patient_id}/kit")
async def read_patient_kit(patient_id: str, user=Depends(current_user)):
    if user["role"] == "patient":
        patient = await db.patients.find_one({"id": patient_id, "user_id": user["id"]}, {"_id": 0})
        if not patient:
            raise HTTPException(404, "Patient not found")
    else:
        patient = await _require_patient(user, patient_id)
    return await _kit_payload(patient, user)


@router.get("/me/kit", dependencies=[Depends(require_roles("patient"))])
async def read_my_kit(user=Depends(current_user)):
    patient = await db.patients.find_one(
        {"user_id": user["id"], "kit": {"$exists": True}}, {"_id": 0}, sort=[("created_at", -1)])
    if not patient:
        raise HTTPException(404, "You are not on a reviewed protocol schedule yet")
    return await _kit_payload(patient, user)


@router.put("/patients/{patient_id}/kit", dependencies=[Depends(require_roles("pi", "crc"))])
async def save_patient_kit(patient_id: str, body: Dict[str, Any], user=Depends(current_user)):
    patient = await _require_patient(user, patient_id)
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    saved = await db.trial_kit_models.find_one({"trial_id": patient.get("trial_id")}, {"_id": 0, "model": 1})
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    participant = clean_participant(saved["model"], body)
    stamp = now()
    await db.patients.update_one({"id": patient_id}, {"$set": {"kit": participant, "kit_updated_at": stamp}})
    await write_audit(user, "patient.kit_update",
                      f"Updated schedule: {len(participant['actuals'])} visits recorded, {len(participant['events'])} events",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return await _kit_payload({**patient, "kit": participant, "kit_updated_at": stamp}, user)
