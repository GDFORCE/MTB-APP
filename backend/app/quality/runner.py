"""Corpus integrity, exact execution and fail-closed clinical release decisions."""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.validator import ScheduleValidator

from .models import Corpus, Predictions, Scenario, SignOff, Thresholds, TrustedReviewer, digest
from .components import COMPONENTS, score_evidence, score_identity, score_mappings
from .scoring import score_fields
from .sequence import run_sequence


def approval_message(signoff: SignOff) -> bytes:
    return json.dumps(signoff.model_dump(mode="json", exclude={"signature"}),
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def verified(signoff: SignOff | None, content_hash: str,
             trusted: dict[str, TrustedReviewer], now: datetime) -> bool:
    if signoff is None or signoff.content_hash != content_hash or signoff.key_id not in trusted:
        return False
    reviewer = trusted[signoff.key_id]
    if (reviewer.reviewer_id != signoff.reviewer_id or signoff.reviewed_at.tzinfo is None
            or signoff.reviewed_at > now):
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(reviewer.public_key, validate=True))
        key.verify(base64.b64decode(signoff.signature, validate=True), approval_message(signoff))
    except (ValueError, InvalidSignature, TypeError):
        return False
    return True


def source_integrity(corpus: Corpus, root: Path) -> dict[str, str | None]:
    root = root.resolve(); results = {}
    for source in corpus.sources:
        path = (root / source.path).resolve()
        if not path.is_relative_to(root):
            results[source.id] = "SOURCE_OUTSIDE_ROOT"
        elif not path.is_file():
            results[source.id] = "SOURCE_MISSING"
        else:
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
            results[source.id] = None if checksum.hexdigest() == source.sha256 else "SOURCE_HASH_MISMATCH"
    return results


def run_scenario(scenario: Scenario) -> dict:
    issues = ScheduleValidator().validate(scenario.schedule, evaluating_approved=True)
    blocking = {row.issue_code for row in ScheduleValidator.blocking(issues)}
    if scenario.expected_validation_codes:
        return {"id": scenario.id, "passed": blocking == scenario.expected_validation_codes,
                "unsafe_dates": 0, "expected_blockers": sorted(scenario.expected_validation_codes),
                "actual_blockers": sorted(blocking), "safety_critical": scenario.safety_critical}
    if blocking:
        return {"id": scenario.id, "passed": False, "unsafe_dates": 0,
                "actual_blockers": sorted(blocking), "safety_critical": scenario.safety_critical}
    try:
        result = ScheduleEvaluator().evaluate(scenario.schedule, scenario.context,
                                             horizon=scenario.horizon, today=scenario.as_of)
    except (ValueError, RuntimeError) as error:
        # Never return raw patient/protocol data contained in exception text.
        return {"id": scenario.id, "passed": False, "unsafe_dates": 0,
                "error": type(error).__name__, "safety_critical": scenario.safety_critical}
    actual = {}
    for row in result.events:
        timing = row.timing

        actual[(row.event_code, row.occurrence_index)] = {
            "status": row.status.value,
            "target": timing.nominal_start if timing else None,
            "earliest": timing.earliest if timing else None,
            "latest": timing.latest if timing else None}
    expected = {(row.event_code, row.occurrence_index): row.model_dump(
        exclude={"event_code", "occurrence_index"}) for row in scenario.expected}
    failures = []; unsafe = 0
    for key in sorted(expected.keys() | actual.keys()):
        before, after = expected.get(key), actual.get(key)
        if before != after:
            failures.append({"event_code": key[0], "occurrence_index": key[1],
                             "different_fields": sorted(name for name in {*(before or {}), *(after or {})}
                                                        if (before or {}).get(name) != (after or {}).get(name))})
        # Any generated target absent from, or different from, the exact oracle is unsafe.
        if after and after["target"] is not None and (
                before is None or before["target"] != after["target"]):
            unsafe += 1
    return {"id": scenario.id, "passed": not failures, "unsafe_dates": unsafe,
            "failures": failures, "safety_critical": scenario.safety_critical,
            "evaluator_version": result.evaluator_version,
            "input_hash": digest(scenario.context.model_dump(mode="json")),
            "output_hash": digest({f"{code}:{index}": row for (code, index), row in actual.items()})}


def evaluate(corpus: Corpus, predictions: Predictions, *, root: Path, mode: str,
             thresholds: Thresholds, trusted: dict[str, TrustedReviewer] | None = None,
             now: datetime | None = None) -> dict:
    if mode not in {"engineering", "clinical"}:
        raise ValueError("unknown quality gate mode")
    trusted = trusted or {}; now = now or datetime.now(timezone.utc)
    integrity = source_integrity(corpus, root)
    sources = {row.id: row for row in corpus.sources}
    eligible = []; excluded = []; metrics = {}; scenarios = []; reasons = []; component_metrics = {}
    for record in corpus.records:
        source = sources[record.source_id]; reason = integrity[source.id]
        if reason is None and record.source_manifest_hash != digest(source.model_dump(mode="json")):
            reason = "GOLD_SOURCE_MANIFEST_STALE"
        if mode == "clinical" and reason is None:
            if record.category != "CLINICAL" or source.synthetic or not source.deidentified:
                reason = "NOT_CLINICAL_GOLD"
            elif source.license_status != "VERIFIED":
                reason = "UNVERIFIED_SOURCE_CONTROLS"
            elif not verified(record.signoff, record.content_hash(), trusted, now):
                reason = "UNVERIFIED_GOLD_SIGNOFF"
        if reason:
            excluded.append({"record_id": record.id, "reason": reason}); continue
        eligible.append(record)
        if record.id not in predictions.fields or record.id in predictions.errors:
            reasons.append("PREDICTION_MISSING_OR_FAILED")
        metrics[record.id] = score_fields(record.fields, predictions.fields.get(record.id, []))
        component_metrics[record.id] = {}
        if record.identity is not None:
            component_metrics[record.id]["identity"] = score_identity(record.identity, predictions.identity.get(record.id))
        if record.evidence:
            component_metrics[record.id]["evidence"] = score_evidence(record.evidence, predictions.evidence.get(record.id, []))
        if record.mappings:
            component_metrics[record.id]["mappings"] = score_mappings(record.mappings, predictions.mappings.get(record.id, []))
        for scenario in record.scenarios:
            scenarios.append({"record_id": record.id, **run_scenario(scenario)})
        for sequence in record.sequences:
            scenarios.append({"record_id": record.id, **run_sequence(sequence)})
    unknown = sorted((predictions.fields.keys() | predictions.errors.keys()) - {row.id for row in corpus.records})
    if unknown:
        reasons.append("UNKNOWN_PREDICTION_RECORD")
    if not eligible:
        reasons.append("NO_ELIGIBLE_GOLD")
    if excluded:
        reasons.append("CORPUS_EXCLUSIONS")
    unscored = sorted(sources.keys() - {row.source_id for row in corpus.records})
    if mode == "clinical" and unscored:
        reasons.append("SOURCE_WITHOUT_GOLD")
    coverage = set().union(*(sources[row.source_id].constructs for row in eligible)) if eligible else set()
    missing = sorted(thresholds.required_constructs - coverage)
    if missing:
        reasons.append("CONSTRUCT_COVERAGE_MISSING")
    covered = {"fields": bool(metrics), "scenarios": bool(scenarios)}
    for part in ("identity", "evidence", "mappings"):
        covered[part] = any(part in row for row in component_metrics.values())
    missing_components = sorted(name for name in COMPONENTS if not covered.get(name))
    if missing_components and mode == "clinical":
        # Engineering runs REPORT missing components; a clinical release may not lack any of them.
        reasons.append("COMPONENT_COVERAGE_MISSING")
    for row in component_metrics.values():
        identity = row.get("identity")
        if identity and identity["critical_false_accept"]:
            reasons.append("IDENTITY_FALSE_ACCEPT")
        elif identity and not identity["match"]:
            reasons.append("IDENTITY_DECISION_WRONG")
        for part, reason in (("evidence", "EVIDENCE_THRESHOLD_FAILED"), ("mappings", "MAPPING_THRESHOLD_FAILED")):
            metric = row.get(part)
            if metric and (metric["precision"] is None or metric["recall"] is None
                           or metric["precision"] < thresholds.min_precision or metric["recall"] < thresholds.min_recall):
                reasons.append(reason)
    unsafe = sum(row["unsafe_dates"] for row in scenarios)
    if unsafe:
        reasons.append("UNSAFE_INVENTED_DATE")
    if any(not row["passed"] for row in scenarios):
        reasons.append("EXECUTION_SCENARIO_FAILED")
    for metric in metrics.values():
        if metric["precision"] is None or metric["recall"] is None:
            reasons.append("INSUFFICIENT_DENOMINATOR")
        elif metric["precision"] < thresholds.min_precision or metric["recall"] < thresholds.min_recall:
            reasons.append("FIELD_THRESHOLD_FAILED")
        if metric["critical_false_positives"]:
            reasons.append("CRITICAL_FALSE_POSITIVE")
    if mode == "clinical" and not verified(thresholds.approval, thresholds.content_hash(), trusted, now):
        reasons.append("UNAPPROVED_THRESHOLDS")
    reasons = sorted(set(reasons))
    return {"schema_version": "uctsm-quality.v1", "mode": mode,
            "decision": "FAIL" if reasons else "PASS",
            "clinical_release_qualified": mode == "clinical" and not reasons,
            "reason_codes": reasons, "dataset_version": corpus.dataset_version,
            "dataset_hash": digest(corpus.model_dump(mode="json")),
            "config_hash": thresholds.content_hash(), "prediction_hash": digest(predictions.model_dump(mode="json")),
            "versions": predictions.versions, "validator_version": ScheduleValidator.version,
            "eligible_records": len(eligible), "excluded": excluded,
            "sources_without_gold": unscored,
            "covered_constructs": sorted(coverage), "missing_constructs": missing,
            "unsafe_dates": unsafe, "metrics": metrics, "components": component_metrics,
            "component_coverage": covered, "missing_components": missing_components, "scenarios": scenarios}
