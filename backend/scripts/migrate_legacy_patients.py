"""Operator CLI: migrate legacy patients of ONE trial into the UCTSM Mongo plane (P11-02).

Dry-run is the default; ``--apply`` is explicit. Nothing here flips UCTSM_AUTHORITATIVE, a trial read
mode or any legacy collection (the legacy database is opened read-only by construction of the adapter).

    UCTSM_LEGACY_MONGO_URL / UCTSM_LEGACY_DB   source (read-only)
    UCTSM_MONGO_URL / UCTSM_MONGO_DB           destination plane
"""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.legacy_migration import LegacyMigrationJob  # noqa: E402
from app.persistence.mongodb.client import build_client, get_database  # noqa: E402
from app.persistence.mongodb.legacy_source import LegacyMongoSource  # noqa: E402
from app.persistence.mongodb.settings import MongoSettings  # noqa: E402
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--actor-id", type=UUID, required=True)
    parser.add_argument("--trial-id", type=UUID, required=True, help="canonical trial id")
    parser.add_argument("--schedule-definition-id", type=UUID, required=True)
    parser.add_argument("--external-trial-id", required=True, help="the legacy trial id")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--apply", action="store_true", help="write; without it the run is a dry run")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    settings = MongoSettings.from_env()
    destination = build_client(settings)
    legacy = build_client(MongoSettings(url=os.environ["UCTSM_LEGACY_MONGO_URL"],
                                        database=os.environ["UCTSM_LEGACY_DB"]).enforce_environment_posture())
    try:
        job = LegacyMigrationJob(
            MongoUnitOfWork(destination, get_database(destination, settings)),
            LegacyMongoSource(legacy[os.environ["UCTSM_LEGACY_DB"]]))
        report = job.run(
            organization_id=args.organization_id, actor_id=args.actor_id, canonical_trial_id=args.trial_id,
            schedule_definition_id=args.schedule_definition_id, external_trial_id=args.external_trial_id,
            run_key=args.run_key, dry_run=not args.apply, batch_size=args.batch_size).as_dict()
        report["reconciliation"] = job.reconciliation(args.organization_id) if args.apply else None
        text = json.dumps(report, indent=2, sort_keys=True, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "counts": report["counts"], "dry_run": report["dry_run"]}))
        return 0 if report["status"] == "COMPLETED" else 2
    finally:
        destination.close()
        legacy.close()


if __name__ == "__main__":
    raise SystemExit(main())
