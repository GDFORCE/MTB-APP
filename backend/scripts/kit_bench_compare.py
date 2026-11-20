"""Compare the schedules a kit model gives with an expected answer written independently of the app.

    python scripts/kit_bench_compare.py <model.json | bench run folder> <expected.json> [--out report.json] [--quiet]

The expected file does not use the model's ids (the AI chooses those), only words:

{
  "cases": [{
    "name": "on time, arm B",
    "given": {
      "today": "2026-06-01",
      "dates":   [{"what": ["screening"], "date": "2026-03-02"}, {"what": ["day 1", "randomi"], "date": "2026-03-16"}],
      "facts":   [{"what": ["arm", "treatment group"], "answer": ["arm b", "b"]}],
      "events":  [{"what": ["withdraw"], "date": "2026-04-20", "answers": [{"what": ["reason"], "answer": ["consent"]}]}],
      "actuals": [{"visit": ["week 4"], "n": null, "date": "2026-04-15"}],
      "checks":  [{"visit": ["cycle 2 day 1"], "n": 2, "entries": [{"date": "2026-04-06", "answer": "no"}]}]
    },
    "expect": {
      "visits": [{"name": "Week 4", "match": ["week 4", "w4"], "date": "2026-04-13", "window": ["2026-04-10", "2026-04-16"],
                  "modality": "site", "has": [["ecg", "electrocardiogram"]], "lacks": [["pk"]]}],
      "absent": [{"name": "Week 8", "match": ["week 8"], "after": "2026-04-20"}],
      "flags":  [{"name": "window not stated at Day 1", "any": [["window", "day 1"], ["window", "baseline"]]}]
    }
  }]
}

A fact, date or event the model has no place for is reported ("could not be entered"); the case
still runs with what could be entered. Words match whole tokens, in order ("week 4" does not match
"week 40"). ``has``/``lacks`` look at the assessments of every visit row on the matched date,
because one protocol visit may be modelled as several rows on one day (assessments, then dosing).
``flags`` are things the app must tell the reviewer rather than decide: each is looked for in the
model's review questions, open points and notes.

Schedules are computed by the app's engine (frontend/scripts/kit-schedule-cli.ts).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = BACKEND.parent / "frontend"


def tokens(text: Any) -> list[str]:
    # "Day -1" keeps its sign; "12-lead", "W-1" and "2026-03-02" do not gain one.
    return re.findall(r"[a-z]+|(?:(?<![a-z0-9)])-)?\d+(?:\.\d+)?", str(text or "").lower().replace("−", "-"))


def phrase_in(phrase: str, toks: list[str]) -> bool:
    """All of the phrase's tokens, adjacent and in order. A phrase token ending in * is a prefix."""
    want = re.findall(r"[a-z]+\*?|-?\d+(?:\.\d+)?", phrase.lower())
    if not want:
        return False
    for i in range(len(toks) - len(want) + 1):
        if all(toks[i + j].startswith(w[:-1]) if w.endswith("*") else toks[i + j] == w for j, w in enumerate(want)):
            return True
    return False


def matches(alternatives: Any, text: Any) -> bool:
    """True when any alternative matches; an alternative that is a list needs all its phrases."""
    toks = tokens(text)
    for alt in alternatives or []:
        parts = alt if isinstance(alt, list) else [alt]
        if all(phrase_in(p, toks) for p in parts):
            return True
    return False


def compute(model: dict[str, Any], site: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=str(BACKEND.parent)) as tmp:
        job, out = Path(tmp) / "job.json", Path(tmp) / "out.json"
        job.write_text(json.dumps({"model": model, "site": site, "records": records}), encoding="utf-8")
        run = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", "--import", "./tests/kit-resolve-hook.mjs",
             "scripts/kit-schedule-cli.ts", str(job), str(out)],
            cwd=str(FRONTEND), capture_output=True, text=True)
        if run.returncode != 0:
            raise RuntimeError(f"schedule engine failed: {run.stderr[-2000:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def _q_text(q: dict[str, Any]) -> str:
    return " ".join(str(q.get(k) or "") for k in ("id", "label", "text", "help"))


def _answer_for(q: dict[str, Any], answer: Any) -> str | None:
    if q.get("kind") == "number":
        return str(answer) if isinstance(answer, (int, float)) and not isinstance(answer, bool) else None
    if q.get("kind") == "boolean":
        if isinstance(answer, bool):
            return "yes" if answer else "no"
        if isinstance(answer, str) and answer.lower() in ("yes", "no"):
            return answer.lower()
        return None
    alts = answer if isinstance(answer, list) else [str(answer)]
    for o in q.get("options") or []:
        if matches(alts, f"{o.get('value')} {o.get('label')}"):
            return o["value"]
    return None


def build_record(model: dict[str, Any], given: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """The scenario as a participant record with this model's ids, the site answers, and what
    could not be entered."""
    problems: list[str] = []
    start = next((a for a in model["anchors"] if a["kind"] == "start"), None)
    record: dict[str, Any] = {"startAnchor": start["id"] if start else None, "startDate": None, "answers": {},
                              "events": [], "actuals": {}, "doseTimes": {}, "checks": {}, "baselines": {}}
    if given.get("today"):
        record["today"] = given["today"]
    site: dict[str, Any] = {}
    used_dates = set()
    for a in model["anchors"]:
        text = f"{a['id']} {a.get('label')} {a.get('question') or ''}"
        hit = next((i for i, d in enumerate(given.get("dates") or []) if i not in used_dates and matches(d["what"], text)), None)
        if hit is None:
            continue
        if not given["dates"][hit].get("all"):
            used_dates.add(hit)
        date = given["dates"][hit]["date"]
        if a["kind"] == "start":
            record["startDate"] = date
        elif a["kind"] == "last_visit":
            problems.append(f"date “{given['dates'][hit]['what'][0]}” is worked out by the model ({a['id']}), not entered")
        else:
            record["answers"]["@" + a["id"]] = date
    if start and record["startDate"] is None:
        problems.append(f"the model's start date “{start.get('label')}” ({start['id']}) is not among the scenario's dates")
    for i, d in enumerate(given.get("dates") or []):
        if i not in used_dates and not d.get("optional") and not d.get("all"):
            problems.append(f"date “{d['what'][0]}” ({d['date']}): the model has no date for it")
    event_questions = {q["id"] for e in model.get("events") or [] for q in e.get("questions") or []}
    for f in given.get("facts") or []:
        qs = [q for q in model["questions"] if matches(f["what"], _q_text(q)) and not matches(f.get("unless"), _q_text(q))]
        done = False
        for q in qs:
            value = _answer_for(q, f["answer"])
            if value is None:
                continue
            (site if q.get("scope") == "study" else record["answers"])[q["id"]] = value
            done = True
            if not f.get("all"):
                break
        if not done and not f.get("optional"):
            where = "its options do not include the answer" if qs else "the model asks no such question"
            problems.append(f"fact “{f['what'][0]}” = {f['answer']!r}: {where}")
    for n, e in enumerate(given.get("events") or []):
        d = next((x for x in model.get("events") or [] if matches(e["what"], f"{x['id']} {x.get('label')}")), None)
        if d is None:
            problems.append(f"event “{e['what'][0]}” ({e['date']}): the model has no such event")
            continue
        answers = {}
        for f in e.get("answers") or []:
            q = next((q for q in d.get("questions") or [] if matches(f["what"], _q_text(q))), None)
            value = _answer_for(q, f["answer"]) if q else None
            if value is None or q is None:
                if not f.get("optional"):
                    problems.append(f"event “{e['what'][0]}” answer “{f['what'][0]}”: not asked when the event is logged")
            else:
                answers[q["id"]] = value
        record["events"].append({"key": f"e{n + 1}", "type": d["id"], "date": e["date"], "answers": answers})
    _ = event_questions
    return record, site, problems


def _row_text(row: dict[str, Any]) -> str:
    return f"{row.get('code')} {row.get('name')}"


def _find_rows(schedule: dict[str, Any], what: Any, n: int | None = None) -> list[dict[str, Any]]:
    rows = [r for r in schedule.get("rows", []) if r.get("type") == "visit" and matches(what, _row_text(r))]
    if n is not None:
        rows = [r for r in rows if str(r["key"]).endswith(f"#{n}")] or rows[n - 1:n]
    return rows


def add_recorded(model: dict[str, Any], site: dict[str, Any], record: dict[str, Any], given: dict[str, Any],
                 problems: list[str]) -> dict[str, Any]:
    """Recorded visit dates and check answers, entered one at a time in the order given: each one
    is placed on the visit row the schedule shows at that point, as staff would."""
    measures = model.get("measures") or []
    steps = [("actual", a) for a in given.get("actuals") or []] + [("check", c) for c in given.get("checks") or []]
    steps.sort(key=lambda s: s[1].get("order", 0))
    for kind, item in steps:
        schedule = compute(model, site, {"r": record})["r"]
        rows = _find_rows(schedule, item["visit"], item.get("n"))
        if item.get("on"):
            rows = [r for r in rows if r.get("date") == item["on"] or r.get("planned") == item["on"]] or rows
        if not rows:
            problems.append(f"{kind} for visit “{item['visit'][0]}”: no such visit on the schedule")
            continue
        row = rows[0]
        if kind == "actual":
            # "all": one protocol visit modelled as several rows on a day (assessments, then dosing).
            same_day = [r for r in rows if r.get("date") == row.get("date")] if item.get("all") else [row]
            for r in same_day:
                record["actuals"][r["key"]] = item["date"]
                if item.get("time"):
                    record["doseTimes"][r["key"]] = item["time"]
            continue
        row = next((r for r in rows if r.get("check")), None)
        if row is None:
            problems.append(f"check at visit “{item['visit'][0]}”: the model asks nothing there")
            continue
        entries = []
        for e in item["entries"]:
            entry: dict[str, Any] = {"date": e["date"]}
            if e.get("answer"):
                entry["answer"] = e["answer"]
            values = {}
            for what, value in (e.get("values") or {}).items():
                m = next((m for m in measures if matches([what], f"{m['id']} {m.get('label')}")), None)
                if m is None or m["id"] not in [x["id"] for x in row["check"].get("measures") or []]:
                    problems.append(f"value “{what}” at “{item['visit'][0]}”: the check takes no such value")
                else:
                    values[m["id"]] = value
            if values:
                entry["values"] = values
            if "answer" in entry or "values" in entry:
                entries.append(entry)
        record["checks"].setdefault(row["key"], []).extend(entries)
    for what, value in (given.get("baselines") or {}).items():
        m = next((m for m in measures if matches([what], f"{m['id']} {m.get('label')}")), None)
        if m is None:
            problems.append(f"baseline “{what}”: the model has no such value")
        else:
            record["baselines"][m["id"]] = value
    return record


def _proc_names(rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for r in rows:
        for p in r.get("procedures") or []:
            out.append((p["name"], p))
        for tp in r.get("timepoints") or []:
            for name in tp.get("procedures") or []:
                out.append((f"{name} {tp.get('label')}", {"name": name, "timepoint": tp.get("label")}))
    return out


def flag_texts(model: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What the reviewer is shown as open or uncertain: (asked or listed as an open point with the
    protocol passage, only mentioned in the free-text notes)."""
    raised = [f"{q.get('text')} {q.get('why')}" for q in model.get("review_questions") or []]
    for g in model.get("open_points") or []:
        raised.append(" ".join(str(g.get(k) or "") for k in ("text", "quote", "why")))
    # Pattern rules are shown to the reviewer with the protocol's words (calculated or blocked).
    for r in model.get("pattern_rules") or []:
        raised.append(" ".join(str(r.get(k) or "") for k in ("label", "quote")))
    return raised, [str(n) for n in model.get("notes") or []]


def compare_case(model: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    given, expect = case.get("given") or {}, case.get("expect") or {}
    record, site, problems = build_record(model, given)
    findings: list[dict[str, Any]] = [{"kind": "not_entered", "text": p} for p in problems]
    result: dict[str, Any] = {"name": case["name"], "record": record, "site": site, "findings": findings,
                              "checks": 0, "passed": 0}
    if not record.get("startDate"):
        findings.append({"kind": "no_schedule", "text": "no start date could be entered, so there is no schedule"})
        n = len(expect.get("visits") or []) + len(expect.get("absent") or [])
        result["checks"] += n
        return result
    extra_problems: list[str] = []
    record = add_recorded(model, site, record, given, extra_problems)
    findings.extend({"kind": "not_entered", "text": p} for p in extra_problems)
    schedule = compute(model, site, {"r": record})["r"]
    result["record"] = record
    if "error" in schedule:
        findings.append({"kind": "engine_error", "text": schedule["error"][:500]})
        return result
    rows = [r for r in schedule["rows"] if r["type"] == "visit"]
    claimed: set[str] = set()

    def check(ok: bool, kind: str, text: str) -> None:
        result["checks"] += 1
        if ok:
            result["passed"] += 1
        else:
            findings.append({"kind": kind, "text": text})

    for ev in expect.get("visits") or []:
        name = ev["name"]
        cands = _find_rows(schedule, ev["match"], ev.get("n"))
        want = ev.get("date")
        if "date" in ev and want is None:
            # Expected to be on the schedule without a date yet (waiting for something).
            waiting = [r for r in cands if r.get("date") is None]
            check(bool(waiting), "date", f"{name}: expected to wait for a date; "
                  + (f"the app dates it {cands[0].get('date')}" if cands else "not on the schedule"))
            claimed.update(r["key"] for r in cands)
            continue
        on_date = [r for r in cands if r.get("date") == want] if want else cands
        between = ev.get("date_in")
        if between and cands:
            on_date = [r for r in cands if r.get("date") and between[0] <= r["date"] <= between[1]]
            if not on_date:
                claimed.update(r["key"] for r in cands)
                check(False, "date", f"{name}: expected between {between[0]} and {between[1]}, the app gives "
                      + ", ".join(sorted({str(r.get('date')) for r in cands})))
                continue
            want = on_date[0]["date"]
        if not cands:
            by_date = [r for r in rows if want and r.get("date") == want and r["key"] not in claimed]
            check(False, "missing_visit", f"{name}: not on the schedule"
                  + (f" (a visit named “{by_date[0]['name']}” falls on {want})" if by_date else ""))
            continue
        if want and not on_date:
            claimed.update(r["key"] for r in cands)
            check(False, "date", f"{name}: expected {want}, the app gives "
                  + ", ".join(sorted({str(r.get('date')) for r in cands})))
            continue
        check(True, "date", "")
        row = on_date[0]
        # One protocol visit may be several rows on the same day; they are read together.
        group = [r for r in rows if r.get("date") == row.get("date") and (r in on_date or r["key"] not in claimed)] if want else on_date
        group = [r for r in group if r in on_date] + [r for r in group if r not in on_date and ev.get("same_day_rows", True)]
        claimed.update(r["key"] for r in group)
        if "window" in ev:
            if ev["window"] is None:
                stated = any((r.get("windowDays") or [0, 0]) != [0, 0] for r in on_date) or any(
                    r.get("windowStated") is False for r in on_date)
                check(any(r.get("windowStated") is False for r in on_date) and not any(
                    (r.get("windowDays") or [0, 0]) != [0, 0] for r in on_date), "window",
                    f"{name}: the protocol states no window; the app shows "
                    + ("a window" if stated and not any(r.get('windowStated') is False for r in on_date) else "±0 days as if stated"))
            else:
                got = [row.get("windowStart"), row.get("windowEnd")]
                check(got == ev["window"], "window", f"{name}: window expected {ev['window'][0]} to {ev['window'][1]}, "
                      f"the app gives {got[0]} to {got[1]}")
        if ev.get("modality"):
            got = {r.get("modality") for r in on_date}
            check(ev["modality"] in got, "modality", f"{name}: expected {ev['modality']}, the app gives {', '.join(sorted(map(str, got)))}")
        if ev.get("status"):
            check(row.get("status") == ev["status"], "status", f"{name}: expected status {ev['status']}, the app gives {row.get('status')}")
        procs = _proc_names(group)
        for alts in ev.get("has") or []:
            spec = alts if isinstance(alts, dict) else {"any": alts}
            hit = [p for nm, p in procs if matches(spec["any"], nm)]
            label = spec.get("name") or (spec["any"][0] if isinstance(spec["any"][0], str) else " + ".join(spec["any"][0]))
            check(bool(hit), "missing_assessment", f"{name}: “{label}” is not listed")
            if hit and "conditional" in spec:
                cond = any(p.get("conditional") or p.get("condition") or p.get("need") in ("if_indicated", "optional") for p in hit)
                check(cond == spec["conditional"], "assessment_condition",
                      f"{name}: “{label}” should {'depend on a condition' if spec['conditional'] else 'be unconditional'}")
        for alts in ev.get("lacks") or []:
            spec = alts if isinstance(alts, dict) else {"any": alts}
            hit = [nm for nm, _ in procs if matches(spec["any"], nm)]
            label = spec.get("name") or (spec["any"][0] if isinstance(spec["any"][0], str) else " + ".join(spec["any"][0]))
            check(not hit, "extra_assessment", f"{name}: “{label}” is listed ({'; '.join(hit[:2])}) but the protocol does not have it there")
        for spec in ev.get("says") or []:
            # Anything the visit shows: assessments with their own limits, time points, notes.
            shown = json.dumps([{k: r.get(k) for k in ("procedures", "timepoints", "notes", "check", "doses")} for r in group],
                               ensure_ascii=False)
            check(matches(spec["any"], shown), "not_shown", f"{name}: nothing on the visit says “{spec['name']}”")
    for ab in expect.get("absent") or []:
        cands = _find_rows(schedule, ab["match"], ab.get("n"))
        if ab.get("after"):
            cands = [r for r in cands if r.get("date") is None or r["date"] > ab["after"]]
        if ab.get("on"):
            cands = [r for r in cands if r.get("date") == ab["on"]]
        cands = [r for r in cands if r.get("status") != "done"]
        check(not cands, "extra_visit", f"{ab['name']}: should not be on the schedule"
              + (f" after {ab['after']}" if ab.get("after") else "") + "; the app still has "
              + ", ".join(f"{r['code']} {r.get('date')}" for r in cands[:4]))
        claimed.update(r["key"] for r in _find_rows(schedule, ab["match"]))
    if expect.get("no_other_visits"):
        allow = expect.get("allow_extra") or []
        extra = [r for r in rows if r["key"] not in claimed and not matches(allow, _row_text(r))]
        check(not extra, "extra_visit", "visits the protocol does not have for this patient: "
              + ", ".join(f"{r['code']} ({r.get('date') or 'no date'})" for r in extra[:8]))
    raised, noted = flag_texts(model)
    for fl in expect.get("flags") or []:
        if any(matches(fl["any"], t) for t in raised):
            check(True, "not_flagged", "")
        elif any(matches(fl["any"], t) for t in noted):
            check(False, "flag_in_notes_only", f"only a free-text note, no question or open point with the protocol passage: {fl['name']}")
        else:
            check(False, "not_flagged", f"not raised with the reviewer: {fl['name']}")
    for w in expect.get("warnings") or []:
        check(any(matches(w["any"], t) for t in schedule.get("warnings") or []), "no_warning", f"no warning: {w['name']}")
    result["schedule"] = [
        {k: r.get(k) for k in ("key", "code", "name", "date", "windowStart", "windowEnd", "modality", "status")}
        | {"procedures": [p["name"] for p in r.get("procedures") or []]} for r in rows]
    result["warnings"] = schedule.get("warnings") or []
    return result


def load_model(path: Path) -> dict[str, Any]:
    if path.is_dir():
        path = path / "result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["model"] if isinstance(data.get("model"), dict) and "visits" in data["model"] else data


def compare(model: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    cases = [compare_case(model, c) for c in expected.get("cases") or []]
    kinds: dict[str, int] = {}
    for c in cases:
        for f in c["findings"]:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    return {"checks": sum(c["checks"] for c in cases), "passed": sum(c["passed"] for c in cases),
            "findings": kinds, "cases": cases}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        raise SystemExit(2)
    out = None
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
        args = [a for a in args if Path(a) != out]
    model = load_model(Path(args[0]))
    report = compare(model, json.loads(Path(args[1]).read_text(encoding="utf-8")))
    if out:
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{report['passed']}/{report['checks']} checks passed; findings: {report['findings'] or 'none'}")
    if "--quiet" not in sys.argv:
        for c in report["cases"]:
            print(f"\n== {c['name']}: {c['passed']}/{c['checks']}")
            for f in c["findings"]:
                print(f"   [{f['kind']}] {f['text']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
