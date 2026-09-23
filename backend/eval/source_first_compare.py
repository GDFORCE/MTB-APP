"""Explicit paid comparison; writes both outputs and measured calls/latency.

python eval/source_first_compare.py protocol.pdf --output report.json --compare
Without --compare only the source-first candidate runs. No app flags are changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse completed identical requests from this output's checkpoint")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from app.extraction.runner import build_client, document_text, pages_from_pdf, pages_from_text, run_extraction_for_all_options
    from app.extraction.source_first import run_source_first
    from eval.extraction_checkpoint import CheckpointClient
    data = args.document.read_bytes()
    is_pdf = args.document.suffix.lower() == ".pdf"
    pages = pages_from_pdf(data) if is_pdf else pages_from_text(data.decode("utf-8-sig"))
    report = {"document": args.document.name, "pages": len(pages), "runs": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = args.output.with_suffix(".calls.jsonl")
    for pipeline in (["category", "source_first"] if args.compare else ["source_first"]):
        client = build_client(document_text=document_text(pages), pdf_bytes=data if is_pdf and (
            pipeline == "source_first" or any(page.text_status != "text" for page in pages)) else None)
        counted = CheckpointClient(client, journal, document_hash=hashlib.sha256(data).hexdigest(), resume=args.resume)
        started = time.monotonic()
        os.environ["UCTSM_EXTRACTION_PIPELINE"] = "category"
        function = run_source_first if pipeline == "source_first" else run_extraction_for_all_options
        try:
            results = function(counted, pages, document_hash=hashlib.sha256(data).hexdigest(), schedule_name=args.document.stem)
        except Exception as error:
            report["runs"][pipeline] = {"calls": counted.calls, "error": str(error)}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise
        report["runs"][pipeline] = {"calls": counted.calls, "reused_responses": counted.reused,
            "measurement": "resumed" if counted.reused else "fresh", "seconds": round(time.monotonic() - started, 2),
            "results": [{"option": option, **result.model_dump(mode="json")} for option, result in results]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"pipeline": pipeline, "calls": counted.calls,
            "events": [len(result.schedule.events) if result.schedule else 0 for _, result in results],
            "issues": [[issue.issue_code for issue in result.issues] for _, result in results]}), flush=True)


if __name__ == "__main__":
    main()
