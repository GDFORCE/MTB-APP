"""The before/after of a patient command (P5-04) - pure, shared by both planes.

``impact`` compares what the schedule showed with what the SAME evaluator now
produces, for every occurrence (changed AND unchanged), and keeps three values
apart: the protocol target/window, the operational appointment / override, and
the actual. ``semantic_digest`` is what preview and commit must agree on; it
contains no generated ids or timestamps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from .models import EvaluatedEvent

DIGEST_VERSION = "command-impact.v1"


def _d(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return (value.date() if isinstance(value, datetime) else value).isoformat()


@dataclass(frozen=True)
class OccurrenceView:
    logical_key: str
    event_code: str
    occurrence_index: int
    target: str | None
    earliest: str | None
    latest: str | None
    status: str | None
    explanation: dict[str, Any] = field(default_factory=dict)

    @property
    def operational(self) -> dict[str, Any]:
        return dict((self.explanation or {}).get("operational") or {})

    @property
    def track(self) -> dict[str, Any] | None:
        return (self.explanation or {}).get("track")


def view_of_evaluated(item: EvaluatedEvent) -> OccurrenceView:
    timing = item.timing
    return OccurrenceView(
        logical_key=f"{item.event_code}:{item.occurrence_index}", event_code=item.event_code,
        occurrence_index=item.occurrence_index,
        target=_d(timing.nominal_start) if timing else None,
        earliest=_d(timing.earliest) if timing else None,
        latest=_d(timing.latest) if timing else None,
        status=item.status.value if item.status else None,
        explanation={**item.explanation,
                     **({"booked": _d(item.booked_time)} if item.booked_time else {}),
                     **({"actual": _d(item.actual_time)} if item.actual_time else {})})


def view_of_record(
    logical_key: str, event_code: str, occurrence_index: int, target: date | None,
    earliest: date | None, latest: date | None, status: str | None, explanation: dict[str, Any] | None,
) -> OccurrenceView:
    return OccurrenceView(
        logical_key, event_code, occurrence_index, _d(target), _d(earliest), _d(latest), status,
        dict(explanation or {}))


def _operational_summary(view: OccurrenceView | None) -> dict[str, Any]:
    if view is None:
        return {}
    explanation = view.explanation or {}
    operational = view.operational
    booking = explanation.get("booking") or {}
    performed = explanation.get("performed") or {}
    override = operational.get("override")
    summary: dict[str, Any] = {}
    if booking:
        summary["appointment"] = {
            "date": booking.get("booked"), "outside_protocol_window": (
                None if booking.get("within_permitted") is None
                else not booking["within_permitted"]),
            "flag": booking.get("flag")}
    if override:
        summary["override"] = {
            key: override.get(key) for key in (
                "target_date", "window_earliest", "window_latest", "authority", "reason",
                "outside_protocol_window", "protocol_window", "command_id")}
    if performed:
        summary["actual"] = {"visit": performed.get("value")}
    if operational.get("cancelled"):
        summary["cancelled"] = operational["cancelled"]
    if operational.get("effective_date"):
        summary["effective_date"] = operational["effective_date"]
    return summary


def _reason(old: OccurrenceView | None, new: OccurrenceView | None, *, moved: bool,
            status_changed: bool, operational_changed: bool, command_label: str) -> str:
    track = (new.track if new else None) or (old.track if old else None) or {}
    if old is None:
        return "added by this change"
    if new is None:
        return "no longer scheduled"
    if moved:
        return track.get("reason") or "recalculated from the changed inputs"
    if status_changed:
        return f"status changed by {command_label}"
    if operational_changed:
        return "an operational value changed; the protocol target and window did not"
    if track.get("reason"):
        return f"unchanged: {track['reason']}"
    return "unchanged: this occurrence does not depend on the change"


def impact(
    before: Iterable[OccurrenceView], after: Iterable[OccurrenceView], *, command_label: str,
) -> list[dict[str, Any]]:
    old = {item.logical_key: item for item in before}
    new = {item.logical_key: item for item in after}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(old) | set(new), key=lambda k: (
            (new.get(k) or old[k]).event_code, (new.get(k) or old[k]).occurrence_index)):
        was, now = old.get(key), new.get(key)
        ref = now or was
        assert ref is not None
        moved = was is not None and now is not None and (
            (was.target, was.earliest, was.latest) != (now.target, now.earliest, now.latest))
        status_changed = was is not None and now is not None and was.status != now.status
        before_op, after_op = _operational_summary(was), _operational_summary(now)
        operational_changed = before_op != after_op
        changed = was is None or now is None or moved or status_changed or operational_changed
        kind = ("ADDED" if was is None else "REMOVED" if now is None
                else "PROTOCOL_TARGET" if moved else "STATUS" if status_changed
                else "OPERATIONAL" if operational_changed else "NONE")
        rows.append({
            "logical_key": key, "event_code": ref.event_code,
            "occurrence_index": ref.occurrence_index, "changed": changed, "change_kind": kind,
            "protocol": {
                "old_target": was.target if was else None, "new_target": now.target if now else None,
                "old_window": {"earliest": was.earliest if was else None,
                               "latest": was.latest if was else None},
                "new_window": {"earliest": now.earliest if now else None,
                               "latest": now.latest if now else None},
            },
            "status": {"old": was.status if was else None, "new": now.status if now else None},
            "operational": {"before": before_op, "after": after_op},
            "track": (now.track if now else None) or (was.track if was else None),
            "reason": _reason(was, now, moved=moved, status_changed=status_changed,
                              operational_changed=operational_changed,
                              command_label=command_label),
        })
    return rows


def notification_intents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the visit-reminder projection would create / update / cancel. Intent
    only: nothing is sent from here and the medication-reminder system is separate."""
    intents: list[dict[str, Any]] = []
    for row in rows:
        if not row["changed"]:
            continue
        after = row["operational"]["after"]
        before = row["operational"]["before"]
        new_date = after.get("effective_date") or (after.get("appointment") or {}).get("date") \
            or row["protocol"]["new_target"]
        old_date = before.get("effective_date") or (before.get("appointment") or {}).get("date") \
            or row["protocol"]["old_target"]
        status = row["status"]["new"]
        if status in ("CANCELLED", "MISSED", "COMPLETED") or row["change_kind"] == "REMOVED":
            if old_date is not None and status != "COMPLETED":
                intents.append({"action": "CANCEL", "logical_key": row["logical_key"],
                                "old_date": old_date, "new_date": None})
            continue
        if new_date is None:
            continue
        if old_date is None:
            intents.append({"action": "CREATE", "logical_key": row["logical_key"],
                            "old_date": None, "new_date": new_date})
        elif old_date != new_date:
            intents.append({"action": "UPDATE", "logical_key": row["logical_key"],
                            "old_date": old_date, "new_date": new_date})
    return intents


def _without_ids(value: Any) -> Any:
    """Drop generated identifiers (command / fact ids) and timestamps, recursively."""
    if isinstance(value, dict):
        return {key: _without_ids(item) for key, item in value.items()
                if key not in {"command_id", "fact", "recorded_at"}}
    if isinstance(value, list):
        return [_without_ids(item) for item in value]
    return value


def semantic_digest(rows: list[dict[str, Any]], intents: list[dict[str, Any]]) -> str:
    """What the calculation says, without any generated id or timestamp."""
    rows = _without_ids(rows)
    stable = [{
        "key": row["logical_key"], "protocol": row["protocol"], "status": row["status"],
        "operational": row["operational"],
        "track": (row["track"] or {}).get("code"), "kind": row["change_kind"],
    } for row in rows]
    encoded = json.dumps(
        {"version": DIGEST_VERSION, "rows": stable, "intents": intents},
        sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_payload_hash(*parts: Any) -> str:
    encoded = json.dumps(list(parts), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
