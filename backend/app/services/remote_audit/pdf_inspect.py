"""Structural inspection of an uploaded document — spec §10.2 (metadata
extraction) and §16.8's malformed/hostile-file edge cases.

Runs ONLY in the worker process, never in the API request path: everything
here parses attacker-controlled bytes (Production-Grade Implementation Plan
v1.3 correction #4 — "the API process never parses untrusted
evidence/media/documents directly, in any environment").

Three principles hold throughout:

* A parse failure is a RESULT, not an exception. `inspect_pdf()` returns an
  `unreadable` verdict with a reason so the pipeline can route the document
  to REVIEW_REQUIRED. A crashing parser would take down a worker that is
  processing other tenants' documents.
* Nothing is skipped silently. An encrypted PDF, a page-count bomb, or an
  embedded payload each produce a named finding, never an empty result that
  reads like a clean document.
* Extraction output is a SUGGESTION. Nothing here is a fact about the
  document's contents — the reviewer confirms, per this app's
  extract -> review -> commit rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Hostile-input guards. These are deliberately generous for real certificates
# and calibration reports while still bounding what a decompression bomb or a
# page-flood can cost one worker slot.
MAX_PAGES = 500
MAX_TEXT_CHARS = 2_000_000
PDF_MAGIC = b"%PDF-"


@dataclass
class PdfInspection:
    readable: bool
    reason: Optional[str] = None
    encrypted: bool = False
    needs_password: bool = False
    page_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    text_truncated: bool = False
    has_embedded_files: bool = False
    has_javascript: bool = False
    findings: List[str] = field(default_factory=list)


def looks_like_pdf(data: bytes) -> bool:
    """Magic-byte check. The claimed content-type is never trusted on its own
    (spec §8.4 MIME_MATCH); a polyglot that merely *ends* in PDF structure is
    not accepted as a PDF here."""
    return data[:1024].startswith(PDF_MAGIC)


def _parse_pdf_date(value: Any) -> Optional[datetime]:
    """PDF date strings look like D:20260917103000+05'30'. Returns None for
    anything unparseable rather than guessing — a wrong creation date on a
    calibration certificate is worse than no creation date."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.startswith("D:"):
        raw = raw[2:]
    digits = ""
    for ch in raw:
        if ch.isdigit():
            digits += ch
        else:
            break
    if len(digits) < 8:
        return None
    try:
        year, month, day = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
        hour = int(digits[8:10]) if len(digits) >= 10 else 0
        minute = int(digits[10:12]) if len(digits) >= 12 else 0
        second = int(digits[12:14]) if len(digits) >= 14 else 0
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def inspect_pdf(data: bytes) -> PdfInspection:
    """Open the document with PyMuPDF and report what it actually contains.

    Never raises on malformed input: every failure path returns
    `readable=False` with a reason. PyMuPDF is a C library operating on
    untrusted bytes, so the broad `except Exception` here is the containment
    boundary, not laziness — the alternative is a worker crash loop driven by
    whatever a site uploads.
    """
    if not looks_like_pdf(data):
        return PdfInspection(readable=False, reason="NOT_A_PDF", findings=["NOT_A_PDF"])

    try:
        import fitz  # PyMuPDF — lazy import so the API process never loads it
    except ImportError:
        return PdfInspection(readable=False, reason="PDF_PARSER_UNAVAILABLE",
                             findings=["PDF_PARSER_UNAVAILABLE"])

    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 -- malformed input must not crash the worker
        return PdfInspection(readable=False, reason=f"PDF_PARSE_FAILED: {type(exc).__name__}",
                             findings=["PDF_PARSE_FAILED"])

    result = PdfInspection(readable=True)
    try:
        result.encrypted = bool(getattr(doc, "is_encrypted", False))
        # `needs_pass` stays True when PyMuPDF could not open the content
        # without a password. Policy (spec §16.8) is to hold such a document
        # for a human, never to quietly treat an unreadable file as empty.
        result.needs_password = bool(getattr(doc, "needs_pass", False))
        if result.needs_password:
            result.findings.append("PASSWORD_PROTECTED")
            result.reason = "PASSWORD_PROTECTED"
            return result

        result.page_count = int(doc.page_count)
        if result.page_count > MAX_PAGES:
            result.findings.append("PAGE_COUNT_EXCEEDS_LIMIT")
            result.readable = False
            result.reason = f"PAGE_COUNT_EXCEEDS_LIMIT: {result.page_count} > {MAX_PAGES}"
            return result

        raw_meta = dict(doc.metadata or {})
        result.metadata = {
            "title": raw_meta.get("title") or None,
            "author": raw_meta.get("author") or None,
            "subject": raw_meta.get("subject") or None,
            "producer": raw_meta.get("producer") or None,
            "creator": raw_meta.get("creator") or None,
            "creation_date_raw": raw_meta.get("creationDate") or None,
            "mod_date_raw": raw_meta.get("modDate") or None,
        }
        created = _parse_pdf_date(raw_meta.get("creationDate"))
        modified = _parse_pdf_date(raw_meta.get("modDate"))
        result.metadata["creation_date"] = created.isoformat() if created else None
        result.metadata["mod_date"] = modified.isoformat() if modified else None
        # A document modified after it was created is entirely normal; a
        # document created AFTER it was modified is not, and is worth a
        # reviewer's attention rather than a silent pass.
        if created and modified and modified < created:
            result.findings.append("MODIFIED_BEFORE_CREATED")

        result.has_embedded_files = bool(getattr(doc, "embfile_count", lambda: 0)())
        if result.has_embedded_files:
            result.findings.append("HAS_EMBEDDED_FILES")

        text_parts: List[str] = []
        total = 0
        for page in doc:
            try:
                page_text = str(page.get_text() or "")
            except Exception:  # noqa: BLE001 -- one bad page must not lose the rest
                result.findings.append("PAGE_TEXT_EXTRACTION_FAILED")
                continue
            total += len(page_text)
            if total > MAX_TEXT_CHARS:
                text_parts.append(page_text[: max(0, MAX_TEXT_CHARS - (total - len(page_text)))])
                result.text_truncated = True
                result.findings.append("TEXT_TRUNCATED")
                break
            text_parts.append(page_text)
        result.text = "\n".join(text_parts)

        # A text-free PDF is usually a scan. That is not a defect — it just
        # means OCR or the vision model, not the text layer, has to supply
        # any extracted fields, and the reviewer needs to know which it was.
        if not result.text.strip():
            result.findings.append("NO_TEXT_LAYER")
    except Exception as exc:  # noqa: BLE001
        result.readable = False
        result.reason = f"PDF_INSPECTION_FAILED: {type(exc).__name__}"
        result.findings.append("PDF_INSPECTION_FAILED")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001 -- close failures are not findings
                pass
    return result


def render_first_page_png(data: bytes, *, max_dimension: int = 1600) -> Optional[Tuple[bytes, str]]:
    """Rasterise page 1 so a scanned certificate can be sent to the vision
    model, which takes images rather than PDFs. Returns None (never raises) if
    rendering isn't possible — the pipeline then falls back to manual entry,
    which the plan requires to always remain available."""
    try:
        import fitz
    except ImportError:
        return None
    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.needs_pass or doc.page_count < 1:
            return None
        page = doc.load_page(0)
        rect = page.rect
        longest = max(rect.width, rect.height) or 1
        zoom = min(2.0, max_dimension / longest)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return bytes(pix.tobytes("png")), "image/png"
    except Exception:  # noqa: BLE001 -- rendering is best-effort by design
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass
