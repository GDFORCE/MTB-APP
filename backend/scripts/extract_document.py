"""Run the real extraction engine on one PDF, resumably, and save the full result for analysis.

    python backend/scripts/extract_document.py <pdf> <work-dir>

Every model answer is kept in <work-dir>/artifacts as it arrives, so a quota failure or a crash
loses nothing and a rerun (or an offline analysis) reuses them instead of paying again. The
provider and key come from backend/.env (never printed). A run that was interrupted says so and
lists the unread pages; it is not a complete extraction.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from app.extraction import runner
from app.extraction.artifacts import DiskArtifactStore
from app.extraction.documents import pages_from_pdf


def main() -> int:
    pdf_path, work = Path(sys.argv[1]), Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    pdf = pdf_path.read_bytes()
    digest = hashlib.sha256(pdf).hexdigest()
    pages = pages_from_pdf(pdf)
    print(f"{pdf_path.name}: {len(pages)} pages, provider={runner.configured_provider()}")
    results = runner.extract_schedules(
        pages, pdf_bytes=pdf, document_hash=digest, schedule_name=pdf_path.stem,
        artifacts=DiskArtifactStore(work / "artifacts"))
    dump = []
    for option, result in results:
        blocking = [issue for issue in result.issues if issue.blocking]
        interrupted = [issue for issue in result.issues if issue.issue_code == "EXTRACTION_INTERRUPTED"]
        print(f"option={option and option.get('label')}: {len(result.schedule.events)} events, "
              f"{len(result.issues)} issues, {len(blocking)} blocking"
              + (f", INTERRUPTED (unread pages: {interrupted[0].details.get('pages')})" if interrupted else ""))
        for code, count in Counter(issue.issue_code for issue in blocking).most_common():
            print(f"   {count:3d}  {code}")
        dump.append({"issues": [json.loads(i.model_dump_json()) for i in result.issues],
                     "events": [json.loads(e.model_dump_json()) for e in result.schedule.events],
                     "accountability": result.accountability})
    (work / "result.json").write_text(json.dumps({"document_hash": digest, "results": dump},
                                                 default=str, indent=1), encoding="utf-8")
    print(f"saved {work / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
