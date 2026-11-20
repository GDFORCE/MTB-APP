"""History-based visit intervals and combined visits: clean-up of the operations the AI writes.

A repeating visit may carry ``repeat.adaptive`` ("treat and extend"): from occurrence
``decide_from_n`` on, the interval to the next occurrence follows from the interval that led to
the previous one, the finding confirmed there and the history of earlier occurrences. The engine
is ``frontend/src/features/kit/adaptive.ts``; this module only checks what the protocol stated.

Nothing is filled in that the protocol did not state. An operation missing a part it needs (the
limits, what the next date counts from, which input decides when a calculated interval and an
external recommendation differ) is kept with ``blocked`` set to the reason: the engine never runs
a blocked operation, the workflow cannot be approved and nobody can be enrolled until the reviewer
completes it (``patterns.blockers`` lists it).

A visit may also carry ``combine_with``: [{"visit": fixed visit id, "date": "adaptive"|"fixed"}]:
when one of its occurrences falls inside that visit's window, both are done at one visit, listed
once with both sets of assessments ("adaptive": on this visit's date; "fixed": on the fixed
visit's target).
"""

from __future__ import annotations

import re
from typing import Any

BASES = ("actual", "target")
FINDING_KINDS = ("count", "boolean", "number")
PRECEDENCE = ("calculated", "recommendation", "recommendation_if_given", "shorter", "longer", "review")
ACTIVITY_COUNTS = ("extended_intervals", "any_interval")
STAFF_ROLES = ("pi", "crc")
OPS = ("=", "!=", "<", "<=", ">", ">=")
INPUT_ID = re.compile(r"^[A-Za-z][\w-]{0,59}$")


def _arr(x: Any) -> list:
    return x if isinstance(x, list) else []


def _s(x: Any) -> str:
    return "" if x is None else str(x)


def _num(x: Any) -> float | int | None:
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return int(n) if n == int(n) else n


def _roles(x: Any) -> list[str]:
    roles = [r for r in dict.fromkeys(_s(v).lower() for v in _arr(x)) if r in STAFF_ROLES]
    return roles or list(STAFF_ROLES)


def _test(c: Any, problems: list[str], what: str) -> dict[str, Any] | None:
    """{"op": "=", "value": 0} or {"in": ["yes"]}: the condition a finding is compared with."""
    if not isinstance(c, dict):
        problems.append(f"{what} has no condition")
        return None
    if isinstance(c.get("in"), list) and c["in"]:
        return {"in": [_s(v) for v in c["in"]]}
    op, value = c.get("op"), _num(c.get("value"))
    if op in OPS and value is not None:
        return {"op": op, "value": value}
    problems.append(f"{what} has a condition the app cannot read")
    return None


def _input(x: Any, problems: list[str], what: str, *, number_only: bool = False) -> dict[str, Any] | None:
    if not isinstance(x, dict):
        problems.append(f"the {what} is not described")
        return None
    iid, label = _s(x.get("id")).strip(), _s(x.get("label")).strip()
    kind = "number" if number_only else _s(x.get("kind"))
    if not INPUT_ID.match(iid) or not label or kind not in FINDING_KINDS:
        problems.append(f"the {what} needs an id, a label and a kind")
        return None
    return {"id": iid, "label": label[:120], "kind": kind, "roles": _roles(x.get("roles"))}


def norm_adaptive(a: Any, repeat: dict[str, Any]) -> dict[str, Any] | None:
    """The cleaned operation, with ``blocked`` naming what is missing; None when there is none."""
    if not isinstance(a, dict):
        return None
    problems: list[str] = []
    out: dict[str, Any] = {}
    n = _num(a.get("decide_from_n"))
    if n is None or n < 1 or n != int(n):
        problems.append("the occurrence from which findings set the interval is not stated")
        n = 1
    out["decide_from_n"] = int(n)
    basis = _s(a.get("basis"))
    if basis not in BASES:
        problems.append("what the next date counts from (the previous visit's actual date or its target) is not stated")
    out["basis"] = basis if basis in BASES else None
    start = _num(a.get("start_days"))
    if start is None:
        # The interval that led to the first decision is the loading interval the repeat states.
        start = _num(repeat.get("every_days"))
    lo, hi = _num(a.get("min_days")), _num(a.get("max_days"))
    if start is None or start <= 0:
        problems.append("the interval before the first decision is not stated")
    if lo is None or hi is None or lo <= 0 or hi < lo:
        problems.append("the shortest and longest allowed intervals are not both stated")
    out.update(start_days=start, min_days=lo, max_days=hi)
    out["finding"] = _input(a.get("finding"), problems, "finding confirmed at each visit")
    steps = []
    for i, st in enumerate(_arr(a.get("steps"))):
        if not isinstance(st, dict):
            continue
        cond = _test(st.get("if"), problems, f"interval rule {i + 1}")
        effects = [k for k in ("add_days", "set_days", "keep") if st.get(k) not in (None, False, "")]
        if cond is None:
            continue
        if len(effects) != 1:
            problems.append(f"interval rule {i + 1} must either add days, set days or keep the interval")
            continue
        step: dict[str, Any] = {"if": cond}
        if effects[0] == "keep":
            step["keep"] = True
        else:
            value = _num(st[effects[0]])
            if value is None or (effects[0] == "set_days" and value <= 0):
                problems.append(f"interval rule {i + 1} has a number the app cannot read")
                continue
            step[effects[0]] = value
        if st.get("last") in ("extended", "not_extended"):
            step["last"] = st["last"]
        if _s(st.get("label")).strip():
            step["label"] = _s(st["label"]).strip()[:160]
        steps.append(step)
    if not steps:
        problems.append("no rule says how a finding changes the interval")
    out["steps"] = steps
    bp = a.get("breakpoint")
    out["breakpoint"] = None
    if isinstance(bp, dict):
        after, offset = _num(bp.get("after_extensions")), _num(bp.get("cap_offset_days"))
        counts = _s(bp.get("count_activity_at"))
        if after is None or after < 0 or after != int(after) or offset is None:
            problems.append("the historical limit needs the number of earlier extensions and the cap's offset in days")
        elif counts not in ACTIVITY_COUNTS:
            problems.append("the historical limit does not say which earlier findings count (only intervals an extension "
                            "reached, or every interval)")
        else:
            out["breakpoint"] = {"after_extensions": int(after), "cap_offset_days": offset, "count_activity_at": counts}
    elif bp not in (None, False):
        problems.append("the historical limit has a form the app cannot read")
    out["active_if"] = None
    if a.get("active_if") is not None:
        out["active_if"] = _test(a.get("active_if"), problems, "the activity condition")
    if out["breakpoint"] and not out["active_if"]:
        problems.append("the historical limit needs to know which findings count as activity")
    rec = a.get("recommendation")
    out["recommendation"] = None
    if isinstance(rec, dict):
        inp = _input(rec, problems, "external recommendation", number_only=True)
        unit = _num(rec.get("unit_days"))
        if inp and (unit is None or unit <= 0):
            problems.append("the recommendation's unit (days per unit) is not stated")
        elif inp:
            out["recommendation"] = {**inp, "unit_days": unit}
    precedence = _s(a.get("precedence"))
    if precedence in PRECEDENCE:
        out["precedence"] = precedence
    elif rec is None:
        out["precedence"] = "calculated"  # nothing else can set the interval
    else:
        out["precedence"] = None
        problems.append("the protocol does not say whether the calculated interval or the recommendation decides the booking")
    if out["finding"] and out["recommendation"] and out["finding"]["id"] == out["recommendation"]["id"]:
        problems.append("the finding and the recommendation need different ids")
    for key in ("source", "quote"):
        if _s(a.get(key)).strip():
            out[key] = _s(a[key]).strip()[:600]
    page = _num(a.get("page"))
    if page is not None and page >= 1:
        out["page"] = int(page)
    if problems:
        out["blocked"] = "; ".join(dict.fromkeys(problems))
    return out


def adaptive_point(v: dict[str, Any]) -> dict[str, Any]:
    a = v["repeat"]["adaptive"]
    return {
        "id": f"adaptive_{v['id']}", "kind": "not_stated", "stage": "cleanup", "affects": [v["id"]],
        "text": f"The history-based interval for {v['code']} cannot run: {a['blocked']}.",
        "quote": a.get("quote") or "", "page": a.get("page"),
        "used": "Visits after the loading occurrences stay undated until the rule is completed in workflow review.",
    }


def norm_combine(items: Any, own_id: str, scheduled: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in _arr(items):
        if not isinstance(c, dict):
            continue
        vid = _s(c.get("visit"))
        target = scheduled.get(vid)
        if not target or vid == own_id or target.get("repeat") or c.get("date") not in ("adaptive", "fixed"):
            continue
        if any(x["visit"] == vid for x in out):
            continue
        out.append({"visit": vid, "date": c["date"]})
    return out


def adaptive_inputs(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{visit id: {input id: definition}} for every runnable history-based interval: what staff
    may record at that visit's occurrences (the finding, the recommendation, the interval choice)."""
    out: dict[str, dict[str, Any]] = {}
    for v in _arr(model.get("visits")):
        a = (v.get("repeat") or {}).get("adaptive") if isinstance(v, dict) else None
        if not isinstance(a, dict) or not a.get("finding"):
            continue
        inputs = {a["finding"]["id"]: a["finding"]}
        if a.get("recommendation"):
            inputs[a["recommendation"]["id"]] = a["recommendation"]
        if a.get("precedence") == "review":
            inputs["@interval"] = {"id": "@interval", "label": "Interval chosen by the investigator", "kind": "number", "roles": ["pi"]}
        out[v["id"]] = inputs
    return out


def operation_blockers(model: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Operations that cannot run as written, in the shape of a pattern-rule blocker."""
    out = []
    for v in _arr((model or {}).get("visits")):
        a = (v.get("repeat") or {}).get("adaptive") if isinstance(v, dict) else None
        if isinstance(a, dict) and a.get("blocked"):
            out.append({"rule_id": f"adaptive:{v['id']}", "pattern": "ADAPTIVE_INTERVAL",
                        "label": f"History-based interval for {v.get('code') or v['id']}", "quote": a.get("quote") or "",
                        "page": a.get("page"), "quote_found": None, "visits": [v["id"]], "reason": "params",
                        "problem": a["blocked"], "has_handler": True})
    for e in _arr((model or {}).get("study_events")):
        if isinstance(e, dict) and e.get("blocked"):
            out.append({"rule_id": f"study_event:{e['id']}", "pattern": "STUDY_EVENT", "label": e.get("label") or e["id"],
                        "quote": e.get("quote") or "", "page": e.get("page"), "quote_found": None,
                        "visits": [x for a in _arr(e.get("actions")) for x in _arr(a.get("supersede_visits"))],
                        "reason": "params", "problem": e["blocked"], "has_handler": True})
    return out
