"""Facilities & equipment inventory — spec §9 (Infrastructure and Asset
Evidence). Template-driven, not hard-coded (spec §9.1): what facility types
and equipment categories are expected comes from the audit's template
(templates.py), not from this module.

Equipment identification follows "extract -> review -> commit" (this app's
own architecture-doc AI pattern, and spec's human-in-the-loop principle):
an AI suggestion from a photo (vision_extraction.analyze_equipment_photo) is
stored separately from the human-confirmed value, and only the confirmed
value is ever treated as fact.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

FACILITIES_COLLECTION = "remote_audit_facilities"
EQUIPMENT_COLLECTION = "remote_audit_equipment"

EQUIPMENT_STATUS_IN_SERVICE = "IN_SERVICE"
EQUIPMENT_STATUS_OUT_OF_SERVICE = "OUT_OF_SERVICE"

_CONFIRMABLE_EQUIPMENT_FIELDS = {"name", "manufacturer", "model", "serial_number", "category", "notes"}


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[FACILITIES_COLLECTION].create_index("id", unique=True)
    await db[FACILITIES_COLLECTION].create_index("audit_id")
    await db[EQUIPMENT_COLLECTION].create_index("id", unique=True)
    await db[EQUIPMENT_COLLECTION].create_index("facility_id")


async def create_facility(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    facility_type: str,
    name: str,
    created_by: str,
    floor: Optional[str] = None,
    location_text: Optional[str] = None,
    capacity: Optional[int] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()), "audit_id": audit_id, "type": facility_type, "name": name,
        "floor": floor, "location_text": location_text, "capacity": capacity,
        "status": EQUIPMENT_STATUS_IN_SERVICE, "created_by": created_by,
        "created_at": now, "updated_at": now,
    }
    await db[FACILITIES_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def list_facilities(db: AsyncIOMotorDatabase, audit_id: str) -> List[Dict[str, Any]]:
    return await db[FACILITIES_COLLECTION].find({"audit_id": audit_id}, {"_id": 0}).to_list(None)


async def get_facility(db: AsyncIOMotorDatabase, facility_id: str) -> Optional[Dict[str, Any]]:
    return await db[FACILITIES_COLLECTION].find_one({"id": facility_id}, {"_id": 0})


async def create_equipment(
    db: AsyncIOMotorDatabase,
    *,
    facility_id: str,
    category: str,
    created_by: str,
    quantity: int = 1,
    serial_number: Optional[str] = None,
    calibration_date: Optional[str] = None,
    ai_suggestion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """`serial_number` may be provided directly (manual entry) even when an
    `ai_suggestion` is also attached -- spec §9.2: serial number is not
    universally mandatory, and this module never requires a photo path."""
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()), "facility_id": facility_id, "category": category,
        "quantity": quantity, "serial_number": serial_number, "calibration_date": calibration_date,
        "status": EQUIPMENT_STATUS_IN_SERVICE,
        "ai_suggestion": ai_suggestion,  # raw suggestion + provenance -- never treated as fact on its own
        "confirmed": None,               # only this is read as the compliance record, once set
        "confirmed_by": None, "confirmed_at": None,
        "created_by": created_by, "created_at": now, "updated_at": now,
    }
    await db[EQUIPMENT_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def confirm_equipment(
    db: AsyncIOMotorDatabase,
    equipment_id: str,
    *,
    fields: Dict[str, Any],
    confirmed_by: str,
) -> Optional[Dict[str, Any]]:
    """The human-confirmation step of "extract -> review -> commit": whether
    a field's value came from the AI suggestion untouched, was edited, or
    was typed manually with no suggestion at all, only what's confirmed
    here becomes the equipment record's fact."""
    unknown = set(fields) - _CONFIRMABLE_EQUIPMENT_FIELDS
    if unknown:
        raise ValueError(f"unknown equipment field(s): {sorted(unknown)}")
    now = datetime.now(timezone.utc)
    await db[EQUIPMENT_COLLECTION].update_one(
        {"id": equipment_id},
        {"$set": {"confirmed": fields, "confirmed_by": confirmed_by, "confirmed_at": now, "updated_at": now}},
    )
    return await get_equipment(db, equipment_id)


async def get_equipment(db: AsyncIOMotorDatabase, equipment_id: str) -> Optional[Dict[str, Any]]:
    return await db[EQUIPMENT_COLLECTION].find_one({"id": equipment_id}, {"_id": 0})


async def list_equipment(db: AsyncIOMotorDatabase, facility_id: str) -> List[Dict[str, Any]]:
    return await db[EQUIPMENT_COLLECTION].find({"facility_id": facility_id}, {"_id": 0}).to_list(None)


async def set_equipment_service_status(db: AsyncIOMotorDatabase, equipment_id: str, status: str) -> None:
    if status not in (EQUIPMENT_STATUS_IN_SERVICE, EQUIPMENT_STATUS_OUT_OF_SERVICE):
        raise ValueError(f"invalid equipment status: {status!r}")
    await db[EQUIPMENT_COLLECTION].update_one(
        {"id": equipment_id}, {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
    )
