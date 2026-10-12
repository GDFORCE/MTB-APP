"""Mapping between domain values and BSON-storable values.

Rules (D6 and the P14-01 collection architecture):
* UUIDs stay UUIDs - stored as binary subtype 4, never as ObjectId;
* a clinical calendar DATE is stored as an ISO ``YYYY-MM-DD`` string, because
  BSON has no date-only type and a date must never become midnight UTC;
* an instant (``datetime``) is stored as a UTC BSON date; a naive datetime is
  rejected rather than silently read as UTC;
* ``Decimal`` becomes ``float``; enums become their value.
Round-tripping domain payloads (timings, constraints) stays JSON-shaped exactly
as the SQL JSON columns held them; only envelope fields use native BSON types.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str, UUID, bytes)):
        return value.value if isinstance(value, Enum) else value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("a naive datetime cannot be stored as an instant; supply a zone")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [encode(item) for item in value]
    raise TypeError(f"cannot store a {type(value).__name__} in MongoDB")


def decode_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def aware(value: datetime | None) -> datetime | None:
    """Make an instant read from BSON explicitly UTC (defensive: ``tz_aware``)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
