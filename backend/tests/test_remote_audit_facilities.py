"""Tests for facilities/equipment (app/services/remote_audit/facilities.py)
— "extract -> review -> commit": an AI suggestion is stored separately from
the human-confirmed value, and only the confirmed value is fact.
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

from app.services.remote_audit import facilities as fac  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
AUDIT_ID = f"audit-{RUN_ID}"


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await fac.ensure_indexes(database)
    yield database
    await database[fac.FACILITIES_COLLECTION].delete_many({"audit_id": AUDIT_ID})
    await database[fac.EQUIPMENT_COLLECTION].delete_many({"facility_id": {"$regex": f"^{RUN_ID}"}})
    database.client.close()


@pytest.mark.asyncio
async def test_create_and_list_facilities(db):
    f1 = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="ICU", name="ICU Ward A", created_by="u1")
    facilities = await fac.list_facilities(db, AUDIT_ID)
    assert any(f["id"] == f1["id"] for f in facilities)


@pytest.mark.asyncio
async def test_equipment_with_ai_suggestion_is_not_fact_until_confirmed(db):
    facility = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="Pharmacy", name="Pharmacy", created_by="u1")
    ai_suggestion = {
        "fields": {"name": "Infusion Pump", "manufacturer": "Acme", "model": "IP-500", "serial_number": None},
        "confidence": "medium", "confidence_notes": "serial number obscured", "provider": "anthropic",
    }
    equipment = await fac.create_equipment(
        db, facility_id=facility["id"], category="infusion_pump", created_by="u1", ai_suggestion=ai_suggestion,
    )
    assert equipment["confirmed"] is None  # AI suggestion alone is never fact
    assert equipment["ai_suggestion"]["fields"]["manufacturer"] == "Acme"

    confirmed = await fac.confirm_equipment(
        db, equipment["id"], fields={"name": "Infusion Pump", "manufacturer": "Acme", "model": "IP-500",
                                      "serial_number": "SN-CONFIRMED-123"},
        confirmed_by="reviewer-1",
    )
    assert confirmed["confirmed"]["serial_number"] == "SN-CONFIRMED-123"
    assert confirmed["confirmed_by"] == "reviewer-1"
    assert confirmed["confirmed_at"] is not None
    # the raw AI suggestion is retained for provenance, unchanged
    assert confirmed["ai_suggestion"]["fields"]["serial_number"] is None


@pytest.mark.asyncio
async def test_equipment_can_be_entered_manually_with_no_ai_suggestion_at_all(db):
    facility = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="OT", name="OT-1", created_by="u1")
    equipment = await fac.create_equipment(db, facility_id=facility["id"], category="ventilator", created_by="u1")
    assert equipment["ai_suggestion"] is None
    confirmed = await fac.confirm_equipment(db, equipment["id"], fields={"name": "Ventilator"}, confirmed_by="u1")
    assert confirmed["confirmed"]["name"] == "Ventilator"


@pytest.mark.asyncio
async def test_confirm_equipment_rejects_unknown_fields(db):
    facility = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="OT", name="OT-2", created_by="u1")
    equipment = await fac.create_equipment(db, facility_id=facility["id"], category="ventilator", created_by="u1")
    with pytest.raises(ValueError):
        await fac.confirm_equipment(db, equipment["id"], fields={"not_a_real_field": "x"}, confirmed_by="u1")


@pytest.mark.asyncio
async def test_equipment_service_status_toggle(db):
    facility = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="OT", name="OT-3", created_by="u1")
    equipment = await fac.create_equipment(db, facility_id=facility["id"], category="ventilator", created_by="u1")
    assert equipment["status"] == fac.EQUIPMENT_STATUS_IN_SERVICE
    await fac.set_equipment_service_status(db, equipment["id"], fac.EQUIPMENT_STATUS_OUT_OF_SERVICE)
    updated = await fac.get_equipment(db, equipment["id"])
    assert updated["status"] == fac.EQUIPMENT_STATUS_OUT_OF_SERVICE
    with pytest.raises(ValueError):
        await fac.set_equipment_service_status(db, equipment["id"], "NOT_A_REAL_STATUS")


@pytest.mark.asyncio
async def test_quantity_greater_than_one_is_supported(db):
    """spec §9.2: for quantity>1, the template defines the sampling rule --
    this module just needs to be able to represent an aggregate count."""
    facility = await fac.create_facility(db, audit_id=AUDIT_ID, facility_type="OT", name="OT-4", created_by="u1")
    equipment = await fac.create_equipment(db, facility_id=facility["id"], category="iv_pole", created_by="u1", quantity=10)
    assert equipment["quantity"] == 10
