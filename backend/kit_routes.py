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
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server import _can_access_trial, _require_patient, current_user, db, now, require_roles, write_audit
import server as _server

from app.kit import analyze as kit_analyze
from app.kit.llm import AnalysisError, OpenAIKitClient
from app.kit.normalize import build_template_model, default_review_answers, normalize
from app.kit import offline as kit_offline
from app.kit import patterns as kit_patterns
from app.kit import study_events as kit_study
from app.kit import workflow as kit_workflow
from app.kit.adaptive import adaptive_inputs
from app.kit.pdf_input import PdfReadError, prepare_protocol_input

router = APIRouter(prefix="/api")
# Blocked protocol rules across trials (app/kit/patterns.py): admin only, fail-closed like admin_routes.
admin_router = APIRouter(prefix="/api/admin/kit", dependencies=[Depends(require_roles("admin"))])

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
    "bad_model": 422, "no_api_key": 503, "auth": 503, "no_offline_model": 422,
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
    # Offline mode (app/kit/offline.py): the file is read for real; the model's answers come from
    # files prepared for this exact file. No provider is called.
    folder = kit_offline.offline_folder()
    client = kit_offline.OfflineKitClient(folder, hashlib.sha256(data).hexdigest()) if folder else OpenAIKitClient()
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
        job["result"]["workflow"] = kit_workflow.build_workflow(job["result"]["model"], 0)
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
        "version": p.get("version") or "", "date": p.get("date") or "", "design": p.get("design") or "",
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
    workflowAction: Optional[Literal["draft", "approve"]] = None
    workflowDigest: Optional[str] = None
    expectedVersion: Optional[int] = None
    blockEdits: dict[str, dict] = Field(default_factory=dict)
    resolutions: dict[str, str] = Field(default_factory=dict)


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
    if current and version is not None and int(current.get("version") or 1) != int(version):
        current = await db.trial_kit_model_history.find_one({"trial_id": trial_id, "version": int(version)}, {"_id": 0})
    if current and current.get("workflow"):
        try:
            current["model"] = kit_workflow.saved_model(current)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        current["site_answers"] = dict(current["workflow"]["study_answers"])
        if any(a.get("scope") == "study" for a in current["model"]["anchors"]):
            shared = await db.trial_kit_shared_dates.find_one({"_id": f"{trial_id}:{current['version']}"}) or {}
            current["study_dates"] = shared.get("dates") or {}
            current["study_revision"] = shared.get("revision", 0)
            current["site_answers"].update({f"@{aid}": value for aid, value in current["study_dates"].items()})
    return current


def _record_problem(model: dict, record: Any, *, workflow: bool = False) -> Optional[str]:
    """Why a patient's record does not fit a model, or None when it does."""
    try:
        clean_participant(model, record, workflow=workflow)
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
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _prepare_workflow(body: KitModelIn, version: int, user: dict) -> tuple[dict, dict, dict]:
    """Use the same workflow for the preview and for the approval write."""
    try:
        base = normalize(copy.deepcopy(body.baseModel))
        model = build_template_model(base, body.reviewAnswers)
        draft = kit_workflow.build_workflow(model, version)
        by_id = {b["id"]: b for b in draft["blocks"]}
        for block_id, edit in body.blockEdits.items():
            block = by_id.get(block_id)
            if not block or edit.get("id") != block["operation"]["id"]:
                raise ValueError("Every edit must refer to an existing block and keep its ID.")
            before = block["operation"]
            if edit.keys() - before.keys() - {"follows", "relative_to", "offset_days", "window_before",
                                               "window_after", "window_stated", "repeat", "source"}:
                raise ValueError(f"{block['label']}: the edit contains an unsupported field.")
            for field in ("source", "page", "quote", "check" if block["kind"] == "event" else "id"):
                if field in before:
                    edit = {**edit, field: before[field]}
            block["operation"] = {**before, **edit}
        edited = kit_workflow.compile_workflow(draft)
        cleaned = normalize(copy.deepcopy(edited))
        # A reviewer must see a broken link or a rejected expression before approval.
        # Compare the normalized operations so cleanup cannot silently change the edit.
        if body.blockEdits:
            established = kit_workflow.build_workflow(normalize(copy.deepcopy(model)), version)
            checked = kit_workflow.build_workflow(cleaned, version)
            prior = {b["id"]: b["operation"] for b in established["blocks"]}
            after = {b["id"]: b["operation"] for b in checked["blocks"]}
            for key, edit in body.blockEdits.items():
                if any(edit[k] != prior.get(key, {}).get(k) and edit[k] != after.get(key, {}).get(k)
                       for k in edit if k not in ("source", "page", "quote")):
                    raise ValueError(f"{by_id[key]['label']}: a value or reference is invalid.")
            model = cleaned
        resolutions = {key: {"text": value.strip()[:2000], "by": user["id"]}
                       for key, value in body.resolutions.items() if value.strip()}
        workflow = kit_workflow.build_workflow(model, version, resolutions)
        review_required = [q["text"] for q in base["review_questions"]
                           if body.reviewAnswers.get(q["id"]) not in {o["value"] for o in q["options"]}]
        study_required = [q["text"] for q in model["questions"]
                          if q["scope"] == "study" and not _checked(q, body.siteAnswers.get(q["id"]))]
        workflow["errors"].extend(review_required + study_required)
        if any(q.get("answer_roles") and user["role"] not in q["answer_roles"] for q in model["questions"] if q["scope"] == "study"):
            workflow["errors"].append("An investigator must confirm the study settings.")
        if set(body.siteAnswers) - {q["id"] for q in model["questions"] if q["scope"] == "study"}:
            workflow["errors"].append("Study settings may only answer declared study questions.")
        workflow["study_answers"] = body.siteAnswers
        workflow["file_sha256"] = body.fileSha256
        kit_workflow.fingerprint(workflow)
        return base, kit_workflow.compile_workflow(workflow), workflow
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error) or "The workflow is invalid") from error


@router.post("/kit/workflow-preview", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def preview_workflow(body: KitModelIn, user=Depends(current_user)):
    _, model, workflow = _prepare_workflow(body, (body.expectedVersion or 0) + 1, user)
    return {"model": model, "workflow": workflow, "digest": kit_workflow.fingerprint(workflow),
            "problems": kit_workflow.approval_problems(workflow)}


@router.get("/trials/{trial_id}/kit-draft", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def read_workflow_draft(trial_id: str, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    draft = await db.trial_kit_drafts.find_one({"_id": trial_id}, {"_id": 0})
    if not draft:
        raise HTTPException(404, "This trial has no workflow draft")
    return draft


@router.put("/trials/{trial_id}/kit-model", dependencies=[Depends(require_roles("sponsor", "cro", "pi"))])
async def save_kit_model(trial_id: str, body: KitModelIn, user=Depends(current_user)):
    trial = await _trial_for(user, trial_id)
    previous = await db.trial_kit_models.find_one({"trial_id": trial_id}, {"_id": 0})
    prior_version = int((previous or {}).get("version") or (1 if previous else 0))
    draft = None
    if body.workflowAction:
        if body.expectedVersion != prior_version:
            raise HTTPException(409, "The trial has a newer workflow. Reopen the review before saving.")
        base, model, draft = _prepare_workflow(body, prior_version + 1, user)
    else:
        if previous and previous.get("workflow"):
            raise HTTPException(409, "Use workflow review to approve an amendment.")
        try:
            base = normalize(copy.deepcopy(body.baseModel))
            model = build_template_model(base, body.reviewAnswers or default_review_answers(base))
        except ValueError as error:
            raise HTTPException(422, "The protocol model is not valid") from error
    missing = [q["id"] for q in model["questions"]
               if q["scope"] == "study" and not _checked(q, body.siteAnswers.get(q["id"]))]
    if missing and draft is None:
        raise HTTPException(400, {"message": "Answer every site setting.", "fields": missing})
    sha = (body.fileSha256 or "").strip().lower()
    doc = {
        "trial_id": trial_id, "base_model": base, "review_answers": body.reviewAnswers,
        "site_answers": body.siteAnswers, "model": model, "file_name": body.fileName,
        "file_sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else None,
        "analysis_warnings": [str(w) for w in body.warnings][:50],
        "corrections": None if body.corrections is None else [str(c) for c in body.corrections][:200],
        "version": prior_version + 1,
        "saved_by": user["id"], "saved_at": now(),
        # Rules the app cannot calculate yet: the model is saved and reviewable, but nobody can be
        # enrolled on it until a handler exists (re-checked by the admin, see admin_router).
        "blockers": kit_patterns.blockers(model),
    }
    job = _jobs.get(body.analysisJobId or "")
    prior_draft = await db.trial_kit_drafts.find_one({"_id": trial_id}, {"_id": 0}) if draft else None
    if job and job["user_id"] == user["id"] and job.get("file") and doc["file_sha256"] \
            and hashlib.sha256(job["file"]).hexdigest() == doc["file_sha256"]:
        await _store_protocol_file(doc["file_sha256"], job["file"], body.fileName, trial_id)
        doc["file_stored"] = True
    else:
        doc["file_stored"] = any(
            saved and doc["file_sha256"] and saved.get("file_sha256") == doc["file_sha256"] and saved.get("file_stored")
            for saved in (previous, prior_draft))
    if draft is not None:
        digest = kit_workflow.fingerprint(draft)
        issues = kit_workflow.approval_problems(draft)
        source_base = None
        if doc["file_sha256"] and job and job["user_id"] == user["id"] and (job.get("result") or {}).get("fileSha256") == doc["file_sha256"]:
            source_base = job["result"].get("baseModel")
        elif prior_draft and prior_draft.get("file_sha256") == doc["file_sha256"]:
            source_base = prior_draft["base_model"]
        if source_base:
            def point_keys(points):
                return {(p["id"], p["text"], p.get("quote"), p.get("page")) for p in points or []}
            if point_keys(source_base.get("open_points")) - point_keys(base.get("open_points")):
                raise HTTPException(422, "Restore the source issues and record their interpretations in workflow review.")
        protocol = model.get("protocol") or {}
        number = protocol.get("number")
        identifiers = {str(protocol.get(key) or "").strip().casefold() for key in ("number", "registry_id")}
        if number and trial.get("protocol_id") and trial["protocol_id"].strip().casefold() not in identifiers:
            issues.append(f"The uploaded protocol identifies itself as {number}, which differs from this trial.")
        if not doc["file_stored"]:
            issues.append("Upload the original protocol before approval.")
        if body.workflowAction == "draft":
            await db.trial_kit_drafts.replace_one({"_id": trial_id}, {
                **doc, "_id": trial_id, "workflow": draft, "digest": digest, "expected_version": prior_version,
                "block_edits": body.blockEdits, "resolutions": body.resolutions, "problems": issues,
            }, upsert=True)
            await write_audit(user, "trial.kit_workflow_draft", "Saved workflow draft", target_id=trial_id, trial_id=trial_id)
            return {"status": "draft", "version": doc["version"], "problems": issues}
        if body.workflowDigest != digest:
            issues.append("Review the current workflow preview before approval.")
        if issues:
            raise HTTPException(422, {"message": issues[0], "problems": issues})
        doc.update(workflow=draft, workflow_sha256=digest, approved_at=doc["saved_at"], approved_by=user["id"])
    # Earlier versions are kept, never overwritten: patients were scheduled with them, and they
    # stay on the version they were enrolled on until staff move them (nobody moves by default).
    if previous:
        previous_version = int(previous.get("version") or 1)
        historical = {**previous, "version": previous_version, "replaced_at": doc["saved_at"], "replaced_by": user["id"]}
        if draft is not None:
            history_id = f"{trial_id}:{previous_version}"
            await db.trial_kit_model_history.update_one({"_id": history_id}, {"$setOnInsert": historical}, upsert=True)
        else:
            await db.trial_kit_model_history.insert_one(historical)
        await db.patients.update_many(
            {"trial_id": trial_id, "kit": {"$exists": True}, "kit_model_version": {"$exists": False}},
            {"$set": {"kit_model_version": previous_version}})
        # Invited but not yet registered: the patient record is created from the invitation.
        await db.invitations.update_many(
            {"trial_id": trial_id, "status": "pending", "patient_data.kit": {"$exists": True},
             "patient_data.kit_model_version": {"$exists": False}},
            {"$set": {"patient_data.kit_model_version": previous_version}})
    if draft is not None:
        from pymongo.errors import DuplicateKeyError
        guard: dict[str, Any] = {"trial_id": trial_id, "version": (previous or {}).get("version", {"$exists": False})}
        if not previous:
            doc["_id"] = trial_id
        try:
            updated = await db.trial_kit_models.replace_one(guard, doc, upsert=previous is None)
        except DuplicateKeyError as error:
            raise HTTPException(409, "The workflow changed during approval. Reopen the review.") from error
        if previous and updated.matched_count != 1:
            raise HTTPException(409, "The workflow changed during approval. Reopen the review.")
        if prior_draft:
            await db.trial_kit_drafts.delete_one({"_id": trial_id, "digest": prior_draft["digest"]})
    else:
        await db.trial_kit_models.replace_one({"trial_id": trial_id}, doc, upsert=True)

    # Which patients on earlier versions could move to this one as they are.
    earlier, movable, blocked = 0, 0, []
    async for patient in db.patients.find(
            {"trial_id": trial_id, "kit": {"$exists": True}, "kit_model_version": {"$ne": doc["version"]}},
            {"_id": 0, "id": 1, "subject_id": 1, "kit": 1}):
        earlier += 1
        problem = _record_problem(model, patient["kit"], workflow=draft is not None)
        if problem is None:
            movable += 1
        elif len(blocked) < 20:
            blocked.append({"patient_id": patient["id"], "subject_id": patient.get("subject_id"), "problem": problem})
    await write_audit(user, "trial.kit_model_save", f"Saved the reviewed protocol model ({len(model['visits'])} visits)",
                      target_id=trial_id, trial_id=trial_id)
    return {"status": "approved", "trial_id": trial_id, "visits": len(model["visits"]), "saved_at": doc["saved_at"], "version": doc["version"],
            "file_stored": doc["file_stored"], "blockers": doc["blockers"],
            "enrol_problem": kit_patterns.enrol_problem(doc["blockers"]),
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
    if user["role"] == "patient":
        raise HTTPException(403, "Use your own visit calendar.")
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


class SharedDateIn(BaseModel):
    date: Optional[str] = None
    revision: int


@router.put("/trials/{trial_id}/kit-model/{version}/study-dates/{anchor_id}",
            dependencies=[Depends(require_roles("pi"))])
async def set_workflow_study_date(trial_id: str, version: int, anchor_id: str, body: SharedDateIn,
                                  user=Depends(current_user)):
    await _trial_for(user, trial_id)
    saved = await _model_doc(trial_id, version)
    if not saved or not saved.get("workflow"):
        raise HTTPException(404, "Approved workflow not found")
    anchor = next((a for a in saved["model"]["anchors"] if a["id"] == anchor_id and a.get("scope") == "study"), None)
    if not anchor:
        raise HTTPException(404, "Shared study date not found")
    value = _iso(body.date, anchor["label"]) if body.date is not None else None
    dates = dict(saved["study_dates"])
    revision = saved["study_revision"]
    if dates.get(anchor_id) == value:
        return {"dates": dates, "revision": revision}
    if revision != body.revision:
        raise HTTPException(409, "A study date changed. Reload before saving.")
    if value is None:
        dates.pop(anchor_id, None)
    else:
        dates[anchor_id] = value
    from pymongo.errors import DuplicateKeyError
    key = f"{trial_id}:{version}"
    try:
        changed = await db.trial_kit_shared_dates.replace_one({"_id": key, "revision": revision}, {
            "_id": key, "revision": revision + 1, "dates": dates,
        }, upsert=revision == 0)
    except DuplicateKeyError as error:
        raise HTTPException(409, "A study date changed. Reload before saving.") from error
    if revision and not changed.matched_count:
        raise HTTPException(409, "A study date changed. Reload before saving.")
    await write_audit(user, "trial.kit_study_date", f"Updated {anchor['label']} for workflow version {version}",
                      target_id=trial_id, trial_id=trial_id)
    return {"dates": dates, "revision": revision + 1}


_STUDY_KEY = re.compile(r"^se\.([\w-]{1,80})\.([\w-]{1,80})#1$")


def observed_value(definition: dict, value: Any) -> Any:
    """A recorded input as stored: a whole number >= 0 for a count, yes/no, or a positive number."""
    label = definition["label"]
    if definition["kind"] == "boolean":
        text = str(value).strip().lower()
        if text not in ("yes", "no"):
            raise HTTPException(422, f"{label}: answer Yes or No")
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label}: enter a number") from None
    if not math.isfinite(number) or number < 0 or (definition["kind"] == "count" and number != int(number)):
        raise HTTPException(422, f"{label}: enter {'a whole number' if definition['kind'] == 'count' else 'a number'} of 0 or more")
    if definition["kind"] == "number" and number <= 0:
        raise HTTPException(422, f"{label}: enter a number greater than 0")
    return int(number) if number == int(number) else number


def _answer(q: dict, value: Any, where: str) -> str:
    text = str(value).strip()
    if not _checked(q, text):
        raise HTTPException(422, f"{where}: “{text}” is not an answer to “{q['label']}”")
    return text


def clean_participant(model: Dict[str, Any], raw: Any, *, workflow: bool = False) -> Dict[str, Any]:
    """Check one patient's kit record against the trial's reviewed model.

    Raises 422 naming the first problem. Participant questions must all be
    answered (they are known at enrolment by definition); decisions, entered
    protocol dates, events and recorded visits are optional and filled in over time.
    """
    if not isinstance(raw, dict):
        raise HTTPException(422, "The schedule details are missing")
    start = next(a for a in model["anchors"] if a["kind"] == "start")
    start_date = _iso(raw.get("startDate"), start["label"])
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
            if not anchor or anchor["kind"] in ("start", "last_visit") or anchor.get("scope") == "study":
                raise HTTPException(422, f"Unknown protocol date “{key[1:]}”")
            answers[key] = _iso(value, anchor["label"])
        elif key in questions:
            answers[key] = _answer(questions[key], value, "Answer")
        else:
            raise HTTPException(422, f"Unknown question “{key}”")
    missing = [q["label"] for q in questions.values() if q["scope"] == "participant" and not answers.get(q["id"])]
    if missing and not workflow:
        raise HTTPException(422, "Answer: " + ", ".join(missing))
    for anchor in anchors.values():
        g = anchor.get("range")
        value = answers.get("@" + anchor["id"])
        if anchor.get("date_basis") != "confirmed" or not g or not value:
            continue
        base_date = start_date if g["relative_to"] == start["id"] else answers.get("@" + g["relative_to"])
        if base_date is None:
            continue
        days = (date.fromisoformat(value) - date.fromisoformat(base_date)).days
        if (g["min_days"] is not None and days < g["min_days"]) or (g["max_days"] is not None and days > g["max_days"]):
            raise HTTPException(422, f"{anchor['label']}: the confirmed booking is outside the protocol interval.")

    events_by_id = {e["id"]: e for e in model["events"]}
    events = []
    event_keys: dict[str, dict] = {}
    for item in raw.get("events") or []:
        if not isinstance(item, dict) or item.get("type") not in events_by_id:
            raise HTTPException(422, "Unknown event")
        definition = events_by_id[item["type"]]
        event_questions = {q["id"]: q for q in definition["questions"]}
        event_answers = {}
        for key, value in (item.get("answers") or {}).items():
            if value in (None, ""):
                continue
            if key not in event_questions:
                raise HTTPException(422, f"Unknown question “{key}” on {definition['label']}")
            event_answers[key] = _answer(event_questions[key], value, definition["label"])
        if workflow and not re.fullmatch(r"[\w-]{1,80}", str(item.get("key") or "")):
            raise HTTPException(422, "An event needs a stable identifier.")
        event = {
            "key": str(item.get("key") or uuid.uuid4().hex[:8]), "type": definition["id"],
            "date": _iso(item.get("date"), definition["label"]), "answers": event_answers,
        }
        if event["key"] in event_keys:
            if event == event_keys[event["key"]]:
                continue
            raise HTTPException(422, "An event identifier is already used for a different entry.")
        if not definition["repeatable"] and any(e["type"] == definition["id"] for e in events):
            raise HTTPException(422, f"“{definition['label']}” can only be logged once")
        event_keys[event["key"]] = event
        events.append(event)

    visit_ids = {v["id"] for v in model["visits"]} | {v["id"] for e in model["events"] for v in e["visits"]}
    # Visits a logged event added are keyed "ev.<logged event key>.<visit id>#n" by the engine.
    visit_ids |= {f"ev.{e['key']}.{v['id']}" for e in events for v in events_by_id[e["type"]]["visits"]}
    # A visit a confirmed study event added (an early closeout) is "se.<confirmation id>.<visit id>#1".
    study_visits = {a["add_visit"]["id"] for e in model.get("study_events") or [] for a in e["actions"] if a.get("add_visit")}

    def occurrence(key: Any) -> str:
        text = str(key)
        visit_id, _, n = text.rpartition("#")
        added = _STUDY_KEY.match(text)
        if added and added.group(2) in study_visits:
            return text
        if visit_id not in visit_ids or not n.lstrip("-").isdigit():
            raise HTTPException(422, f"Unknown visit “{text}”")
        return text

    actuals = {occurrence(k): _iso(v, "Visit date") for k, v in (raw.get("actuals") or {}).items() if v}
    # A dose not given at a visit that took place ("withheld: IOP 32 mm Hg"): generated calculations
    # and conditions read it as given = false.
    withheld = {}
    for k, v in (raw.get("withheld") or {}).items() if isinstance(raw.get("withheld"), dict) else []:
        reason = str(v or "").strip()
        if reason:
            withheld[occurrence(k)] = reason[:200]
    bookings = {occurrence(k): _iso(v, "Booked date") for k, v in (raw.get("bookings") or {}).items() if v}
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

    # Inputs confirmed at an occurrence of a visit with a history-based interval (app/kit/adaptive.py):
    # the finding, the external recommendation and, under "review" precedence, the chosen interval.
    inputs = adaptive_inputs(model)
    observations: Dict[str, Dict[str, Any]] = {}
    for key, entries in (raw.get("observations") or {}).items():
        key = occurrence(key)
        defs = inputs.get(key.rpartition("#")[0])
        if not defs or not isinstance(entries, dict):
            raise HTTPException(422, f"“{key}” has no inputs to record")
        kept = {}
        for iid, entry in entries.items():
            if iid not in defs:
                raise HTTPException(422, f"“{iid}” is not recorded at {key}")
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value in (None, ""):
                continue
            item = {"value": observed_value(defs[iid], value)}
            for meta in ("at", "by", "id", "source"):
                if isinstance(entry, dict) and entry.get(meta) not in (None, ""):
                    item[meta] = str(entry[meta])[:120]
            kept[iid] = item
        if kept:
            observations[key] = kept

    out = {
        "startAnchor": start["id"], "startDate": start_date,
        "answers": answers, "events": events, "actuals": actuals, "doseTimes": dose_times,
    }
    if observations:
        out["observations"] = observations
    if withheld:
        out["withheld"] = withheld
    if checks:
        out["checks"] = checks
    if baselines:
        out["baselines"] = baselines
    if dose_levels:
        out["doseLevels"] = dose_levels
    if bookings:
        out["bookings"] = bookings
    return out


async def _kit_payload(patient: dict, user: dict) -> dict:
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    if saved.get("workflow") and user["role"] in ("sponsor", "cro"):
        raise HTTPException(403, "Individual calendars are available to the care team and the participant.")
    trial = await db.trials.find_one({"id": patient.get("trial_id")}, {"_id": 0, "id": 1, "title": 1, "protocol_id": 1}) or {}
    version = int(saved.get("version") or 1)
    latest = None
    if user["role"] in ("pi", "crc") and patient.get("kit_model_version") is not None:
        current = await _model_doc(patient.get("trial_id"), None)
        if current and int(current.get("version") or 1) != version:
            # A newer reviewed model exists: staff can compare and move this patient to it.
            latest = {"version": int(current.get("version") or 1), "model": current["model"],
                      "site_answers": current.get("site_answers") or {},
                      "problem": kit_patterns.enrol_problem(kit_patterns.blockers(current["model"]))
                      or _record_problem(current["model"], patient["kit"], workflow=bool(current.get("workflow")))}
    participant = dict(patient["kit"])
    effects = await study_effects_for(patient["id"])
    if effects:
        # Server-owned: computed into the calendar, stripped again when the record is saved.
        participant["studyEffects"] = effects
    return {
        "model_version": version, "latest": latest,
        # Sponsors and CROs see subjects de-identified, as everywhere else.
        "patient": {key: patient.get(key) for key in ("id", "subject_id", "full_name", "avatar_initials", "trial_id")
                    if not (key == "full_name" and user["role"] in ("sponsor", "cro"))},
        "trial": trial, "model": saved["model"], "site_answers": saved.get("site_answers") or {},
        "participant": participant, "updated_at": patient.get("kit_updated_at"),
        "workflow_version": version if saved.get("workflow") else None,
        "revision": patient.get("kit_revision", 0),
        "actual_versions": patient.get("kit_actual_versions") or {},
    }


def stamp_observations(before: dict, after: dict, user: dict, when: Any) -> None:
    """An input whose value changed is stamped by the server (who, when, a new id); an unchanged one
    keeps its stored stamp, so a client cannot rewrite who confirmed what."""
    old = before.get("observations") or {}
    for key, entries in (after.get("observations") or {}).items():
        for iid, entry in entries.items():
            prior = (old.get(key) or {}).get(iid)
            if prior and prior.get("value") == entry["value"]:
                entries[iid] = prior
            else:
                entries[iid] = {"value": entry["value"], "at": when.isoformat().replace("+00:00", "Z"),
                                "by": user["id"], "id": uuid.uuid4().hex, **({"source": entry["source"]} if entry.get("source") else {})}


def check_observation_roles(model: dict, before: dict, after: dict, role: str) -> None:
    """Who may confirm each input recorded at a visit (a finding may be investigator-only)."""
    inputs = adaptive_inputs(model)
    old_obs, new_obs = before.get("observations") or {}, after.get("observations") or {}
    for key in old_obs.keys() | new_obs.keys():
        defs = inputs.get(key.rpartition("#")[0]) or {}
        for iid in (old_obs.get(key) or {}).keys() | (new_obs.get(key) or {}).keys():
            old = ((old_obs.get(key) or {}).get(iid) or {}).get("value")
            new = ((new_obs.get(key) or {}).get(iid) or {}).get("value")
            roles = (defs.get(iid) or {}).get("roles") or ["pi"]
            if old != new and role not in roles:
                raise HTTPException(403, f"Only {' or '.join(r.upper() for r in roles)} may record “{(defs.get(iid) or {}).get('label', iid)}”.")


def check_workflow_input_roles(model: dict, before: dict, after: dict, role: str) -> None:
    old_answers, new_answers = before.get("answers", {}), after.get("answers", {})
    for question in model["questions"]:
        qid = question["id"]
        if question.get("answer_roles") == ["pi"] and role != "pi" and old_answers.get(qid) != new_answers.get(qid):
            raise HTTPException(403, f"An investigator must answer “{question['text']}”.")
    check_observation_roles(model, before, after, role)
    old_events = {e["key"]: e for e in before.get("events", [])}
    new_events = {e["key"]: e for e in after.get("events", [])}
    definitions = {e["id"]: e for e in model["events"]}
    if role == "pi":
        return
    for key in old_events.keys() | new_events.keys():
        old, new = old_events.get(key), new_events.get(key)
        if old == new:
            continue
        for value in (old, new):
            definition = definitions.get((value or {}).get("type"))
            if definition and definition.get("record_roles") == ["pi"]:
                raise HTTPException(403, f"An investigator must record “{definition['label']}”.")
        if not new:
            continue
        for q in definitions[new["type"]]["questions"]:
            if q.get("answer_roles") == ["pi"] and (old or {}).get("answers", {}).get(q["id"]) != new["answers"].get(q["id"]):
                raise HTTPException(403, f"An investigator must answer “{q['text']}”.")


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
async def save_patient_kit(patient_id: str, body: Dict[str, Any], user=Depends(current_user), revision: Optional[int] = None):
    patient = await _require_patient(user, patient_id)
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
    if not saved:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    workflow = bool(saved.get("workflow"))
    participant = clean_participant(saved["model"], body, workflow=workflow)
    if workflow:
        check_workflow_input_roles(saved["model"], patient["kit"], participant, user["role"])
    else:
        check_observation_roles(saved["model"], patient["kit"], participant, user["role"])
    stamp_observations(patient["kit"], participant, user, now())
    if participant == patient["kit"]:
        return await _kit_payload(patient, user)
    stamp = now()
    guard = {"id": patient_id}
    patch = {"kit": participant, "kit_updated_at": stamp}
    if workflow:
        old_revision = patient.get("kit_revision", 0)
        if revision != old_revision:
            raise HTTPException(409, "The calendar has changed. Reopen it before saving.")
        guard.update(kit_revision=patient.get("kit_revision", {"$exists": False}),
                     kit_model_version=patient.get("kit_model_version", {"$exists": False}))
        versions = patient.get("kit_actual_versions", {}).copy()
        for key in participant["actuals"]:
            versions.setdefault(key, int(saved.get("version") or 1))
        patch.update(kit_revision=old_revision + 1, kit_actual_versions=versions)
    result = await db.patients.update_one(guard, {"$set": patch})
    if workflow and not result.matched_count:
        latest = await _require_patient(user, patient_id)
        if latest.get("kit_model_version") == patient.get("kit_model_version") and latest.get("kit") == participant:
            return await _kit_payload(latest, user)
        raise HTTPException(409, "The calendar has changed. Reopen it before saving.")
    changes = [field for field in participant.keys() | patient["kit"].keys()
               if participant.get(field) != patient["kit"].get(field)]
    await write_audit(user, "patient.kit_update",
                      f"Updated calendar inputs: {', '.join(sorted(changes))}",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return await _kit_payload({**patient, **patch}, user)


@router.post("/patients/{patient_id}/kit/move-to-latest", dependencies=[Depends(require_roles("pi", "crc"))])
async def move_patient_to_latest(patient_id: str, user=Depends(current_user),
                                 version: Optional[int] = None, revision: Optional[int] = None):
    """Put a patient on the trial's current reviewed model. Their recorded dates, answers and
    events are kept; the record must fit the new model as it is."""
    patient = await _require_patient(user, patient_id)
    if not patient.get("kit"):
        raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
    current = await _model_doc(patient.get("trial_id"), None)
    if not current:
        raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
    new_version = int(current.get("version") or 1)
    problem = kit_patterns.enrol_problem(kit_patterns.blockers(current["model"]))
    if problem:
        raise HTTPException(409, problem)
    workflow = bool(current.get("workflow"))
    if workflow and version == new_version == patient.get("kit_model_version"):
        return await _kit_payload(patient, user)
    if workflow and (version != new_version or revision != patient.get("kit_revision", 0)):
        raise HTTPException(409, "Reopen the amendment preview before moving this patient.")
    participant = clean_participant(current["model"], patient["kit"], workflow=workflow)
    stamp = now()
    patch = {"kit": participant, "kit_model_version": new_version, "kit_updated_at": stamp}
    guard = {"id": patient_id}
    if workflow:
        guard.update(kit_revision=patient.get("kit_revision", {"$exists": False}),
                     kit_model_version=patient.get("kit_model_version", {"$exists": False}))
        actual_versions = {k: patient.get("kit_model_version", 1) for k in patient["kit"].get("actuals", {})}
        actual_versions.update(patient.get("kit_actual_versions") or {})
        patch.update(kit_revision=(revision or 0) + 1, kit_actual_versions=actual_versions)
    result = await db.patients.update_one(guard, {"$set": patch})
    if workflow and not result.matched_count:
        raise HTTPException(409, "The calendar has changed. Reopen the amendment preview.")
    await write_audit(user, "patient.kit_model_move", f"Moved to protocol model version {new_version}",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return await _kit_payload({**patient, **patch}, user)


# ── Inputs confirmed at a visit, delivered late or out of order ───────────────
# A finding or a reading-centre recommendation may reach the server after a later one was already
# confirmed (a delayed feed, a device that was offline). Each carries a stable id and the time it
# was confirmed: the same id again changes nothing, and an older confirmation never replaces a
# newer one for the same occurrence and input. Staff corrections go through PUT /kit, where the
# calendar revision guards against overwriting a change someone else made.


class ObservationIn(BaseModel):
    id: str = Field(pattern=r"^[\w-]{6,80}$")
    key: str
    input: str
    value: Any
    confirmed_at: str
    source: Optional[str] = None


def _utc(text: str) -> str:
    from datetime import datetime, timezone
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "confirmed_at: give the date and time it was confirmed (ISO 8601)") from None
    if moment.tzinfo is None:
        raise HTTPException(422, "confirmed_at: include the time zone")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/patients/{patient_id}/kit/observations", dependencies=[Depends(require_roles("pi", "crc"))])
async def record_observation(patient_id: str, body: ObservationIn, user=Depends(current_user)):
    for _ in range(3):
        patient = await _require_patient(user, patient_id)
        if not patient.get("kit"):
            raise HTTPException(404, "This patient is not on a reviewed protocol schedule")
        saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
        if not saved:
            raise HTTPException(409, "The trial's reviewed protocol schedule is missing")
        definition = (adaptive_inputs(saved["model"]).get(body.key.rpartition("#")[0]) or {}).get(body.input)
        if not definition:
            raise HTTPException(422, f"“{body.input}” is not recorded at {body.key}")
        if user["role"] not in definition["roles"]:
            raise HTTPException(403, f"Only {' or '.join(r.upper() for r in definition['roles'])} may record “{definition['label']}”.")
        entry = {"value": observed_value(definition, body.value), "at": _utc(body.confirmed_at), "by": user["id"], "id": body.id,
                 **({"source": body.source[:120]} if body.source else {})}
        current = ((patient["kit"].get("observations") or {}).get(body.key) or {}).get(body.input)
        if current and current.get("id") == body.id:
            if current.get("value") != entry["value"] or current.get("at") != entry["at"]:
                raise HTTPException(409, "This input id was already used for a different value.")
            return {"status": "replayed", "observation": current, "revision": patient.get("kit_revision", 0)}
        if current and (current.get("at") or "", current.get("id") or "") > (entry["at"], entry["id"]):
            return {"status": "ignored_older", "observation": current, "revision": patient.get("kit_revision", 0)}
        record = copy.deepcopy(patient["kit"])
        record.setdefault("observations", {}).setdefault(body.key, {})[body.input] = entry
        participant = clean_participant(saved["model"], record, workflow=bool(saved.get("workflow")))
        revision = patient.get("kit_revision", 0)
        guard = {"id": patient_id, "kit_revision": patient.get("kit_revision", {"$exists": False}),
                 "kit_model_version": patient.get("kit_model_version", {"$exists": False})}
        result = await db.patients.update_one(guard, {"$set": {"kit": participant, "kit_revision": revision + 1, "kit_updated_at": now()}})
        if result.matched_count:
            await write_audit(user, "patient.kit_observation",
                              f"Recorded {definition['label']} at {body.key} (confirmed {entry['at']})",
                              target_id=patient_id, trial_id=patient.get("trial_id"))
            return {"status": "applied", "observation": entry, "revision": revision + 1}
    raise HTTPException(409, "The calendar kept changing. Try again.")


# ── Study-wide events ───────────────────────────────────────────────────────
# One confirmed fact about the whole study (app/kit/study_events.py), applied by the server to every
# participant it concerns on their own workflow version. The confirmation (who, when) is stored apart
# from when the event happened; one effect per participant is stored under a stable id, so a retry,
# a replay or a site that fetches its calendar days later sees exactly one of each.

# "review": a booked closeout that a correction no longer applies (kept, flagged for the site);
# "to_review": a participant in a phase the workflow lists for review without an action.
EFFECT_STATES = ("applied", "excluded", "unresolved", "to_review", "review", "unmatched", "already_completed", "ended", "withdrawn")


class StudyEventIn(BaseModel):
    id: str = Field(pattern=r"^[\w-]{6,80}$")
    type: str
    effective_date: str
    fields: Dict[str, str] = Field(default_factory=dict)


class StudyEventCorrectionIn(BaseModel):
    effective_date: str
    fields: Dict[str, str] = Field(default_factory=dict)
    revision: int
    preview: bool = False
    acknowledge_booked: list[str] = Field(default_factory=list)


class EffectBookingIn(BaseModel):
    date: Optional[str] = None


async def study_effects_for(patient_id: str) -> list[dict]:
    """The study effects a participant's calendar applies, in the engine's shape."""
    out = []
    async for e in db.kit_study_effects.find({"patient_id": patient_id, "state": {"$in": ["applied", "review"]}}, {"_id": 0}):
        out.append({"id": e["id"], "event": e["event_type"], "action": e["action"], "label": e.get("label"),
                    "effective": e["effective"], "state": e["state"], "booked": e.get("booked"), "taskKey": e.get("task_key")})
    out.sort(key=lambda x: (x["effective"], x["id"]))
    return out


async def _study_definition(trial_id: str, event_type: str) -> tuple[dict, dict]:
    current = await _model_doc(trial_id, None)
    if not current or not current.get("workflow"):
        raise HTTPException(409, "Study events need an approved workflow.")
    definition = kit_study.definition_for(current["model"], event_type)
    if not definition:
        raise HTTPException(404, "The approved workflow has no such study event.")
    return current, definition


async def _checked_fields(trial_id: str, definition: dict, fields: dict[str, str]) -> dict[str, str]:
    out = {}
    for f in definition["fields"]:
        value = str(fields.get(f["id"]) or "").strip()
        if not value:
            raise HTTPException(422, f"{f['label']}: required")
        if f["kind"] == "date":
            value = _iso(value, f["label"])
        if f["kind"] == "subject" and not await db.patients.find_one(
                {"trial_id": trial_id, "subject_id": value}, {"_id": 0, "id": 1}):
            raise HTTPException(422, f"{f['label']}: no participant {value} in this trial")
        out[f["id"]] = value[:120]
    if set(fields) - set(out):
        raise HTTPException(422, "Only the fields the workflow defines can be given.")
    return out


def _check_study_role(user: dict, definition: dict) -> None:
    if user["role"] not in definition["confirm_roles"]:
        raise HTTPException(403, f"Only {' or '.join(r.upper() for r in definition['confirm_roles'])} study users may confirm "
                                 f"“{definition['label']}”.")


async def _staff_ids(patient: dict) -> list[str]:
    return [x for x in dict.fromkeys([patient.get("pi_id"), patient.get("crc_id")]) if x]


async def _desired(event: dict, patient: dict) -> tuple[dict, Optional[dict], int]:
    saved = await _model_doc(patient.get("trial_id"), patient.get("kit_model_version"))
    model = saved["model"] if saved else {}
    version = int((saved or {}).get("version") or 1)
    definition = kit_study.definition_for(model, event["type"])
    return kit_study.desired_effect(definition, model, patient, event), definition, version


def _next_state(prior: Optional[dict], want: dict, patient: dict) -> str:
    """The state an effect moves to when the event is (re)processed. Completed work is kept, and a
    booked closeout is never removed silently."""
    if prior is None or prior["state"] not in ("applied", "review"):
        return want["state"]
    task_done = bool(prior.get("task_key") and (patient["kit"].get("actuals") or {}).get(prior["task_key"]))
    if task_done:
        return prior["state"]
    if want["state"] == "applied" and want.get("action") == prior.get("action"):
        return "applied"
    if want["state"] == "applied":
        return "applied"
    return "review" if prior.get("booked") else "withdrawn"


async def _process_study_event(event: dict) -> dict:
    """Apply one confirmed event (at its current revision) to every participant of the trial.
    Idempotent: a participant already processed at this revision is skipped; a failure is recorded
    and processing continues, so a retry finishes the rest."""
    summary: Dict[str, list] = {state: [] for state in EFFECT_STATES}
    summary["failed"] = []
    async for patient in db.patients.find({"trial_id": event["trial_id"], "kit": {"$exists": True}}, {"_id": 0}):
        effect_id = f"{event['id']}:{patient['id']}"
        try:
            prior = await db.kit_study_effects.find_one({"_id": effect_id})
            if prior and prior.get("revision") == event["revision"]:
                summary[prior["state"]].append(prior["subject_id"])
                continue
            want, definition, version = await _desired(event, patient)
            state = _next_state(prior, want, patient)
            action = next((a for a in (definition or {}).get("actions") or [] if a["id"] == (want.get("action") or (prior or {}).get("action"))), None)
            task_key = f"se.{event['id']}.{action['add_visit']['id']}#1" if action and action.get("add_visit") else None
            doc = {
                "_id": effect_id, "id": effect_id, "event_id": event["id"], "event_type": event["type"], "label": event["label"],
                "trial_id": event["trial_id"], "patient_id": patient["id"], "subject_id": patient.get("subject_id") or patient["id"],
                "site": await _server._patient_site_org(patient), "version": version, "state": state,
                "action": want.get("action") or (prior or {}).get("action"), "reason": want.get("reason"), "phase": want.get("phase"),
                "changes": want.get("changes") or [] if state == "applied" else [], "effective": event["effective_date"],
                "task_key": task_key if state in ("applied", "review") else None,
                "booked": (prior or {}).get("booked") if state in ("applied", "review") else None,
                "booked_by": (prior or {}).get("booked_by"), "booked_at": (prior or {}).get("booked_at"),
                "revision": event["revision"], "created_at": (prior or {}).get("created_at") or now(), "updated_at": now(),
                "history": [*((prior or {}).get("history") or []),
                            *([{k: prior.get(k) for k in ("state", "action", "revision", "effective", "booked", "reason")}] if prior else [])],
            }
            if state == "review" and prior and prior.get("state") != "review":
                doc["reason"] = f"Corrected: {want.get('reason')} The booking on {prior['booked']} needs review."
            if prior is None:
                await db.kit_study_effects.update_one({"_id": effect_id}, {"$setOnInsert": doc}, upsert=True)
            else:
                await db.kit_study_effects.replace_one({"_id": effect_id, "revision": prior["revision"]}, doc)
            if state == "applied" and task_key and not (prior or {}).get("state") == "applied":
                # One reminder per site staff member and effect; the id makes a retry a no-op.
                for uid in await _staff_ids(patient):
                    await db.notifications.update_one({"id": f"kit-study:{effect_id}:{uid}"}, {"$setOnInsert": {
                        "id": f"kit-study:{effect_id}:{uid}", "user_id": uid, "kind": "schedule", "trial_id": event["trial_id"],
                        "title": f"Book {action['add_visit']['name']}",
                        "body": f"{event['label']} ({event['effective_date']}). Book the next available date for "
                                f"{patient.get('subject_id') or 'the participant'}.",
                        "read": False, "created_at": now()}}, upsert=True)
            summary[state].append(doc["subject_id"])
        except Exception as error:  # noqa: BLE001 - recorded on the event; a retry continues from here
            summary["failed"].append({"subject_id": patient.get("subject_id") or patient["id"], "error": str(error)[:200]})
    status = "partial" if summary["failed"] else "applied"
    counts = {k: len(v) for k, v in summary.items()}
    await db.kit_study_events.update_one({"_id": event["id"]}, {"$set": {
        "status": status, "counts": counts, "failed": summary["failed"], "processed_at": now()}})
    return {"status": status, "counts": counts, "summary": summary}


async def _event_view(event_id: str, user: dict) -> dict:
    event = await db.kit_study_events.find_one({"_id": event_id}, {"_id": 0})
    effects = [e async for e in db.kit_study_effects.find({"event_id": event_id}, {"_id": 0, "history": 0})]
    if user["role"] in ("pi", "crc"):
        # Site staff see only the participants they manage.
        visible = []
        for e in effects:
            patient = await db.patients.find_one({"id": e["patient_id"]}, {"_id": 0})
            if patient and await _server._can_access_patient(user, patient):
                visible.append(e)
        effects = visible
    effects.sort(key=lambda e: (e["site"], e["subject_id"]))
    return {**event, "effects": effects}


@router.post("/trials/{trial_id}/kit-study-events")
async def confirm_study_event(trial_id: str, body: StudyEventIn, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    _, definition = await _study_definition(trial_id, body.type)
    _check_study_role(user, definition)
    if definition.get("blocked"):
        raise HTTPException(409, f"This study event cannot be applied yet: {definition['blocked']}")
    effective = _iso(body.effective_date, "When it happened")
    if effective > now().date().isoformat():
        raise HTTPException(422, "When it happened: the date cannot be in the future.")
    fields = await _checked_fields(trial_id, definition, body.fields)
    existing = await db.kit_study_events.find_one({"_id": body.id}, {"_id": 0})
    if existing is None:
        doc = {"_id": body.id, "id": body.id, "trial_id": trial_id, "type": body.type, "label": definition["label"],
               "effective_date": effective, "fields": fields, "confirmed_at": now(), "confirmed_by": user["id"],
               "confirmed_role": user["role"], "revision": 1, "status": "processing", "corrections": []}
        from pymongo.errors import DuplicateKeyError
        try:
            await db.kit_study_events.insert_one(doc)
            await write_audit(user, "trial.kit_study_event_confirm",
                              f"Confirmed “{definition['label']}” effective {effective}", target_id=body.id, trial_id=trial_id)
        except DuplicateKeyError:
            pass
        existing = await db.kit_study_events.find_one({"_id": body.id}, {"_id": 0})
    if (existing["trial_id"], existing["type"], existing["effective_date"], existing["fields"]) != (trial_id, body.type, effective, fields) \
            and existing.get("revision", 1) == 1:
        raise HTTPException(409, "This confirmation id was already used for a different event.")
    if existing["status"] != "applied":
        await _process_study_event(existing)
    return await _event_view(body.id, user)


@router.post("/trials/{trial_id}/kit-study-events/{event_id}/process")
async def retry_study_event(trial_id: str, event_id: str, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    event = await db.kit_study_events.find_one({"_id": event_id, "trial_id": trial_id}, {"_id": 0})
    if not event:
        raise HTTPException(404, "Study event not found")
    _, definition = await _study_definition(trial_id, event["type"])
    _check_study_role(user, definition)
    result = await _process_study_event(event)
    await write_audit(user, "trial.kit_study_event_process", f"Processed “{event['label']}”: {result['status']}",
                      target_id=event_id, trial_id=trial_id)
    return await _event_view(event_id, user)


async def _correction_preview(event: dict, corrected: dict) -> dict:
    out: Dict[str, list] = {"added": [], "removed": [], "kept": [], "booked_affected": [], "completed_kept": []}
    async for patient in db.patients.find({"trial_id": event["trial_id"], "kit": {"$exists": True}}, {"_id": 0}):
        prior = await db.kit_study_effects.find_one({"_id": f"{event['id']}:{patient['id']}"}, {"_id": 0})
        want, _, _ = await _desired(corrected, patient)
        state = _next_state(prior, want, patient)
        was = (prior or {}).get("state") in ("applied", "review")
        item = {"effect_id": f"{event['id']}:{patient['id']}", "subject_id": patient.get("subject_id"),
                "from": (prior or {}).get("state"), "to": state, "reason": want.get("reason"), "booked": (prior or {}).get("booked")}
        task_done = bool(prior and prior.get("task_key") and (patient["kit"].get("actuals") or {}).get(prior["task_key"]))
        if task_done:
            out["completed_kept"].append(item)
        elif was and state in ("applied",):
            out["kept"].append(item)
        elif was:
            out["removed"].append(item)
            if item["booked"]:
                out["booked_affected"].append(item)
        elif state == "applied":
            out["added"].append(item)
    return out


@router.put("/trials/{trial_id}/kit-study-events/{event_id}")
async def correct_study_event(trial_id: str, event_id: str, body: StudyEventCorrectionIn, user=Depends(current_user)):
    await _trial_for(user, trial_id)
    event = await db.kit_study_events.find_one({"_id": event_id, "trial_id": trial_id}, {"_id": 0})
    if not event:
        raise HTTPException(404, "Study event not found")
    _, definition = await _study_definition(trial_id, event["type"])
    _check_study_role(user, definition)
    if body.revision != event["revision"]:
        raise HTTPException(409, "The study event changed. Reload it before correcting.")
    effective = _iso(body.effective_date, "When it happened")
    if effective > now().date().isoformat():
        raise HTTPException(422, "When it happened: the date cannot be in the future.")
    fields = await _checked_fields(trial_id, definition, body.fields)
    corrected = {**event, "effective_date": effective, "fields": fields, "revision": event["revision"] + 1}
    preview = await _correction_preview(event, corrected)
    if body.preview:
        return {"preview": preview, "revision": event["revision"]}
    unacknowledged = [x for x in preview["booked_affected"] if x["effect_id"] not in body.acknowledge_booked]
    if unacknowledged:
        raise HTTPException(409, {"message": "Booked closeout visits would no longer apply. Review them and acknowledge each one.",
                                  "preview": preview})
    change = {"effective_date": event["effective_date"], "fields": event["fields"], "revision": event["revision"],
              "corrected_at": now(), "corrected_by": user["id"]}
    result = await db.kit_study_events.update_one({"_id": event_id, "revision": event["revision"]}, {"$set": {
        "effective_date": effective, "fields": fields, "revision": corrected["revision"], "status": "processing",
        "corrections": [*event.get("corrections", []), change]}})
    if not result.matched_count:
        raise HTTPException(409, "The study event changed. Reload it before correcting.")
    await write_audit(user, "trial.kit_study_event_correct",
                      f"Corrected “{event['label']}”: effective {event['effective_date']} → {effective}",
                      target_id=event_id, trial_id=trial_id)
    await _process_study_event(corrected)
    return {**await _event_view(event_id, user), "preview": preview}


@router.get("/trials/{trial_id}/kit-study-events")
async def list_study_events(trial_id: str, user=Depends(current_user)):
    if user["role"] == "patient":
        raise HTTPException(403, "Not available")
    await _trial_for(user, trial_id)
    current = await _model_doc(trial_id, None)
    events = [e["id"] async for e in db.kit_study_events.find({"trial_id": trial_id}, {"_id": 0, "id": 1})]
    return {"definitions": (current or {}).get("model", {}).get("study_events") or [],
            "events": [await _event_view(eid, user) for eid in sorted(events)]}


@router.put("/patients/{patient_id}/kit/study-effects/{effect_id}/booking", dependencies=[Depends(require_roles("pi", "crc"))])
async def book_study_effect(patient_id: str, effect_id: str, body: EffectBookingIn, user=Depends(current_user)):
    patient = await _require_patient(user, patient_id)
    effect = await db.kit_study_effects.find_one({"_id": effect_id, "patient_id": patient_id}, {"_id": 0})
    if not effect or not effect.get("task_key"):
        raise HTTPException(404, "There is no visit to book for this study event.")
    if effect["state"] not in ("applied", "review"):
        raise HTTPException(409, "This study event no longer applies to the participant.")
    value = _iso(body.date, "Booked date") if body.date else None
    if value and effect["state"] == "review":
        raise HTTPException(409, "The study event was corrected and no longer applies: clear this booking instead.")
    if value and value < effect["effective"]:
        raise HTTPException(422, "Booked date: it cannot be before the event.")
    if value == effect.get("booked"):
        return effect
    patch: Dict[str, Any] = {"booked": value, "booked_by": user["id"], "booked_at": now(), "updated_at": now()}
    if value is None and effect["state"] == "review":
        patch.update(state="withdrawn", task_key=None)
    await db.kit_study_effects.update_one({"_id": effect_id}, {"$set": patch})
    await write_audit(user, "patient.kit_study_booking",
                      f"{'Booked' if value else 'Cleared'} {effect['label']} closeout{f' for {value}' if value else ''}",
                      target_id=patient_id, trial_id=patient.get("trial_id"))
    return {**effect, **patch}


# ── Blocked rules across trials (admin) ────────────────────────────────────
# A pattern rule nothing can calculate yet blocks enrolment on its trial (app/kit/patterns.py).
# The admin sees them grouped by name, ranked by how many trials wait on each, with the
# protocol's words; after a developer ships a handler, one re-check clears every trial it unblocks.


def recheck_saved_model(doc: dict) -> tuple[list[dict], bool]:
    """Re-evaluate a saved model's pattern rules against the handlers this server has, in place.
    Returns (blockers, changed)."""
    changed = False
    for key in ("base_model", "model"):
        m = doc.get(key)
        if not isinstance(m, dict) or not m.get("pattern_rules"):
            continue
        before = copy.deepcopy(m["pattern_rules"])
        kit_patterns.apply_status(m)
        changed = changed or before != m["pattern_rules"]
    found = kit_patterns.blockers(doc.get("model"))
    return found, changed or found != (doc.get("blockers") or [])


def group_blockers(docs: list[dict], trials: dict[str, dict]) -> list[dict]:
    """Saved models' blockers grouped by pattern name, most trials waiting first, with the
    protocol's words (once per distinct quote and trial)."""
    groups: Dict[str, Dict[str, Any]] = {}
    for doc in docs:
        for b in doc.get("blockers") or []:
            g = groups.setdefault(b["pattern"], {"pattern": b["pattern"],
                                                 "has_handler": b.get("has_handler", b["pattern"] in kit_patterns.PATTERNS),
                                                 "trials": set(), "rules": 0, "evidence": []})
            g["trials"].add(doc["trial_id"])
            g["rules"] += 1
            if len(g["evidence"]) < 25 and not any(e["quote"] == b["quote"] and e["trial_id"] == doc["trial_id"]
                                                   for e in g["evidence"]):
                t = trials.get(doc["trial_id"]) or {}
                g["evidence"].append({
                    "trial_id": doc["trial_id"], "trial_title": t.get("title"), "protocol_id": t.get("protocol_id"),
                    "version": doc.get("version"), "label": b["label"], "quote": b["quote"], "page": b.get("page"),
                    "quote_found": b.get("quote_found"), "reason": b["reason"], "problem": b.get("problem")})
    out = [{**g, "trials": len(g["trials"])} for g in groups.values()]
    out.sort(key=lambda g: (-g["trials"], -g["rules"], g["pattern"]))
    return out


@admin_router.get("/blockers")
async def list_blockers():
    docs = [doc async for doc in db.trial_kit_models.find(
        {"blockers.0": {"$exists": True}}, {"_id": 0, "trial_id": 1, "version": 1, "blockers": 1})]
    ids = list({d["trial_id"] for d in docs})
    trials = {t["id"]: t async for t in db.trials.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "title": 1, "protocol_id": 1})}
    return {"handlers": [{"pattern": n, "title": p.title} for n, p in sorted(kit_patterns.PATTERNS.items())],
            "blocked_trials": len(ids), "groups": group_blockers(docs, trials)}


class RecheckIn(BaseModel):
    pattern: Optional[str] = None


@admin_router.post("/blockers/recheck")
async def recheck_blockers(body: RecheckIn, user=Depends(current_user)):
    """Re-evaluate every saved model that has pattern rules (or only those naming one pattern)."""
    query: Dict[str, Any] = {"model.pattern_rules.0": {"$exists": True}}
    if body.pattern:
        query["model.pattern_rules.pattern"] = kit_patterns.pattern_name(body.pattern)
    checked, unblocked, still = 0, [], 0
    async for doc in db.trial_kit_models.find(query, {"_id": 0}):
        checked += 1
        was = bool(doc.get("blockers"))
        found, changed = recheck_saved_model(doc)
        if changed:
            await db.trial_kit_models.update_one(
                {"trial_id": doc["trial_id"], "version": doc.get("version")},
                {"$set": {"base_model": doc.get("base_model"), "model": doc.get("model"), "blockers": found,
                          "blockers_checked_at": now()}})
        if was and not found:
            unblocked.append(doc["trial_id"])
        elif found:
            still += 1
    scope = f" for {body.pattern}" if body.pattern else ""
    await write_audit(user, "kit.blockers_recheck",
                      f"Re-checked {checked} protocol models{scope}: {len(unblocked)} unblocked, {still} still blocked")
    return {"checked": checked, "unblocked": unblocked, "still_blocked": still, "handlers": kit_patterns.handler_names()}
