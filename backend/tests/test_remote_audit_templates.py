"""Tests for audit templates (app/services/remote_audit/templates.py) —
spec §16.11's "weight totals not 100%" must block activation, and versioning
must never mutate/delete history.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import templates as tpl  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]

VALID_POLICY = {
    "ComplianceScore": {"InfrastructureCompleteness": 30, "EquipmentVerification": 25,
                         "DocumentCompliance": 25, "QueryCAPAResolution": 20},
    "EvidenceConfidence": {"ProvenanceIntegrity": 30, "LocationConfidence": 25,
                           "DeviceAppIntegrity": 20, "MediaDocumentQuality": 15, "LiveCorroboration": 10},
}
INVALID_POLICY = {
    "ComplianceScore": {"InfrastructureCompleteness": 30, "EquipmentVerification": 25,
                         "DocumentCompliance": 25, "QueryCAPAResolution": 30},  # sums to 110
}


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await tpl.ensure_indexes(database)
    yield database
    database.client.close()


def test_valid_policy_passes_weight_validation():
    tpl.validate_scoring_policy_weights(VALID_POLICY)  # does not raise


def test_invalid_weight_total_is_rejected():
    with pytest.raises(tpl.TemplateValidationError) as exc:
        tpl.validate_scoring_policy_weights(INVALID_POLICY)
    assert exc.value.code == "WEIGHT_TOTAL_INVALID"


def test_empty_scoring_policy_is_rejected():
    with pytest.raises(tpl.TemplateValidationError) as exc:
        tpl.validate_scoring_policy_weights({})
    assert exc.value.code == "EMPTY_SCORING_POLICY"


def test_empty_axis_weights_is_rejected():
    with pytest.raises(tpl.TemplateValidationError) as exc:
        tpl.validate_scoring_policy_weights({"ComplianceScore": {}})
    assert exc.value.code == "EMPTY_AXIS_WEIGHTS"


@pytest.mark.asyncio
async def test_activation_blocks_bad_weight_totals(db):
    draft = await tpl.create_template_draft(
        db, owner_org_id=f"org-{RUN_ID}", jurisdiction="IN",
        scoring_policy_json=INVALID_POLICY, requirement_items=[], created_by="u1",
    )
    with pytest.raises(tpl.TemplateValidationError):
        await tpl.activate_template(db, draft["id"], activated_by="admin-1")
    stored = await tpl.get_template(db, draft["id"])
    assert stored["status"] == tpl.STATUS_DRAFT  # never silently activated


@pytest.mark.asyncio
async def test_activating_a_new_version_supersedes_the_previous_one_without_deleting_it(db):
    family_id = str(uuid.uuid4())
    v1 = await tpl.create_template_draft(
        db, owner_org_id=f"org-{RUN_ID}", jurisdiction="IN",
        scoring_policy_json=VALID_POLICY, requirement_items=[], created_by="u1", family_id=family_id,
    )
    activated_v1 = await tpl.activate_template(db, v1["id"], activated_by="admin-1")
    assert activated_v1["status"] == tpl.STATUS_ACTIVE

    v2 = await tpl.create_template_draft(
        db, owner_org_id=f"org-{RUN_ID}", jurisdiction="IN",
        scoring_policy_json=VALID_POLICY, requirement_items=[{"section": "ICU"}],
        created_by="u1", family_id=family_id,
    )
    assert v2["version"] == 2
    await tpl.activate_template(db, v2["id"], activated_by="admin-1")

    v1_after = await tpl.get_template(db, v1["id"])
    assert v1_after["status"] == tpl.STATUS_SUPERSEDED  # retained, not deleted

    active = await tpl.get_active_template(db, family_id)
    assert active["id"] == v2["id"]

    versions = await tpl.list_template_versions(db, family_id)
    assert len(versions) == 2
