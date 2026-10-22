"""P13-04: the whole restore drill - data, archived payloads, configuration, RTO/RPO, and what alerts watch."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.evaluation_archive import ArchivePolicy, EvaluationArchiveService, FileArchiveStore
from app.observability.metrics import METRICS
from app.persistence.mongodb.drill import config_manifest, run_drill
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]
LATER = datetime.now(timezone.utc) + timedelta(days=400)


@pytest.fixture
def seeded(mongo_uow, mongo_client, _mongo_database, tmp_path):
    world = World(mongo_uow)
    patient = world.patient_ids[0]
    first, _ = world.patients.evaluate(patient, organization_id=world.org, horizon=HORIZON, idempotency_key="d1", today=TODAY)
    world.patients.evaluate(patient, organization_id=world.org, horizon=HORIZON, idempotency_key="d2", today=TODAY)
    key = AESGCM.generate_key(bit_length=256)
    archive_root = tmp_path / "archive"
    EvaluationArchiveService(mongo_uow, FileArchiveStore(archive_root), key).archive(
        world.org, first.id, actor_id=uuid4(), policy=ArchivePolicy(30), now=LATER)
    return world, mongo_client, _mongo_database.name, key, archive_root, tmp_path


def test_a_full_drill_passes_and_verifies_archives_configuration_and_targets(seeded):
    _, client, source, key, archive_root, tmp = seeded
    before = client.list_database_names()
    report = run_drill(client, source, key=key, workdir=tmp, archive_root=archive_root, rto_seconds=300, rpo_seconds=300)
    assert report["status"] == "PASSED" and report["problems"] == []
    assert report["restore"]["status"] == "VERIFIED_ISOLATED_RESTORE"
    assert report["archives"] == {"archived": 1, "verified": 1, "failed": []}
    assert report["rto_rpo_target_status"] == "MET" and report["elapsed_seconds"] <= 300
    assert report["config"]["sha256"] == config_manifest()["sha256"] and len(report["config"]["migrations"]) >= 19
    assert set(report["config"]["files"]) == {"deploy/monitoring/alerts.yml", "deploy/monitoring/routes.yml",
                                              "deploy/release/criteria.yml", "deploy/mongo/roles.json"}
    assert sorted(client.list_database_names()) == sorted(before)                 # the restored database was dropped
    assert METRICS.restore_drill_status.value() == 1 and METRICS.restore_drill_last_success.value() > 0


def test_a_missing_damaged_or_unreadable_archive_object_fails_the_drill_and_flips_the_gauge(seeded):
    _, client, source, key, archive_root, tmp = seeded
    path = next(archive_root.rglob("*.enc"))
    good = path.read_bytes()
    path.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    damaged = run_drill(client, source, key=key, workdir=tmp, archive_root=archive_root)
    assert damaged["status"] == "FAILED" and damaged["problems"] == ["ARCHIVE_OBJECTS_NOT_VERIFIED"]
    assert damaged["archives"]["failed"] and METRICS.restore_drill_status.value() == 0
    path.unlink()
    assert run_drill(client, source, key=key, workdir=tmp, archive_root=archive_root)["archives"]["failed"] == ["ArchiveIntegrityError"]
    path.write_bytes(good)
    assert run_drill(client, source, key=AESGCM.generate_key(bit_length=256), workdir=tmp,
                     archive_root=archive_root)["status"] == "FAILED"                # another key cannot read them
    assert run_drill(client, source, key=key, workdir=tmp, archive_root=None)["archives"]["failed"] == ["ARCHIVE_STORE_NOT_PROVIDED"]
    path.write_bytes(good)
    assert run_drill(client, source, key=key, workdir=tmp, archive_root=archive_root)["status"] == "PASSED"


def test_an_unmet_target_and_an_unreachable_source_both_fail_without_leaking_text(seeded):
    _, client, source, key, archive_root, tmp = seeded
    slow = run_drill(client, source, key=key, workdir=tmp, archive_root=archive_root, rto_seconds=0.0001, rpo_seconds=300)
    assert slow["status"] == "FAILED" and "RTO_RPO_NOT_MET" in slow["problems"] and slow["rto_rpo_target_status"] == "NOT_MET"
    missing = run_drill(client, f"does_not_exist_{uuid4().hex[:6]}", key=key, workdir=tmp)
    assert missing["status"] == "FAILED" and "SOURCE_EMPTY_OR_MISSING" in missing["problems"]     # a backup of nothing proves nothing
    assert "mongodb://" not in str(missing)


def test_the_drill_alerts_exist_with_a_runbook_and_watch_real_metrics():
    from app.observability import routing
    by_name = {rule["alert"]: rule for rule in routing.alerts()}
    assert {"UctsmRestoreDrillFailed", "UctsmRestoreDrillOverdue"} <= set(by_name)
    assert "uctsm_restore_drill_status" in by_name["UctsmRestoreDrillFailed"]["expr"]
    assert "8 * 86400" in by_name["UctsmRestoreDrillOverdue"]["expr"]


def test_the_scheduler_entry_point_runs_a_drill_and_fails_closed_on_bad_setup(seeded, monkeypatch, tmp_path):
    import base64
    import json
    import os
    from app.persistence.mongodb.drill import main
    _, _, source, key, archive_root, _ = seeded
    monkeypatch.setenv("UCTSM_RECOVERY_MONGO_URL", os.environ["UCTSM_TEST_MONGO_URL"])
    monkeypatch.setenv("UCTSM_RECOVERY_KEY_B64", base64.b64encode(key).decode())
    monkeypatch.setenv("UCTSM_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("UCTSM_RTO_SECONDS", "300"); monkeypatch.setenv("UCTSM_RPO_SECONDS", "300")
    out = tmp_path / "report.json"
    assert main(["--database", source, "--report", str(out)]) == 0
    assert json.loads(out.read_text())["status"] == "PASSED"
    monkeypatch.setenv("UCTSM_RECOVERY_KEY_B64", base64.b64encode(b"short").decode())
    assert main(["--database", source, "--report", str(out)]) == 1
    assert json.loads(out.read_text())["problems"] == ["DRILL_SETUP_ERROR"]
