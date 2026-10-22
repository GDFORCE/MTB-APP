"""P8 metrics and release evidence must fail safely, including empty/synthetic gold."""
import base64
from datetime import date, datetime, timezone
import hashlib
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.application.pilot import build_pilot_schedule
from app.domain.schedule.models import PatientContext, ScheduleStatus
from app.quality.__main__ import main
from app.quality.models import (
    Corpus, ExpectedField, ExpectedVisit, GoldRecord, Predictions, Scenario,
    SignOff, Source, Thresholds, TrustedReviewer, digest,
)
from app.quality.runner import approval_message, evaluate, run_scenario, verified
from app.quality.scoring import score_fields

AS_OF = date(2026, 1, 1)


def field(value=14, **kwargs):
    return ExpectedField(rule_id="DOSE", path="timing/offset", value=value, **kwargs)


@pytest.fixture()
def sample(tmp_path):
    text = b"Synthetic engineering source: treatment day 7, weekly doses, baseline imaging day 30."
    (tmp_path / "source.txt").write_bytes(text)
    source = Source(id="pilot", version="1", sha256=hashlib.sha256(text).hexdigest(),
                    path="source.txt", provenance="Constructed engineering fixture", license="Synthetic test data",
                    deidentified=True, synthetic=True, constructs={"linear", "recurrence", "multi_track", "actual_relative"})
    schedule = build_pilot_schedule(uuid4(), "Synthetic quality fixture")
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
                             anchors={"BASELINE": AS_OF})
    expected = [ExpectedVisit(event_code=code, occurrence_index=index, status="RESOLVED", target=on)
                for code, index, on in [
                    ("C1D1", 0, date(2026, 1, 8)), ("DOSE", 0, date(2026, 1, 15)),
                    ("DOSE", 1, date(2026, 1, 22)), ("DOSE", 2, date(2026, 1, 29)),
                    ("DOSE", 3, date(2026, 2, 5))]]
    expected += [ExpectedVisit(event_code="IMAGING_BASELINE", status="RESOLVED", target=date(2026, 1, 31),
                               earliest=date(2026, 1, 28), latest=date(2026, 2, 3)),
                 ExpectedVisit(event_code="LAB_AFTER_TX", status="BLOCKED", target=None)]
    scenario = Scenario(id="baseline-and-no-actual", as_of=AS_OF, horizon=date(2026, 2, 28),
                        schedule=schedule, context=context, expected=expected)
    record = GoldRecord(id="pilot-1", source_id=source.id,
                        source_manifest_hash=digest(source.model_dump(mode="json")), category="ENGINEERING",
                        fields=[field()], scenarios=[scenario])
    corpus = Corpus(dataset_version="engineering-1", sources=[source], records=[record])
    predictions = Predictions(versions={"compiler": "constructed.v1"}, fields={record.id: [field()]})
    thresholds = Thresholds(required_constructs=source.constructs)
    return corpus, predictions, thresholds, tmp_path


def test_metrics_have_hand_calculated_denominators_and_critical_mismatches():
    expected = [field(), ExpectedField(rule_id="LAB", path="basis", value="ACTUAL_TREATMENT")]
    predicted = [field(), ExpectedField(rule_id="LAB", path="basis", value="CURRENT_PLANNED", critical=False)]
    result = score_fields(expected, predicted)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["precision"] == result["recall"] == result["f1"] == 0.5
    assert result["critical_false_positives"] == 1


def test_duplicates_are_not_extra_correct_predictions():
    result = score_fields([field()], [field(), field()])
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 0)
    assert result["precision"] == 0.5


def test_missing_predictions_and_empty_denominators_are_not_perfect():
    assert score_fields([field()], [])["recall"] == 0
    assert score_fields([], [])["precision"] is None


@pytest.mark.parametrize("expected,actual", [(None, 0), (True, 1), ({"x": True}, {"x": 1})])
def test_typed_material_values_are_not_normalized_away(expected, actual):
    assert score_fields([field(expected)], [field(actual)])["tp"] == 0


def test_equivalence_is_limited_to_explicit_unordered_strings():
    assert score_fields([field(["A", "B"], equivalence="UNORDERED_STRINGS")],
                        [field(["B", "A"])])["tp"] == 1
    assert score_fields([field(["A", "A"], equivalence="UNORDERED_STRINGS")],
                        [field(["A"])])["tp"] == 0


def test_engineering_pass_does_not_qualify_a_clinical_release(sample):
    corpus, predictions, thresholds, root = sample
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert result["decision"] == "PASS", result
    assert not result["clinical_release_qualified"]
    clinical = evaluate(corpus, predictions, root=root, mode="clinical", thresholds=thresholds)
    assert clinical["decision"] == "FAIL"
    assert {"NO_ELIGIBLE_GOLD", "UNAPPROVED_THRESHOLDS"} <= set(clinical["reason_codes"])


def test_wrong_generated_date_is_an_unconditional_gate_failure(sample):
    corpus, predictions, thresholds, root = sample
    corpus.records[0].scenarios[0].expected[0].target = date(2026, 1, 9)
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert result["unsafe_dates"] == 1
    assert "UNSAFE_INVENTED_DATE" in result["reason_codes"]
    assert result["decision"] == "FAIL"


def test_zero_eligible_or_missing_source_cannot_pass(sample):
    corpus, predictions, thresholds, root = sample
    (root / "source.txt").unlink()
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert result["decision"] == "FAIL"
    assert result["excluded"][0]["reason"] == "SOURCE_MISSING"


def test_source_escape_and_tamper_are_refused(sample):
    corpus, predictions, thresholds, root = sample
    corpus.sources[0].path = "../forbidden.txt"
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert result["excluded"][0]["reason"] == "SOURCE_OUTSIDE_ROOT"
    corpus.sources[0].path = "source.txt"; (root / "source.txt").write_text("edited")
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert result["excluded"][0]["reason"] == "SOURCE_HASH_MISMATCH"


def test_provider_errors_and_uncovered_constructs_remain_visible(sample):
    corpus, predictions, thresholds, root = sample
    predictions.errors["pilot-1"] = "PROVIDER_UNAVAILABLE"
    thresholds.required_constructs.add("cycle")
    result = evaluate(corpus, predictions, root=root, mode="engineering", thresholds=thresholds)
    assert {"PREDICTION_MISSING_OR_FAILED", "CONSTRUCT_COVERAGE_MISSING"} <= set(result["reason_codes"])
    assert result["missing_constructs"] == ["cycle"]


def test_approval_binds_content_reviewer_and_date_to_a_trusted_key():
    # Ephemeral keys prove the mechanism; they are never production clinical sign-off.
    secret = Ed25519PrivateKey.generate(); actor = uuid4()
    review = SignOff(reviewer_id=actor, key_id="ephemeral-test", reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     content_hash="a" * 64, signature="pending")
    review.signature = base64.b64encode(secret.sign(approval_message(review))).decode()
    public = base64.b64encode(secret.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    keys = {review.key_id: TrustedReviewer(reviewer_id=actor, public_key=public)}
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert verified(review, "a" * 64, keys, now)
    assert not verified(review, "b" * 64, keys, now)
    review.reviewed_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert not verified(review, "a" * 64, keys, now)


def test_cli_fails_closed_and_writes_inspectable_artifact(sample):
    corpus, predictions, thresholds, root = sample
    for name, value in [("corpus", corpus), ("predictions", predictions), ("thresholds", thresholds)]:
        (root / f"{name}.json").write_text(value.model_dump_json(), encoding="utf-8")
    args = ["--corpus", str(root / "corpus.json"), "--predictions", str(root / "predictions.json"),
            "--thresholds", str(root / "thresholds.json"), "--source-root", str(root),
            "--output", str(root / "report.json")]
    assert main(args) == 1                         # clinical is the safe default
    assert not json.loads((root / "report.json").read_text())["clinical_release_qualified"]
    assert main([*args, "--mode", "engineering"]) == 0
    (root / "corpus.json").write_text("invalid-json")
    assert main(args) == 2
