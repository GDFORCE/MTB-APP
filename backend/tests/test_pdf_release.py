"""P13-05: release manifest, objective canary decisions and rollback criteria."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.release import manifest as release

BACKEND = Path(__file__).resolve().parents[1]
CLEAN = {"commit": "a" * 40, "dirty": False, "changed_paths": 0}


def gate(tmp_path, **overrides):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"decision": "PASS", "complete": True, "results": [{"file": "x"}], **overrides}))
    return path


def build(tmp_path, environ=None, tree=CLEAN, **gate_overrides):
    return release.build_manifest(gate_report=gate(tmp_path, **gate_overrides), environ=environ or {}, tree=tree)


def test_a_clean_tree_a_passing_complete_gate_and_safe_flags_may_proceed(tmp_path):
    manifest = build(tmp_path)
    assert release.blocking_issues(manifest) == []
    assert set(manifest["versions"]) == {"policy", "evaluator", "schema"}
    assert [row["version"] for row in manifest["migrations"]] == sorted(row["version"] for row in manifest["migrations"])
    assert all(len(row["checksum"]) == 64 for row in manifest["migrations"])
    assert len(manifest["manifest_sha256"]) == 64 and manifest["evidence"]["curated_gate"]["files"] == 1


def test_the_manifest_hash_is_deterministic_and_changes_with_any_input(tmp_path):
    first, second = build(tmp_path), build(tmp_path)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert build(tmp_path, tree={**CLEAN, "commit": "b" * 40})["manifest_sha256"] != first["manifest_sha256"]
    assert build(tmp_path, environ={"DEV_OTP_MODE": "true"})["manifest_sha256"] != first["manifest_sha256"]


@pytest.mark.parametrize("kwargs, code", [
    ({"tree": {**CLEAN, "dirty": True, "changed_paths": 3}}, "WORKING_TREE_NOT_CLEAN"),
    ({"decision": "FAIL"}, "CURATED_GATE_NOT_PASSED"),
    ({"complete": False}, "CURATED_GATE_NOT_PASSED"),
    ({"environ": {"UCTSM_AUTHORITATIVE": "true"}}, "UNAPPROVED_FLAG_UCTSM_AUTHORITATIVE"),
    ({"environ": {"DEV_OTP_MODE": "1"}}, "UNAPPROVED_FLAG_DEV_OTP_MODE"),
])
def test_each_blocking_condition_stops_the_release(tmp_path, kwargs, code):
    assert code in release.blocking_issues(build(tmp_path, **kwargs))


def test_a_missing_gate_report_blocks_and_an_explicit_approval_allows_only_that_flag(tmp_path):
    missing = release.build_manifest(gate_report=None, environ={}, tree=CLEAN)
    assert "CURATED_GATE_REPORT_MISSING" in release.blocking_issues(missing)
    manifest = build(tmp_path, environ={"UCTSM_AUTHORITATIVE": "true", "DEV_OTP_MODE": "true"})
    issues = release.blocking_issues(manifest, approved_flags=frozenset({"UCTSM_AUTHORITATIVE"}))
    assert issues == ["UNAPPROVED_FLAG_DEV_OTP_MODE"]


def test_canary_decisions_are_objective():
    decide = lambda **k: release.canary_decision(**{"stage_percent": 10, "minutes_observed": 45,
                                                    "firing": set(), "synthetic_failures": 0, **k})
    assert decide() == {"decision": "PROCEED", "reasons": [], "next_stage_percent": 50}
    assert decide(stage_percent=100)["next_stage_percent"] is None
    assert decide(minutes_observed=10)["reasons"] == ["OBSERVATION_WINDOW_NOT_COMPLETE"]
    assert decide(firing={"UctsmOutboxDeadLetters"})["decision"] == "HOLD"
    assert decide(synthetic_failures=1)["reasons"] == ["SYNTHETIC_CHECK_FAILED"]
    rollback = decide(firing={"UctsmHttp5xxRate", "UctsmOutboxDeadLetters"})
    assert rollback == {"decision": "ROLLBACK", "reasons": ["ALERT_UctsmHttp5xxRate"]}
    with pytest.raises(ValueError):
        decide(stage_percent=7)


def test_criteria_name_real_alerts_and_a_real_runbook():
    rules = release.criteria()
    from app.observability import routing
    known = {rule["alert"] for rule in routing.alerts()}
    assert (set(rules["canary"]["abort_if_alert_firing"]) | set(rules["canary"]["hold_if_alert_firing"])) <= known
    assert (release.REPO / rules["rollback"]["procedure"]).is_file()
    assert set(rules["flags_that_must_be_off_unless_approved"]) == {"UCTSM_AUTHORITATIVE", "DEV_OTP_MODE"}


def test_the_cli_fails_closed_without_a_gate_report(tmp_path):
    out = tmp_path / "manifest.json"
    done = subprocess.run([sys.executable, "scripts/release_gate.py", "--output", str(out)],
                          cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 1 and out.is_file()
    assert "CURATED_GATE_REPORT_MISSING" in json.loads(out.read_text())["blocking_issues"]


# ---- P8-05: the quality report is part of the release decision -------------------------------------------
def quality(tmp_path, **fields):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps({"decision": "PASS", "mode": "engineering", "clinical_release_qualified": False,
                                "reason_codes": [], "dataset_hash": "d" * 64, **fields}))
    return path


def with_quality(tmp_path, kind="engineering", **fields):
    return release.build_manifest(gate_report=gate(tmp_path), environ={}, tree=CLEAN,
                                  quality_report=quality(tmp_path, **fields), release_kind=kind)


def test_an_engineering_release_needs_no_quality_report_but_a_failing_one_blocks(tmp_path):
    assert release.blocking_issues(build(tmp_path)) == []
    assert release.blocking_issues(with_quality(tmp_path)) == []
    assert "QUALITY_GATE_FAILED" in release.blocking_issues(with_quality(tmp_path, decision="FAIL"))
    assert "QUALITY_REGRESSION_AGAINST_BASELINE" in release.blocking_issues(
        with_quality(tmp_path, comparison={"regressed": True}))


def test_a_clinical_release_needs_a_qualified_clinical_report_and_engineering_never_qualifies(tmp_path):
    missing = release.build_manifest(gate_report=gate(tmp_path), environ={}, tree=CLEAN, release_kind="clinical")
    assert "QUALITY_REPORT_MISSING" in release.blocking_issues(missing)
    assert "CLINICAL_RELEASE_NOT_QUALIFIED" in release.blocking_issues(with_quality(tmp_path, kind="clinical"))
    ok = with_quality(tmp_path, kind="clinical", mode="clinical", clinical_release_qualified=True)
    assert release.blocking_issues(ok) == []
    assert ok["evidence"]["quality"]["report_sha256"] and ok["evidence"]["quality"]["dataset_hash"] == "d" * 64


def test_the_manifest_hash_changes_with_the_quality_evidence(tmp_path):
    assert with_quality(tmp_path)["manifest_sha256"] != build(tmp_path)["manifest_sha256"]
    assert with_quality(tmp_path)["manifest_sha256"] != with_quality(tmp_path, decision="FAIL")["manifest_sha256"]
