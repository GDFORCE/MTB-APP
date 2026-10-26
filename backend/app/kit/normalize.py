"""Step 5 of the kit: clean and validate the model's JSON, and apply review answers."""

from __future__ import annotations

import copy
import math
from typing import Any


def arr(x: Any) -> list:
    return x if isinstance(x, list) else []


def num(x: Any, d: float = 0) -> float:
    if x is None or x == "" or isinstance(x, bool):
        return d
    try:
        value = float(x)
    except (TypeError, ValueError):
        return d
    if not math.isfinite(value):
        return d
    return int(value) if value == int(value) else value


def _s(x: Any) -> str:
    return "" if x is None else str(x)


def norm_proc(p: Any) -> dict[str, Any] | None:
    if isinstance(p, str):
        return {"name": p.strip(), "if": []} if p.strip() else None
    if isinstance(p, dict):
        name = _s(p.get("name") if p.get("name") is not None else p.get("procedure")).strip()
        return {"name": name, "if": arr(p.get("if"))} if name else None
    return None


def _procs(items: Any) -> list[dict[str, Any]]:
    return [p for p in map(norm_proc, arr(items)) if p]


def norm_q(q: Any, scope_default: str) -> dict[str, Any] | None:
    if not isinstance(q, dict) or not q.get("id") or not q.get("text"):
        return None
    kind = q.get("kind") if q.get("kind") in ("choice", "boolean", "number") else "choice"
    options = []
    for o in arr(q.get("options")):
        if isinstance(o, dict):
            value = o.get("value") if o.get("value") is not None else o.get("label")
            label = o.get("label") if o.get("label") is not None else o.get("value")
            options.append({"value": _s(value), "label": _s(label)})
        else:
            options.append({"value": _s(o), "label": _s(o)})
    if kind == "choice" and not options:
        return None
    return {
        **q, "id": _s(q["id"]), "label": _s(q.get("label") or q["id"]), "text": _s(q["text"]),
        "help": _s(q["help"]) if q.get("help") else "", "kind": kind, "options": options,
        "scope": q.get("scope") if q.get("scope") in ("study", "decision", "participant") else scope_default,
        "ask_at": q["ask_at"] if isinstance(q.get("ask_at"), dict) else None,
    }


def norm_visit(v: Any, i: int, prefix: str) -> dict[str, Any] | None:
    if not isinstance(v, dict) or not (v.get("name") or v.get("code")):
        return None
    r = dict(v["repeat"]) if isinstance(v.get("repeat"), dict) else None
    if r is not None:
        r["every_days"] = num(r.get("every_days"), 0)
        r["chain"] = bool(r.get("chain"))
        r["open_ended"] = bool(r.get("open_ended"))
        overrides = []
        for o in arr(r.get("overrides")):
            if isinstance(o, dict) and isinstance(o.get("n"), list):
                removed = []
                for x in arr(o.get("remove")):
                    name = x if isinstance(x, str) else (x or {}).get("name") if isinstance(x, dict) else None
                    if name:
                        removed.append(name)
                overrides.append({
                    "n": [num(n) for n in o["n"]], "add": _procs(o.get("add")), "remove": removed,
                    "note": _s(o["note"]) if o.get("note") else None,
                })
        r["overrides"] = overrides
    timepoints = []
    for tp in arr(v.get("timepoints")):
        if isinstance(tp, dict) and tp.get("label"):
            timepoints.append({
                "label": _s(tp["label"]), "offset_min": num(tp.get("offset_min")),
                "window_before_min": abs(num(tp.get("window_before_min"))),
                "window_after_min": abs(num(tp.get("window_after_min"))),
                "procedures": _procs(tp.get("procedures")),
            })
    duration = (abs(num(v["duration_days"])) or None) if v.get("duration_days") else None
    return {
        **v, "id": _s(v.get("id") or f"{prefix}{i}"), "code": _s(v.get("code") or f"V{i + 1}"),
        "name": _s(v.get("name") or v.get("code")), "phase": _s(v.get("phase") or "Visit"),
        "modality": v.get("modality") if v.get("modality") in ("site", "phone", "home", "remote") else "site",
        "offset_days": num(v.get("offset_days")), "window_before": abs(num(v.get("window_before"))),
        "window_after": abs(num(v.get("window_after"))), "duration_days": duration,
        "follows": _s(v["follows"]) if v.get("follows") else None,
        "follows_occurrence": "last" if v.get("follows_occurrence") == "last" else None,
        "include_if": arr(v.get("include_if")), "procedures": _procs(v.get("procedures")),
        "repeat": r, "timepoints": timepoints,
        "condition": _s(v["condition"]) if v.get("condition") else None,
    }


def _opt_num(x: Any) -> float | None:
    return None if x is None or x == "" or isinstance(x, bool) else num(x, None)  # type: ignore[arg-type]


def _in_values(c: dict[str, Any]) -> list[str] | None:
    values = c.get("in")
    values = values if isinstance(values, list) else [values] if values not in (None, "") else []
    out = [_s(v) for v in values if v is not None and _s(v) != ""]
    return out or None


def norm_rule_if(conds: Any, trigger: dict[str, Any], q_ids: set[str], events: list[dict[str, Any]],
                 a_ids: set[str], v_ids: set[str]) -> list[dict[str, Any]] | None:
    """A rule's conditions ("if", all must hold), checked. None when any condition is unusable:
    the rule is then dropped, because keeping it without the condition would make it act for
    every branch of its event."""
    out: list[dict[str, Any]] = []
    event = next((e for e in events if e["id"] == trigger.get("event")), None)
    event_q = {q["id"] for q in (event or {}).get("questions", [])}
    for c in arr(conds):
        if not isinstance(c, dict):
            return None
        if "occurrence" in c:
            n = _opt_num(c.get("occurrence"))
            if event is None or n is None or n < 1 or n != int(n):
                return None
            out.append({"occurrence": int(n)})
        elif "event_question" in c:
            values = _in_values(c)
            if event is None or _s(c["event_question"]) not in event_q or values is None:
                return None
            out.append({"event_question": _s(c["event_question"]), "in": values})
        elif "timing" in c:
            t = c["timing"] if isinstance(c.get("timing"), dict) else {}
            target = ({"anchor": _s(t["anchor"])} if _s(t.get("anchor")) in a_ids
                      else {"visit": _s(t["visit"])} if _s(t.get("visit")) in v_ids else None)
            lo, hi = _opt_num(t.get("min_days")), _opt_num(t.get("max_days"))
            if target is None or (lo is None and hi is None):
                return None
            n = _opt_num(t.get("n"))
            out.append({"timing": {**target, "n": int(n) if n is not None and "visit" in target else None,
                                   "min_days": lo, "max_days": hi}})
        elif "question" in c:
            values = _in_values(c)
            if _s(c["question"]) not in q_ids or values is None:
                return None
            out.append({"question": _s(c["question"]), "in": values})
        else:
            return None
    return out


def norm_rules(items: Any, a_ids: set[str], events: list[dict[str, Any]], v_ids: set[str],
               q_ids: set[str] | None = None, notes: list[str] | None = None) -> list[dict[str, Any]]:
    """What happens to the schedule if something happens. A rule whose trigger is unknown is dropped;
    references to unknown visits, anchors or rules are removed rather than guessed."""
    e_ids = {e["id"] for e in events}
    rules: list[dict[str, Any]] = []
    for i, r in enumerate(arr(items)):
        if not isinstance(r, dict):
            continue
        when: dict[str, Any] = r["when"] if isinstance(r.get("when"), dict) else {}
        if _s(when.get("anchor")) in a_ids:
            trigger = {"anchor": _s(when["anchor"])}
        elif _s(when.get("event")) in e_ids:
            trigger = {"event": _s(when["event"])}
        else:
            continue
        allowed = r.get("allowed") if isinstance(r.get("allowed"), dict) else None
        if allowed is not None:
            rel = _s(allowed.get("relative_to"))
            lo, hi = _opt_num(allowed.get("min_days")), _opt_num(allowed.get("max_days"))
            not_on = _s(allowed.get("not_on_visit"))
            allowed = {
                "relative_to": rel if rel in a_ids else None,
                "min_days": lo if rel in a_ids else None, "max_days": hi if rel in a_ids else None,
                "not_on_visit": not_on if not_on in v_ids else None,
            }
            if allowed["min_days"] is None and allowed["max_days"] is None and allowed["not_on_visit"] is None:
                allowed = None
        conditions = norm_rule_if(r.get("if"), trigger, q_ids or set(), events, a_ids, v_ids)
        if conditions is None:
            if notes is not None:
                notes.append(f"Rule \"{_s(r.get('label')) or _s(r.get('id'))}\" was left out: one of its conditions "
                             "refers to something the model does not have. Check this rule against the protocol.")
            continue
        stop_visits = []
        for vid in arr(r.get("stop_visits")):
            if _s(vid) in v_ids and _s(vid) not in stop_visits:
                stop_visits.append(_s(vid))
        label = _s(r.get("label")).strip() or trigger.get("anchor") or trigger.get("event")
        rules.append({
            "id": _s(r.get("id") or f"rule{i + 1}"), "label": label, "when": trigger,
            "stop_visits": stop_visits, "stop_all": bool(r.get("stop_all")),
            "same_day": "cancel" if r.get("same_day") == "cancel" else "keep",
            "starts": _s(r["starts"]) if _s(r.get("starts")) in a_ids else None,
            "blocks": [_s(b) for b in arr(r.get("blocks"))],
            "allowed": allowed,
            "effect": _s(r.get("effect")).strip(),
            "source": _s(r["source"]) if r.get("source") else None,
            "if": conditions,
        })
    r_ids = {r["id"] for r in rules}
    for r in rules:
        r["blocks"] = [b for b in dict.fromkeys(r["blocks"]) if b in r_ids and b != r["id"]]
        if r["stop_all"] and "event" in r["when"] and not r["if"]:
            # An event that ends participation ends the schedule the same way the event flag does.
            next(e for e in events if e["id"] == r["when"]["event"])["ends_schedule"] = True
    return rules


def normalize(st: Any) -> dict[str, Any]:
    if not isinstance(st, dict):
        raise ValueError("bad")
    st["protocol"] = st.get("protocol") if isinstance(st.get("protocol"), dict) else {}
    st["arms"] = arr(st.get("arms"))
    anchors = []
    for a in arr(st.get("anchors")):
        if isinstance(a, dict) and a.get("id"):
            kind = a.get("kind") if a.get("kind") in ("start", "relative", "ask") else "relative"
            anchors.append({
                **a, "id": _s(a["id"]), "label": _s(a.get("label") or a["id"]), "kind": kind,
                "offset_days": num(a.get("offset_days")), "estimates": arr(a.get("estimates")),
                "is_day1": bool(a.get("is_day1")), "day_zero": bool(a.get("day_zero")),
                "optional": (a.get("optional") is not False) if kind == "ask" else False,
            })
    st["anchors"] = anchors
    if not anchors:
        anchors.append({"id": "start", "label": "First visit", "kind": "start", "offset_days": 0,
                        "estimates": [], "is_day1": True, "day_zero": False})
    if not any(a["kind"] == "start" for a in anchors):
        pick = next((a for a in anchors if a["kind"] != "ask"), anchors[0])
        pick["kind"] = "start"
    a_ids = {a["id"] for a in anchors}
    start_id = next(a["id"] for a in anchors if a["kind"] == "start")
    st["questions"] = [q for q in (norm_q(q, "participant") for q in arr(st.get("questions"))) if q]
    st["notes"] = [_s(n) for n in arr(st.get("notes"))]
    st["visits"] = [v for v in (norm_visit(v, i, "v") for i, v in enumerate(arr(st.get("visits")))) if v]
    v_ids = {v["id"] for v in st["visits"]}
    for v in st["visits"]:
        if v.get("anchor") not in a_ids:
            v["anchor"] = start_id
        if v["follows"] and (v["follows"] not in v_ids or v["follows"] == v["id"]):
            v["follows"] = None
        if v["repeat"] and v["repeat"].get("until_anchor") and v["repeat"]["until_anchor"] not in a_ids:
            v["repeat"]["until_anchor"] = None
    by_id = {v["id"]: v for v in st["visits"]}
    for v in st["visits"]:  # break follow cycles
        seen = {v["id"]}
        f = v["follows"]
        while f:
            if f in seen:
                v["follows"] = None
                break
            seen.add(f)
            f = (by_id.get(f) or {}).get("follows")
    for a in anchors:
        if a.get("set_by_visit") and a["set_by_visit"] not in v_ids:
            a["set_by_visit"] = None
    for q in st["questions"]:
        if q["scope"] == "decision":
            at = q.get("ask_at") or {}
            if not (at.get("visit") and at["visit"] in v_ids) and not (at.get("anchor") and at["anchor"] in a_ids):
                q["scope"] = "participant"
    events = []
    for e in arr(st.get("events")):
        if isinstance(e, dict) and e.get("id"):
            events.append({
                "id": _s(e["id"]), "label": _s(e.get("label") or e["id"]),
                "repeatable": e.get("repeatable") is not False, "ends_schedule": bool(e.get("ends_schedule")),
                "sets_anchor": e["sets_anchor"] if e.get("sets_anchor") in a_ids else None,
                "note": _s(e["note"]) if e.get("note") else None,
                "questions": [q for q in (norm_q(q, "event") for q in arr(e.get("questions"))) if q],
                "visits": [v for v in (norm_visit(v, i, f"{e['id']}_v") for i, v in enumerate(arr(e.get("visits")))) if v],
            })
    st["events"] = events
    st["rules"] = norm_rules(st.get("rules"), a_ids, events, v_ids,
                             q_ids={q["id"] for q in st["questions"]}, notes=st["notes"])
    review = []
    for q in arr(st.get("review_questions")):
        if isinstance(q, dict) and q.get("id") and q.get("text"):
            options = []
            for o in arr(q.get("options")):
                if not isinstance(o, dict):
                    continue
                label = o.get("label") if o.get("label") is not None else o.get("value")
                if label:
                    value = o.get("value") if o.get("value") is not None else o.get("label")
                    options.append({"value": _s(value), "label": _s(label), "patches": arr(o.get("patches"))})
            if options:
                review.append({
                    "id": _s(q["id"]), "text": _s(q["text"]), "why": _s(q["why"]) if q.get("why") else "",
                    "recommended": _s(q["recommended"]) if q.get("recommended") is not None else None,
                    "options": options})
    st["review_questions"] = review
    if not st["visits"]:
        raise ValueError("no visits")
    return st


PATCH_FIELDS = {
    "visit": ["offset_days", "window_before", "window_after", "duration_days", "repeat.count",
              "repeat.every_days", "repeat.chain", "repeat.open_ended", "repeat.until_anchor"],
    "anchor": ["offset_days", "relative_to"],
}


def apply_patches(model: dict[str, Any], patches: Any) -> dict[str, Any]:
    for p in arr(patches):
        if not isinstance(p, dict) or p.get("field") not in PATCH_FIELDS.get(p.get("target"), []):
            continue
        pool = model["visits"] if p["target"] == "visit" else model["anchors"]
        obj = next((x for x in pool if x["id"] == _s(p.get("id"))), None)
        if obj is None:
            continue
        parts = p["field"].split(".")
        o: Any = obj
        for key in parts[:-1]:
            o = o.get(key) if isinstance(o, dict) else None
            if not o:
                o = None
                break
        if o is not None:
            o[parts[-1]] = p.get("value")
    return model


def build_template_model(base: dict[str, Any], review_answers: dict[str, str] | None = None) -> dict[str, Any]:
    m = copy.deepcopy(base)
    for rq in arr(m.get("review_questions")):
        o = next((x for x in rq["options"] if x["value"] == (review_answers or {}).get(rq["id"])), None)
        if o:
            apply_patches(m, o["patches"])
    return normalize(m)


def default_review_answers(model: dict[str, Any]) -> dict[str, str]:
    out = {}
    for rq in arr(model.get("review_questions")):
        rec = rq.get("recommended")
        out[rq["id"]] = rec if rec is not None and any(o["value"] == rec for o in rq["options"]) else rq["options"][0]["value"]
    return out
