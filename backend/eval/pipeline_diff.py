"""Phase 2 part 3: run the legacy and native extraction pipelines on the same
protocol PDF and diff their resulting canonical schedules.

Per the architecture plan (BUILD-SEQUENCE.md, Phase 2 item 3): "Run both
pipelines on the same protocols and diff. This is the only moment the old
pipeline is worth anything - as a reference implementation." This script is
that comparison, reusing the domain layer's own typed schedule-diff
(app/domain/schedule/diff.py) - the same tool that classifies a protocol
amendment's clinical significance - rather than inventing a second diff
mechanism just for this.

Run from backend/:
  python eval/pipeline_diff.py path/to/protocol.pdf
  python eval/pipeline_diff.py --corpus-dir <dir>          # every numbered PDF
  python eval/pipeline_diff.py path/to/protocol.pdf --json report.json

This makes REAL, BILLED calls to both providers - ANTHROPIC_API_KEY for the
native pipeline, and whatever PROTOCOL_EXTRACTION_PROVIDER needs (default
Gemini -> GEMINI_API_KEY) for the legacy one. It is never invoked
automatically by the app or its tests; a human runs it deliberately when
comparing pipelines before a cutover decision.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
load_dotenv(os.path.join(_BACKEND, ".env"))

from app.domain.schedule.diff import compare_schedule_versions  # noqa: E402
from app.domain.schedule.models import UniversalSchedule  # noqa: E402

_NUM_RE = re.compile(r"^(\d+)\.")


async def run_legacy(pdf_bytes: bytes) -> tuple[UniversalSchedule | None, list[str]]:
    """Legacy pipeline: PDF -> ExtractedSchedule -> CanonicalSchedulePlan ->
    UniversalSchedule, via the same translation production import already
    uses (app/services/canonical_import.py:universal_schedule_from_plan),
    fed the same way server.py feeds it (canonical_plan/evidence_facts as
    plain dicts, server.py:3795-3797).
    """
    import protocol_extraction as pe
    from app.services.canonical_import import universal_schedule_from_plan

    try:
        details, results = await pe.extract_protocol_bundle_all(pdf_bytes)
    except Exception as error:  # noqa: BLE001 - reported, not raised
        return None, [f"legacy extraction failed: {error}"]
    if not results:
        return None, ["legacy pipeline produced no schedule option"]
    _option, schedule = results[0]
    if schedule.canonical_plan is None:
        return None, ["legacy pipeline produced no canonical plan"]
    try:
        universal, issues = universal_schedule_from_plan(
            schedule.canonical_plan.model_dump(),
            name=details.title or "Schedule of Assessments",
            evidence_facts=[item.model_dump() for item in schedule.evidence_facts],
        )
    except Exception as error:  # noqa: BLE001
        return None, [f"legacy-to-canonical translation failed: {error}"]
    return universal, [f"{item.issue_code}: {item.message}" for item in issues if item.blocking]


def run_native(pdf_bytes: bytes) -> tuple[UniversalSchedule | None, list[str]]:
    """Native pipeline: PDF -> UniversalSchedule directly, via the same
    ingestion/assembly path a real extraction run uses
    (app/extraction/runner.py:execute_run), minus the DB persistence step.
    """
    from app.extraction.graph import run_extraction
    from app.extraction.llm_client import ExtractionModelError
    from app.extraction.runner import build_provider, pages_from_pdf

    try:
        pages = pages_from_pdf(pdf_bytes)
        provider = build_provider(pages, pdf_bytes=pdf_bytes)
        result = run_extraction(
            provider,
            document_hash=hashlib.sha256(pdf_bytes).hexdigest(),
            pages=pages,
        )
    except ExtractionModelError as error:
        return None, [f"native extraction failed: {error}"]
    blocking = [item.message for item in result.issues if item.blocking]
    if result.schedule is None:
        return None, blocking or ["native pipeline produced no schedule"]
    return result.schedule, blocking


def diff_report(
    legacy: UniversalSchedule | None, legacy_issues: list[str],
    native: UniversalSchedule | None, native_issues: list[str],
) -> dict:
    """Build the comparison report. Matches events by ``event_code`` - the
    same correlation ``compare_schedule_versions`` already uses to diff two
    versions of one protocol, which is exactly the question here too: do the
    two pipelines' readings of the SAME protocol agree. A visit the two gave
    different codes to is not a bug in this harness; it is a real,
    reportable disagreement (VISIT_ADDED on one side, VISIT_REMOVED on the
    other) worth a human's attention.
    """
    if legacy is None or native is None:
        return {
            "comparable": False,
            "legacy_issues": legacy_issues,
            "native_issues": native_issues,
        }
    diff = compare_schedule_versions(legacy, native)
    significant = diff.clinically_significant()
    by_type: dict[str, int] = {}
    for item in diff.typed_changes:
        by_type[item.change_type.value] = by_type.get(item.change_type.value, 0) + 1
    return {
        "comparable": True,
        "legacy_event_count": len(legacy.events),
        "native_event_count": len(native.events),
        "legacy_issues": legacy_issues,
        "native_issues": native_issues,
        "added_events": diff.added_events,
        "removed_events": diff.removed_events,
        "total_typed_changes": len(diff.typed_changes),
        "clinically_significant_changes": len(significant),
        "changes_by_type": by_type,
        "significant_change_summaries": [item.summary for item in significant],
        "significant_changes": [
            {
                "change_type": item.change_type.value,
                "entity_code": item.entity_code,
                "summary": item.summary,
                "detail": item.detail,
                "before": item.before,
                "after": item.after,
            }
            for item in significant
        ],
    }


async def diff_one(pdf_path: Path) -> dict:
    pdf_bytes = pdf_path.read_bytes()
    (legacy, legacy_issues), native_pair = await asyncio.gather(
        run_legacy(pdf_bytes), asyncio.to_thread(run_native, pdf_bytes),
    )
    native, native_issues = native_pair
    report = diff_report(legacy, legacy_issues, native, native_issues)
    report["file"] = pdf_path.name
    return report


def _corpus_files(corpus_dir: Path) -> list[Path]:
    numbered = [
        item for item in corpus_dir.glob("*.pdf")
        if _NUM_RE.match(item.name)
    ]
    return sorted(numbered, key=lambda item: int(_NUM_RE.match(item.name).group(1)))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", help="one protocol PDF to diff")
    parser.add_argument("--corpus-dir", help="diff every numbered PDF in this directory")
    parser.add_argument("--json", help="write the full report(s) to this path")
    args = parser.parse_args()

    if not args.pdf and not args.corpus_dir:
        parser.error("give a PDF path or --corpus-dir")

    files = _corpus_files(Path(args.corpus_dir)) if args.corpus_dir else [Path(args.pdf)]
    reports = []
    for pdf_path in files:
        print(f"--- {pdf_path.name} ---")
        report = await diff_one(pdf_path)
        reports.append(report)
        if not report["comparable"]:
            print(f"  NOT COMPARABLE - legacy: {report['legacy_issues']}; "
                  f"native: {report['native_issues']}")
            continue
        print(
            f"  events: legacy={report['legacy_event_count']} "
            f"native={report['native_event_count']}"
        )
        print(
            f"  typed changes: {report['total_typed_changes']} "
            f"({report['clinically_significant_changes']} clinically significant)"
        )
        for summary in report["significant_change_summaries"]:
            print(f"    - {summary}")

    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2, default=str))
        print(f"\nWrote {len(reports)} report(s) to {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
