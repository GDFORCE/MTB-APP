from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.uctsm import create_uctsm_router
from _authz_fixtures import permissive_authorizer


async def authenticated_user():
    return {"id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "role": "sponsor", "status": "Active"}


def test_openapi_exposes_new_boundary_without_forward_reference_errors():
    app = FastAPI()
    app.include_router(create_uctsm_router(
        authenticated_user,
        authorizer=permissive_authorizer(
            "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")))
    paths = app.openapi()["paths"]
    assert "/api/uctsm/schedule-versions/{schedule_version_id}/validate" in paths
    assert "/api/uctsm/patients/{patient_id}/schedule/evaluate" in paths
    assert "/api/uctsm/patient-events/{patient_event_id}/occurrences" in paths

