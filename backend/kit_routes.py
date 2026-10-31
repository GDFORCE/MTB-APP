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
import hashlib
import math
import os
import re
import time
import uuid
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
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
    ("events", "Checking what each event changes"),
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
    hidden = prepared.get("hiddenPages") or []
    shown = (f" · hidden content read on p. {', '.join(str(h['page']) for h in hidden)}" if hidden else "")
    _set_stage(job, "read", "done",
               f"{prepared['pageCount']} pages · {round(len(prepared['text']) / 1000)}k characters · {sent}{shown}")
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
        # Which file the model was read from, so a saved model can be matched to its protocol.
        # The file itself stays with the job until the model is saved (or the job expires).
        job["result"]["fileSha256"] = hashlib.sha256(data).hexdigest()
        job["file"] = data
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
    fileSha256: Optional[str] = None
    # What the analysis reported but the model itself does not carry.
    warnings: list[str] = Field(default_factory=list)
    corrections: Optional[list[str]] = None
    # The analysis job the model came from: its protocol file is stored with the saved model.
    analysisJobId: Optional[str] = None


PROTOCOL_FILES = "kit_protocol_files"


async def _store_protocol_file(sha: str, data: bytes, file_name: Optional[str], trial_id: str) -> None:
    """The protocol a model was read from, kept once per content hash (GridFS: files can exceed
    a document's size limit)."""
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    if await db[f"{PROTOCOL_FILES}.files"].find_one({"filename": sha}, {"_id": 1}):
        return
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=PROTOCOL_FILES)
    is_pdf = data[:5] == b"%PDF-"
    await bucket.upload_from_stream(sha, data, metadata={
        "trial_id": trial_id, "file_name": file_name, "content_type": "application/pdf" if is_pdf else "text/plain"})


async def _read_protocol_file(sha: str) -> Optional[tuple[bytes, dict]]:
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    meta = await db[f"{PROTOCOL_FILES}.files"].find_one({"filename": sha})
    if not meta:
        return None
    stream = await AsyncIOMotorGridFSBucket(db, bucket_name=PROTOCOL_FILES).open_download_stream(meta["_id"])
    data: Any = await stream.read()  # type: ignore[misc]  # motor's read() is a coroutine
    return data, meta.get("metadata") or {}


async def _model_doc(trial_id: Any, version: Any) -> Optional[dict]:
    """The saved model a patient is scheduled with: the current one, or the earlier version they
    were enrolled on (kept in history when the model was saved again)."""
    current = await db.trial_kit_models.find_one({"trial_id": trial_id}, {"_id": 0})
    if not current or version is None or int(current.get("version") or 1) == int(version):
        return current
    return await db.trial_kit_model_history.find_one({"trial_id": trial_id, "version": int(version)}, {"_id": 0})


def _record_problem(model: dict, record: Any) -> Optional[str]:
    """Why a patient's record does not fit a model, or None when it does."""
    try:
        clean_participant(model, record)
        return None
    except HTTPException as error:
        return str(error.detail)


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
    previous = await db.trial_kit_models.find_one({"trial_id": trial_id}, {"_id": 0})
    sha = (body.fileSha256 or "").strip().lower()
    doc = {
        "trial_id": trial_id, "base_model": base, "review_answers": body.reviewAnswers,
        "site_answers": body.siteAnswers, "model": model, "file_name": body.fileName,
        "file_sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else None,
        "analysis_warnings": [str(w) for w in body.warnings][:50],
        "corrections": None if body.corrections is None else [str(c) for c in body.corrections][:200],
        "version": int((previous or {}).get("version") or (1 if previous else 0)) + 1,
        "saved_by": user["id"], "saved_at": now(),
    }
    # Earlier versions are kept, never overwritten: patients were scheduled with them, and they
    # stay on the version they were enrolled on until staff move them (nobody moves by default).
    if previous:
        previous_version = int(previous.get("version") or 1)
        await db.trial_kit_model_history.insert_one({**previous, "version": previous_version,
                                                     "replaced_at": doc["saved_at"], "replaced_by": user["id"]})
        await db.patients.update_many(
            {"trial_id": trial_id, "kit": {"$exists": True}, "kit_model_version": {"$exists": False}},
            {"$set": {"kit_model_version": previous_version}})
        # Invited but not yet registered: the patient record is created from the invitation.
        await db.invitations.update_many(
            {"trial_id": trial_id, "status": "pending", "patient_data.kit": {"$exists": True},
             "patient_data.kit_model_version": {"$exists": False}},
            {"$set": {"patient_data.kit_model_version": previous_version}})
    job = _jobs.get(body.analysisJobId or "")
    if job and job["user_id"] == user["id"] and job.get("file") and doc["file_sha256"] \
            and hashlib.sha256(job["file"]).hexdigest() == doc["file_sha256"]:
        await _store_protocol_file(doc["file_sha256"], job["file"], body.fileName, trial_id)
        doc["file_stored"] = True
    elif doc["file_sha256"] and previous and previous.get("file_sha256") == doc["file_sha256"]:
        doc["file_stored"] = bool(previous.get("file_stored"))
    else:
        doc["file_stored"] = False
    await db.trial_kit_models.replace_one({"trial_id": trial_id}, doc, upsert=True)

    # Which patients on earlier versions could move to this one as they are.
    earlier, movable, blocked = 0, 0, []
    async for patient in db.patients.find(
            {"trial_id": trial_id, "kit": {"$exists": True}, "kit_model_version": {"$ne": doc["version"]}},
            {"_id": 0, "id": 1, "subject_id": 1, "kit": 1}):
        earlier += 1
        problem = _record_problem(model, patient["kit"])
        if problem is None:
            movable += 1
        elif len(blocked) < 20:
            blocked.append({"patient_id": patient["id"], "subject_id": patient.get("subject_id"), "problem": problem})
    await write_audit(user, "trial.kit_model_save", f"Saved the reviewed protocol model ({len(model['visits'])} visits)",
                      target_id=trial_id, trial_id=trial_id)
    return {"trial_id": trial_id, "visits": len(model["visits"]), "saved_at": doc["saved_at"], "version": doc["version"],
            "file_stored": doc["file_stored"],
            "patients": {"on_earlier_version": earlier, "can_move": movable, "cannot_move": blocked}}


@router.get("/trials/{trial_id}/kit-model/file")
async def download_protocol_file(trial_id: str, version: Optional[int] = None, user=Depends(current_user)):
    """The protocol file a saved model (current, or an earlier version) was read from."""
    if user["role"] == "patient":
        raise HTTPException(403, "Not available")
    await _trial_for(user, trial_id)
    saved = await _model_doc(trial_id, version)
    if not saved or not saved.get("file_sha256") or not saved.get("file_stored"):
        raise HTTPException(404, "The protocol file was not stored with this model")
    found = await _read_protocol_file(saved["file_sha256"])
    if not found:
        raise HTTPException(404, "The protocol file was not stored with this model")
    data, meta = found
    name = (saved.get("file_name") or meta.get("file_name") or "protocol.pdf").replace('"', "")
    return Response(data, media_type=meta.get("content_type") or "application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/trials/{trial_id}/kit-model")
async def read_kit_model(trial_id: str, version: Optional[int] = None, user=Depends(current_user)):
    """The trial's current reviewed model, or an earlier version patients may still be on."""
    await _trial_for(user, trial_id)
    doc = await _model_doc(trial_id, version)
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
    answered (they are known at enrolment by definition); decisions, entered
    protocol dates, events and recorded visits are optional and filled in over time.
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
            # Any protocol date except the start date (stored as startDate) can be entered;
            # an entered date replaces one counted from another date.
            # The last of several visits (last dose) is worked out from them, never entered.
            if not anchor or anchor["kind"] in ("start", "last_visit"):
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
    # Visits a logged event added are keyed "ev.<logged event key>.<visit id>#n" by the engine.
    visit_ids |= {f"ev.{e['key']}.{v['id']}" for e in events for v in events_by_id[e["type"]]["visits"]}

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

    measures = {m["id"]: m for m in model.get("measures") or []}

    def measured(m: Dict[str, Any], value: Any, what: str) -> float:
        """A value entered for a measure: a number, and a whole grade 0-5 for a toxicity grade."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise HTTPException(422, f"{what}: enter a number for {m['label']}")
        if not math.isfinite(number) or (m["kind"] == "grade" and (number != int(number) or not 0 <= number <= 5)):
            raise HTTPException(422, f"{what}: {m['label']} must be " + ("a grade from 0 to 5" if m["kind"] == "grade" else "a number"))
        return int(number) if number == int(number) else number

    # Answers to visit checks ("ANC ≥ 1.0?"), per occurrence, kept in date order. An answer may be
    # values entered for the check's measures (the engine works out Yes/No), a Yes/No, or both; with a
    # dosing table also "skip" or "reduce" (staff chose that row). Visits a logged event added have
    # checks when they repeat until met ("follow until resolved").
    checked = {v["id"]: v["check"] for v in model["visits"] if v.get("check")}
    for e in events:
        for v in events_by_id[e["type"]]["visits"]:
            if v.get("check"):
                checked[f"ev.{e['key']}.{v['id']}"] = v["check"]
    checks: Dict[str, list] = {}
    for key, items in (raw.get("checks") or {}).items():
        key = occurrence(key)
        check = checked.get(key.rpartition("#")[0])
        if not check:
            raise HTTPException(422, f"“{key}” has no check to answer")
        if not isinstance(items, list) or len(items) > 100:
            raise HTTPException(422, "Check answers: a list of dated answers")
        given = []
        for item in items:
            if not isinstance(item, dict):
                raise HTTPException(422, "Check answer: Yes or No")
            entry: Dict[str, Any] = {"date": _iso(item.get("date"), "Check date")}
            values = {}
            for mid, value in (item.get("values") or {}).items() if isinstance(item.get("values"), dict) else []:
                if value in (None, ""):
                    continue
                if mid not in check.get("measures", []) or mid not in measures:
                    raise HTTPException(422, f"“{mid}” is not measured at this check")
                values[mid] = measured(measures[mid], value, "Check value")
            if values:
                entry["values"] = values
            rows = {a.get("do") for a in check.get("actions") or []}
            allowed = ("yes", "no") + tuple(x for x in ("skip", "reduce") if x in rows)
            if item.get("answer") in allowed:
                entry["answer"] = item["answer"]
            elif item.get("answer") not in (None, "") or not values:
                names = [x.capitalize() for x in allowed]
                raise HTTPException(422, f"Check answer: {', '.join(names[:-1])} or {names[-1]}, or the measured values")
            given.append(entry)
        if given:
            checks[key] = sorted(given, key=lambda x: x["date"])

    # Baseline values the limits compare with ("creatinine ≤ 1.5 × baseline").
    baselines = {}
    for mid, value in (raw.get("baselines") or {}).items():
        if value in (None, ""):
            continue
        if mid not in measures:
            raise HTTPException(422, f"Unknown measure “{mid}”")
        baselines[mid] = measured(measures[mid], value, "Baseline")

    # Dose levels staff set at a dose ("reduced at investigator's discretion"), kept from that dose on.
    doses = {d["id"]: d for d in model.get("doses") or []}
    dose_levels: Dict[str, Dict[str, str]] = {}
    for key, chosen in (raw.get("doseLevels") or {}).items():
        key = occurrence(key)
        if not isinstance(chosen, dict):
            raise HTTPException(422, "Dose level: choose a level")
        kept = {}
        for did, level in chosen.items():
            d = doses.get(did)
            if not d or key.rpartition("#")[0] not in d["visits"]:
                raise HTTPException(422, f"“{did}” is not given at {key}")
            if level in (None, ""):
                continue
            if not any(lv["id"] == level for lv in d["levels"]):
                raise HTTPException(422, f"{d['label']} has no level “{level}”")
            kept[did] = str(level)
        if kept:
            dose_levels[key] = kept

    out = {
        "startAnchor": start["id"], "startDate": _iso(raw.get("startDate"), start["label"]),
        "answers": answers, "events": events, "actuals": actuals, "doseTimes": dose_times,
    }
    if checks:
        out["checks"] = checks
    if baselines:
        out["baselines"] = baselines
    if dose_levels:
        out["doseLevels"] = dose_levels
    return out


async def _kit_payload(patient: dict, user: dict) -> dict:
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    trial = await db.trials.find_one({"id": patient.get("trial_id")}, {"_id": 0, "id": 1, "title": 1, "protocol_id": 1}) or {}
    version = int(saved.get("version") or 1)
    latest = None
    if user["role"] in ("pi", "crc") and patient.get("kit_model_version") is not None:
        current = await db.trial_kit_models.find_one({"trial_id": patient.get("trial_id")}, {"_id": 0})
        if current and int(current.get("version") or 1) != version:
            # A newer reviewed model exists: staff can compare and move this patient to it.
            latest = {"version": int(current.get("version") or 1), "model": current["model"],
                      "site_answers": current.get("site_answers") or {},
                      "problem": _record_problem(current["model"], patient["kit"])}
    return {
        "model_version": version, "latest": latest,
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
    saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    participant = clean_participant(saved["model"], body)
    stamp = now()
    await db.patients.update_one({"id": patient_id}, {"$set": {"kit": participant, "kit_updated_at": stamp}})
    await write_audit(user, "patient.kit_update",
                      f"Updated schedule: {len(participant['actuals'])} visits recorded, {len(participant['events'])} events",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return await _kit_payload({**patient, "kit": participant, "kit_updated_at": stamp}, user)


@router.post("/patients/{patient_id}/kit/move-to-latest", dependencies=[Depends(require_roles("pi", "crc"))])
async def move_patient_to_latest(patient_id: str, user=Depends(current_user)):
    """Put a patient on the trial's current reviewed model. Their recorded dates, answers and
    events are kept; the record must fit the new model as it is."""
    patient = await _require_patient(user, patient_id)
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    current = await db.trial_kit_models.find_one({"trial_id": patient.get("trial_id")}, {"_id": 0})
    if not current:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    version = int(current.get("version") or 1)
    participant = clean_participant(current["model"], patient["kit"])
    stamp = now()
    await db.patients.update_one({"id": patient_id}, {"$set": {
        "kit": participant, "kit_model_version": version, "kit_updated_at": stamp}})
    await write_audit(user, "patient.kit_model_move", f"Moved to protocol model version {version}",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return await _kit_payload({**patient, "kit": participant, "kit_model_version": version, "kit_updated_at": stamp}, user)
