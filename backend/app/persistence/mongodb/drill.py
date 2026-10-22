"""A complete, repeatable restore drill (P13-04): snapshot, isolated restore, archive objects, configuration.

One call (or a scheduler) takes an encrypted logical snapshot of the source database, restores it into an isolated
``uctsm_restore_*`` database, reconciles it, verifies every archived evaluation payload in the archive store (exists,
authenticates, matches its recorded hash and counts), and records a configuration manifest, so the restore proves the
data AND the things needed to read it. The outcome feeds the drill gauges that the alerts watch. It never touches the
source or the live application, and reports error types only (no database text).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.application.evaluation_archive import EvaluationArchiveService, FileArchiveStore
from app.observability.metrics import METRICS

from .migrate import _checksum, discover
from .recovery import restore_isolated, seal, snapshot, unseal
from .unit_of_work import MongoUnitOfWork

REPO = Path(__file__).resolve().parents[4]
CONFIG_FILES = ("deploy/monitoring/alerts.yml", "deploy/monitoring/routes.yml", "deploy/release/criteria.yml",
                "deploy/mongo/roles.json")


def config_manifest() -> dict:
    """Non-secret configuration needed to operate the restored data; environment VALUES are never recorded."""
    files = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
             for name in CONFIG_FILES if (REPO / name).is_file()}
    manifest = {"migrations": [{"version": module.VERSION, "checksum": _checksum(module)} for module in discover()],
                "files": files}
    manifest["sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return manifest


def verify_archive_objects(client, database, archive_root: Path | None, key: bytes | None) -> dict:
    rows = list(database["evaluation_archives"].find({"state": "ARCHIVED"}, {"organization_id": 1, "evaluation_id": 1}))
    if not rows:
        return {"archived": 0, "verified": 0, "failed": []}
    if archive_root is None or key is None:
        return {"archived": len(rows), "verified": 0, "failed": ["ARCHIVE_STORE_NOT_PROVIDED"]}
    service = EvaluationArchiveService(MongoUnitOfWork(client, database), FileArchiveStore(archive_root), key)
    verified, failed = 0, []
    for row in rows:
        try:
            service.verify(row["organization_id"], row["evaluation_id"])
            verified += 1
        except Exception as error:                       # noqa: BLE001 - the type only, never object contents
            failed.append(type(error).__name__)
    return {"archived": len(rows), "verified": verified, "failed": failed}


def run_drill(client, source_name: str, *, key: bytes, workdir: Path, archive_root: Path | None = None,
              rto_seconds: float | None = None, rpo_seconds: float | None = None, keep_restored: bool = False) -> dict:
    started = time.monotonic()
    report: dict = {"source": source_name, "started_at": datetime.now(timezone.utc).isoformat()}
    restored = f"uctsm_restore_{uuid4().hex[:12]}"
    try:
        source = client[source_name]
        payload, snap = snapshot(source)
        path = Path(workdir) / f"{restored}.enc"
        report["snapshot"] = {**snap, "encrypted_file_hash": seal(payload, key, path)}
        sealed = unseal(path, key)
        taken = time.monotonic()
        report["restore"] = restore_isolated(client[restored], sealed)
        report["restore_seconds"] = round(time.monotonic() - taken, 3)
        report["archives"] = verify_archive_objects(client, source, archive_root, key)
        report["config"] = config_manifest()
        problems = []
        if not snap["documents"]:
            problems.append("SOURCE_EMPTY_OR_MISSING")      # a backup of nothing proves nothing
        if report["restore"].get("status") != "VERIFIED_ISOLATED_RESTORE":
            problems.append("RESTORE_NOT_VERIFIED")
        if report["archives"]["failed"]:
            problems.append("ARCHIVE_OBJECTS_NOT_VERIFIED")
        elapsed = round(time.monotonic() - started, 3)
        report["elapsed_seconds"] = elapsed
        report["snapshot_age_seconds"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(snap["created_at"])).total_seconds(), 3)
        if rto_seconds is not None and rpo_seconds is not None:
            met = elapsed <= rto_seconds and report["snapshot_age_seconds"] <= rpo_seconds
            report["rto_rpo_target_status"] = "MET" if met else "NOT_MET"
            if not met:
                problems.append("RTO_RPO_NOT_MET")
        else:
            report["rto_rpo_target_status"] = "NOT_CONFIGURED"
        report["status"] = "FAILED" if problems else "PASSED"
        report["problems"] = problems
    except Exception as error:                           # noqa: BLE001 - never echo database or key material
        report.update({"status": "FAILED", "problems": ["DRILL_ERROR"], "error_type": type(error).__name__})
    finally:
        if not keep_restored:
            client.drop_database(restored)
    METRICS.restore_drill_status.set(1 if report["status"] == "PASSED" else 0)
    if report["status"] == "PASSED":
        METRICS.restore_drill_last_success.set(datetime.now(timezone.utc).timestamp())
    return report


def main(argv=None) -> int:
    """Scheduler entry point: ``python -m app.persistence.mongodb.drill --database NAME --report FILE``.

    Reads UCTSM_RECOVERY_MONGO_URL (local replica set only, like the other recovery tools), UCTSM_RECOVERY_KEY_B64,
    optional UCTSM_ARCHIVE_ROOT, UCTSM_RTO_SECONDS and UCTSM_RPO_SECONDS. Exit 0 only when the drill PASSED.
    """
    import argparse
    import base64
    import os
    import tempfile

    from .client import build_client
    from .settings import MongoSettings, require_local

    parser = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    parser.add_argument("--database", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    client = None
    try:
        key = base64.b64decode(os.environ["UCTSM_RECOVERY_KEY_B64"], validate=True)
        if len(key) != 32:
            raise ValueError("key length")
        settings = require_local(MongoSettings(url=os.environ["UCTSM_RECOVERY_MONGO_URL"], database=args.database))
        client = build_client(settings, server_selection_timeout_ms=3000)
        rto, rpo = os.environ.get("UCTSM_RTO_SECONDS"), os.environ.get("UCTSM_RPO_SECONDS")
        root = os.environ.get("UCTSM_ARCHIVE_ROOT")
        with tempfile.TemporaryDirectory() as work:
            report = run_drill(client, args.database, key=key, workdir=Path(work),
                               archive_root=Path(root) if root else None,
                               rto_seconds=float(rto) if rto else None, rpo_seconds=float(rpo) if rpo else None)
    except Exception as error:                       # noqa: BLE001 - never echo the URL or the key
        report = {"status": "FAILED", "problems": ["DRILL_SETUP_ERROR"], "error_type": type(error).__name__}
    finally:
        if client is not None:
            client.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "problems": report.get("problems", [])}))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
