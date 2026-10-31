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


NEEDS = ("required", "if_indicated", "optional")


def norm_proc(p: Any) -> dict[str, Any] | None:
    if isinstance(p, str):
        return {"name": p.strip(), "if": []} if p.strip() else None
    if isinstance(p, dict):
        name = _s(p.get("name") if p.get("name") is not None else p.get("procedure")).strip()
        if not name:
            return None
        out: dict[str, Any] = {"name": name, "if": arr(p.get("if"))}
        if p.get("need") in NEEDS and p["need"] != "required":
            out["need"] = p["need"]
        reuse = _opt_num(p.get("reuse_within_days"))
        if reuse is not None and reuse > 0:
            out["reuse_within_days"] = reuse
        # "Pregnancy test within 3 days before dosing", "labs within 72 h": this test's own limit.
        wb = p.get("within_before") if isinstance(p.get("within_before"), dict) else None
        hours, days = (_opt_num(wb.get("hours")), _opt_num(wb.get("days"))) if wb else (None, None)
        if hours is not None and hours > 0:
            out["within_before"] = {"hours": hours}
        elif days is not None and days >= 0:
            out["within_before"] = {"days": days}
        if p.get("source"):
            out["source"] = _s(p["source"])
        return out
    return None


OPS = ("in", "=", "!=", "<", "<=", ">", ">=")


def norm_cond(c: Any, depth: int = 0) -> dict[str, Any] | None:
    """One answer condition: {question, in}, {question, op, value}, {any: [...]} or {not: ...}
    (two levels at most). None when it is not usable."""
    if not isinstance(c, dict) or depth > 2:
        return None
    if "any" in c:
        parts = [norm_cond(x, depth + 1) for x in arr(c.get("any"))]
        return {"any": parts} if parts and all(parts) else None
    if "not" in c:
        inner = norm_cond(c.get("not"), depth + 1)
        return {"not": inner} if inner else None
    # What has happened by the date being checked: an event logged, a visit done or missed.
    if _s(c.get("event")):
        out = {"event": _s(c["event"]), "happened": c.get("happened") is not False}
        # "Stopped treatment before Week 12": logged before that visit's (or date's) day.
        b = c.get("before")
        if b is not None:
            if isinstance(b, dict) and _s(b.get("visit")):
                n = _opt_num(b.get("n"))
                out["before"] = {"visit": _s(b["visit"]), "n": int(n) if n is not None else None}
            elif isinstance(b, dict) and _s(b.get("anchor")):
                out["before"] = {"anchor": _s(b["anchor"])}
            else:
                return None
        return out
    if _s(c.get("visit")):
        n = _opt_num(c.get("n"))
        status = c.get("status") if c.get("status") in ("done", "missed", "not_done") else "done"
        return {"visit": _s(c["visit"]), "n": int(n) if n is not None else None, "status": status}
    if not _s(c.get("question")):
        return None
    op = c.get("op") or "in"
    if op not in OPS:
        return None
    if op == "in":
        values = _in_values(c)
        return {"question": _s(c["question"]), "in": values} if values else None
    if c.get("value") in (None, ""):
        return None
    return {"question": _s(c["question"]), "op": op, "value": c["value"]}


def cond_facts(c: dict[str, Any]) -> list[tuple[str, str]]:
    """The events and visits a condition refers to, as ("event"|"visit", id)."""
    if "any" in c:
        return [f for x in c["any"] for f in cond_facts(x)]
    if "not" in c:
        return cond_facts(c["not"])
    if c.get("event"):
        b = c.get("before") or {}
        return [("event", c["event"])] + ([("visit", b["visit"])] if b.get("visit")
                                          else [("anchor", b["anchor"])] if b.get("anchor") else [])
    return [("visit", c["visit"])] if c.get("visit") else []


def cond_questions(c: dict[str, Any]) -> list[str]:
    if "any" in c:
        return [q for x in c["any"] for q in cond_questions(x)]
    if "not" in c:
        return cond_questions(c["not"])
    return [c["question"]] if c.get("question") else []


def norm_conds(items: Any) -> list[dict[str, Any]]:
    """Answer conditions (all must hold). One that is not usable is kept as written, so the visit
    or procedure still shows as depending on it rather than silently applying to everyone."""
    out = []
    for c in arr(items):
        n = norm_cond(c)
        out.append(n if n is not None else c)
    return out


def _procs(items: Any) -> list[dict[str, Any]]:
    out = [p for p in map(norm_proc, arr(items)) if p]
    for p in out:
        p["if"] = norm_conds(p["if"])
    return out


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
        stages = []
        for st in arr(r.get("stages")):
            if isinstance(st, dict) and num(st.get("every_days")) > 0:
                until = _opt_num(st.get("until_day"))
                stages.append({"every_days": num(st["every_days"]), "until_day": until if until and until > 0 else None})
        # Stages run in order of their end day; only the last may run on.
        stages.sort(key=lambda st: float("inf") if st["until_day"] is None else st["until_day"])
        stages = [st for i, st in enumerate(stages) if st["until_day"] is not None or i == len(stages) - 1]
        r["stages"] = stages
        if stages and not r["every_days"]:
            r["every_days"] = stages[0]["every_days"]
        for key in ("max_days", "max_delay_days"):
            value = _opt_num(r.get(key))
            r[key] = value if value is not None and value > 0 else None
        r["delay_effect"] = _s(r["delay_effect"]).strip() or None if r.get("delay_effect") else None
        # "Weekly until CrCl >= 50", "follow until resolved": repeats until the visit's check is met.
        if r.get("until_check") is True:
            r["until_check"] = True
        else:
            r.pop("until_check", None)
        count = _opt_num(r.get("count"))
        r["count"] = count if count is not None and count > 0 else None
        overrides = []
        for o in arr(r.get("overrides")):
            if not isinstance(o, dict):
                continue
            listed = [num(n) for n in o["n"]] if isinstance(o.get("n"), list) else []
            every = _opt_num(o.get("every"))
            if not listed and not (every and every > 0):
                continue
            removed = []
            for x in arr(o.get("remove")):
                name = x if isinstance(x, str) else (x or {}).get("name") if isinstance(x, dict) else None
                if name:
                    removed.append(name)
            entry: dict[str, Any] = {
                "n": listed, "add": _procs(o.get("add")), "remove": removed,
                "note": _s(o["note"]) if o.get("note") else None,
            }
            if every and every > 0:
                entry["every"] = int(every)
                entry["from"] = int(_opt_num(o.get("from")) or 1)
                to = _opt_num(o.get("to"))
                entry["to"] = int(to) if to else None
            overrides.append(entry)
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
    gap = v.get("min_gap") if isinstance(v.get("min_gap"), dict) else None
    gap_days = _opt_num(gap.get("days")) if gap else None
    prep = v.get("prep") if isinstance(v.get("prep"), dict) else None
    fasting = _opt_num(prep.get("fasting_hours")) if prep else None
    prep_notes = [_s(n).strip() for n in arr(prep.get("notes")) if _s(n).strip()] if prep else []
    vid = _s(v.get("id") or f"{prefix}{i}")
    out = {
        **v, "id": vid, "code": _s(v.get("code") or f"V{i + 1}"),
        "name": _s(v.get("name") or v.get("code")), "phase": _s(v.get("phase") or "Visit"),
        "modality": v.get("modality") if v.get("modality") in ("site", "phone", "home", "remote") else "site",
        "offset_days": num(v.get("offset_days")), "window_before": abs(num(v.get("window_before"))),
        "window_after": abs(num(v.get("window_after"))), "duration_days": duration,
        "follows": _s(v["follows"]) if v.get("follows") else None,
        "follows_occurrence": "last" if v.get("follows_occurrence") == "last" else None,
        "include_if": arr(v.get("include_if")), "procedures": _procs(v.get("procedures")),
        "repeat": r, "timepoints": timepoints,
        "condition": _s(v["condition"]) if v.get("condition") else None,
        "min_gap": {"days": gap_days, "after": _s(gap["after"]) if gap.get("after") else None} if gap_days and gap_days > 0 else None,
        "prep": {"fasting_hours": fasting if fasting and fasting > 0 else None, "notes": prep_notes}
        if (fasting and fasting > 0) or prep_notes else None,
        "check": norm_check(v.get("check"), vid),
    }
    if r is not None and r.get("until_check") and not out["check"]:
        r.pop("until_check")  # nothing to be met
    # "4 weeks after the last dose, or as soon as possible if that has passed when the patient leaves".
    if v.get("asap_if_past") is True:
        out["asap_if_past"] = True
    else:
        out.pop("asap_if_past", None)
    return out


def norm_check(c: Any, visit_id: str) -> dict[str, Any] | None:
    """A visit's yes/no check, asked at every occurrence ("ANC ≥ 1.0 × 10^9/L?"). Numbers the
    protocol doesn't state stay None, and nothing stops unless on_timeout is "stop" with a
    waiting limit to measure it by."""
    if not isinstance(c, dict) or not _s(c.get("text")).strip():
        return None
    recheck, wait = _opt_num(c.get("recheck_days")), _opt_num(c.get("max_wait_days"))
    wait = wait if wait is not None and wait >= 0 else None
    return {
        "id": _s(c.get("id")).strip() or f"{visit_id}_check", "text": _s(c["text"]).strip(),
        "help": _s(c["help"]).strip() or None if c.get("help") else None,
        "recheck_days": recheck if recheck is not None and recheck > 0 else None,
        "max_wait_days": wait,
        "on_timeout": "stop" if c.get("on_timeout") == "stop" and wait is not None else "warn",
        "stop_visits": [_s(x) for x in dict.fromkeys(arr(c.get("stop_visits"))) if _s(x) and _s(x) != visit_id],
        "source": _s(c["source"]) if c.get("source") else None,
        # Checked against the model's measures and doses once those are known (link_measures_and_doses).
        "measures": [_s(x) for x in arr(c.get("measures")) if _s(x)],
        "limits": arr(c.get("limits")), "dose_changes": arr(c.get("dose_changes")),
        **({"actions": arr(c["actions"])} if arr(c.get("actions")) else {}),
    }


CHECK_ACTIONS = ("give", "reduce", "skip", "hold")


def norm_action(a: Any, measures: dict[str, dict[str, Any]], doses: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """One row of a dosing table ("ANC 1000-1499: 50% of the dose"): what happens at this occurrence when
    its limits hold. give = goes ahead, reduce = goes ahead at percent of the dose this time only,
    skip = not given and not made up, hold = waits as for a No. None when unusable."""
    if not isinstance(a, dict) or a.get("do") not in CHECK_ACTIONS:
        return None
    conds = [norm_limit(c, measures) for c in arr(a.get("if"))]
    if not conds or not all(conds):
        return None
    out: dict[str, Any] = {"do": a["do"], "if": conds, "note": _s(a.get("note")).strip() or None}
    if a["do"] == "reduce":
        pct = _opt_num(a.get("percent"))
        if pct is None or not 0 < pct < 100:
            return None
        out["percent"] = pct
        out["dose"] = _s(a.get("dose")) if _s(a.get("dose")) in doses else None
    return out


MEASURE_OPS = ("<", "<=", ">", ">=")
# Which recorded value a limit looks at: the one entered at this check, the worst entered for this
# visit so far (during a hold), or the worst entered for the participant so far.
MEASURE_OF = ("latest", "worst_here", "worst_so_far")


def norm_measures(items: Any) -> list[dict[str, Any]]:
    """Values entered at visit checks (ANC, creatinine, a toxicity grade). A grade gets worse as it
    goes up by definition; for other values "worse" stays None unless the protocol makes it clear."""
    out: list[dict[str, Any]] = []
    for m in arr(items):
        mid = _s(m.get("id")).strip() if isinstance(m, dict) else ""
        if not mid or any(x["id"] == mid for x in out):
            continue
        kind = "grade" if m.get("kind") == "grade" else "value"
        worse = m.get("worse") if m.get("worse") in ("lower", "higher") else "higher" if kind == "grade" else None
        out.append({
            "id": mid, "label": _s(m.get("label")).strip() or mid, "unit": _s(m.get("unit")).strip() or None,
            "kind": kind, "worse": worse, "baseline": _s(m.get("baseline")).strip() or None,
        })
    return out


def norm_doses(items: Any, v_ids: set[str], notes: list[str]) -> list[dict[str, Any]]:
    """Dose levels per drug, highest dose first. A dose given at no known visit is left out; a
    starting level the protocol doesn't name stays None (the engine then shows it as not stated)."""
    out: list[dict[str, Any]] = []
    for d in arr(items):
        did = _s(d.get("id")).strip() if isinstance(d, dict) else ""
        if not did or any(x["id"] == did for x in out):
            continue
        visits = [_s(x) for x in dict.fromkeys(arr(d.get("visits"))) if _s(x) in v_ids]
        levels: list[dict[str, Any]] = []
        for lv in arr(d.get("levels")):
            lid = _s(lv.get("id") if isinstance(lv, dict) else lv).strip()
            if lid and not any(x["id"] == lid for x in levels):
                levels.append({"id": lid, "label": _s(lv.get("label")).strip() or lid if isinstance(lv, dict) else lid})
                # "Second reduction: omit Day 8": at this level these visits of the drug are not given.
                drop = [x for x in dict.fromkeys(_s(y) for y in arr(lv.get("drop_visits") if isinstance(lv, dict) else []))
                        if x in visits]
                if drop:
                    levels[-1]["drop_visits"] = drop
        label = _s(d.get("label")).strip() or did
        if not levels or not visits:
            notes.append(f"Dose levels for {label} were left out: "
                         + ("no levels were given." if not levels else "the visits it is given at are not in the model."))
            continue
        start = _s(d.get("start"))
        out.append({
            "id": did, "label": label, "levels": levels,
            "start": start if any(x["id"] == start for x in levels) else None,
            "visits": visits, "below_lowest": "stop" if d.get("below_lowest") == "stop" else "warn",
            "stop_visits": [_s(x) for x in dict.fromkeys(arr(d.get("stop_visits"))) if _s(x) in v_ids],
            "source": _s(d["source"]) if d.get("source") else None,
        })
        if out[-1]["start"] is None:
            notes.append(f"The starting dose level of {label} is not stated; staff set it on each patient's first dose.")
    return out


def norm_limit(c: Any, measures: dict[str, dict[str, Any]], held: bool = False, depth: int = 0) -> dict[str, Any] | None:
    """One limit on entered values: {measure, op, value} ("ANC ≥ 1.0"), {measure, op, baseline_factor}
    ("creatinine ≤ 1.5 × baseline"), with "of" latest / worst_here / worst_so_far, or {any: [...]}.
    With held=True also {held_days: {op, value}} (how long the visit waited). None when unusable."""
    if not isinstance(c, dict) or depth > 2:
        return None
    if "any" in c:
        parts = [norm_limit(x, measures, held, depth + 1) for x in arr(c.get("any"))]
        return {"any": parts} if parts and all(parts) else None
    if held and isinstance(c.get("held_days"), dict):
        h = c["held_days"]
        value = _opt_num(h.get("value"))
        return {"held_days": {"op": h["op"], "value": value}} if h.get("op") in MEASURE_OPS and value is not None else None
    m = measures.get(_s(c.get("measure")))
    if not m or c.get("op") not in MEASURE_OPS:
        return None
    of = c.get("of") if c.get("of") in MEASURE_OF else "latest"
    if of != "latest" and m["worse"] is None:
        return None  # "worst" needs to know which way is worse
    # "ANC < 500 lasting 7 days or more": an unbroken run of the values entered for that visit met it
    # over at least that many days.
    lasting: dict[str, Any] = {}
    if c.get("for_days") not in (None, ""):
        days = _opt_num(c.get("for_days"))
        if days is None or days <= 0 or of != "latest":
            return None
        lasting = {"for_days": days}
    factor = _opt_num(c.get("baseline_factor"))
    if factor is not None:
        return {"measure": m["id"], "op": c["op"], "baseline_factor": factor, "of": of, **lasting} if factor > 0 else None
    value = _opt_num(c.get("value"))
    return {"measure": m["id"], "op": c["op"], "value": value, "of": of, **lasting} if value is not None else None


def limit_measures(c: dict[str, Any]) -> list[str]:
    if "any" in c:
        return [m for x in c["any"] for m in limit_measures(x)]
    return [c["measure"]] if c.get("measure") else []


def norm_dose_change(x: Any, doses: dict[str, dict[str, Any]], measures: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    """A change of dose level: one step (-1 = one level lower), a named level, or stopping the drug.
    On a check (measures given) it needs conditions; on a rule the rule's own trigger and "if" decide."""
    if not isinstance(x, dict):
        return None
    d = doses.get(_s(x.get("dose")))
    if not d:
        return None
    step = _opt_num(x.get("step"))
    step = int(step) if step is not None and step == int(step) and step != 0 else None
    to = _s(x.get("to")) if any(lv["id"] == _s(x.get("to")) for lv in d["levels"]) else None
    out: dict[str, Any] = {"dose": d["id"], "step": None, "to": None, "stop": False}
    if x.get("stop") is True:
        out["stop"] = True
    elif to:
        out["to"] = to
    elif step:
        out["step"] = step
    else:
        return None
    if measures is None:
        return out
    conds = [norm_limit(c, measures, held=True) for c in arr(x.get("if"))]
    if not conds or not all(conds):
        return None  # unconditional, it would change the dose at every visit
    occurrence = _opt_num(x.get("occurrence"))
    return {**out, "if": conds, "occurrence": int(occurrence) if occurrence and occurrence > 0 else None,
            "and_later": bool(x.get("and_later")) and bool(occurrence), "note": _s(x.get("note")).strip() or None}


def link_measures_and_doses(st: dict[str, Any]) -> None:
    """Checks' limits and dose changes against the model's measures and doses. A check whose limits
    refer to something unknown keeps only its yes/no question (staff decide), never fewer limits."""
    by_m = {m["id"]: m for m in st["measures"]}
    by_d = {d["id"]: d for d in st["doses"]}
    event_visits = [v for e in st["events"] for v in e["visits"]]
    for v in st["visits"] + event_visits:
        ck = v.get("check")
        if not ck:
            continue
        read = [norm_limit(x, by_m) for x in ck["limits"]]
        limits = [x for x in read if x is not None]
        if len(limits) != len(read):
            st["notes"].append(f"Check “{ck['text']}” on {v['code']}: a limit could not be read, so staff answer it "
                               "Yes or No by hand.")
            limits = []
        changes = []
        for x in ck["dose_changes"]:
            c = norm_dose_change(x, by_d, by_m)
            if c is None:
                st["notes"].append(f"A dose change on the {v['code']} check was left out: it names an unknown dose, "
                                   "level or value, or has no condition. Check it against the protocol.")
            else:
                changes.append(c)
        if changes and any(v is x for x in event_visits):
            st["notes"].append(f"Dose changes on the {v['code']} check were left out: doses run on scheduled visits only.")
            changes = []
        # A dosing table is used whole or not at all: a missing row would give the wrong outcome.
        actions = [norm_action(x, by_m, by_d) for x in ck.get("actions", [])]
        if actions and not all(actions):
            st["notes"].append(f"The outcome table on the {v['code']} check (give / reduce / skip / hold) could not be read, "
                               "so staff decide what happens.")
            actions = []
        used = [m for x in limits for m in limit_measures(x)] + [m for c in changes for x in c["if"] for m in limit_measures(x)]
        used += [m for a in actions for x in a["if"] for m in limit_measures(x)]
        ck["measures"] = [m for m in dict.fromkeys(ck["measures"] + used) if m in by_m]
        ck["limits"], ck["dose_changes"] = limits, changes
        if actions:
            ck["actions"] = actions
        else:
            ck.pop("actions", None)


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
        if ("any" in c or "not" in c) and any(isinstance(x, dict) and set(x) & {"timing", "occurrence", "event_question"}
                                              for x in arr(c.get("any")) + arr([c.get("not")])):
            return None  # "or"/"not" over answers only; timing branches are separate rules
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
        elif "question" in c or "any" in c or "not" in c:
            n = norm_cond(c)
            # Rules are told apart by answers and the trigger's own timing; "what happened"
            # conditions belong on visits and assessments.
            if n is None or cond_facts(n) or any(q not in q_ids for q in cond_questions(n)):
                return None
            out.append(n)
        else:
            return None
    return out


def norm_rules(items: Any, a_ids: set[str], events: list[dict[str, Any]], v_ids: set[str],
               q_ids: set[str] | None = None, notes: list[str] | None = None,
               questions: list[dict[str, Any]] | None = None,
               doses: list[dict[str, Any]] | None = None,
               stop_ids: set[str] | None = None) -> list[dict[str, Any]]:
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
        # Visits a rule stops: scheduled ones, and visits an event added (stop_ids) when given.
        stop_visits = []
        for vid in arr(r.get("stop_visits")):
            if _s(vid) in (stop_ids or v_ids) and _s(vid) not in stop_visits:
                stop_visits.append(_s(vid))
        label = _s(r.get("label")).strip() or trigger.get("anchor") or trigger.get("event")
        # Answers that change from the rule's date on (crossover): only real questions and options.
        by_q = {q["id"]: q for q in (questions or [])}
        set_answers = {}
        for qid, value in (r.get("set_answers") or {}).items() if isinstance(r.get("set_answers"), dict) else []:
            q = by_q.get(_s(qid))
            if q and (q["kind"] != "choice" or any(o["value"] == _s(value) for o in q["options"])) and _s(value):
                set_answers[q["id"]] = _s(value)
        dose_changes = []
        for x in arr(r.get("dose_changes")):
            c = norm_dose_change(x, {d["id"]: d for d in doses or []}, None)
            if c is not None:
                dose_changes.append(c)
            elif notes is not None:
                notes.append(f"A dose change on rule \"{_s(r.get('label')) or _s(r.get('id'))}\" was left out: "
                             "it names an unknown dose or level.")
        pause = r.get("pause") if isinstance(r.get("pause"), dict) else None
        if pause is not None:
            visits = [_s(x) for x in dict.fromkeys(arr(pause.get("visits"))) if _s(x) in v_ids]
            resume = _s(pause.get("resume"))
            pause = {"visits": visits, "resume": resume if resume in a_ids else None,
                     "mode": "skip" if pause.get("mode") == "skip" else "shift"} if visits else None
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
            "set_answers": set_answers or None,
            **({"keeps_visits": keeps} if (keeps := [_s(x) for x in dict.fromkeys(arr(r.get("keeps_visits")))
                                                    if _s(x) in (stop_ids or v_ids)]) and r.get("stop_all") else {}),
            "pause": pause,
            **({"dose_changes": dose_changes} if dose_changes else {}),
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
            kind = a.get("kind") if a.get("kind") in ("start", "relative", "ask", "last_visit") else "relative"
            anchors.append({
                **a, "id": _s(a["id"]), "label": _s(a.get("label") or a["id"]), "kind": kind,
                "offset_days": num(a.get("offset_days")), "estimates": arr(a.get("estimates")),
                "is_day1": bool(a.get("is_day1")), "day_zero": bool(a.get("day_zero")),
                "optional": (a.get("optional") is not False) if kind == "ask" else False,
            })
    st["anchors"] = anchors
    ids = {a["id"] for a in anchors}
    for a in anchors:
        g = a.get("range") if isinstance(a.get("range"), dict) else None
        lo, hi = (_opt_num(g.get("min_days")), _opt_num(g.get("max_days"))) if g else (None, None)
        rel = _s(g.get("relative_to")) if g else ""
        a["range"] = ({"relative_to": rel, "min_days": lo, "max_days": hi}
                      if g and rel in ids and rel != a["id"] and (lo is not None or hi is not None)
                      and (lo is None or hi is None or lo <= hi) else None)
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
        if a["kind"] == "last_visit":
            # "The last of these visits": scheduled visits only, never one counted from this date itself.
            own = {v["id"] for v in st["visits"] if v["anchor"] == a["id"]}
            a["last_of"] = [x for x in dict.fromkeys(_s(x) for x in arr(a.get("last_of"))) if x in v_ids and x not in own]
            a["relative_to"], a["is_day1"] = None, False
            # Daily dosing between visits: a logged early stop is the last dose (schedule.ts setLast).
            if a.get("stop_is_last_dose"):
                a["stop_is_last_dose"] = True
            else:
                a.pop("stop_is_last_dose", None)
            if not a["last_of"]:
                a["kind"], a["optional"] = "ask", True
                st["notes"].append(f"“{a['label']}” is entered by the site: the visits it is the last of are not in the model.")
        else:
            a.pop("last_of", None)
            a.pop("stop_is_last_dose", None)
    for q in st["questions"]:
        if q["scope"] == "decision":
            at = q.get("ask_at") or {}
            if not (at.get("visit") and at["visit"] in v_ids) and not (at.get("anchor") and at["anchor"] in a_ids):
                q["scope"] = "participant"
    events = []
    for e in arr(st.get("events")):
        if isinstance(e, dict) and e.get("id"):
            ev = {
                "id": _s(e["id"]), "label": _s(e.get("label") or e["id"]),
                "repeatable": e.get("repeatable") is not False, "ends_schedule": bool(e.get("ends_schedule")),
                "sets_anchor": e["sets_anchor"] if e.get("sets_anchor") in a_ids else None,
                # Scheduled visits that still happen when this event ends the schedule (safety follow-up).
                **({"keeps_visits": kept} if (kept := [_s(x) for x in dict.fromkeys(arr(e.get("keeps_visits")))
                                                       if _s(x) in v_ids]) else {}),
                "note": _s(e["note"]) if e.get("note") else None,
                "questions": [q for q in (norm_q(q, "event") for q in arr(e.get("questions"))) if q],
                "visits": [v for v in (norm_visit(v, i, f"{e['id']}_v") for i, v in enumerate(arr(e.get("visits")))) if v],
            }
            # From the events check (kit/events.py): what the protocol says, and what the site must
            # do that is not a visit or an assessment.
            actions = [_s(a).strip() for a in arr(e.get("site_actions")) if _s(a).strip()]
            if actions:
                ev["site_actions"] = actions
            if isinstance(e.get("check"), dict):
                ev["check"] = e["check"]
            events.append(ev)
    st["events"] = events
    st["measures"] = norm_measures(st.get("measures"))
    st["doses"] = norm_doses(st.get("doses"), v_ids, st["notes"])
    st["rules"] = norm_rules(st.get("rules"), a_ids, events, v_ids,
                             q_ids={q["id"] for q in st["questions"]}, notes=st["notes"], questions=st["questions"],
                             doses=st["doses"], stop_ids=v_ids | {v["id"] for e in events for v in e["visits"]})
    for v in st["visits"] + [v for e in events for v in e["visits"]]:
        v["include_if"] = norm_conds(v["include_if"])
        if v.get("min_gap") and v["min_gap"]["after"] and v["min_gap"]["after"] not in v_ids:
            v["min_gap"]["after"] = None
        if v.get("check"):
            v["check"]["stop_visits"] = [x for x in v["check"]["stop_visits"] if x in v_ids]
        # "Check in at least 12 h before dosing": a scheduled dose visit and a number of hours, or nothing.
        ab = v.get("arrive_before") if isinstance(v.get("arrive_before"), dict) else None
        hours = _opt_num(ab.get("hours")) if ab else None
        if ab and _s(ab.get("visit")) in v_ids and _s(ab.get("visit")) != v["id"] and hours and hours > 0:
            v["arrive_before"] = {"visit": _s(ab["visit"]), "hours": hours}
        else:
            v.pop("arrive_before", None)
    for e in events:
        for v in e["visits"]:
            if v.get("check") and not (v["repeat"] or {}).get("until_check"):
                # The engine asks checks on scheduled visits, and on event visits that repeat until they are met.
                st["notes"].append(f"Check “{v['check']['text']}” on {e['label']} visit {v['code']} is not asked "
                                   "automatically; staff check it by hand.")
                v["check"] = None
    link_measures_and_doses(st)
    # Only protocols that have them carry measures and doses.
    for key in ("measures", "doses"):
        if not st[key]:
            del st[key]
    for a in anchors:
        a["estimates"] = [{**e, "when": norm_conds(e.get("when"))} for e in a["estimates"]
                          if isinstance(e, dict) and _s(e.get("relative_to")) in a_ids]
    _note_unknown_questions(st, events)
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
    _prune_review(st)
    _dates_with_ranges(st)
    if not st["visits"]:
        raise ValueError("no visits")
    return st


def _note_unknown_questions(st: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """A visit or assessment condition naming a question nobody is asked can never be met:
    say so once, so it is fixed in review instead of waiting forever."""
    known = {q["id"] for q in st["questions"]} | {q["id"] for e in events for q in e["questions"]}
    known_facts = ({("event", e["id"]) for e in events} | {("visit", v["id"]) for v in st["visits"]}
                   | {("anchor", a["id"]) for a in st["anchors"]})
    missing: dict[str, list[str]] = {}
    for v in st["visits"] + [v for e in events for v in e["visits"]]:
        conds = [c for c in v["include_if"] if isinstance(c, dict)]
        conds += [c for p in v["procedures"] for c in p["if"] if isinstance(c, dict)]
        for c in conds:
            for q in cond_questions(c) if norm_cond(c) else [_s(c.get("question")) or "?"]:
                if q not in known:
                    missing.setdefault(q, []).append(v.get("code") or v["id"])
            for kind, ref in cond_facts(c) if norm_cond(c) else []:
                if (kind, ref) not in known_facts:
                    missing.setdefault(ref, []).append(v.get("code") or v["id"])
    for q, codes in missing.items():
        note = (f"Condition on “{q}” ({', '.join(dict.fromkeys(codes))}) refers to no question, event or visit, "
                "so it can never be met.")
        if note not in st["notes"]:
            st["notes"].append(note)


def _patch_key(p: Any) -> tuple:
    return (p.get("target"), _s(p.get("id")), p.get("field")) if isinstance(p, dict) else ()


def _prune_review(st: dict[str, Any]) -> None:
    """Keep the reviewer to choices that matter. A question is answered without a person when:
    every option does the same thing; it only picks the gap between two dates (the date is then
    entered per participant within the range its options span); or it only picks how many visits
    of an open-ended repeat to show ahead (a display choice)."""
    by_anchor = {a["id"]: a for a in st["anchors"]}
    by_visit = {v["id"]: v for v in st["visits"]}
    kept = []
    for rq in st["review_questions"]:
        if rq["id"].startswith("table_check"):
            kept.append(rq)
            continue
        options = rq["options"]
        patches = [[p for p in arr(o["patches"]) if isinstance(p, dict)] for o in options]
        keys = {_patch_key(p) for ps in patches for p in ps}
        if len(options) > 1 and all(sorted(map(str, ps)) == sorted(map(str, patches[0])) for ps in patches):
            st["notes"].append(f"Review question “{rq['text']}” was dropped: every answer gives the same schedule.")
            continue
        if keys and all(k[0] == "anchor" and k[2] == "offset_days" and k[1] in by_anchor
                        and by_anchor[k[1]].get("relative_to") in by_anchor and by_anchor[k[1]]["kind"] != "start"
                        for k in keys) and len({k[1] for k in keys}) == 1:
            a = by_anchor[next(iter(keys))[1]]
            values = [_opt_num(p.get("value")) for ps in patches for p in ps]
            values = [x for x in values if x is not None] + [a["offset_days"]]
            if not a.get("range"):
                a["range"] = {"relative_to": a["relative_to"], "min_days": min(values), "max_days": max(values)}
            st["notes"].append(f"{a['label']} is entered for each participant (the protocol allows "
                               f"{a['range']['min_days']:g} to {a['range']['max_days']:g} days after "
                               f"{by_anchor[a['range']['relative_to']]['label']}) instead of asking for one fixed gap.")
            continue
        if keys and all(k[0] == "visit" and k[2] == "repeat.count" and (by_visit.get(k[1], {}).get("repeat") or {}).get("open_ended")
                        for k in keys):
            continue
        kept.append(rq)
    st["review_questions"] = kept


def _dates_with_ranges(st: dict[str, Any]) -> None:
    """A date the protocol allows within a range is entered per participant, never counted from a
    typical gap: a counted one with a range becomes an entered date, its gap kept only as the
    planned timing for ordering visits."""
    for a in st["anchors"]:
        if a.get("range") and a["kind"] == "relative":
            a["kind"] = "ask"
            if a.get("relative_to") and not a["estimates"]:
                a["estimates"] = [{"when": [], "relative_to": a["relative_to"], "offset_days": a["offset_days"]}]
            a["optional"] = True


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
