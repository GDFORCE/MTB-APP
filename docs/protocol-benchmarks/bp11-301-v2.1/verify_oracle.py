"""Check this independent draft against the local source, not against the app.

Usage: python verify_oracle.py --pdf "D:/Copy of S-103 Syneos Health.pdf"
       --output <report.json>
This checks provenance, textual citations, table cells and calendar arithmetic.
It never imports the app, executes clinical decisions or grants clinical sign-off.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import fitz

HERE = Path(__file__).resolve().parent


def normalized(value):
    return re.sub(r"\s+", " ", value or "").strip()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def check(pdf_path, manifest, oracle):
    contents = pdf_path.read_bytes()
    require(hashlib.sha256(contents).hexdigest() == manifest["sha256"], "SOURCE_HASH_MISMATCH")
    require(len(contents) == manifest["size_bytes"], "SOURCE_SIZE_MISMATCH")
    require(oracle["source_id"] == manifest["source_id"], "SOURCE_ID_MISMATCH")
    require(not oracle["clinical_release_qualified"] and not manifest["review"]["clinical_release_qualified"],
            "DRAFT_MUST_NOT_CLAIM_CLINICAL_QUALIFICATION")
    require(manifest["review"]["clinical_review_signature"] is None, "NO_REVIEW_SIGNATURE_WAS_SUPPLIED")
    with fitz.open(stream=contents, filetype="pdf") as document:
        require(len(document) == manifest["page_count"], "SOURCE_PAGE_COUNT_MISMATCH")
        evidence_ids = set(); text_count = 0; visual = []
        for item in oracle["evidence"]:
            require(item["id"] not in evidence_ids, "DUPLICATE_EVIDENCE_ID")
            evidence_ids.add(item["id"])
            require(1 <= item["page"] <= len(document), "INVALID_EVIDENCE_PAGE")
            if item["kind"] == "TEXT":
                require(normalized(item["quote"]) in normalized(document[item["page"] - 1].get_text()),
                        "QUOTE_NOT_FOUND:" + item["id"])
                text_count += 1
            elif item["kind"] == "VISUAL":
                require(bool(item.get("description")), "VISUAL_REFERENCE_NEEDS_DESCRIPTION")
                visual.append({"id": item["id"], "page": item["page"], "status": "HUMAN_REVIEW_PENDING"})
            else:
                raise ValueError("UNKNOWN_EVIDENCE_KIND")
        tables = {}
        for page in (49, 50):
            found = document[page - 1].find_tables().tables
            require(len(found) == 1, "TABLE_LAYOUT_CHANGED")
            tables[page] = found[0].extract()
        matrix_keys = set()
        for row in oracle["assessment_matrix"]:
            key = (row["page"], row["row_label"])
            require(key not in matrix_keys, "DUPLICATE_ASSESSMENT_ROW")
            matrix_keys.add(key)
            found = [item for item in tables[row["page"]][5:]
                     if normalized(item[0]) == row["row_label"]]
            require(len(found) == 1, "ASSESSMENT_ROW_NOT_FOUND:" + row["row_label"])
            marked = [index for index, value in enumerate(found[0][1:], 1)
                      if normalized(value).lower() == "x"]
            require(marked == row["marked_visits"], "ASSESSMENT_CELLS_DIFFER:" + row["row_label"])
        require(len(matrix_keys) == sum(len(table) - 5 for table in tables.values()), "ASSESSMENT_ROW_OMITTED")
        baseline = date.fromisoformat(oracle["baseline_example"])
        require([item["visit_number"] for item in oracle["visits"]] == list(range(1, 13)), "VISIT_SET_CHANGED")
        date_count = 0
        for visit in oracle["visits"]:
            number = visit["visit_number"]
            for table in tables.values():
                require(normalized(table[2][number]) == visit["week_label"], "STUDY_WEEK_LABEL_DIFFERS")
                require(normalized(table[3][number]) == visit["study_day_label"], "STUDY_DAY_LABEL_DIFFERS")
                window = "" if visit["window_days"] is None else "\u00b1" + str(visit["window_days"])
                require(normalized(table[4][number]) == window, "VISIT_WINDOW_DIFFERS")
            if visit["study_day"] is None:
                require(visit["target_date"] is None, "AMBIGUOUS_PERIOD_MUST_NOT_GAIN_A_TARGET")
                continue
            target = baseline + timedelta(days=visit["study_day"] - 1)
            require(target.isoformat() == visit["target_date"], "STUDY_DAY_ARITHMETIC_DIFFERS")
            require(target == baseline + timedelta(weeks=visit["week"]), "WEEK_DAY_ARITHMETIC_DISAGREES")
            if visit["window_days"] is None:
                require(visit["earliest"] is None and visit["latest"] is None, "BLANK_WINDOW_MUST_STAY_UNSTATED")
            else:
                require((target - timedelta(days=visit["window_days"])).isoformat() == visit["earliest"],
                        "EARLIEST_ARITHMETIC_DIFFERS")
                require((target + timedelta(days=visit["window_days"])).isoformat() == visit["latest"],
                        "LATEST_ARITHMETIC_DIFFERS")
            date_count += 1
        scenario_ids = set()
        for scenario in oracle["scenarios"]:
            require(scenario["id"] not in scenario_ids, "DUPLICATE_SCENARIO_ID")
            scenario_ids.add(scenario["id"])
            require(bool(scenario.get("evidence") or scenario.get("evidence_pages")), "SCENARIO_LACKS_SOURCE")
            require(set(scenario.get("evidence", [])) <= evidence_ids, "SCENARIO_EVIDENCE_NOT_FOUND")
            require(bool(scenario.get("expect")), "SCENARIO_LACKS_EXPECTATION")
        question_ids = {item["id"] for item in oracle["open_questions"]}
        require(len(question_ids) == len(oracle["open_questions"]), "DUPLICATE_QUESTION_ID")
        for item in oracle["assessment_matrix"]:
            if item.get("review_issue"):
                require(item["review_issue"] in question_ids, "ASSESSMENT_REVIEW_ISSUE_MISSING")
    cases = {item["id"]: item for item in oracle["scenarios"]}
    for case_id in ("TIME01", "TIME02", "TIME03"):
        case = cases[case_id]
        elapsed = datetime.fromisoformat(case["given"]["planned_dose"]) - datetime.fromisoformat(case["given"]["pk_sample"])
        require((timedelta(0) < elapsed <= timedelta(hours=2)) == case["expect"]["within_predose_two_hours"],
                "PREDOSE_EXAMPLE_ARITHMETIC_DIFFERS")
    case = cases["TIME04"]
    require(datetime.fromisoformat(case["given"]["actual_administration"]) + timedelta(minutes=60)
            == datetime.fromisoformat(case["expect"]["earliest_observation_end"]), "OBSERVATION_ARITHMETIC_DIFFERS")
    case = cases["ET01"]
    target = date.fromisoformat(case["given"]["last_actual_administration"]) + timedelta(weeks=4)
    require(target.isoformat() == case["expect"]["target_date"], "EARLY_TERMINATION_ARITHMETIC_DIFFERS")
    require((target - timedelta(days=7)).isoformat() == case["expect"]["earliest"], "EARLY_TERMINATION_WINDOW_DIFFERS")
    require((target + timedelta(days=7)).isoformat() == case["expect"]["latest"], "EARLY_TERMINATION_WINDOW_DIFFERS")
    case = cases["SAFETY01"]
    require(datetime.fromisoformat(case["given"]["investigator_awareness"]) + timedelta(hours=24)
            == datetime.fromisoformat(case["expect"]["latest_reporting_time_from_awareness"]), "DEADLINE_ARITHMETIC_DIFFERS")
    for case_id in ("DIARY02", "DIARY03"):
        case = cases[case_id]; scores = case["given"]["available_daily_scores"]
        score = sum(scores) / len(scores) * 7 if 4 <= len(scores) <= 7 else None
        require(score == case["expect"]["weekly_score"], "WEEKLY_SCORE_EXAMPLE_ARITHMETIC_DIFFERS")
    return {"status": "SOURCE_AND_ARITHMETIC_CHECKS_PASSED", "source_sha256": manifest["sha256"],
            "source_pages": manifest["page_count"], "matched_text_citations": text_count,
            "visual_references_pending_independent_review": visual,
            "assessment_rows_verified_against_pdf_cells": len(matrix_keys),
            "assessment_visit_cells_checked": len(matrix_keys) * 12,
            "calendar_targets_independently_checked": date_count, "scenario_count": len(scenario_ids),
            "open_question_count": len(question_ids), "app_tests_executed": 0,
            "app_comparison_status": "NOT_RUN", "clinical_release_qualified": False,
            "qualification": "Source fidelity and arithmetic only; not clinical approval or application acceptance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest_bytes = (HERE / "source-manifest.json").read_bytes()
    oracle_bytes = (HERE / "oracle-draft.json").read_bytes()
    manifest, oracle = json.loads(manifest_bytes), json.loads(oracle_bytes)
    try:
        report = check(args.pdf or Path(manifest["local_source_path"]), manifest, oracle)
        code = 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {"status": "FAILED", "error_type": type(error).__name__,
                  "reason": str(error) if isinstance(error, ValueError) else "INPUT_NOT_READABLE_OR_INVALID",
                  "clinical_release_qualified": False}
        code = 1
    report.update(checked_at=datetime.now(timezone.utc).isoformat(),
                  source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                  oracle_sha256=hashlib.sha256(oracle_bytes).hexdigest())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
