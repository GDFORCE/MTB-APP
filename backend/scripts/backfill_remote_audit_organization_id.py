"""Backfill `organization_id` on existing user documents.

Production-Grade Implementation Plan v1.3, Phase 1: user docs previously only
carried a free-text `organization` name string, matched by string comparison
wherever tenant scoping was needed (fragile — typos/renames silently break
it). New registrations set `organization_id` going forward (see
`server.py`'s `/auth/register`); this script backfills everyone who signed up
before that change.

Idempotent and safe to re-run: only touches users missing `organization_id`,
and a user whose `organization` name doesn't resolve to any organization
record is skipped (reported, not guessed at) rather than assigned a wrong id.

Usage (from backend/):
    python scripts/backfill_remote_audit_organization_id.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import os  # noqa: E402


async def backfill(dry_run: bool = False) -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], tz_aware=True)
    db = client[os.environ.get("DB_NAME", "mtb_app")]

    updated = 0
    skipped_no_org_name = 0
    skipped_unresolved = 0

    cursor = db.users.find(
        {"organization_id": {"$exists": False}},
        {"_id": 0, "id": 1, "organization": 1},
    )
    async for user in cursor:
        org_name = (user.get("organization") or "").strip()
        if not org_name:
            skipped_no_org_name += 1
            continue
        key = " ".join(org_name.split()).casefold()
        organization = await db.organizations.find_one({"name_key": key}, {"_id": 0, "id": 1})
        if not organization:
            skipped_unresolved += 1
            print(f"  SKIP (no matching organization): user {user['id']!r}, organization={org_name!r}")
            continue
        if not dry_run:
            await db.users.update_one(
                {"id": user["id"]}, {"$set": {"organization_id": organization["id"]}}
            )
        updated += 1

    print(
        f"{'Would update' if dry_run else 'Updated'} {updated} user(s); "
        f"skipped {skipped_no_org_name} with no organization name, "
        f"{skipped_unresolved} with an unresolved organization name."
    )
    client.close()


if __name__ == "__main__":
    asyncio.run(backfill(dry_run="--dry-run" in sys.argv))
