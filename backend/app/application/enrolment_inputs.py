"""Check and read the details a clinic supplies at enrolment against the schedule's acknowledged list.

Enforced only for a schedule whose list was reviewed and acknowledged (anything approved before that step existed keeps
its old optional-anchor behaviour). Every required entry must be supplied or explicitly deferred ("will enter later");
nothing unknown is accepted; values are parsed strictly. A date-and-time value must carry its UTC offset, because a time
of day without one is exactly the guess this feature exists to remove.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.domain.schedule.required_inputs import RequiredInput, inputs_hash

from .required_inputs import acknowledgement, effective_inputs

MAX_TEXT = 500


@dataclass
class ResolvedInputs:
    enforced: bool = False
    anchor_dates: dict[str, date] = field(default_factory=dict)
    anchor_datetimes: dict[str, datetime] = field(default_factory=dict)
    custom: list[tuple[str, Any]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


def _parse(entry: RequiredInput, value: Any) -> Any:
    try:
        if entry.kind == "DATE":
            return date.fromisoformat(str(value))
        if entry.kind == "DATETIME":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("offset")
            return parsed
        if entry.kind == "NUMBER":
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                raise ValueError("finite")
            return number
        text = str(value).strip()
        if not text or len(text) > MAX_TEXT:
            raise ValueError("text")
        return text
    except (TypeError, ValueError) as error:
        hint = " with its UTC offset, for example 2026-03-10T23:30:00+05:30" if entry.kind == "DATETIME" else ""
        raise ValueError(f"{entry.label}: enter a valid {entry.kind.lower()} value{hint}") from error


def resolve_enrolment_inputs(tx, schedule, version_id: UUID, supplied: dict[str, Any] | None,
                             deferred: list[str] | None) -> ResolvedInputs:
    supplied, deferred = dict(supplied or {}), list(dict.fromkeys(deferred or []))
    decisions = tx.schedules.list_decisions(version_id)
    effective = effective_inputs(schedule, decisions)[2]
    if not (effective and acknowledgement(decisions, inputs_hash(effective))):
        if supplied or deferred:
            raise ValueError("this schedule has no acknowledged list of required details to enter them against")
        return ResolvedInputs()
    by_code = {entry.code: entry for entry in effective}
    unknown = sorted((set(supplied) | set(deferred)) - set(by_code))
    if unknown:
        raise ValueError("unknown details: " + ", ".join(unknown))
    both = sorted(set(supplied) & set(deferred))
    if both:
        raise ValueError("a detail cannot be both entered and deferred: " + ", ".join(both))
    missing = sorted(set(by_code) - set(supplied) - set(deferred))
    if missing:
        raise ValueError("enter these details, or mark them 'will enter later': "
                         + ", ".join(by_code[code].label for code in missing))
    resolved = ResolvedInputs(enforced=True, deferred=deferred)
    for code, value in supplied.items():
        entry, parsed = by_code[code], _parse(by_code[code], value)
        if entry.anchor_code:
            resolved.anchor_dates[entry.anchor_code] = parsed.date() if isinstance(parsed, datetime) else parsed
            if isinstance(parsed, datetime):
                resolved.anchor_datetimes[entry.anchor_code] = parsed
        else:
            resolved.custom.append((code, parsed.isoformat() if isinstance(parsed, (date, datetime)) else parsed))
    return resolved
