"""Explicit live smoke check on synthetic source text; never runs in unit tests.

Run from backend: python eval/workflow_smoke.py --live
Uses the configured provider and writes an inspectable report, without patient data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from app.extraction.runner import build_client, document_text, pages_from_text, run_extraction_for_all_options
    source = (ROOT / "eval/workflow-smoke-protocol.txt").read_text(encoding="utf-8")
    pages = pages_from_text(source)
    client = build_client(document_text=document_text(pages))
    class ProgressClient:
        calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            print(f"Model stage {self.calls}: {kwargs['prompt'].splitlines()[0]}", flush=True)
            return client.complete(**kwargs)
    results = run_extraction_for_all_options(ProgressClient(), pages,
        document_hash=hashlib.sha256(source.encode()).hexdigest(), schedule_name="Workflow smoke")
    report = [{"option": option, **result.model_dump(mode="json")} for option, result in results]
    output = ROOT.parent / "docs/audits/uctsm-live-smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"schedules": len(results), "events": [len(result.schedule.events) if result.schedule else 0
        for _, result in results], "blocking_findings": [sum(issue.blocking for issue in result.issues)
        for _, result in results], "report": str(output)}), flush=True)


if __name__ == "__main__":
    main()
