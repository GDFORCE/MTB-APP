"""P8-04: transactional command/history scenarios execute through the real services against hand-written oracles."""
import hashlib
import json
from datetime import date
from pathlib import Path

from app.quality.models import Corpus, ExpectedCalendar, Predictions, Thresholds
from app.quality.runner import evaluate
from app.quality.sequence import run_sequence

ROOT = Path(__file__).resolve().parents[2]
CORPUS = Corpus.model_validate_json((ROOT / "backend/quality_corpus/manifest.json").read_text(encoding="utf-8"))
SEQUENCE = next(row.sequences[0] for row in CORPUS.records if row.sequences)


def changed(**step_changes):
    sequence = SEQUENCE.model_copy(deep=True)
    for index, update in step_changes.items():
        sequence.steps[int(index)] = sequence.steps[int(index)].model_copy(update=update)
    return sequence


def test_the_checked_in_sequence_passes_through_the_real_command_path():
    result = run_sequence(SEQUENCE)
    assert result["passed"] and result["failures"] == [] and result["unsafe_dates"] == 0 and result["steps"] == 2


def test_a_wrong_date_a_wrong_moved_set_and_a_wrong_reevaluation_flag_are_each_reported_exactly():
    wrong_date = changed(**{"0": {"expected": [ExpectedCalendar(logical_key="LAB_AFTER_TX:0", planned=date(2026, 1, 23))]}})
    result = run_sequence(wrong_date)
    assert not result["passed"] and result["unsafe_dates"] == 1
    assert result["failures"][0]["field"] == "LAB_AFTER_TX:0" and result["failures"][0]["actual"]["planned"] == "2026-01-22"
    moved = run_sequence(changed(**{"0": {"expect_moved": ["LAB_AFTER_TX:0", "IMAGING_BASELINE:0"]}}))
    assert any(row["field"] == "moved" and row["actual"] == ["LAB_AFTER_TX:0"] for row in moved["failures"])
    flag = run_sequence(changed(**{"1": {"expect_reevaluated": True}}))
    assert {row["field"] for row in flag["failures"]} >= {"reevaluated", "evaluation_count"}


def test_an_unknown_visit_or_a_command_the_schedule_cannot_take_is_a_failure_not_a_pass():
    missing = run_sequence(changed(**{"0": {"expected": [ExpectedCalendar(logical_key="NOPE:0", planned=date(2026, 1, 1))]}}))
    assert not missing["passed"] and missing["failures"][0]["actual"] is None
    refused = run_sequence(changed(**{"0": {"logical_key": "NOT_A_VISIT:0"}}))
    assert not refused["passed"] and "error" in refused and refused["error"] != ""


def test_the_gate_runs_sequences_and_counts_a_failed_one_as_unsafe_and_blocking(tmp_path):
    predictions = Predictions.model_validate_json((ROOT / "backend/quality_corpus/predictions.json").read_text(encoding="utf-8"))
    thresholds = Thresholds.model_validate_json((ROOT / "backend/quality_corpus/thresholds.json").read_text(encoding="utf-8"))
    report = evaluate(CORPUS, predictions, root=ROOT, mode="engineering", thresholds=thresholds)
    sequences = [row for row in report["scenarios"] if row.get("kind") == "SEQUENCE"]
    assert len(sequences) == 1 and sequences[0]["passed"] and report["decision"] == "PASS"
    broken = CORPUS.model_copy(deep=True)
    record = next(row for row in broken.records if row.sequences)
    record.sequences[0].steps[0] = record.sequences[0].steps[0].model_copy(update={
        "expected": [ExpectedCalendar(logical_key="LAB_AFTER_TX:0", planned=date(2026, 1, 30))]})
    failed = evaluate(broken, predictions, root=ROOT, mode="engineering", thresholds=thresholds)
    assert failed["decision"] == "FAIL" and "EXECUTION_SCENARIO_FAILED" in failed["reason_codes"]
    assert "UNSAFE_INVENTED_DATE" in failed["reason_codes"]


def test_regeneration_is_deterministic():
    from app.quality.engineering_corpus import build
    first, _, _ = build(ROOT)
    second, _, _ = build(ROOT)
    assert hashlib.sha256(first.model_dump_json().encode()).hexdigest() == hashlib.sha256(second.model_dump_json().encode()).hexdigest()
    on_disk = json.loads((ROOT / "backend/quality_corpus/manifest.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(first.model_dump_json())


def test_manifest_hashes_do_not_depend_on_set_iteration_order():
    """Regression: unordered sets made a gold record's source hash differ after a reload (about 1 run in 4),
    which would have randomly rejected any clinical source tagged with several constructs."""
    from app.quality.models import Source, Thresholds, digest
    base = dict(id="s", version="1", sha256="a" * 64, path="p", provenance="x", license="y",
                deidentified=True, synthetic=True)
    names = ["linear", "cycle", "branch", "device", "amendment", "conflict", "recurrence"]
    forward = Source(constructs=set(names), **base)
    backward = Source(constructs=set(reversed(names)), **base)
    assert digest(forward.model_dump(mode="json")) == digest(backward.model_dump(mode="json"))
    assert forward.model_dump(mode="json")["constructs"] == sorted(names)
    first, second = Thresholds(required_constructs=set(names)), Thresholds(required_constructs=set(reversed(names)))
    assert first.content_hash() == second.content_hash()
