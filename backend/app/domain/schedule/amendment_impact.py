"""What moving one patient from V1 to V2 does to their visits, mapping-aware (P6-03/04).

Pure functions over plain values. The patient service supplies the stored V1 projection,
the V2 evaluation and the reviewed mappings; this module decides, per visit:

* UNCHANGED / MOVED / STATUS_CHANGED - the same logical visit, translated through the
  mapping (a renamed or split visit is matched by its mapping, never by a shared code);
* PROTECTED - completed, missed or actual-dated history: kept exactly as recorded;
* ADDED - a visit the new version brings (including every unmatched V2 occurrence);
* REMOVED - a visit with no counterpart; its history stays on V1, on purpose;
* WAITING / BLOCKED - the new version cannot date it yet / cannot schedule it.

Nothing here writes anything.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Iterable

from .amendment import Translation, canonical_json, sha256_text

WAITING_STATUSES = frozenset({
    "WAITING_FOR_ANCHOR", "WAITING_FOR_CONDITION", "WAITING_FOR_ASSIGNMENT", "UNRESOLVED",
    "AVAILABLE_ON_DEMAND"})
BLOCKED_STATUSES = frozenset({"BLOCKED", "PAUSED"})
CLOSED_STATUSES = frozenset({"COMPLETED", "MISSED", "CANCELLED"})

IMPACT_CATEGORIES = (
    "UNCHANGED", "MOVED", "STATUS_CHANGED", "PROTECTED", "ADDED", "REMOVED", "WAITING", "BLOCKED")


def _date(value: Any) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _key(code: str, index: int) -> str:
    return f"{code}:{index}"


def _window(row: Any) -> tuple[str | None, str | None]:
    return _iso(_date(row.earliest_date)), _iso(_date(row.latest_date))


def build_visit_impacts(
    prior: Iterable[Any], final: Iterable[Any],
    translate: Callable[[str, int], tuple[Translation, tuple[str, int] | None, str | None]],
    *, provisional: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """What the move does to each visit, read from the FINAL RECONCILED projection.

    ``prior`` are the patient's CURRENT stored projection rows (``PatientEventRecord``);
    ``final`` are the rows reconciliation will PERSIST for the destination version
    (``PatientScheduleService.reconcile_projection`` - protected history, effective-point
    boundaries, additions, cancellations and statuses already applied), so the preview shows
    exactly what the commit writes and never a raw evaluation that persistence would
    override. ``translate`` maps a prior (code, index) to its destination (forward through
    the mapping for an adoption, the unique inverse for a return).

    Returns ``(impacts, blockers)``. A prior visit whose destination cannot be decided
    (unreviewed, ambiguous or missing mapping) is a blocker and a BLOCKED impact - never
    silently treated as removed. ``provisional`` marks every still-open (not protected)
    result as depending on an effective point that is not known yet."""
    final_by_key = {row.logical_key: row for row in final}
    used: set[str] = set()
    impacts: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []

    for old in sorted(prior, key=lambda row: (row.event_code, row.occurrence_index)):
        from_key = _key(old.event_code, old.occurrence_index)
        history = old.status in CLOSED_STATUSES or bool(old.protected_history)
        kind, target, reason = translate(old.event_code, old.occurrence_index)
        old_window = _window(old)
        base = {
            "from_key": from_key, "to_key": None, "from_code": old.event_code,
            "old_target": _iso(old.nominal_start_date), "old_earliest": old_window[0],
            "old_latest": old_window[1], "old_status": old.status,
            "new_target": None, "new_earliest": None, "new_latest": None, "new_status": None,
            "protected": history, "provisional": False, "reason": ""}
        if kind == Translation.UNRESOLVED:
            blockers.append({"code": "VISIT_MAPPING_UNRESOLVED", "path": from_key,
                             "detail": reason or "no destination"})
            impacts.append({**base, "category": "BLOCKED", "reason": reason or "no destination"})
            continue
        if kind == Translation.NONE:
            impacts.append({
                **base, "category": "REMOVED",
                "reason": "the new version no longer has this visit; its history stays on the "
                          "earlier version"})
            continue
        assert target is not None
        to_key = _key(*target)
        used.add(to_key)
        base["to_key"], base["to_code"] = to_key, target[0]
        new = final_by_key.get(to_key)
        if new is None:
            impacts.append({**base, "category": "PROTECTED" if history else "REMOVED",
                            "reason": ("completed history is kept" if history
                                       else "the new version no longer produces this occurrence")})
            continue
        new_window = _window(new)
        base.update(new_target=_iso(new.nominal_start_date), new_earliest=new_window[0],
                    new_latest=new_window[1], new_status=new.status)
        change = ((new.generation_reason or {}).get("reconciliation") or {}).get("change")
        if new.protected_history:
            impacts.append({
                **base, "category": "PROTECTED", "protected": True,
                "reason": ("recorded history is kept as it happened" if history else
                           "this visit falls before the adoption point, so it keeps its "
                           "current date and status")})
            continue
        if change == "CANCELLED" or new.status == "CANCELLED":
            impacts.append({**base, "category": "REMOVED",
                            "reason": "the new version no longer produces this occurrence"})
            continue
        unchanged_by_the_move = (
            new.status == old.status and base["new_target"] == base["old_target"]
            and new_window == old_window)
        if unchanged_by_the_move:
            # a visit that was waiting or blocked before stays so for the same reason: that
            # is not something the move did, so it is not reported as a new problem
            category, why = "UNCHANGED", ""
        elif new.status in BLOCKED_STATUSES:
            category, why = "BLOCKED", f"the new version holds this visit ({new.status})"
        elif new.status in WAITING_STATUSES and new.nominal_start_date is None:
            category, why = "WAITING", f"the new version cannot date this yet ({new.status})"
        elif base["new_target"] != base["old_target"]:
            category, why = "MOVED", "the new version dates this visit differently"
        elif new_window != old_window:
            category, why = "MOVED", "the allowed window changed"
        else:
            category, why = "STATUS_CHANGED", f"status changes from {old.status} to {new.status}"
        impacts.append({**base, "category": category, "reason": why,
                        "provisional": provisional and not old.protected_history})

    for key in sorted(final_by_key.keys() - used):
        new = final_by_key[key]
        window = _window(new)
        impacts.append({
            "from_key": None, "to_key": key, "from_code": None, "to_code": new.event_code,
            "old_target": None, "old_earliest": None, "old_latest": None, "old_status": None,
            "new_target": _iso(new.nominal_start_date), "new_earliest": window[0],
            "new_latest": window[1], "new_status": new.status, "protected": False,
            "provisional": provisional,
            "category": ("BLOCKED" if new.status in BLOCKED_STATUSES else
                         "WAITING" if new.status in WAITING_STATUSES else "ADDED"),
            "reason": "a visit the new version brings"})
    impacts.sort(key=lambda row: (row["to_key"] or row["from_key"] or "", row["from_key"] or ""))
    return impacts, blockers


def impact_summary(impacts: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in IMPACT_CATEGORIES}
    for row in impacts:
        counts[row["category"]] += 1
    return counts


def impact_signature(impacts: Iterable[dict[str, Any]], *, strict: bool) -> dict[str, str]:
    """A comparable digest of an impact, per visit.

    ``strict`` (SAME_IMPACT): category, new target and new status - any difference is a
    different impact. Otherwise (SAME_STRUCTURE): only WHICH visits correspond - the same
    earlier visit still maps to the same later visit, and the same visits are added or
    removed. How a visit is dated or whether it is waiting, blocked or already performed is
    the expected course of time for a decision that waits for its point, not a different
    impact (a hard blocker is checked separately at execution)."""
    signature: dict[str, str] = {}
    for row in impacts:
        key = row["to_key"] or row["from_key"]
        category = row["category"]
        if strict:
            value = "|".join(str(item) for item in (
                category, row["new_target"], row["new_earliest"], row["new_latest"],
                row["new_status"], row["protected"]))
        else:
            value = category if category in {"ADDED", "REMOVED"} else "PRESENT"
        signature[f"{row['from_key'] or '-'}>{key}"] = value
    return signature


def signature_difference(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def binding_hash(bindings: dict[str, Any]) -> str:
    return sha256_text(canonical_json(bindings))


def binding_differences(approved: dict[str, Any], current: dict[str, Any]) -> list[str]:
    return sorted(key for key in approved.keys() | current.keys()
                  if canonical_json(approved.get(key)) != canonical_json(current.get(key)))
