"""Generated calculations ("calc v1"): logic the analysis writes for one protocol, as data.

When a protocol states a clear timing rule that no kit field and no named handler can hold, the
analysis writes a pattern rule with a ``program``: a small expression tree in the language below,
the inputs it reads, the output it produces and test cases with expected results. Nothing is
executed as code: a fixed interpreter (here for validation, ``frontend/src/features/kit/calc.ts``
in the scheduler, same semantics, checked against shared vectors) evaluates it with a step budget,
so a generated calculation cannot loop, read files, call out or touch anything but the facts the
engine hands it. No isolated Python runner is needed.

A rule with a program is used only when: the program is well formed; every fact it reads is one it
declares and exists in the model; its output has the declared shape; and every one of its own test
cases (at least two) passes. Otherwise it is ``blocked`` with the reason, exactly like a rule with no
handler. Even then it runs only after the study team confirms it in workflow review (an open point
of kind "generated" asks for that), and the approved workflow pins it by content.

Language (JSON):
  literals      numbers, true, false, null; {"str": "text"}; {"date": "YYYY-MM-DD"}
  variables     {"var": "o"} or {"var": "o.actual"}  (bound by map/filter/let; "this" in date rules)
  facts         {"fact": "visit", "id": V}        occurrences of scheduled visit V, in order:
                                                  {n, key, planned, actual, given, status, window_start, window_end}
                {"fact": "anchor", "id": A}       a protocol date, or null
                {"fact": "answer", "id": Q}       a participant, site or decision answer, or null
                {"fact": "event_dates", "id": E}  dates the event was logged, oldest first
                {"fact": "today"}
  functions     {"op": name, "args": [...]}: add_days, days_between, weekday (0 = Sunday), if, and, or,
                not, ==, !=, <, <=, >, >=, +, -, *, /, %, floor, min, max, is_null, coalesce, count,
                first, last, nth (1-based), text (joins values into a string)
  lists         {"map": list, "as": "o", "do": expr}; {"filter": list, "as": "o", "where": expr}
  binding       {"let": {"x": expr, ...}, "in": expr}; records {"record": {"field": expr, ...}}

Outputs: kind "occurrences" (the rule's one visit gets exactly these occurrences): a list of
{n, date, window_before?, window_after?, pending?, note?}; kind "date" (each occurrence of the
rule's visits is re-dated; ``this`` = {n, date, base, anchor}): {date, window_before?, window_after?,
pending?, note?}. A null date needs ``pending``, the reason it has no date yet.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from hashlib import sha256
from typing import Any

VERSION = 1
MAX_NODES = 600
MAX_DEPTH = 40
MAX_STEPS = 200_000
MAX_LIST = 500
FACTS = {"visit", "anchor", "answer", "event_dates", "today"}
OPS = {
    "add_days": 2, "days_between": 2, "weekday": 1, "if": 3, "and": -2, "or": -2, "not": 1,
    "==": 2, "!=": 2, "<": 2, "<=": 2, ">": 2, ">=": 2, "+": -2, "-": 2, "*": -2, "/": 2, "%": 2, "floor": 1,
    "min": -1, "max": -1, "is_null": 1, "coalesce": -1, "count": 1, "first": 1, "last": 1, "nth": 2, "text": -1,
}  # -k: at least k arguments
KINDS = ("occurrences", "date")
OCC_FIELDS = ("n", "key", "planned", "actual", "given", "status", "window_start", "window_end")
NAME = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalcError(Exception):
    pass


def fingerprint(program: Any) -> str:
    return sha256(json.dumps(program, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ── static checks ──────────────────────────────────────────────────────────

def check(program: Any, kind: str, model: dict[str, Any], declared: list[dict[str, Any]]) -> tuple[list[str], set[tuple[str, str]]]:
    """(problems, facts used). Nothing is evaluated."""
    problems: list[str] = []
    used: set[tuple[str, str]] = set()
    ids = {
        "visit": {v["id"] for v in model.get("visits") or []},
        "anchor": {a["id"] for a in model.get("anchors") or []},
        "answer": {q["id"] for q in model.get("questions") or []},
        "event_dates": {e["id"] for e in model.get("events") or []},
    }
    count = 0

    def walk(node: Any, scope: set[str], depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise CalcError("the calculation is too large")
        if node is None or isinstance(node, (bool, int, float)):
            if isinstance(node, float) and not math.isfinite(node):
                problems.append("it contains a number that is not finite")
            return
        if isinstance(node, list):
            problems.append("a list must be built with map or filter, not written out")
            return
        if not isinstance(node, dict) or not node:
            problems.append(f"it contains something that is not an expression: {json.dumps(node)[:60]}")
            return
        keys = set(node)
        if keys == {"str"}:
            if not isinstance(node["str"], str):
                problems.append("a text literal must be text")
        elif keys == {"date"}:
            if not (isinstance(node["date"], str) and ISO.match(node["date"])):
                problems.append("a date literal must be YYYY-MM-DD")
        elif keys == {"var"}:
            name = str(node["var"]).split(".")[0]
            if name not in scope:
                problems.append(f"it uses “{node['var']}”, which is not defined there")
        elif "fact" in node:
            fact = node["fact"]
            if fact not in FACTS or keys - {"fact", "id"}:
                problems.append(f"it reads an unknown fact {json.dumps(node)[:60]}")
            elif fact == "today":
                used.add(("today", ""))
            else:
                fid = str(node.get("id") or "")
                if fid not in ids[fact]:
                    problems.append(f"it reads {fact} “{fid}”, which the model does not have")
                used.add((fact, fid))
        elif "op" in node:
            op, args = node["op"], node.get("args")
            if op not in OPS or keys - {"op", "args"} or not isinstance(args, list):
                problems.append(f"it uses an unknown operation “{op}”")
                return
            want = OPS[op]
            if (want >= 0 and len(args) != want) or (want < 0 and len(args) < -want):
                problems.append(f"“{op}” has the wrong number of arguments")
            for a in args:
                walk(a, scope, depth + 1)
        elif "map" in node or "filter" in node:
            body = "do" if "map" in node else "where"
            source = node.get("map", node.get("filter"))
            name = node.get("as")
            if keys != {("map" if "map" in node else "filter"), "as", body} or not (isinstance(name, str) and NAME.match(name)):
                problems.append("map/filter needs a list, a name (“as”) and its expression")
                return
            walk(source, scope, depth + 1)
            walk(node[body], scope | {name}, depth + 1)
        elif keys == {"let", "in"} and isinstance(node["let"], dict):
            inner = set(scope)
            for name, expr in node["let"].items():
                if not NAME.match(str(name)):
                    problems.append(f"“{name}” is not a usable name")
                walk(expr, inner, depth + 1)
                inner.add(str(name))
            walk(node["in"], inner, depth + 1)
        elif keys == {"record"} and isinstance(node["record"], dict):
            for name, expr in node["record"].items():
                if not NAME.match(str(name)):
                    problems.append(f"“{name}” is not a usable field name")
                walk(expr, scope, depth + 1)
        else:
            problems.append(f"it contains an expression the language does not have: {json.dumps(node)[:80]}")

    if kind not in KINDS:
        return [f"its output kind must be one of {', '.join(KINDS)}"], used
    try:
        walk(program, {"this"} if kind == "date" else set(), 0)
    except CalcError as error:
        problems.append(str(error))
    declared_set = {(str(d.get("fact")), str(d.get("id") or "")) for d in declared if isinstance(d, dict)}
    undeclared = sorted(f"{f} {i}".strip() for f, i in used - declared_set)
    if undeclared:
        problems.append(f"it reads inputs it does not declare ({', '.join(undeclared)})")
    return list(dict.fromkeys(problems)), used


# ── evaluation ─────────────────────────────────────────────────────────────

def _d(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str) and ISO.match(value):
        return date.fromisoformat(value)
    raise CalcError(f"expected a date, got {value!r}")


def _num(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalcError(f"expected a number, got {value!r}")
    return value


def _cmp(op: str, a: Any, b: Any) -> bool:
    # Nothing compares as smaller or larger than a missing value; == and != still say whether both are missing.
    if a is None or b is None:
        both = a is None and b is None
        return both if op == "==" else (not both) if op == "!=" else False
    if isinstance(a, bool) or isinstance(b, bool):
        if op in ("==", "!="):
            return (a == b) == (op == "==")
        raise CalcError("cannot order yes/no values")
    if isinstance(a, date) or isinstance(b, date):
        a, b = _d(a), _d(b)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        raise CalcError(f"cannot compare {a!r} with {b!r}")
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


class _Run:
    def __init__(self, facts: dict[str, Any]):
        self.facts = facts
        self.steps = 0

    def ev(self, node: Any, env: dict[str, Any]) -> Any:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise CalcError("the calculation took too many steps")
        if node is None or isinstance(node, (bool, int, float)):
            return node
        if "str" in node:
            return node["str"]
        if "date" in node and len(node) == 1:
            return date.fromisoformat(node["date"])
        if "var" in node:
            name, *path = str(node["var"]).split(".")
            value = env[name]
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            return value
        if "fact" in node:
            return self.fact(node["fact"], node.get("id"))
        if "op" in node:
            return self.op(node["op"], node["args"], env)
        if "map" in node or "filter" in node:
            items = self.ev(node.get("map", node.get("filter")), env)
            if items is None:
                return []
            if not isinstance(items, list):
                raise CalcError("map/filter needs a list")
            if "map" in node:
                out = [self.ev(node["do"], {**env, node["as"]: x}) for x in items]
            else:
                out = [x for x in items if self.ev(node["where"], {**env, node["as"]: x}) is True]
            if len(out) > MAX_LIST:
                raise CalcError("a list is too long")
            return out
        if "let" in node:
            inner = dict(env)
            for name, expr in node["let"].items():
                inner[name] = self.ev(expr, inner)
            return self.ev(node["in"], inner)
        if "record" in node:
            return {k: self.ev(v, env) for k, v in node["record"].items()}
        raise CalcError("unknown expression")

    def fact(self, kind: str, fid: Any) -> Any:
        f = self.facts
        if kind == "today":
            return _d(f.get("today"))
        if kind == "visit":
            return [{**o, "planned": _d(o.get("planned")), "actual": _d(o.get("actual")),
                     "window_start": _d(o.get("window_start")), "window_end": _d(o.get("window_end"))}
                    for o in (f.get("visits") or {}).get(fid) or []]
        if kind == "anchor":
            return _d((f.get("anchors") or {}).get(fid))
        if kind == "answer":
            return (f.get("answers") or {}).get(fid)
        return sorted(_d(x) for x in (f.get("events") or {}).get(fid) or [])

    def op(self, op: str, args: list, env: dict[str, Any]) -> Any:
        if op == "if":
            return self.ev(args[1] if self.ev(args[0], env) is True else args[2], env)
        if op == "and":
            return all(self.ev(a, env) is True for a in args)
        if op == "or":
            return any(self.ev(a, env) is True for a in args)
        v = [self.ev(a, env) for a in args]
        if op == "not":
            return v[0] is not True
        if op == "is_null":
            return v[0] is None
        if op == "coalesce":
            return next((x for x in v if x is not None), None)
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return _cmp(op, v[0], v[1])
        if op == "count":
            return len(v[0] or [])
        if op in ("first", "last"):
            items = v[0] or []
            return (items[0] if op == "first" else items[-1]) if items else None
        if op == "nth":
            items, i = v[0] or [], _num(v[1])
            return items[int(i) - 1] if i is not None and 1 <= i <= len(items) else None
        if op == "text":
            return "".join("" if x is None else x.isoformat() if isinstance(x, date) else _text(x) for x in v)
        if any(x is None for x in v):
            return None
        if op == "add_days":
            days = _num(v[1])
            if days != int(days):
                raise CalcError("add_days needs a whole number of days")
            return _d(v[0]) + timedelta(days=int(days))
        if op == "days_between":
            return (_d(v[1]) - _d(v[0])).days
        if op == "weekday":
            return (_d(v[0]).weekday() + 1) % 7
        if op in ("min", "max"):
            vals = [_d(x) if isinstance(x, (date, str)) else _num(x) for x in v]
            return (min if op == "min" else max)(vals)
        nums = [_num(x) for x in v]
        if op == "+":
            return sum(nums)
        if op == "*":
            out = 1
            for x in nums:
                out *= x
            return out
        if op == "-":
            return nums[0] - nums[1]
        if op in ("/", "%"):
            if nums[1] == 0:
                raise CalcError("division by zero")
            # % is the mathematical remainder (never negative for a positive divisor), as in calc.ts.
            return nums[0] / nums[1] if op == "/" else nums[0] - nums[1] * math.floor(nums[0] / nums[1])
        if op == "floor":
            return math.floor(nums[0])
        raise CalcError(f"unknown operation {op}")


def _text(x: Any) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(x) for x in value]
    if isinstance(value, dict):
        return {k: _plain(x) for k, x in value.items()}
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def evaluate(program: Any, facts: dict[str, Any], this: dict[str, Any] | None = None) -> Any:
    """The program's result as plain JSON (dates as YYYY-MM-DD). Raises CalcError."""
    env = {"this": {k: _d(x) if k in ("date", "base", "anchor") else x for k, x in this.items()}} if this else {}
    return _plain(_Run(facts).ev(program, env))


def output_problem(kind: str, out: Any) -> str | None:
    def one(item: Any, where: str) -> str | None:
        if not isinstance(item, dict):
            return f"{where} is not a record"
        if item.get("date") is None and not item.get("pending"):
            return f"{where} has no date and no reason (pending)"
        if item.get("date") is not None and not (isinstance(item["date"], str) and ISO.match(item["date"])):
            return f"{where} has a date that is not a date"
        for k in ("window_before", "window_after"):
            if item.get(k) is not None and (isinstance(item[k], bool) or not isinstance(item[k], (int, float)) or item[k] < 0):
                return f"{where} has an unusable {k}"
        return None

    if kind == "date":
        return None if isinstance(out, str) and ISO.match(out) else one(out, "the result")
    if not isinstance(out, list):
        return "the result is not a list of occurrences"
    seen = set()
    for i, item in enumerate(out):
        problem = one(item, f"occurrence {i + 1}")
        if problem:
            return problem
        n = item.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or n in seen:
            return f"occurrence {i + 1} needs its own whole number n (1 or more)"
        seen.add(n)
    return None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) == float(b)
    return a == b


def complete_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Test facts with the occurrence fields the engine always provides filled in."""
    out = dict(facts or {})
    visits = {}
    for vid, occs in (out.get("visits") or {}).items():
        rows = []
        for o in occs or []:
            row = {k: o.get(k) for k in OCC_FIELDS}
            row["key"] = row["key"] or f"{vid}#{row['n']}"
            if row["status"] is None:
                row["status"] = "done" if row["actual"] else "planned" if row["planned"] else "waiting"
            if row["given"] is None and "given" not in o:
                row["given"] = True if row["actual"] else None
            rows.append(row)
        visits[vid] = rows
    out["visits"] = visits
    return out


def run_tests(rule: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for t in rule.get("tests") or []:
        item = {"name": str(t.get("name") or f"case {len(results) + 1}")[:120], "passed": False}
        try:
            got = evaluate(rule["program"], complete_facts(t.get("facts") or {}), t.get("this"))
            problem = output_problem(rule["output"]["kind"], got)
            item.update(got=got, passed=problem is None and _same(got, t.get("expect")), problem=problem)
        except (CalcError, KeyError, TypeError, ValueError, OverflowError) as error:
            item.update(got=None, problem=f"error: {error}")
        item["expected"] = t.get("expect")
        results.append(item)
    return results


def generated_status(rule: dict[str, Any], model: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Like a handler's params check: (status, None) when the generated calculation may be used,
    (None, why) when it is blocked."""
    output = rule.get("output") if isinstance(rule.get("output"), dict) else {}
    kind = output.get("kind")
    scheduled = {v["id"]: v for v in model.get("visits") or []}
    if kind == "occurrences" and (len(rule.get("visits") or []) != 1):
        return None, "an occurrences calculation produces the dates of exactly one visit"
    if kind == "date" and not rule.get("visits"):
        return None, "it names no visit to date"
    if kind == "occurrences" and scheduled.get(rule["visits"][0], {}).get("repeat"):
        return None, "the visit it produces must not also repeat on its own"
    problems, used = check(rule.get("program"), kind, model, rule.get("inputs") or [])
    if kind == "occurrences" and ("visit", rule["visits"][0]) in used:
        problems.append("it reads the visit it produces")
    if problems:
        return None, "; ".join(problems)
    tests = rule.get("tests") or []
    if len(tests) < 2:
        return None, "it needs at least two test cases with expected results"
    results = run_tests(rule)
    failed = [r for r in results if not r["passed"]]
    if failed:
        f = failed[0]
        return None, (f"its own test “{f['name']}” fails: expected {json.dumps(f['expected'])[:160]}, got "
                      f"{json.dumps(f.get('got'))[:160]}" + (f" ({f['problem']})" if f.get("problem") else ""))
    return {"calc_version": VERSION, "sha256": fingerprint(rule["program"]), "tests": results,
            "reads": sorted(f"{f} {i}".strip() for f, i in used)}, None


# ── readable form for the study team ───────────────────────────────────────

def render(node: Any) -> str:
    if node is None:
        return "nothing"
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int, float)):
        return str(node)
    if not isinstance(node, dict):
        return "?"
    if "str" in node:
        return json.dumps(node["str"])
    if "date" in node and len(node) == 1:
        return node["date"]
    if "var" in node:
        return str(node["var"])
    if "fact" in node:
        return {"visit": "occurrences of {}", "anchor": "date {}", "answer": "answer {}", "event_dates": "dates {} was logged",
                "today": "today"}[node["fact"]].format(node.get("id"))
    if "op" in node:
        a = [render(x) for x in node["args"]]
        op = node["op"]
        if op == "if":
            return f"(if {a[0]} then {a[1]} else {a[2]})"
        if op in ("==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "%"):
            return "(" + f" {op} ".join(a) + ")"
        if op in ("and", "or"):
            return "(" + f" {op} ".join(a) + ")"
        if op == "add_days":
            return f"{a[0]} + {a[1]} days"
        return f"{op}({', '.join(a)})"
    if "map" in node:
        return f"for each {node['as']} in {render(node['map'])}: {render(node['do'])}"
    if "filter" in node:
        return f"[{node['as']} in {render(node['filter'])} where {render(node['where'])}]"
    if "let" in node:
        return "let " + ", ".join(f"{k} = {render(v)}" for k, v in node["let"].items()) + f"; {render(node['in'])}"
    if "record" in node:
        return "{" + ", ".join(f"{k}: {render(v)}" for k, v in node["record"].items()) + "}"
    return "?"
