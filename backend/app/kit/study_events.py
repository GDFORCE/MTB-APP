"""Study-wide events: one confirmed fact about the whole study, applied to the participants it concerns.

A study event ("the final participant in the study completed Visit 6") is confirmed once by an
authorised study-level user, with the date it actually happened (``effective``) kept apart from
when it was confirmed. The approved workflow says who it concerns and what changes for them::

    "phases": [{"id": "extension", "label": "Extension follow-up",
                "starts": {"visit": "v7"}, "ends": [{"visit": "v8"}]}],
    "study_events": [{"id": "final_v6", "label": "...", "confirm_roles": ["sponsor", "cro"],
                      "fields": [{"id": "final_subject", "label": "Final participant", "kind": "subject"}],
                      "actions": [{"id": "final", "audience": {"field": "final_subject"}, "supersede_visits": ["v8"]},
                                  {"id": "close", "audience": {"phase": "extension"}, "supersede_visits": ["v8"],
                                   "add_visit": {"id": "early_eos", "code": "EOS-E", "name": "Early closeout",
                                                 "date": "site_booking", ...}}],
                      "review_phases": ["safety"]}]

Phases are read from recorded facts only (a recorded visit date, a logged event, an entered date),
as they stood on the effective date, so a late confirmation selects the participants who were in
extension then. Where the facts are missing (a visit whose window closed before the event has no
recorded date) the participant is not guessed into or out of the audience: they are listed for
resolution. The first action whose audience matches applies; ``review_phases`` lists phases whose
participants get no action but are shown for review.

Everything here is pure; ``kit_routes`` stores the effects and the engine applies them.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .normalize import norm_visit

STUDY_ROLES = ("sponsor", "cro", "pi", "admin")
FIELD_KINDS = ("subject", "text", "date")
ID = re.compile(r"^[A-Za-z][\w-]{0,59}$")


def _arr(x: Any) -> list:
    return x if isinstance(x, list) else []


def _s(x: Any) -> str:
    return "" if x is None else str(x)


def _num(x: Any) -> float | None:
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fact(x: Any, ids: dict[str, set[str]]) -> dict[str, str] | None:
    if not isinstance(x, dict):
        return None
    for kind in ("visit", "event", "anchor"):
        if x.get(kind) is not None and _s(x[kind]) in ids[kind]:
            return {kind: _s(x[kind])}
    return None


def norm_phases(items: Any, ids: dict[str, set[str]]) -> list[dict[str, Any]]:
    out = []
    for p in _arr(items):
        if not isinstance(p, dict) or not ID.match(_s(p.get("id"))):
            continue
        start = _fact(p.get("starts"), ids)
        if start is None or any(x["id"] == p["id"] for x in out):
            continue
        ends = [f for f in (_fact(x, ids) for x in _arr(p.get("ends"))) if f]
        out.append({"id": _s(p["id"]), "label": _s(p.get("label") or p["id"])[:120], "starts": start, "ends": ends})
    return out


def norm_study_events(items: Any, *, visit_ids: set[str], phase_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(cleaned events, open points). An event whose actions cannot run is kept with ``blocked``."""
    out: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    for e in _arr(items):
        if not isinstance(e, dict) or not ID.match(_s(e.get("id"))) or any(x["id"] == e["id"] for x in out):
            continue
        problems: list[str] = []
        roles = [r for r in dict.fromkeys(_s(x).lower() for x in _arr(e.get("confirm_roles"))) if r in STUDY_ROLES]
        fields = []
        for f in _arr(e.get("fields")):
            if isinstance(f, dict) and ID.match(_s(f.get("id"))) and f.get("kind") in FIELD_KINDS:
                fields.append({"id": _s(f["id"]), "label": _s(f.get("label") or f["id"])[:120], "kind": f["kind"]})
        field_ids = {f["id"] for f in fields}
        actions = []
        for i, a in enumerate(_arr(e.get("actions"))):
            if not isinstance(a, dict):
                continue
            aid = _s(a.get("id")) if ID.match(_s(a.get("id"))) else f"action{i + 1}"
            aud = a.get("audience") if isinstance(a.get("audience"), dict) else {}
            if _s(aud.get("phase")) in phase_ids:
                audience = {"phase": _s(aud["phase"])}
            elif _s(aud.get("field")) in field_ids:
                audience = {"field": _s(aud["field"])}
            else:
                problems.append(f"action “{a.get('label') or aid}” names no phase or confirmed field that the model has")
                continue
            supersede = [x for x in dict.fromkeys(_s(v) for v in _arr(a.get("supersede_visits"))) if x in visit_ids]
            unknown = [x for x in (_s(v) for v in _arr(a.get("supersede_visits"))) if x and x not in visit_ids]
            if unknown:
                problems.append(f"action “{a.get('label') or aid}” supersedes visits the model does not have ({', '.join(unknown)})")
            add = None
            if a.get("add_visit") is not None:
                add = norm_visit(a["add_visit"], 0, f"{e['id']}_{aid}_")
                if add is None or add["id"] in visit_ids or not ID.match(add["id"]):
                    problems.append(f"action “{a.get('label') or aid}” adds a visit without its own id, code and name")
                    add = None
                elif a["add_visit"].get("date") != "site_booking":
                    problems.append(f"the visit action “{a.get('label') or aid}” adds must be dated by the site's booking "
                                    "(date \"site_booking\"); other dating is not supported")
                    add = None
                else:
                    add.update(date="site_booking", repeat=None, follows=None, include_if=[])
                    add.pop("combine_with", None)
            if not supersede and add is None:
                problems.append(f"action “{a.get('label') or aid}” changes nothing")
                continue
            actions.append({"id": aid, "label": _s(a.get("label") or aid)[:160], "audience": audience,
                            "supersede_visits": supersede, "add_visit": add,
                            "effect": _s(a.get("effect")).strip()[:300] or None})
        if not actions:
            problems.append("it has no action the app can apply")
        event: dict[str, Any] = {
            "id": _s(e["id"]), "label": _s(e.get("label") or e["id"])[:160],
            "confirm_roles": roles or ["sponsor", "cro"], "fields": fields, "actions": actions,
            "review_phases": [p for p in dict.fromkeys(_s(x) for x in _arr(e.get("review_phases"))) if p in phase_ids],
        }
        for key in ("source", "quote"):
            if _s(e.get(key)).strip():
                event[key] = _s(e[key]).strip()[:600]
        page = _num(e.get("page"))
        if page is not None and page >= 1:
            event["page"] = int(page)
        if e.get("detect") not in (None, False, {}, []):
            # Automatic detection needs study-wide enrolment closure and per-site completeness data
            # the app does not hold; the study team confirms the event instead.
            points.append({"id": f"study_event_detect_{e['id']}", "kind": "cannot_represent", "stage": "cleanup",
                           "affects": [x for a in actions for x in a["supersede_visits"]],
                           "text": f"“{event['label']}” is described as detected automatically. The app cannot detect it: "
                                   "it needs an authorised study user to confirm it with the date it happened.",
                           "quote": event.get("quote") or "", "page": event.get("page"),
                           "used": "The event is applied only after a study user confirms it."})
        if problems:
            event["blocked"] = "; ".join(dict.fromkeys(problems))
            points.append({"id": f"study_event_{e['id']}", "kind": "cannot_represent", "stage": "cleanup",
                           "affects": [x for a in actions for x in a["supersede_visits"]],
                           "text": f"The study event “{event['label']}” cannot be applied: {event['blocked']}.",
                           "quote": event.get("quote") or "", "page": event.get("page"),
                           "used": "Confirming it changes no calendar until the workflow is completed and approved."})
        out.append(event)
    return out, points


# ── reading a participant's record as of a date ──────────────────────────────

def _d(text: Any) -> date | None:
    try:
        return date.fromisoformat(_s(text)) if text else None
    except ValueError:
        return None


def _start_n(v: dict[str, Any]) -> int:
    n = _num((v.get("repeat") or {}).get("start_n"))
    return int(n) if n is not None else 1


def anchor_date(model: dict[str, Any], kit: dict[str, Any], aid: str, depth: int = 0) -> date | None:
    anchors = {a["id"]: a for a in model.get("anchors") or []}
    a = anchors.get(aid)
    if not a or depth > 20:
        return None
    if a["kind"] == "start":
        return _d(kit.get("startDate"))
    entered = _d((kit.get("answers") or {}).get("@" + aid))
    if entered:
        return entered
    if a.get("set_by_visit"):
        v = next((x for x in model.get("visits") or [] if x["id"] == a["set_by_visit"]), None)
        done = _d((kit.get("actuals") or {}).get(f"{a['set_by_visit']}#{_start_n(v or {})}"))
        if done:
            return done
    if a["kind"] == "relative" and a.get("relative_to"):
        base = anchor_date(model, kit, a["relative_to"], depth + 1)
        return base + timedelta(days=round(a.get("offset_days") or 0)) if base else None
    return None


def visit_plan(model: dict[str, Any], kit: dict[str, Any], vid: str, depth: int = 0) -> tuple[date | None, date | None]:
    """(recorded or planned date, end of its window) of a scheduled visit's first occurrence.
    (None, None) when the server cannot work it out (the engine on the device can)."""
    v = next((x for x in model.get("visits") or [] if x["id"] == vid), None)
    if not v or depth > 20:
        return None, None
    actual = _d((kit.get("actuals") or {}).get(f"{vid}#{_start_n(v)}"))
    if actual:
        return actual, actual
    if v.get("follows"):
        base, _ = visit_plan(model, kit, v["follows"], depth + 1)
    else:
        base = anchor_date(model, kit, v.get("anchor") or "")
    if base is None:
        return None, None
    planned = base + timedelta(days=round(v.get("offset_days") or 0))
    return planned, planned + timedelta(days=round(v.get("window_after") or 0))


def _fact_date(model: dict[str, Any], kit: dict[str, Any], fact: dict[str, str]) -> date | None:
    if "visit" in fact:
        v = next((x for x in model.get("visits") or [] if x["id"] == fact["visit"]), {})
        return _d((kit.get("actuals") or {}).get(f"{fact['visit']}#{_start_n(v)}"))
    if "event" in fact:
        dates = [_d(e.get("date")) for e in kit.get("events") or [] if e.get("type") == fact["event"]]
        dates = [x for x in dates if x]
        return min(dates) if dates else None
    return anchor_date(model, kit, fact["anchor"])


def _missing(model: dict[str, Any], kit: dict[str, Any], fact: dict[str, str], at: date) -> str | None:
    """Why a visit fact that should have a recorded date by `at` has none, or None."""
    if "visit" not in fact:
        return None
    planned, closes = visit_plan(model, kit, fact["visit"])
    if planned is None or _fact_date(model, kit, fact) or closes is None or closes >= at:
        return None
    v = next((x for x in model.get("visits") or [] if x["id"] == fact["visit"]), {})
    return f"{v.get('code') or fact['visit']} has no recorded date, but its window closed on {closes.isoformat()}"


def phase_at(model: dict[str, Any], kit: dict[str, Any], at: date) -> dict[str, Any]:
    """{"phase": id or None, "unresolved": reason or None} from the facts recorded for `at`.
    Facts recorded with a date after `at` count as not yet happened then."""
    current, started_on, missing = None, None, []
    for p in model.get("phases") or []:
        start = _fact_date(model, kit, p["starts"])
        if start is None or start > at:
            reason = _missing(model, kit, p["starts"], at)
            if reason:
                missing.append(reason)
            continue
        ends = [x for x in (_fact_date(model, kit, f) for f in p["ends"]) if x]
        if any(x <= at for x in ends):
            continue
        missing.extend(r for r in (_missing(model, kit, f, at) for f in p["ends"]) if r)
        if started_on is None or start >= started_on:
            current, started_on = p["id"], start
    if missing:
        return {"phase": None, "unresolved": "; ".join(dict.fromkeys(missing))}
    return {"phase": current, "unresolved": None}


def ending(model: dict[str, Any], kit: dict[str, Any]) -> tuple[date, str] | None:
    """The first logged event that ends participation, with its label."""
    labels = {e["id"]: e["label"] for e in model.get("events") or [] if e.get("ends_schedule")}
    found = sorted((d, labels[e["type"]]) for e in kit.get("events") or []
                   if e.get("type") in labels and (d := _d(e.get("date"))))
    return found[0] if found else None


def desired_effect(definition: dict[str, Any] | None, model: dict[str, Any], patient: dict[str, Any],
                   event: dict[str, Any]) -> dict[str, Any]:
    """What the event means for one participant: state "applied" (with its action and changes),
    "excluded", "unresolved" (data to complete), "to_review" (listed, no action), "unmatched" (their
    workflow version has no action), "already_completed" or "ended" (nothing left to change)."""
    kit = patient.get("kit") or {}
    at = date.fromisoformat(event["effective_date"])
    if definition is None:
        return {"state": "unmatched", "reason": "Their workflow version has no action for this study event."}
    if definition.get("blocked"):
        return {"state": "unmatched", "reason": f"Their workflow version cannot apply it: {definition['blocked']}."}
    stop = ending(model, kit)
    if stop and stop[0] <= at:
        return {"state": "excluded", "reason": f"{stop[1]} on {stop[0].isoformat()}, before the event."}
    subject = {patient.get("id"), patient.get("subject_id")} - {None, ""}
    action, phase = None, None
    for a in definition["actions"]:
        if "field" in a["audience"] and _s((event.get("fields") or {}).get(a["audience"]["field"])).strip() in subject:
            action = a
            break
    if action is None:
        found = phase_at(model, kit, at)
        if found["unresolved"]:
            return {"state": "unresolved", "reason": found["unresolved"]}
        phase = found["phase"]
        action = next((a for a in definition["actions"] if a["audience"].get("phase") == phase and phase), None)
        if action is None:
            label = next((p["label"] for p in model.get("phases") or [] if p["id"] == phase), None)
            where = f"in {label.lower()}" if label else "not in any listed phase"
            if phase in definition.get("review_phases", []):
                return {"state": "to_review", "phase": phase,
                        "reason": f"{where.capitalize()} on the effective date; the workflow gives no action. Review."}
            return {"state": "excluded", "phase": phase, "reason": f"{where.capitalize()} on the effective date."}
    # Closed out already: every one-off visit the action replaces (the routine end visit) is recorded.
    # Recorded occurrences of a repeating visit only mean treatment went on.
    by_id = {v["id"]: v for v in model.get("visits") or []}
    single = [v for v in action["supersede_visits"] if v in by_id and not by_id[v].get("repeat")]
    done = [by_id[v].get("code") or v for v in single if (kit.get("actuals") or {}).get(f"{v}#{_start_n(by_id[v])}")]
    if single and len(done) == len(single):
        return {"state": "already_completed", "action": action["id"], "phase": phase,
                "reason": f"{', '.join(done)} was already recorded as done; nothing is replaced."}
    if stop:
        return {"state": "ended", "action": action["id"], "phase": phase,
                "reason": f"{stop[1]} on {stop[0].isoformat()}, after the event; nothing is added."}
    changes = [{"visit": v, "change": "superseded"} for v in action["supersede_visits"]]
    if action.get("add_visit"):
        changes.append({"key": f"se.{event['id']}.{action['add_visit']['id']}#1", "change": "added"})
    return {"state": "applied", "action": action["id"], "phase": phase, "changes": changes,
            "reason": action["label"]}


def definition_for(model: dict[str, Any] | None, event_type: str) -> dict[str, Any] | None:
    return next((e for e in (model or {}).get("study_events") or [] if e["id"] == event_type), None)
