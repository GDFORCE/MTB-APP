"""P14-04: connection posture, least-privilege roles and drill evidence."""
import json
from pathlib import Path

import pytest

from app.persistence.mongodb.recovery_cli import evaluate_targets
from app.persistence.mongodb.settings import MongoPostureError, MongoSettings, MongoSettingsError

ROLES = json.loads((Path(__file__).resolve().parents[2] / "deploy" / "mongo" / "roles.json").read_text(encoding="utf-8"))
FORBIDDEN = {"anyAction", "dropDatabase", "dropCollection", "dropIndex", "grantRole", "revokeRole", "createRole",
             "createUser", "dropUser", "shutdown", "dbAdmin", "root", "userAdmin"}


@pytest.mark.parametrize("url, issues", [
    ("mongodb://127.0.0.1:27017/?replicaSet=rs0", []),
    ("mongodb+srv://app:pw@cluster.example.net/?retryWrites=true&w=majority", []),
    ("mongodb://app:pw@db.example.net:27017/?tls=true", []),
    ("mongodb://db.example.net:27017/?tls=true", ["AUTHENTICATION_REQUIRED"]),
    ("mongodb://app:pw@db.example.net:27017/", ["TLS_REQUIRED"]),
    ("mongodb://app:pw@db.example.net/?tls=true&tlsAllowInvalidCertificates=true", ["CERTIFICATE_VALIDATION_DISABLED"]),
    ("mongodb://app:pw@db.example.net/?tls=true&retryWrites=false&w=1", ["RETRYABLE_WRITES_DISABLED", "WRITE_CONCERN_NOT_MAJORITY"]),
    ("mongodb://db.example.net/", ["TLS_REQUIRED", "AUTHENTICATION_REQUIRED"]),
])
def test_posture_issues_are_stable_codes(url, issues):
    assert MongoSettings(url=url, database="uctsm_x").posture_issues() == issues


def test_production_refuses_an_unsafe_connection_without_echoing_the_url(monkeypatch):
    monkeypatch.setenv("UCTSM_MONGO_URL", "mongodb://admin:SuperSecret1@db.example.net:27017/")
    monkeypatch.setenv("UCTSM_MONGO_DB", "uctsm_prod"); monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.setenv("UCTSM_ENV", "production")
    with pytest.raises(MongoPostureError) as raised:
        MongoSettings.from_env()
    assert "TLS_REQUIRED" in str(raised.value) and "SuperSecret1" not in str(raised.value)
    monkeypatch.setenv("UCTSM_ENV", "development")                      # development keeps working
    assert MongoSettings.from_env().database == "uctsm_prod"
    monkeypatch.setenv("UCTSM_MONGO_URL", "mongodb://admin:SuperSecret1@db.example.net:27017/?tls=true")
    monkeypatch.setenv("UCTSM_ENV", "production")
    assert MongoSettings.from_env().posture_issues() == []


def test_settings_never_print_credentials():
    settings = MongoSettings(url="mongodb://admin:SuperSecret1@db.example.net:27017/?tls=true", database="d")
    assert "SuperSecret1" not in repr(settings) and "SuperSecret1" not in str(settings)
    assert settings.safe_url == "db.example.net:27017"
    from app.persistence.mongodb.settings import require_local
    with pytest.raises(MongoSettingsError) as raised:
        require_local(settings)
    assert "SuperSecret1" not in str(raised.value)


def test_roles_are_least_privilege_and_scoped_to_the_uctsm_database():
    roles = ROLES["roles"]
    assert set(roles) == {"uctsm_runtime", "uctsm_migrator", "uctsm_backup_reader"}
    for name, role in roles.items():
        for privilege in role["privileges"]:
            assert privilege["resource"]["db"] == "<db>" and privilege["resource"]["db"] != "admin", name
            assert not set(privilege["actions"]) & FORBIDDEN, name
    runtime = set(roles["uctsm_runtime"]["privileges"][0]["actions"])
    assert not runtime & {"createCollection", "createIndex", "collMod"}
    assert not set(roles["uctsm_backup_reader"]["privileges"][0]["actions"]) & {"insert", "update", "remove"}
    assert "network" in ROLES and "tls" in ROLES["network"] and "authentication" in ROLES["network"]


def test_restore_evidence_is_compared_with_configured_targets_and_never_invented():
    drill = {"elapsed_seconds": 40.0, "snapshot_age_seconds": 600.0}
    assert evaluate_targets(drill, "restore", {})["rto_rpo_target_status"] == "NOT_CONFIGURED"
    assert evaluate_targets(drill, "restore", {"UCTSM_RTO_SECONDS": "60", "UCTSM_RPO_SECONDS": "900"})["rto_rpo_target_status"] == "MET"
    assert evaluate_targets(drill, "restore", {"UCTSM_RTO_SECONDS": "30", "UCTSM_RPO_SECONDS": "900"})["rto_rpo_target_status"] == "NOT_MET"
    assert evaluate_targets(drill, "restore", {"UCTSM_RTO_SECONDS": "60", "UCTSM_RPO_SECONDS": "300"})["rto_rpo_target_status"] == "NOT_MET"
    assert evaluate_targets({}, "restore", {"UCTSM_RTO_SECONDS": "60", "UCTSM_RPO_SECONDS": "300"})["rto_rpo_target_status"] == "NOT_MET"
    assert evaluate_targets(drill, "snapshot", {"UCTSM_RTO_SECONDS": "60", "UCTSM_RPO_SECONDS": "900"})["rto_rpo_target_status"] == "NOT_APPLICABLE_TO_SNAPSHOT"
