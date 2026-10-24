"""Run the real reader/reconciler/compiler with no server or database writes."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from . import bootstrap  # noqa: F401
from app.extraction.artifacts import MemoryArtifactStore
from app.extraction.documents import pages_from_pdf, pages_from_text
from app.extraction.reader import ReaderLimits
from app.extraction.runner import extract_protocol
from .client import ReplayClient, safe_error
from .gold import Expectations, score


def reader_limits(*, max_pages=40, max_calls=8, pages_per_batch=4, time_seconds=600):
    return ReaderLimits(max_selected_pages=max_pages, max_model_calls=max_calls,
                        pages_per_batch=pages_per_batch, time_budget_seconds=time_seconds,
                        read_all_pages=True, read_amendment_history=False)


def load_document(data: bytes, filename: str):
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError("Use a nonempty PDF or UTF-8 text file up to 20 MB.")
    is_pdf = data.startswith(b"%PDF-")
    if filename.lower().endswith(".pdf") and not is_pdf:
        raise ValueError("The selected .pdf file has no PDF header.")
    if is_pdf:
        import pypdfium2 as pdfium
        with closing(pdfium.PdfDocument(data)) as document:
            if len(document) > 1200:
                raise ValueError("This laboratory accepts at most 1,200 PDF pages. No partial document was sent.")
        return pages_from_pdf(data), data
    if len(data) > 600_000:
        raise ValueError("Text exceeds the laboratory's 600 KB text limit; no text was silently truncated.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Use UTF-8 text or a PDF; this file could not be decoded.") from None
    return pages_from_text(text), None


def extract(data: bytes, filename: str, *, client, artifacts=None,
            limits: ReaderLimits | None = None, expected: Expectations | None = None) -> dict:
    artifacts = artifacts if artifacts is not None else MemoryArtifactStore()
    budget = getattr(client, "budget", None)
    record_start = len(budget.records) if budget else 0
    started = time.monotonic()
    report = {"schema": "mtb-extraction-lab.v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "filename": filename, "document_hash": hashlib.sha256(data).hexdigest(),
              "mode": "OFFLINE_FIXTURE_REPLAY" if isinstance(client, ReplayClient) else
                      "GEMINI_SCHEMA" if client.constrained else "GEMINI_JSON_ONLY",
              "model": getattr(client, "model_name", "scripted fixtures; no model invoked"),
              "status": "FAILED", "coverage_status": "NOT_ASSESSED", "facts": None, "results": []}
    try:
        pages, pdf = load_document(data, filename)
        report["pages"] = [{"page": p.page_number, "text_status": p.text_status, "characters": len(p.text)} for p in pages]
        # The PDF packet logic lives on the injected client. Preserve the actual
        # bytes here rather than trusting a UI filename to decide attachments.
        client._pdf_bytes = pdf
        protocol = extract_protocol(pages, pdf_bytes=pdf, document_hash=report["document_hash"],
                                    schedule_name=Path(filename).stem, client=client,
                                    artifacts=artifacts, limits=limits or reader_limits())
        report["facts"] = protocol.facts.model_dump(mode="json", by_alias=True)
        report["details"] = protocol.details.model_dump(mode="json")
        report["results"] = [{"option": option, **result.model_dump(mode="json", by_alias=True)}
                             for option, result in protocol.results]
        report["coverage_status"] = protocol.facts.accountability.get("coverage", {}).get("status", "NOT_ASSESSED")
        report["status"] = "COMPLETED" if report["coverage_status"] == "COMPLETE" else "PARTIAL"
        report["issue_counts"] = dict(Counter(i.issue_code for _, r in protocol.results for i in r.issues))
        report["event_count"] = sum(len(r.schedule.events) if r.schedule else 0 for _, r in protocol.results)
        report["blocking_issues"] = sum(i.blocking for _, r in protocol.results for i in r.issues)
    except Exception as error:
        report["error"] = safe_error(error, getattr(client, "_api_key", ""))
        # Retain an inspectable partial coverage manifest after early failure.
        report["partial_manifests"] = [item for item in artifacts.items.values()
                                       if isinstance(item, dict) and item.get("kind") == "manifest"]
    report["seconds"] = round(time.monotonic() - started, 3)
    report["requests"] = budget.records[record_start:] if budget else []
    used = list(dict.fromkeys(r["model"] for r in report["requests"] if r.get("status") == "OK"))
    if len(used) > 1 or (used and used[0] != report["model"]):
        report["models_used"] = used
        report["model"] = " → ".join(used)
    report["score"] = score(report, expected)
    return report


def replay(case, *, artifacts=None, limits=None):
    return extract(case.source.encode(), f"{case.id}.txt",
                   client=ReplayClient(case.metadata, case.reply), artifacts=artifacts,
                   limits=limits, expected=case.expected)


def event_rows(report):
    rows = []
    for result in report.get("results", []):
        schedule = result.get("schedule") or {}
        for event in schedule.get("events", []):
            rows.append({"schedule": (result.get("option") or {}).get("label", "Primary"),
                         "visit": event["display_name"], "code": event["code"],
                         "type": event.get("event_type"), "mode": event.get("visit_mode") or "Not stated",
                         "timing": json.dumps(event.get("timing"), ensure_ascii=False),
                         "window": json.dumps(event["window"]) if event.get("window") else "Not stated",
                         "activities": ", ".join(a.get("display_name") or a.get("protocol_label") or a.get("code") or "?"
                                                for a in event.get("activities", [])),
                         "interpretation": event.get("interpretation_status")})
    return rows


def main():
    parser = argparse.ArgumentParser(description="Replay all synthetic extraction fixtures without a model/key/database.")
    parser.add_argument("--offline-suite", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from .cases import CASES
    reports = [replay(case) for case in CASES]
    summary = {"mode": "OFFLINE_FIXTURE_REPLAY", "model_calls": 0,
               "note": "Exercises pipeline plumbing with expected replies; does not measure Gemini accuracy.",
               "cases": [{"id": c.id, "status": r["score"]["status"], "passed": r["score"]["passed"],
                          "total": r["score"]["total"], "error": r.get("error"),
                          "failed_checks": [row for row in r["score"]["checks"] if not row["passed"]]}
                         for c, r in zip(CASES, reports)]}
    payload = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return int(any(row["status"] != "PASS" for row in summary["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
