"""Authorization wiring for tests that exercise UCTSM HTTP behaviour, not policy.

``permissive_authorizer`` still goes THROUGH the real policy and guard: the test
principal simply holds every catalogued capability organization-wide and the
operational relationship checks answer yes. Tests of the authorization rules
themselves (test_uctsm_authz_*.py) build their own authorizers and principals.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.authorization.catalog import Capability
from app.authorization.grants import MemoryGrantStore
from app.authorization.guard import RouteAuthorizer


class AllowAllAccess:
    async def can_access_trial(self, user, external_trial_id):
        return True

    async def can_access_patient(self, user, external_patient_id):
        return True

    async def is_own_patient_record(self, user, external_patient_id):
        return True


def permissive_authorizer(user_id: str, organization_id: str) -> RouteAuthorizer:
    store = MemoryGrantStore()
    for capability in Capability:
        store.grant_sync(str(user_id), str(organization_id), capability.value)
    return RouteAuthorizer(grants=store, access=AllowAllAccess())


def staff(user_id: str, organization_id: str, role: str = "sponsor", **extra) -> dict:
    return {"id": str(user_id), "organization_id": str(organization_id), "role": role,
            "status": "Active", **extra}


def map_operational_ids(client) -> None:
    """Give canonical trials/patients an operational mapping (as an enrolled site
    would have), so a PI/CRC - who reaches canonical records only through that
    mapping - can act on a seeded demo workspace."""
    from app.db import models
    from app.db.base import get_session

    generator = client.app.dependency_overrides[get_session]()
    session = next(generator)
    try:
        for trial in session.query(models.Trial).filter(models.Trial.external_trial_id.is_(None)):
            trial.external_trial_id = f"EXT-{trial.id}"
        for patient in session.query(models.Patient).filter(models.Patient.external_patient_id.is_(None)):
            patient.external_patient_id = f"EXT-{patient.id}"
        session.commit()
    finally:
        generator.close()
