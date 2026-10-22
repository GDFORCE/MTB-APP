"""Local isolated recovery drill; credentials/key are read only from environment."""
import argparse
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .client import build_client
from .recovery import restore_isolated, seal, snapshot, unseal
from .settings import MongoSettings, require_local


def evaluate_targets(report: dict, action: str, environ=os.environ) -> dict:
    """Compare a drill with the approved RTO/RPO (seconds) when they are configured. Never invents a target."""
    rto, rpo = environ.get("UCTSM_RTO_SECONDS"), environ.get("UCTSM_RPO_SECONDS")
    if not rto or not rpo:
        return {"rto_rpo_target_status": "NOT_CONFIGURED"}
    if action != "restore":
        return {"rto_rpo_target_status": "NOT_APPLICABLE_TO_SNAPSHOT"}
    met = report.get("elapsed_seconds", float("inf")) <= float(rto) and report.get("snapshot_age_seconds", float("inf")) <= float(rpo)
    return {"rto_rpo_target_status": "MET" if met else "NOT_MET", "rto_target_seconds": float(rto), "rpo_target_seconds": float(rpo)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Encrypted local Mongo snapshot / isolated restore drill")
    parser.add_argument("action", choices=["snapshot", "restore"])
    parser.add_argument("--database", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    client = None
    started = time.monotonic()
    try:
        key = base64.b64decode(os.environ["UCTSM_RECOVERY_KEY_B64"], validate=True)
        if len(key) != 32: raise ValueError("invalid recovery key length")
        settings = require_local(MongoSettings(url=os.environ["UCTSM_RECOVERY_MONGO_URL"], database=args.database))
        client = build_client(settings, server_selection_timeout_ms=2000)
        if not client.admin.command("hello").get("setName"): raise ValueError("a replica set is required")
        database = client[settings.database]
        if args.action == "snapshot":
            if args.database not in client.list_database_names(): raise ValueError("source database does not exist")
            payload, report = snapshot(database)
            report["encrypted_file_hash"] = seal(payload, key, args.file)
            report["status"] = "ENCRYPTED_LOGICAL_SNAPSHOT"
        else:
            sealed = unseal(args.file, key)
            report = restore_isolated(database, sealed)
            # RPO evidence: how old the data in the backup was when the drill began.
            report["snapshot_age_seconds"] = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(sealed["created_at"])).total_seconds() - (time.monotonic() - started), 3)
        report["production_pitr_verified"] = False
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report.update(evaluate_targets(report, args.action))
        code = 0
    except Exception as error:
        # Raw database/encryption errors can contain a URI or clinical contents.
        report = {"status": "FAILED", "error_type": type(error).__name__}; code = 1
    finally:
        if client is not None: client.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
