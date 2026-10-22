"""Release manifest and objective canary decisions (P13-05). No deployment happens here."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Mapping

import yaml

REPO = Path(__file__).resolve().parents[3]
CRITERIA = REPO / "deploy" / "release" / "criteria.yml"
TRUE = {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def criteria() -> dict:
    return yaml.safe_load(CRITERIA.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def tree_state() -> dict:
    # Untracked files are included: a release must be reproducible from the recorded commit.
    dirty = [line for line in _git("status", "--porcelain").splitlines() if line.strip()]
    return {"commit": _git("rev-parse", "HEAD"), "dirty": bool(dirty), "changed_paths": len(dirty)}


def gate_evidence(report_path: Path | None) -> dict:
    if report_path is None or not report_path.is_file():
        return {"present": False, "passed": False, "reason": "MISSING"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    passed = report.get("decision") == "PASS" and report.get("complete") is True
    return {"present": True, "passed": passed, "files": len(report.get("results", [])),
            "report_sha256": _sha256(report_path), "reason": None if passed else "NOT_PASSED_OR_INCOMPLETE"}


def quality_evidence(report_path: Path | None) -> dict:
    if report_path is None or not report_path.is_file():
        return {"present": False}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    comparison = report.get("comparison")
    return {"present": True, "decision": report.get("decision"), "mode": report.get("mode"),
            "clinical_release_qualified": bool(report.get("clinical_release_qualified")),
            "reason_codes": list(report.get("reason_codes", [])), "dataset_hash": report.get("dataset_hash"),
            "regressed": bool(comparison and comparison.get("regressed")), "report_sha256": _sha256(report_path)}


def build_manifest(*, gate_report: Path | None = None, environ: Mapping[str, str] | None = None,
                   tree: dict | None = None, quality_report: Path | None = None,
                   release_kind: str = "engineering") -> dict:
    from app.observability import routing
    from app.observability.logs import version_metadata
    from app.persistence.mongodb.migrate import _checksum, discover

    environ = os.environ if environ is None else environ
    rules = criteria()
    manifest = {
        "schema": "uctsm-release-manifest.v1",
        "source": tree or tree_state(),
        "versions": version_metadata(),
        "migrations": [{"version": module.VERSION, "name": module.NAME, "checksum": _checksum(module)}
                       for module in discover()],
        "monitoring": {"alerts": sorted(rule["alert"] for rule in routing.alerts()),
                       "alerts_sha256": _sha256(routing.ROOT / "alerts.yml"),
                       "routes_sha256": _sha256(routing.ROOT / "routes.yml")},
        "criteria_sha256": _sha256(CRITERIA),
        "flags": {name: environ.get(name, "") for name in rules["flags_that_must_be_off_unless_approved"]},
        "release_kind": release_kind,
        "evidence": {"curated_gate": gate_evidence(gate_report), "quality": quality_evidence(quality_report)},
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return manifest


def blocking_issues(manifest: dict, *, approved_flags: frozenset[str] = frozenset()) -> list[str]:
    """Stable codes that stop a release. Empty means the manifest may proceed to a canary."""
    rules, issues = criteria(), []
    if manifest["source"]["dirty"]:
        issues.append("WORKING_TREE_NOT_CLEAN")
    gate = manifest["evidence"]["curated_gate"]
    if not gate["present"]:
        issues.append("CURATED_GATE_REPORT_MISSING")
    elif not gate["passed"]:
        issues.append("CURATED_GATE_NOT_PASSED")
    for name, value in manifest["flags"].items():
        if value.strip().lower() in TRUE and name not in approved_flags:
            issues.append(f"UNAPPROVED_FLAG_{name}")
    quality = manifest["evidence"].get("quality", {"present": False})
    if quality["present"]:
        if quality["decision"] != "PASS":
            issues.append("QUALITY_GATE_FAILED")
        if quality["regressed"]:
            issues.append("QUALITY_REGRESSION_AGAINST_BASELINE")
    if manifest.get("release_kind") == "clinical":
        if not quality["present"]:
            issues.append("QUALITY_REPORT_MISSING")
        elif not quality["clinical_release_qualified"]:
            issues.append("CLINICAL_RELEASE_NOT_QUALIFIED")
    known = set(manifest["monitoring"]["alerts"])
    referenced = set(rules["canary"]["abort_if_alert_firing"]) | set(rules["canary"]["hold_if_alert_firing"])
    if referenced - known:
        issues.append("CRITERIA_REFERENCE_UNKNOWN_ALERT")
    if not manifest["migrations"]:
        issues.append("NO_MIGRATIONS_RECORDED")
    return issues


def canary_decision(*, stage_percent: int, minutes_observed: float, firing: set[str],
                    synthetic_failures: int, rules: dict | None = None) -> dict:
    """PROCEED / HOLD / ROLLBACK from objective inputs only."""
    canary = (rules or criteria())["canary"]
    if stage_percent not in canary["stages_percent"]:
        raise ValueError("unknown canary stage")
    abort = sorted(firing & set(canary["abort_if_alert_firing"]))
    if abort:
        return {"decision": "ROLLBACK", "reasons": [f"ALERT_{name}" for name in abort]}
    reasons = [f"ALERT_{name}" for name in sorted(firing & set(canary["hold_if_alert_firing"]))]
    if synthetic_failures > canary["max_synthetic_check_failures"]:
        reasons.append("SYNTHETIC_CHECK_FAILED")
    if reasons:
        return {"decision": "HOLD", "reasons": reasons}
    if minutes_observed < canary["observe_minutes_per_stage"]:
        return {"decision": "HOLD", "reasons": ["OBSERVATION_WINDOW_NOT_COMPLETE"]}
    stages = canary["stages_percent"]
    index = stages.index(stage_percent)
    return {"decision": "PROCEED", "reasons": [], "next_stage_percent": stages[index + 1] if index + 1 < len(stages) else None}
