"""Worker-side document processing — spec §10, Production-Grade
Implementation Plan v1.3 Phase 4.

Runs ONLY in the worker (`remote_audit_worker.py`), never in the API request
path, because every step past step 1 parses attacker-controlled bytes.

Order is not an implementation detail — it is the control:

    1. MALWARE GATE. Nothing else runs until the scanner returns CLEAN. An
       UNAVAILABLE scanner raises `ScannerUnavailableError`, the document
       stays PROCESSING_BLOCKED and INVISIBLE, and the queue retries. This is
       stricter than routing to review on purpose (v1.3 correction #15): a
       reviewer is never shown a file nobody has scanned.
    2. Real hash and real magic bytes, from the stored bytes — never claims.
    3. Structural inspection (PyMuPDF).
    4. Signature validation at full correction-#11 scope.
    5. Extraction (text layer, then the vision model) — SUGGESTIONS only.
    6. One verdict, recorded append-only with every finding that produced it.

`ACCEPTED` is unreachable from here. Processing can only produce
TECHNICALLY_VALID or REVIEW_REQUIRED (or a technical rejection); acceptance is
a human decision made through the API, and it requires confirmed field values.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

import storage as file_storage

from . import documents as documents_service
from . import file_checks
from . import observability as obs
from . import pdf_inspect
from . import signatures as signature_service
from .signatures import SignatureTrust

log = logging.getLogger(__name__)

RULE_SET_VERSION = "document-checks-v1"

OUTCOME_TECHNICALLY_VALID = documents_service.STATUS_TECHNICALLY_VALID
OUTCOME_REVIEW_REQUIRED = documents_service.STATUS_REVIEW_REQUIRED
OUTCOME_REJECTED = documents_service.STATUS_REJECTED


#: States reached by a person, not by the pipeline. Re-processing must refuse
#: to touch these — a worker re-run is a recomputation, and a recomputation
#: must never overrule a human decision.
_HUMAN_DECIDED_STATUSES = frozenset({
    documents_service.STATUS_ACCEPTED,
    documents_service.STATUS_REJECTED,
    documents_service.STATUS_SUPERSEDED,
    documents_service.STATUS_EXPIRED,
})


class ScannerUnavailableError(RuntimeError):
    """Raised so the job queue's retry/backoff handles it. Deliberately NOT
    caught into a status here: swallowing this would mean deciding a
    document's fate without a malware verdict."""


class DocumentAlreadyDecidedError(RuntimeError):
    """A re-processing job arrived for a document a human has already ruled
    on. Not retryable — retrying would not change the answer."""


@dataclass(frozen=True)
class DocumentPolicy:
    #: Hold password-protected PDFs for a human (default) or reject outright.
    #: Either way they are never silently skipped (spec §16.8).
    reject_password_protected: bool = False
    #: Run the vision model on documents whose text layer yielded nothing.
    #: Disabled deployments simply fall back to manual entry, which the plan
    #: requires to always remain available.
    enable_vision_extraction: bool = True
    #: How far in the future a certificate date may plausibly sit.
    max_future_date_days: int = 365 * 10


@dataclass
class DocumentCheckResult:
    outcome: str
    checks: Dict[str, Any]
    findings: List[Dict[str, Any]]
    extraction: Optional[Dict[str, Any]]
    sha256_server: str
    content_blocked: bool


def _finding(code: str, severity: str, explanation: str, **extra: Any) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "explanation": explanation, **extra}


# ── Date handling ───────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")


def classify_date_string(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return `(iso_date, ambiguity_code)`.

    The ambiguity that matters: `03/04/2026` is 3 April in most of the world
    and 4 March in the US. There is no safe default — guessing would silently
    move a calibration expiry by a month. So a d/m-vs-m/d ambiguous string
    returns no date and an `AMBIGUOUS_DATE_FORMAT` code, and a human resolves
    it. A slash date whose day component exceeds 12 is unambiguous and IS
    parsed, because only one reading is possible.
    """
    if value in (None, ""):
        return None, None
    text = str(value).strip()

    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat(), None
        except ValueError:
            return None, "INVALID_DATE"

    slash = _SLASH_DATE.search(text)
    if slash:
        first, second, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if first > 12 and second <= 12:
            try:
                return date(year, second, first).isoformat(), None  # unambiguous: dd/mm
            except ValueError:
                return None, "INVALID_DATE"
        if second > 12 and first <= 12:
            try:
                return date(year, first, second).isoformat(), None  # unambiguous: mm/dd
            except ValueError:
                return None, "INVALID_DATE"
        return None, "AMBIGUOUS_DATE_FORMAT"

    return None, "UNRECOGNIZED_DATE_FORMAT"


# ── Text-layer extraction (free, deterministic, runs before any model) ──────

_CERT_NUMBER_PATTERNS = [
    re.compile(r"certificate\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9/\-]{3,40})", re.I),
    re.compile(r"cert\.?\s*(?:no\.?|#)\s*[:\-]?\s*([A-Za-z0-9/\-]{3,40})", re.I),
]
_CAL_DATE_PATTERNS = [
    re.compile(r"(?:date\s+of\s+calibration|calibration\s+date|calibrated\s+on)\s*[:\-]?\s*([0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{2,4})", re.I),
]
_DUE_DATE_PATTERNS = [
    re.compile(r"(?:due\s+date|next\s+calibration(?:\s+due)?|valid\s+until|expiry\s+date)\s*[:\-]?\s*([0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{2,4})", re.I),
]


def extract_from_text(text: str) -> Dict[str, Any]:
    """Pull certificate fields out of a PDF's own text layer.

    Preferred over the vision model whenever it works: it is deterministic,
    free, and leaves no room for a model to invent a value. Returns only what
    it actually matched — a miss is an absent key, never a placeholder.
    """
    fields: Dict[str, Any] = {}
    if not text or not text.strip():
        return fields
    for patterns, key in ((_CERT_NUMBER_PATTERNS, "certificate_number"),
                          (_CAL_DATE_PATTERNS, "calibration_date"),
                          (_DUE_DATE_PATTERNS, "due_date")):
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                fields[key] = match.group(1).strip()
                break
    return fields


def _normalize_extracted_dates(
    fields: Dict[str, Any], *, policy: DocumentPolicy,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Normalize date-ish suggestions and surface every one that a human must
    resolve. Returns `(normalized_fields, findings)`."""
    findings: List[Dict[str, Any]] = []
    normalized = dict(fields)
    today = datetime.now(timezone.utc).date()

    for key in ("calibration_date", "due_date", "document_date"):
        raw = fields.get(key)
        if raw in (None, ""):
            continue
        iso, ambiguity = classify_date_string(raw)
        normalized[f"{key}_raw"] = raw
        normalized[key] = iso
        if ambiguity:
            findings.append(_finding(
                ambiguity, "medium",
                f"{key.replace('_', ' ')} could not be read unambiguously from {raw!r}; "
                "a reviewer must confirm it.",
                field=key, measured_value=raw))
            continue
        if iso:
            parsed = date.fromisoformat(iso)
            if (parsed - today).days > policy.max_future_date_days:
                findings.append(_finding(
                    "IMPLAUSIBLE_FUTURE_DATE", "medium",
                    f"{key.replace('_', ' ')} is {iso}, implausibly far in the future.",
                    field=key, measured_value=iso))

    cal, due = normalized.get("calibration_date"), normalized.get("due_date")
    if cal and due and due < cal:
        findings.append(_finding(
            "DUE_BEFORE_CALIBRATION", "high",
            f"The due date ({due}) precedes the calibration date ({cal}).",
            measured_value=due, threshold=cal))
    if due and date.fromisoformat(due) < today:
        findings.append(_finding(
            "CERTIFICATE_ALREADY_EXPIRED", "high",
            f"The certificate's due date ({due}) has already passed.",
            measured_value=due, threshold=today.isoformat()))
    return normalized, findings


# ── Main pipeline ───────────────────────────────────────────────────────────

async def run_document_processing(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    policy: Optional[DocumentPolicy] = None,
) -> DocumentCheckResult:
    policy = policy or DocumentPolicy()
    doc = await documents_service.get_document(db, document_id)
    if doc is None:
        raise ValueError(f"document {document_id!r} not found")

    # Re-runs are allowed from any state whose verdict is still the machine's
    # to make. They are NOT allowed once a human has decided: re-running
    # checks must never quietly un-accept a document a reviewer accepted, or
    # resurrect one they rejected. Those are decisions, not computations.
    if doc["status"] in _HUMAN_DECIDED_STATUSES:
        raise DocumentAlreadyDecidedError(
            f"document {document_id} is {doc['status']}; a human decision is not re-run by the worker")
    if doc["status"] != documents_service.STATUS_PROCESSING:
        await documents_service.transition(
            db, document_id, documents_service.STATUS_PROCESSING, expected_status=doc["status"])

    storage = file_storage.get_storage()
    data, _ = await storage.open(doc["object_key"])

    findings: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}

    # ── 1. Malware gate. Nothing below this line runs on unscanned bytes. ──
    scan_status, scan_detail = await file_checks.scan(data)
    checks["malware_status"] = scan_status.value
    scan_skipped = False
    if scan_status == file_checks.MalwareScanStatus.UNAVAILABLE:
        obs.emit_metric(obs.Metric.MALWARE_SCAN_UNAVAILABLE, tags={"kind": "document"})
        if file_checks.scan_is_required():
            await documents_service.transition(
                db, document_id, documents_service.STATUS_PROCESSING_BLOCKED,
                expected_status=documents_service.STATUS_PROCESSING)
            raise ScannerUnavailableError(
                f"malware scanner unavailable ({scan_detail}); document {document_id} remains blocked")
        # Dev/demo only. Processing continues, the document becomes visible --
        # but labelled NOT_SCANNED and forced to review. It is never CLEAN.
        scan_skipped = True
        checks["malware_status"] = "NOT_SCANNED"
        findings.append(_finding(
            "FILE_NOT_SCANNED", "high",
            "This document was never scanned for malware -- the scanner is disabled on this "
            "deployment. Do not open it outside a controlled environment."))
        log.warning("document %s processed WITHOUT a malware scan (%s=false)",
                    document_id, file_checks.REQUIRE_SCAN_ENV)

    if scan_status == file_checks.MalwareScanStatus.DETECTED:
        obs.emit_metric(obs.Metric.MALWARE_DETECTED, tags={"kind": "document"})
        # Rejected outright, and the bytes are never servable afterwards. The
        # RECORD stays visible so the site is told why — the CONTENT does not.
        findings.append(_finding("MALWARE_DETECTED", "critical",
                                 "Malware was detected in this file. It has been rejected and its "
                                 "contents will not be served to anyone.",
                                 measured_value=scan_detail))
        checks["sha256_server"] = hashlib.sha256(data).hexdigest()
        result = DocumentCheckResult(
            outcome=OUTCOME_REJECTED, checks=checks, findings=findings, extraction=None,
            sha256_server=checks["sha256_server"], content_blocked=True)
        await _persist(db, document_id, result, reason_code="MALWARE_DETECTED")
        return result

    # ── 2. Real bytes: hash and magic. Claims are not evidence. ───────────
    sha256_server = hashlib.sha256(data).hexdigest()
    checks["sha256_server"] = sha256_server
    media_type = doc["media_type"]
    mime_match = file_checks.sniff_media_type(data, media_type)
    checks["mime_match"] = mime_match
    checks["size_bytes"] = len(data)

    if not mime_match:
        findings.append(_finding(
            "MIME_MISMATCH", "high",
            f"The file's contents are not a valid {media_type}, despite being uploaded as one.",
            measured_value=media_type))

    duplicate = await documents_service.find_duplicate_sha256(
        db, sha256_server, exclude_document_id=document_id)
    checks["duplicate_sha256"] = duplicate
    if duplicate:
        findings.append(_finding(
            "DUPLICATE_DOCUMENT", "medium",
            "A byte-identical document has already been uploaded for this audit."))

    is_pdf = media_type == "application/pdf"
    extraction: Optional[Dict[str, Any]] = None

    if is_pdf and mime_match:
        # ── 3. Structure ──────────────────────────────────────────────────
        inspection = pdf_inspect.inspect_pdf(data)
        checks["pdf"] = {
            "readable": inspection.readable,
            "reason": inspection.reason,
            "encrypted": inspection.encrypted,
            "needs_password": inspection.needs_password,
            "page_count": inspection.page_count,
            "metadata": inspection.metadata,
            "has_embedded_files": inspection.has_embedded_files,
            "text_truncated": inspection.text_truncated,
            "structural_findings": inspection.findings,
        }
        if not inspection.readable and inspection.reason:
            obs.emit_metric(obs.Metric.PARSER_FAILURE, tags={"reason": inspection.reason[:40]})
        if inspection.needs_password:
            findings.append(_finding(
                "PASSWORD_PROTECTED", "high",
                "This PDF is password-protected, so its contents could not be checked."))
            if policy.reject_password_protected:
                result = DocumentCheckResult(
                    outcome=OUTCOME_REJECTED, checks=checks, findings=findings, extraction=None,
                    sha256_server=sha256_server, content_blocked=False)
                await _persist(db, document_id, result, reason_code="PASSWORD_PROTECTED")
                return result
        elif not inspection.readable:
            findings.append(_finding(
                "PDF_UNREADABLE", "high",
                f"The PDF could not be parsed ({inspection.reason}).",
                measured_value=inspection.reason))
        else:
            if inspection.has_embedded_files:
                findings.append(_finding(
                    "HAS_EMBEDDED_FILES", "medium",
                    "This PDF carries embedded file attachments, which a certificate normally "
                    "does not."))
            if "MODIFIED_BEFORE_CREATED" in inspection.findings:
                findings.append(_finding(
                    "METADATA_TIMELINE_INCONSISTENT", "medium",
                    "The PDF reports a modification date earlier than its creation date."))
            if "NO_TEXT_LAYER" in inspection.findings:
                findings.append(_finding(
                    "NO_TEXT_LAYER", "info",
                    "This PDF has no text layer, so it is most likely a scan. Field values come "
                    "from image analysis and must be confirmed."))

        # ── 4. Signatures — full correction-#11 scope ─────────────────────
        report = signature_service.validate_pdf_signatures(data)
        checks["signature"] = report.to_dict()
        findings.extend(_signature_findings(report))

        # ── 5. Extraction: text layer first, vision model only if needed ──
        extraction = await _extract(data, inspection, policy)
    elif mime_match:
        # A photographed certificate. No structure or signature to check;
        # the vision model is the only extraction path available.
        checks["pdf"] = None
        checks["signature"] = {"overall": SignatureTrust.NONE.value,
                               "error": "not applicable to an image document",
                               "anchors_configured": 0, "signatures": []}
        extraction = await _extract_from_image(data, media_type, policy)

    if extraction:
        normalized, date_findings = _normalize_extracted_dates(extraction.get("fields", {}), policy=policy)
        extraction["fields"] = normalized
        findings.extend(date_findings)
        if extraction.get("confidence") in ("low", None):
            findings.append(_finding(
                "LOW_EXTRACTION_CONFIDENCE", "medium",
                "The automatic field extraction was low-confidence. Every field needs confirming.",
                source=extraction.get("source")))
        await documents_service.set_extraction(db, document_id, extraction)

    outcome = _decide(findings)
    result = DocumentCheckResult(
        outcome=outcome, checks=checks, findings=findings, extraction=extraction,
        sha256_server=sha256_server, content_blocked=False)
    await _persist(db, document_id, result)
    obs.emit_metric(obs.Metric.DOCUMENT_OUTCOME,
                    tags={"outcome": outcome, "document_type": doc["document_type"]})
    return result


def _signature_findings(report: signature_service.SignatureReport) -> List[Dict[str, Any]]:
    """Turn a signature report into findings.

    Note what is NOT here: there is no finding that says "signed, therefore
    fine". A trusted signature produces no finding because it raises no
    question; every other outcome produces one. `SignatureTrust.NONE` is also
    silent — most site documents are legitimately unsigned, and treating
    "unsigned" as suspicious would bury reviewers in noise.
    """
    out: List[Dict[str, Any]] = []
    if report.overall in (SignatureTrust.NONE, SignatureTrust.TRUSTED):
        return out
    if report.overall == SignatureTrust.UNKNOWN:
        out.append(_finding(
            "SIGNATURE_UNKNOWN", "medium",
            f"The document's signatures could not be evaluated ({report.error}). "
            "This is not the same as the document being unsigned.",
            source="pyhanko"))
        return out
    for sig in report.signatures:
        if sig.trust == SignatureTrust.TRUSTED:
            continue
        severity = "high" if sig.trust == SignatureTrust.INVALID else "medium"
        detail = "; ".join(sig.reasons) or sig.trust.value
        out.append(_finding(
            f"SIGNATURE_{sig.trust.value}", severity,
            f"Signature field {sig.field_name!r}: {detail}",
            source="pyhanko", measured_value=sig.trust.value))
    return out


def _decide(findings: List[Dict[str, Any]]) -> str:
    """One place, one rule.

    A document is TECHNICALLY_VALID only when nothing was found that a human
    should look at. Anything else is REVIEW_REQUIRED. Note that a `critical`
    finding can only come from malware, which returned earlier — so this
    function never has to decide whether a critical finding is acceptable.
    """
    if any(f["severity"] in ("critical", "high", "medium") for f in findings):
        return OUTCOME_REVIEW_REQUIRED
    return OUTCOME_TECHNICALLY_VALID


async def _persist(
    db: AsyncIOMotorDatabase, document_id: str, result: DocumentCheckResult,
    *, reason_code: Optional[str] = None,
) -> None:
    await documents_service.record_check(
        db, document_id=document_id, outcome=result.outcome, checks=result.checks,
        findings=result.findings, rule_set_version=RULE_SET_VERSION)
    extra: Dict[str, Any] = {
        "sha256_server": result.sha256_server,
        "content_blocked": result.content_blocked,
    }
    if reason_code:
        extra["decision_reason_code"] = reason_code
        extra["decided_by"] = "system"
        extra["decided_at"] = datetime.now(timezone.utc)
    await documents_service.transition(
        db, document_id, result.outcome,
        expected_status=documents_service.STATUS_PROCESSING, extra=extra)


# ── Extraction helpers ──────────────────────────────────────────────────────

async def _extract(
    data: bytes, inspection: pdf_inspect.PdfInspection, policy: DocumentPolicy,
) -> Optional[Dict[str, Any]]:
    text_fields = extract_from_text(inspection.text) if inspection.readable else {}
    if text_fields:
        return {
            "source": "pdf_text_layer",
            "fields": text_fields,
            # The text layer is a transcription of what the issuer wrote, not
            # an interpretation of a picture — but the reviewer still confirms.
            "confidence": "medium",
            "confidence_notes": "Read from the PDF's own text layer.",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
    if not policy.enable_vision_extraction:
        return None
    rendered = pdf_inspect.render_first_page_png(data)
    if rendered is None:
        return None
    png, media_type = rendered
    return await _extract_from_image(png, media_type, policy)


async def _extract_from_image(
    data: bytes, media_type: str, policy: DocumentPolicy,
) -> Optional[Dict[str, Any]]:
    """Vision-model extraction. Returns None on any failure.

    Extraction is an ASSIST. If no model is configured, or the call fails, the
    reviewer types the fields in — which the plan requires to always work, so a
    provider outage must never become a processing failure.
    """
    if not policy.enable_vision_extraction:
        return None
    from .vision_extraction import ExtractionNotConfigured, analyze_certificate_photo
    try:
        result = await analyze_certificate_photo(image_bytes=data, media_type=media_type)
    except ExtractionNotConfigured:
        log.info("no vision provider configured; document extraction falls back to manual entry")
        return None
    except Exception as exc:  # noqa: BLE001 -- an assist that fails must not fail the document
        log.warning("vision extraction failed; falling back to manual entry: %s", exc)
        return None
    return {
        "source": f"vision:{result.provider}",
        "model": result.model,
        "fields": dict(result.fields),
        "confidence": result.confidence,
        "confidence_notes": result.confidence_notes,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
