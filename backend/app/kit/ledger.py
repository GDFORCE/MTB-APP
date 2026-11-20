"""What clean-up changed: a structural comparison of a model before and after ``normalize``.

``normalize`` validates the model the AI wrote. Whatever it removes or rewrites must be visible to
the reviewer, so instead of trusting every branch of it to leave a note, the two versions are
compared here and every difference that is not a plain default or type clean-up is reported.

``diff_models(before, after)`` returns a list of changes::

    {"path": "visits[c2d1].anchor", "kind": "dropped"|"changed"|"added", "before": ..., "after": ...}

Lists of objects that carry an ``id`` are compared by id, so a rule that disappears is one
"dropped" change, not a shift of every later rule.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Values that mean "nothing stated": filling one in, or swapping one for another, is not a change.
_EMPTY = (None, "", [], {}, False)


def _is_empty(value: Any) -> bool:
    return any(value == e and type(value) is type(e) for e in _EMPTY)


def _same_scalar(a: Any, b: Any) -> bool:
    """Equal after type clean-up: "3" and 3, 3.0 and 3, " x " and "x"."""
    if a == b and type(a) is type(b):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, (str, int, float)) and isinstance(b, (str, int, float)):
        sa, sb = str(a).strip(), str(b).strip()
        if sa == sb:
            return True
        try:
            return float(sa) == float(sb)
        except ValueError:
            return False
    return False


def _keyed(items: list[Any]) -> dict[str, Any] | None:
    """A list of objects with unique string ids, as {id: object}; None when it is not one."""
    if not items or not all(isinstance(x, dict) and x.get("id") not in (None, "") for x in items):
        return None
    out = {str(x["id"]): x for x in items}
    return out if len(out) == len(items) else None


def diff_models(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _walk(before, after, path, out)
    return out


def _walk(a: Any, b: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            sub = f"{path}.{key}" if path else str(key)
            if key not in b:
                if not _is_empty(a[key]):
                    out.append({"path": sub, "kind": "dropped", "before": a[key], "after": None})
            else:
                _walk(a[key], b[key], sub, out)
        for key in b:
            if key not in a and not _is_empty(b[key]):
                out.append({"path": f"{path}.{key}" if path else str(key), "kind": "added", "before": None, "after": b[key]})
        return
    if isinstance(a, list) and isinstance(b, list):
        ka, kb = _keyed(a), _keyed(b)
        if ka is not None and (kb is not None or not b):
            kb = kb or {}
            for key, item in ka.items():
                sub = f"{path}[{key}]"
                if key in kb:
                    _walk(item, kb[key], sub, out)
                else:
                    out.append({"path": sub, "kind": "dropped", "before": item, "after": None})
            for key, item in kb.items():
                if key not in ka:
                    out.append({"path": f"{path}[{key}]", "kind": "added", "before": None, "after": item})
            return
        if len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                _walk(x, y, f"{path}[{i}]", out)
            return
        # Plain lists of different length: report what left and what arrived.
        gone = [x for x in a if x not in b]
        new = [x for x in b if x not in a]
        if gone:
            out.append({"path": path, "kind": "dropped", "before": gone, "after": None})
        if new:
            out.append({"path": path, "kind": "added", "before": None, "after": new})
        return
    if _is_empty(a) and _is_empty(b):
        return
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        # A string procedure becoming {"name": ..., "if": []} is the same procedure.
        if isinstance(a, str) and isinstance(b, dict) and _same_scalar(a, b.get("name")):
            extra = {k: v for k, v in b.items() if k != "name" and not _is_empty(v)}
            if not extra:
                return
        if a != b:
            out.append({"path": path, "kind": "changed", "before": a, "after": b})
        return
    if not _same_scalar(a, b):
        out.append({"path": path, "kind": "added" if _is_empty(a) else "dropped" if _is_empty(b) else "changed",
                    "before": a, "after": b})


# ── from differences to points for the reviewer ──────────────────────────
# Differences that are type or default clean-up, not a loss of meaning.
_IGNORED_ENDS = (".follows_occurrence", ".optional", ".and_later", ".id", ".scope")
_IGNORED_PARTS = (".check.actions[", "review_questions[", "open_points", "schedule_table_pages", "event_check")
# pattern_rules: clean-up keeps every one (app/kit/patterns.py) and shows its own status.
_IGNORED_HEADS = ("notes", "review", "protocol", "arms", "pattern_rules")


def _ignored(change: dict[str, Any]) -> bool:
    path, before, after = change["path"], change["before"], change["after"]
    if change["kind"] == "added":
        return True  # a default filled in
    if path.split(".")[0].split("[")[0] in _IGNORED_HEADS or path.endswith(_IGNORED_ENDS) or any(x in path for x in _IGNORED_PARTS):
        return True
    if path.endswith(".need") and before == "required":
        return True
    if re.fullmatch(r"anchors\[[^\]]+\]\.kind", path) and (before, after) == ("relative", "ask"):
        return True  # a date with a range is entered per participant (normalize._dates_with_ranges)
    if re.search(r"rules\[[^\]]+\]\.if\[\d+\]\.(min_days|max_days)$", path):
        return True  # the limits moved inside "timing"
    if re.fullmatch(r"rules\[[^\]]+\]", path):
        return True  # reported with its reason by normalize (rule_left_out_point)
    if (isinstance(before, (int, float)) and isinstance(after, (int, float)) and not isinstance(before, bool)
            and abs(before) == abs(after)):
        return True  # a window written with a minus sign
    return False


def _label(model: dict[str, Any], path: str) -> tuple[str, list[str]]:
    """("visit W4", ["w4"]) for the object a path points into."""
    found = re.match(r"(events\[([^\]]+)\]\.)?(visits|anchors|rules|events|questions|measures|doses)\[([^\]]+)\]", path)
    if not found:
        return path.split(".")[0], []
    kind, oid = found.group(3), found.group(4)
    pool = model.get(kind) or []
    if found.group(2) and kind == "visits":
        event = next((e for e in model.get("events") or [] if e.get("id") == found.group(2)), {})
        pool = event.get("visits") or []
    obj = next((x for x in pool if isinstance(x, dict) and str(x.get("id")) == oid), {})
    name = obj.get("code") or obj.get("label") or obj.get("name") or oid
    return f"{kind[:-1]} {name}", [oid]


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 140 else text[:137] + "..."


def cleanup_points(before: Any, after: dict[str, Any]) -> list[dict[str, Any]]:
    """Open points for what clean-up removed or changed between two versions of a model."""
    points = []
    for change in diff_models(before, after):
        if _ignored(change):
            continue
        what, ids = _label(after if isinstance(after, dict) else {}, change["path"])
        field = change["path"].rsplit(".", 1)[-1] if "." in change["path"] else change["path"]
        old = _short(change["before"])
        whole = re.fullmatch(r"(events\[[^\]]+\]\.)?(visits|anchors|events|questions|measures|doses)\[[^\]]+\]", change["path"])
        if change["kind"] == "dropped" and whole:
            text = f"The AI wrote a {what.split(' ')[0]} that clean-up removed because it was incomplete: {old}."
        elif field == "anchor" and change["kind"] == "changed":
            text = (f"The {what} was to count from a date the model does not have (“{old}”), so it now counts from "
                    f"“{_short(change['after'])}”. Its dates are probably wrong until this is corrected.")
        elif change["kind"] == "dropped":
            text = (f"On the {what}, clean-up removed “{field}” as written by the AI ({old}): it names something the "
                    "model does not have or has a form the app cannot read.")
        else:
            text = f"On the {what}, clean-up changed “{field}” from {old} to {_short(change['after'])}."
        points.append({"kind": "cleanup", "stage": "cleanup", "text": text, "affects": ids,
                       "used": "The schedule uses the cleaned-up value."})
    return points


# What each kind of object may carry. Anything else the AI wrote is not used by the app, so the
# reviewer is told instead of it being stored and ignored.
KNOWN_KEYS: dict[str, set[str]] = {
    "model": {"protocol", "arms", "anchors", "questions", "visits", "events", "rules", "measures", "doses", "review_questions",
              "notes", "open_points", "schedule_table_pages", "review", "event_check", "pattern_rules",
              "phases", "study_events"},
    "anchor": {"id", "label", "kind", "relative_to", "offset_days", "is_day1", "day_zero", "set_by_visit", "question", "optional",
               "estimates", "note", "range", "last_of", "stop_is_last_dose", "last_with", "offset_stated", "source", "page", "quote", "scope", "date_basis"},
    "question": {"id", "label", "text", "scope", "ask_at", "kind", "options", "help", "default", "answer_roles", "source", "page", "quote"},
    "visit": {"id", "code", "name", "phase", "modality", "anchor", "offset_days", "window_before", "window_after", "follows",
              "follows_occurrence", "duration_days", "include_if", "repeat", "procedures", "timepoints", "condition", "source",
              "min_gap", "prep", "asap_if_past", "arrive_before", "check", "instructions", "window_stated", "count_from", "page", "quote",
              "combine_with"},
    "repeat": {"every_days", "count", "count_question", "start_n", "label_pattern", "code_pattern", "chain", "open_ended",
               "until_anchor", "overrides", "stages", "max_days", "max_delay_days", "delay_effect", "until_check", "adaptive"},
    "event": {"id", "label", "repeatable", "questions", "visits", "ends_schedule", "keeps_visits", "sets_anchor", "note",
              "site_actions", "check", "record_roles"},
    "rule": {"id", "label", "when", "if", "stop_visits", "stop_all", "same_day", "starts", "blocks", "allowed", "effect", "source",
             "set_answers", "pause", "keeps_visits", "dose_changes", "stop_procedures", "add_procedures", "quote", "page"},
}


def unused_fields(raw: Any) -> list[dict[str, Any]]:
    """Open points for fields the AI wrote that the app has no use for (looked for in the model as
    the AI returned it, before clean-up)."""
    if not isinstance(raw, dict):
        return []
    found: list[tuple[str, str, Any, list[str]]] = []

    def scan(obj: Any, kind: str, what: str, ids: list[str]) -> None:
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            if key not in KNOWN_KEYS[kind] and not _is_empty(value):
                found.append((what, key, value, ids))

    scan(raw, "model", "the model", [])
    for a in raw.get("anchors") or []:
        if isinstance(a, dict):
            scan(a, "anchor", f"the date “{a.get('label') or a.get('id')}”", [str(a.get("id"))])
    for q in raw.get("questions") or []:
        if isinstance(q, dict):
            scan(q, "question", f"the question “{q.get('label') or q.get('id')}”", [])
    visits = list(raw.get("visits") or [])
    for e in raw.get("events") or []:
        if isinstance(e, dict):
            scan(e, "event", f"the event “{e.get('label') or e.get('id')}”", [str(e.get("id"))])
            visits += list(e.get("visits") or [])
    for v in visits:
        if isinstance(v, dict):
            what = f"visit {v.get('code') or v.get('name') or v.get('id')}"
            scan(v, "visit", what, [str(v.get("id"))])
            scan(v.get("repeat"), "repeat", f"the repeat of {what}", [str(v.get("id"))])
    for r in raw.get("rules") or []:
        if isinstance(r, dict):
            scan(r, "rule", f"the rule “{r.get('label') or r.get('id')}”", [str(r.get("id"))])
    return [{"kind": "cleanup", "stage": "cleanup", "affects": ids,
             "text": f"The AI wrote “{key}” on {what}, which the app does not use: {_short(value)}.",
             "used": "It has no effect on the schedule. If it matters, it needs another way of being written."}
            for what, key, value, ids in found]
