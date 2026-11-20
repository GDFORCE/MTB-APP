"""Named rule patterns ("blockers"): protocol rules the kit model's fields cannot hold.

The kit model has a fixed set of fields (anchors, visits, repeats, events, rules, checks...).
When a protocol states a timing rule that none of them can hold, the AI does not squeeze it into
the nearest field. It writes a *pattern rule* instead::

    {"id": "pr1", "pattern": "CALENDAR_MONTHS", "label": "Infusions on the same day of each month",
     "visits": ["infm"], "params": {"months": 1, "every_months": 1, "missing_day": "last_day_of_month"},
     "quote": "<the protocol's own words>", "page": 3}

``pattern`` names the general shape of the rule (not the protocol). Each name either has a
handler, a small piece of code that calculates it (here: what its params must look like; in
``frontend/src/features/kit/patterns.ts``: how it changes the dates), or it does not yet.

A pattern rule without a handler, or whose params its handler cannot use, is a **blocker**:
``normalize`` keeps it (never drops it), marks it ``blocked`` with the reason, and a trial whose
saved model has a blocker cannot enrol patients. The rest of the model can still be reviewed
and saved. When a developer adds a handler for that name (both files above, same name), the
admin re-check (``POST /api/admin/kit/blockers/recheck``) re-evaluates every saved model, and
every trial waiting on that name is unblocked at once.

Adding a handler: see ``docs/kit-pattern-handlers.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

PATTERN_NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
MAX_NAME = 60
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _arr(x: Any) -> list:
    return x if isinstance(x, list) else []


def _s(x: Any) -> str:
    return "" if x is None else str(x)


def _int(x: Any) -> int | None:
    """A whole number, or None (booleans and fractions are not)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str) and re.fullmatch(r"-?\d+", x.strip()):
        return int(x.strip())
    return None


def pattern_name(raw: Any) -> str:
    """"calendar months" / "Calendar-Months" -> "CALENDAR_MONTHS"; "" when nothing is left."""
    name = re.sub(r"[^A-Z0-9]+", "_", _s(raw).upper()).strip("_")
    name = re.sub(r"^[0-9_]+", "", name)
    return name[:MAX_NAME].rstrip("_")


# ── handlers: what each pattern's params must be ─────────────────────────
# Each check gets the rule's params and the model, and returns (clean params, None) or
# (None, "why it cannot be used"). It never fills in a value the protocol did not state.

Check = Callable[[dict[str, Any], dict[str, Any], list[str]], "tuple[dict[str, Any] | None, str | None]"]


@dataclass(frozen=True)
class Pattern:
    title: str
    # For the AI: when to use it and what its params are. Kept to one paragraph.
    prompt: str
    check: Check


def _scheduled(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["id"]: v for v in _arr(model.get("visits")) if isinstance(v, dict) and v.get("id")}


def _target_visits(visits: list[str], model: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
    by_id = _scheduled(model)
    if not visits:
        return None, "it names no visit"
    unknown = [v for v in visits if v not in by_id]
    if unknown:
        return None, f"it names visits that are not scheduled visits of this model ({', '.join(unknown)})"
    return [by_id[v] for v in visits], None


def _check_calendar_months(params: dict[str, Any], model: dict[str, Any], visits: list[str]):
    targets, problem = _target_visits(visits, model)
    if problem:
        return None, problem
    months = _int(params.get("months"))
    if months is None or months < 0:
        return None, "“months” (calendar months from the date the visit counts from) is missing or not a whole number ≥ 0"
    out: dict[str, Any] = {"months": months}
    if any(v.get("repeat") for v in targets or []):
        every = _int(params.get("every_months"))
        if every is None or every < 1:
            return None, "the visit repeats, but “every_months” is missing or not a whole number ≥ 1"
        out["every_months"] = every
    missing = _s(params.get("missing_day"))
    if missing not in ("last_day_of_month", "first_day_of_next_month", "not_stated"):
        return None, "“missing_day” must be last_day_of_month, first_day_of_next_month or not_stated"
    out["missing_day"] = missing
    return out, None


def _check_business_days(params: dict[str, Any], model: dict[str, Any], visits: list[str]):
    targets, problem = _target_visits(visits, model)
    if problem:
        return None, problem
    by_id = _scheduled(model)
    for v in targets or []:
        followed = by_id.get(_s(v.get("follows")))
        if v.get("repeat") and not (followed and followed.get("repeat")):
            return None, (f"visit {v.get('code') or v['id']} repeats on its own; business days are only counted from "
                          "an anchor or from each occurrence of the visit it follows")
    n = _int(params.get("business_days"))
    if n is None or n < 1:
        return None, "“business_days” is missing or not a whole number ≥ 1"
    if not isinstance(params.get("within"), bool):
        return None, "“within” must be true (any business day up to then) or false (on that business day)"
    if not isinstance(params.get("excludes_holidays"), bool):
        return None, "“excludes_holidays” must say whether the protocol also excludes public holidays (true or false)"
    return {"business_days": n, "within": params["within"], "excludes_holidays": params["excludes_holidays"]}, None


def _check_weekday(params: dict[str, Any], model: dict[str, Any], visits: list[str]):
    _, problem = _target_visits(visits, model)
    if problem:
        return None, problem
    day = _s(params.get("weekday")).lower()
    if day not in WEEKDAYS:
        return None, "“weekday” must be a day name (monday … sunday)"
    direction = _s(params.get("direction"))
    if direction not in ("on_or_after", "on_or_before"):
        return None, "“direction” must be on_or_after or on_or_before"
    return {"weekday": day, "direction": direction}, None


PATTERNS: dict[str, Pattern] = {
    "CALENDAR_MONTHS": Pattern(
        title="Calendar months (same day of the month)",
        prompt=("a visit counted in calendar months on the same day of the month (\"every calendar month on the same day "
                "as Day 1\", \"3 months after surgery\" when the protocol means calendar months, not 30/28 days). "
                "params: {\"months\": int /* calendar months from the date the visit counts from (its anchor, or the "
                "visit it follows) to its first occurrence */, \"every_months\": int /* repeating visits only */, "
                "\"missing_day\": \"last_day_of_month\"|\"first_day_of_next_month\"|\"not_stated\" /* what the protocol "
                "says when that day does not exist (31st in a 30-day month) */}. Keep the visit's offset_days/every_days "
                "as the nearest day count; the app uses the months."),
        check=_check_calendar_months,
    ),
    "BUSINESS_DAYS": Pattern(
        title="Business days",
        prompt=("a visit or call counted in business/working days (\"within 3 business days after each infusion\"). "
                "params: {\"business_days\": int, \"within\": boolean /* true: any business day up to then */, "
                "\"excludes_holidays\": boolean /* the protocol also excludes public holidays */}. The visit counts "
                "from its anchor or follows the visit it is counted from; offset_days is ignored."),
        check=_check_business_days,
    ),
    "WEEKDAY": Pattern(
        title="Moved to a weekday",
        prompt=("a visit placed on a weekday (\"the first Monday on or after Day 210\", \"the Friday before Week 4\"). "
                "params: {\"weekday\": \"monday\"…\"sunday\", \"direction\": \"on_or_after\"|\"on_or_before\"}. The "
                "visit's own timing gives the day the rule counts from."),
        check=_check_weekday,
    ),
}


def handler_names() -> list[str]:
    return sorted(PATTERNS)


# ── the rules in a model ─────────────────────────────────────────────────
def norm_pattern_rules(items: Any, visit_ids: set[str]) -> list[dict[str, Any]]:
    """Clean the pattern rules the AI wrote. A rule is never dropped for being unknown or
    incomplete: that is exactly what a blocker is. Only entries with nothing to show (no name,
    no label, no quote) are left out."""
    out: list[dict[str, Any]] = []
    for i, r in enumerate(_arr(items)):
        if not isinstance(r, dict):
            continue
        name = pattern_name(r.get("pattern"))
        label = _s(r.get("label")).strip()[:200]
        quote = _s(r.get("quote")).strip()[:600]
        if not (name or label or quote):
            continue
        rid = _s(r.get("id")).strip() or f"pr{i + 1}"
        taken = {x["id"] for x in out}
        if rid in taken:
            n = len(out) + 1
            while f"pr{n}" in taken:
                n += 1
            rid = f"pr{n}"
        page = _int(r.get("page"))
        rule: dict[str, Any] = {
            "id": rid, "pattern": name or "UNNAMED", "label": label or name.replace("_", " ").capitalize() or "Unnamed rule",
            # Unknown visit ids are kept out, but the rule stays (its handler check then reports it).
            "visits": [v for v in dict.fromkeys(_s(x) for x in _arr(r.get("visits"))) if v in visit_ids],
            "params": r["params"] if isinstance(r.get("params"), dict) else {},
            "quote": quote, "page": page,
        }
        if r.get("source"):
            rule["source"] = _s(r["source"])[:300]
        if isinstance(r.get("quote_found"), bool):
            rule["quote_found"] = r["quote_found"]
        if r.get("checked") is True:
            rule["checked"] = True  # ticked by the reviewer (a blocked rule cannot be ticked off)
        # A calculation the analysis generated for a rule no handler covers (app/kit/calc.py): kept as
        # written; rule_status validates it and runs its own test cases.
        if r.get("program") is not None:
            rule["program"] = r["program"]
            rule["output"] = {"kind": _s((r.get("output") or {}).get("kind"))} if isinstance(r.get("output"), dict) else {}
            rule["inputs"] = [{"fact": _s(x.get("fact")), **({"id": _s(x["id"])} if x.get("id") is not None else {})}
                              for x in _arr(r.get("inputs")) if isinstance(x, dict)]
            rule["tests"] = [x for x in _arr(r.get("tests")) if isinstance(x, dict)][:20]
        # Visits the AI named that the model does not have: the rule stays blocked until fixed
        # (kept across clean-ups, which run again when review answers are applied).
        written = [_s(x) for x in [*_arr(r.get("visits")), *_arr(r.get("unknown_visits"))]]
        unknown = [x for x in dict.fromkeys(written) if x and x not in visit_ids]
        if unknown:
            rule["unknown_visits"] = unknown
        out.append(rule)
    return out


def rule_status(rule: dict[str, Any], model: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """(clean params, None) when the rule can be calculated; (None, why) when it is blocked."""
    handler = PATTERNS.get(rule.get("pattern") or "")
    if rule.get("unknown_visits"):
        return None, ("no_handler" if handler is None and rule.get("program") is None
                      else f"it names visits this model does not have ({', '.join(rule['unknown_visits'])})")
    if handler is None:
        if rule.get("program") is not None:
            from .calc import generated_status
            return generated_status(rule, model)
        return None, "no_handler"
    return handler.check(rule.get("params") or {}, model, _arr(rule.get("visits")))


def apply_status(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark each pattern rule handled (clean params) or blocked (with the reason), in place, and
    return the blockers. Run on every clean-up and again by the admin re-check."""
    blockers = []
    for r in _arr(model.get("pattern_rules")):
        params, problem = rule_status(r, model)
        generated = r.get("program") is not None and r.get("pattern") not in PATTERNS
        if problem is None:
            if generated:
                r["generated"] = params  # calc version, content hash, test results, facts read
            else:
                r["params"] = params
            r.pop("blocked", None)
        else:
            r["blocked"] = problem
            r.pop("checked", None)
            r.pop("generated", None)
            blockers.append(blocker_view(r))
    return blockers


def blocker_view(r: dict[str, Any]) -> dict[str, Any]:
    reason = r.get("blocked") or "no_handler"
    return {
        "rule_id": r["id"], "pattern": r["pattern"], "label": r["label"], "quote": r.get("quote") or "",
        "page": r.get("page"), "quote_found": r.get("quote_found"), "visits": r.get("visits") or [],
        "reason": "no_handler" if reason == "no_handler" else "params",
        "problem": None if reason == "no_handler" else reason,
        "has_handler": r["pattern"] in PATTERNS,
        # A calculation the analysis generated, which failed its checks or tests (app/kit/calc.py).
        **({"generated": True} if r.get("program") is not None else {}),
    }


def blockers(model: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The blocked pattern rules of a (cleaned) model, as stored on it, and the operations that cannot
    run as written (a history-based interval or a study event missing a stated part, app/kit/adaptive.py)."""
    from .adaptive import operation_blockers
    return [blocker_view(r) for r in _arr((model or {}).get("pattern_rules")) if r.get("blocked")] + operation_blockers(model)


def enrol_problem(blocked: list[dict[str, Any]]) -> str | None:
    """Why a trial whose model has these blockers cannot enrol, or None."""
    if not blocked:
        return None
    names = ", ".join(dict.fromkeys(b["pattern"] for b in blocked))
    return (f"This trial's protocol has {len(blocked)} rule{'s' if len(blocked) != 1 else ''} the app cannot calculate "
            f"yet ({names}). Patients can be enrolled once the rule{'s are' if len(blocked) != 1 else ' is'} supported.")


def prompt_text() -> str:
    """The instruction and the handled patterns, for the analysis prompt."""
    lines = [f"  · {name}: {p.prompt}" for name, p in sorted(PATTERNS.items())]
    return (
        "- pattern_rules: a rule about WHEN a visit happens that no field above can hold. If it matches one of these "
        "patterns, write it with that name and its params (the app then calculates it):\n" + "\n".join(lines) + "\n"
        "  Any other such rule (a consequence of missed visits, a gap that depends on what happened earlier, "
        "assessments carried over to the next visit, a count of events): still write it, with a NEW name in "
        "UPPER_SNAKE_CASE that describes the general shape, never this protocol (\"CONSECUTIVE_MISSED_VISITS_WITHDRAW\", "
        "\"TREATMENT_DELAY_AFTER_HOLD\"), params holding the values the protocol states, and the visits it concerns. "
        "Every pattern rule has a label, the protocol's own words (quote, max 300 characters, copied exactly) and "
        "its page. A pattern rule replaces the cannot_represent open point for that rule; never write both. "
        "Do not use pattern_rules for anything the fields above can hold.\n"
        + GENERATED_PROMPT)


# Generated calculations (app/kit/calc.py): the analysis writes the missing logic itself, as data.
GENERATED_PROMPT = (
    "  · When such a NEW-named rule is stated clearly enough to calculate, also write the calculation itself, so the "
    "app can run it after the study team approves it: \"program\" (an expression in the calc v1 language below), "
    "\"output\": {\"kind\": \"occurrences\"} (the rule's ONE visit gets exactly the occurrences the program returns; "
    "write that visit normally with its code, name, window and procedures, without repeat) or {\"kind\": \"date\"} "
    "(each occurrence of the rule's visits gets the date the program returns; \"this\" = {n, date, base, anchor}), "
    "\"inputs\": [{\"fact\": \"visit\"|\"anchor\"|\"answer\"|\"event_dates\"|\"today\", \"id\": id}] (every fact it "
    "reads), and \"tests\": at least two cases, including one where the condition does not hold, each {\"name\", "
    "\"facts\": {\"visits\": {visitId: [{\"n\", \"planned\", \"actual\", \"given\"}]}, \"anchors\": {id: date}, "
    "\"answers\": {id: value}, \"events\": {id: [date]}, \"today\": date}, \"this\": {...} (date kind), \"expect\": "
    "the exact result}. If the wording can be read two ways, do NOT write a program: write an ambiguous open point.\n"
    "    calc v1: literals (numbers, true, false, null, {\"str\": text}, {\"date\": \"YYYY-MM-DD\"}); {\"var\": \"o.actual\"}; "
    "{\"fact\": \"visit\", \"id\": V} = V's occurrences {n, key, planned, actual, given (true/false/null), status "
    "(done|planned|waiting|cancelled), window_start, window_end}; {\"fact\": \"anchor\"|\"answer\"|\"event_dates\", "
    "\"id\": id}; {\"fact\": \"today\"}; {\"op\": name, \"args\": [...]} with add_days, days_between, weekday "
    "(0 = Sunday), if, and, or, not, ==, !=, <, <=, >, >=, +, -, *, /, %, floor, min, max, is_null, coalesce, count, "
    "first, last, nth, text; {\"map\": list, \"as\": \"o\", \"do\": expr}; {\"filter\": list, \"as\": \"o\", "
    "\"where\": expr}; {\"let\": {...}, \"in\": expr}; {\"record\": {...}}. An occurrences result is a list of "
    "{\"n\" (unique whole number), \"date\" or null, \"pending\" (why no date yet; required when date is null), "
    "\"window_before\", \"window_after\", \"note\"}. Example: a sample 7 days (+/-1) after each injection that was "
    "given = {\"map\": {\"filter\": {\"fact\": \"visit\", \"id\": \"inj\"}, \"as\": \"o\", \"where\": {\"op\": "
    "\"and\", \"args\": [{\"op\": \"!=\", \"args\": [{\"var\": \"o.given\"}, false]}, {\"op\": \"!=\", \"args\": "
    "[{\"var\": \"o.status\"}, {\"str\": \"cancelled\"}]}]}}, \"as\": \"o\", \"do\": {\"record\": {\"n\": {\"var\": "
    "\"o.n\"}, \"date\": {\"op\": \"if\", \"args\": [{\"var\": \"o.given\"}, {\"op\": \"add_days\", \"args\": "
    "[{\"var\": \"o.actual\"}, 7]}, null]}, \"pending\": {\"op\": \"if\", \"args\": [{\"var\": \"o.given\"}, null, "
    "{\"op\": \"text\", \"args\": [{\"str\": \"7 days after injection \"}, {\"var\": \"o.n\"}, {\"str\": \", once given\"}]}]}, "
    "\"window_before\": 1, \"window_after\": 1}}}.")


SCHEMA_TEXT = (
    '  "pattern_rules": [{"id": string, "pattern": string /* UPPER_SNAKE_CASE */, "label": string /* max 120 characters */, '
    '"visits": [visitId], "params": object, "quote": string /* ONE passage of at most 300 characters copied character '
    'for character from the protocol */, "page": number /* its [[Page n]] */, "program": calc | absent, '
    '"output": {"kind": "occurrences"|"date"} | absent, "inputs": [{"fact": string, "id": string}] | absent, '
    '"tests": [test] | absent /* only for a NEW-named rule the analysis calculates itself, see below */}],'
)
