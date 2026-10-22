"""P8-01/03/05: identity, evidence and amendment-mapping gold, component coverage, and regression against a baseline."""
import copy
import json
from pathlib import Path

from app.quality.__main__ import main
from app.quality.compare import compare_reports
from app.quality.components import score_evidence, score_identity, score_mappings
from app.quality.models import (
    Corpus, ExpectedEvidence, ExpectedIdentity, ExpectedMapping, Predictions, Thresholds)
from app.quality.runner import evaluate

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "backend/quality_corpus"
CORPUS = Corpus.model_validate_json((FOLDER / "manifest.json").read_text(encoding="utf-8"))
PREDICTIONS = Predictions.model_validate_json((FOLDER / "predictions.json").read_text(encoding="utf-8"))
THRESHOLDS = Thresholds.model_validate_json((FOLDER / "thresholds.json").read_text(encoding="utf-8"))


def run(corpus=CORPUS, predictions=PREDICTIONS):
    return evaluate(corpus, predictions, root=ROOT, mode="engineering", thresholds=THRESHOLDS)


def test_the_engineering_corpus_covers_every_gold_component_and_passes():
    report = run()
    assert report["decision"] == "PASS" and report["missing_components"] == []
    assert set(report["component_coverage"]) == {"fields", "identity", "evidence", "mappings", "scenarios"}
    amendment = report["components"]["synthetic-amendment"]
    assert amendment["mappings"]["tp"] == 5 and amendment["mappings"]["precision"] == 1.0   # the REAL proposer vs hand-written gold


def test_a_document_that_must_be_refused_but_is_accepted_is_a_critical_false_accept():
    wrong = score_identity(ExpectedIdentity(status="MISMATCH"), ExpectedIdentity(status="MATCH"))
    assert wrong["critical_false_accept"] and not wrong["match"]
    assert score_identity(ExpectedIdentity(status="UNCERTAIN"), ExpectedIdentity(status="NEW_VERSION"))["critical_false_accept"]
    assert not score_identity(ExpectedIdentity(status="MATCH"), ExpectedIdentity(status="UNCERTAIN"))["critical_false_accept"]
    assert score_identity(ExpectedIdentity(status="MISMATCH"), None)["missing"]
    broken = copy.deepcopy(PREDICTIONS)
    first = next(iter(broken.identity))
    broken.identity[first] = ExpectedIdentity(status="MISMATCH")
    assert "IDENTITY_DECISION_WRONG" in run(predictions=broken)["reason_codes"]
    refusing = copy.deepcopy(CORPUS)
    refusing.records[0].identity = ExpectedIdentity(status="MISMATCH")
    assert "IDENTITY_FALSE_ACCEPT" in run(refusing)["reason_codes"] and run(refusing)["decision"] == "FAIL"


def test_evidence_is_matched_on_rule_page_and_normalised_quote_with_explicit_denominators():
    gold = [ExpectedEvidence(rule_id="C1D1", page=4, quote="Treatment starts  on Day 1")]
    assert score_evidence(gold, [ExpectedEvidence(rule_id="C1D1", page=4, quote="treatment starts on day 1")])["precision"] == 1.0
    for wrong in (ExpectedEvidence(rule_id="C1D1", page=5, quote="Treatment starts on Day 1"),
                  ExpectedEvidence(rule_id="DOSE", page=4, quote="Treatment starts on Day 1"),
                  ExpectedEvidence(rule_id="C1D1", page=4, quote="Something else entirely")):
        metric = score_evidence(gold, [wrong])
        assert (metric["tp"], metric["fp"], metric["fn"]) == (0, 1, 1)
    assert score_evidence(gold, [])["recall"] == 0.0


def test_a_mapping_that_is_auto_confirmed_when_it_should_need_review_is_wrong():
    gold = [ExpectedMapping(source_codes=["A"], target_codes=["B"], mapping_type="RENAMED", requires_review=True)]
    auto = [ExpectedMapping(source_codes=["A"], target_codes=["B"], mapping_type="RENAMED", requires_review=False)]
    assert score_mappings(gold, gold)["precision"] == 1.0
    assert score_mappings(gold, auto)["tp"] == 0
    broken = copy.deepcopy(PREDICTIONS)
    key = next(iter(broken.mappings))
    broken.mappings[key] = [row.model_copy(update={"requires_review": False}) if row.mapping_type == "RENAMED" else row
                            for row in broken.mappings[key]]
    assert "MAPPING_THRESHOLD_FAILED" in run(predictions=broken)["reason_codes"]


def test_dropping_a_component_from_the_gold_is_reported_not_ignored():
    thin = copy.deepcopy(CORPUS)
    for record in thin.records:
        record.mappings = []
    report = run(thin)
    assert "mappings" in report["missing_components"] and report["decision"] == "PASS"      # engineering: reported only
    clinical = evaluate(thin, PREDICTIONS, root=ROOT, mode="clinical", thresholds=THRESHOLDS)
    assert "COMPONENT_COVERAGE_MISSING" in clinical["reason_codes"] and clinical["decision"] == "FAIL"


def test_comparison_names_every_regression_and_every_improvement():
    base = run()
    assert compare_reports(base, run())["regressed"] is False
    worse = copy.deepcopy(base)
    worse["decision"], worse["reason_codes"] = "FAIL", ["IDENTITY_FALSE_ACCEPT"]
    worse["components"]["synthetic-amendment"]["mappings"]["recall"] = 0.5
    worse["components"]["synthetic-linear"]["identity"]["match"] = False
    worse["unsafe_dates"] = 2
    worse["metrics"]["synthetic-linear"]["critical_false_positives"] = 1
    result = compare_reports(base, worse)
    kinds = {row["kind"] for row in result["regressions"]}
    assert result["regressed"] and kinds >= {"DECISION", "NEW_REASON", "METRIC_DROP", "UNSAFE_DATES", "CRITICAL_FALSE_POSITIVE"}
    assert {"metric": "mappings:synthetic-amendment:recall", "from": 1.0, "to": 0.5, "kind": "METRIC_DROP"} in result["regressions"]
    lost = copy.deepcopy(base)
    lost["components"]["synthetic-amendment"]["mappings"]["precision"] = None
    assert any(row["kind"] == "METRIC_LOST" for row in compare_reports(base, lost)["regressions"])
    recovered = compare_reports(worse, base)
    assert not recovered["regressed"] and recovered["improvements"]
    assert result["dataset_changed"] is False and result["baseline"]["dataset_hash"] == base["dataset_hash"]


def test_the_cli_fails_a_run_that_regresses_against_its_baseline(tmp_path):
    args = ["--corpus", str(FOLDER / "manifest.json"), "--predictions", str(FOLDER / "predictions.json"),
            "--thresholds", str(FOLDER / "thresholds.json"), "--source-root", str(ROOT), "--mode", "engineering"]
    first = tmp_path / "first.json"
    assert main([*args, "--output", str(first)]) == 0
    assert main([*args, "--output", str(tmp_path / "second.json"), "--baseline", str(first)]) == 0
    assert json.loads((tmp_path / "second.json").read_text())["comparison"]["regressed"] is False
    better = json.loads(first.read_text())
    better["metrics"]["synthetic-linear"]["precision"] = 1.0
    better["components"]["synthetic-amendment"]["mappings"]["recall"] = 1.0
    impossible = copy.deepcopy(better)
    impossible["unsafe_dates"] = -1                                    # a baseline stricter than any real run
    (tmp_path / "strict.json").write_text(json.dumps(impossible))
    assert main([*args, "--output", str(tmp_path / "third.json"), "--baseline", str(tmp_path / "strict.json")]) == 1
    result = json.loads((tmp_path / "third.json").read_text())
    assert result["decision"] == "FAIL" and "REGRESSION_AGAINST_BASELINE" in result["reason_codes"]
    assert main([*args, "--output", str(tmp_path / "fourth.json"), "--baseline", str(tmp_path / "missing.json")]) == 1


def test_the_checked_in_baseline_is_what_ci_compares_against_and_the_current_corpus_does_not_regress():
    baseline = json.loads((FOLDER / "baseline_report.json").read_text(encoding="utf-8"))
    result = compare_reports(baseline, run())
    assert result["regressed"] is False and result["dataset_changed"] is False, result["regressions"]
