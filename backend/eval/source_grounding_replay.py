"""Check captured outputs against deterministic source guards; no model calls.

python eval/source_grounding_replay.py protocol.pdf comparison.json --output replay.json
This measures guard behavior, not the accuracy of a fresh model extraction.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.extraction.runner import pages_from_pdf
from app.extraction.source_grounding import collective_count_visit, referenced_pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pages = pages_from_pdf(args.document.read_bytes())
    captured = json.loads(args.comparison.read_text())
    results = captured["runs"]["source_first"]["results"]
    selected = {page for result in results for step in result["extraction_trace"]
        if step.get("node") == "SOURCE_MAP" for page in step["pages"]}
    recovered, missing = referenced_pages(pages, selected)
    non_visits = [{"code": event["code"], "label": event["display_name"]}
        for result in results if result.get("schedule") for event in result["schedule"]["events"]
        if collective_count_visit(event["display_name"]) or collective_count_visit(event["protocol_label"])]
    report = {"document": args.document.name, "model_calls": 0,
        "scope": "Deterministic replay of captured output; not a fresh extraction accuracy score",
        "population_counts_rejected_as_visits": non_visits,
        "referenced_pages_recovered": sorted(recovered - selected), "missing_referenced_sources": missing}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
