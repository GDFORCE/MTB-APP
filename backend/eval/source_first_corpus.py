"""Run the revised extractor across selected real corpus documents, sequentially.

From backend: python eval/source_first_corpus.py 2 29 30 --output-dir ../docs/audits/corpus-2026-09-13
Makes paid model calls. Each document and model response is saved separately.
Counts and blocking findings are diagnostics, NOT a clinical accuracy score.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numbers", type=int, nargs="+")
    parser.add_argument("--corpus", type=Path, default=Path(os.getenv("PROTOCOL_CORPUS_DIR",
        r"D:\Patient Visit Schedules-20260429T140317Z-3-001\Patient Visit Schedules")))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"accuracy_score": None, "note": "Structural diagnostics; source review remains required.", "documents": []}
    # Validate the selection before making any model calls.
    files = []
    for number in args.numbers:
        matches = sorted(path for path in args.corpus.glob(f"{number}.*") if path.suffix.lower() == ".pdf")
        if len(matches) != 1:
            parser.error(f"Expected one PDF for corpus number {number}, found {len(matches)}")
        files.append((number, matches[0]))
    for number, path in files:
        output = args.output_dir / f"{number}.json"
        with (args.output_dir / f"{number}.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name("source_first_compare.py")),
                str(path), "--output", str(output), *( ["--resume"] if args.resume else [])],
                stdout=log, stderr=subprocess.STDOUT, check=False)
        item = {"number": number, "document": path.name, "exit_code": completed.returncode}
        if completed.returncode == 0 and output.exists():
            run = json.loads(output.read_text(encoding="utf-8"))["runs"]["source_first"]
            results = run.get("results", [])
            item.update(calls=run["calls"], seconds=run.get("seconds"),
                reused_responses=run.get("reused_responses", 0), measurement=run.get("measurement", "fresh"),
                schedules=sum(result["schedule"] is not None for result in results),
                events=[len(result["schedule"]["events"]) if result["schedule"] else 0 for result in results],
                blocking_findings=dict(Counter(issue["issue_code"] for result in results for issue in result["issues"]
                    if issue.get("blocking"))), source_review="PENDING")
        summary["documents"].append(item)
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(item), flush=True)


if __name__ == "__main__":
    main()
