"""The two prompts sent to the model: the kit's src/ai/prompts.js, plus how schedule-table
grids are written and a request for the model to name the schedule-table pages."""

from __future__ import annotations

import json
from typing import Any

from ._prompt_text import RULES as _KIT_RULES, SCHEMA as _KIT_SCHEMA


def _extend(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        if old not in text:
            raise RuntimeError(f"prompt fragment not found: {old[:60]}")
        text = text.replace(old, new, 1)
    return text


# The kit's schema with the fields added since: date ranges, repeat stages and limits, delay
# limits, every-nth assessments, minimum gaps, preparation, assessment need, richer conditions,
# answer changes and pauses. ``_prompt_text`` itself stays identical to the kit.
SCHEMA = _extend(_KIT_SCHEMA, [
    ('"kind": "start"|"relative"|"ask", "relative_to": anchorId|null,',
     '"kind": "start"|"relative"|"ask"|"last_visit", "relative_to": anchorId|null,\n'
     '     "last_of": [visitId] /* "last_visit" only: the date of the LAST of these visits still on the participant\'s '
     'schedule (recorded if done, planned otherwise). "30 days after the last dose of study drug" = an anchor "last_dose" '
     'with last_of = every dosing visit, and the follow-up visit counted from it: after an early stop it is the last dose '
     'actually given */,'),
    ('"ends_schedule": boolean /* early termination, withdrawal: cancels every later scheduled visit */,',
     '"ends_schedule": boolean /* early termination, withdrawal: cancels every later scheduled visit */,\n'
     '     "keeps_visits": [visitId] /* visits that still happen when this event ends the schedule, only when the protocol '
     'says so: "patients who discontinue have a safety follow-up 30 days after the last dose". Never for death */,'),
    ('"stop_all": boolean /* participation ends: every later visit stops, except visits counted from the anchor this event sets */,',
     '"stop_all": boolean /* participation ends: every later visit stops, except visits counted from the anchor this event sets */,\n'
     '     "keeps_visits": [visitId] /* with stop_all: visits that still happen, as for an event */,'),
    ('"optional": boolean,',
     '"optional": boolean,\n     "range": null | {"relative_to": anchorId, "min_days": number|null, "max_days": number|null} '
     '/* where the protocol allows this date, in days after relative_to (negative = before): "within 28 days before '
     'Day 1" on Day 1 = {"relative_to": "screening", "min_days": 1, "max_days": 28} */,'),
    ('"chain": boolean /* next occurrence counts from the previous one\'s ACTUAL date (treatment cycles) */,',
     '"chain": boolean /* next occurrence counts from the previous one\'s ACTUAL date (treatment cycles; event visits '
     'too): true only when the protocol counts the interval from the previous visit, false when it counts from a fixed '
     'date (Day 1, the event date) */,'),
    ('"stop_visits": [visitId] /* scheduled visits (every occurrence) still to come after that date that no longer happen */,',
     '"stop_visits": [visitId] /* visits (every occurrence) still to come after that date that no longer happen: scheduled '
     'visits, or visits an event added ("starting new therapy stops the post-progression safety visits") */,'),
    ('"until_anchor": anchorId|null /* no occurrence after this anchor\'s date */,',
     '"until_anchor": anchorId|null /* no occurrence after this anchor\'s date */,\n'
     '       "stages": [{"every_days": number, "until_day": number|null}] /* interval changes over time, days counted from the first '
     'occurrence: "every 8 weeks for the first year, then every 12 weeks" = [{"every_days": 56, "until_day": 365}, {"every_days": 84, '
     '"until_day": null}]; [] when the interval never changes */,\n'
     '       "max_days": number|null /* time limit from the first occurrence: "up to 2 years" = 730; with count, whichever comes first */,\n'
     '       "max_delay_days": number|null /* longest delay the protocol allows for an occurrence ("cycles may be delayed up to 21 days") */,\n'
     '       "delay_effect": string|null /* max 100 characters: what happens beyond that delay */,\n'
     '       "until_check": boolean /* repeats every every_days until this visit\'s check is met, then stops: "repeat CrCl '
     'weekly until >= 50 ml/min", "follow up weekly until the AE resolves"; a No there means "not yet", never a hold. Needs a '
     'check; count null unless the protocol caps it */,'),
    ('"overrides": [{"n": [number], "add": [procedure], "remove": [procedure name], "note": string|null}]},',
     '"overrides": [{"n": [number], "every": number|null, "from": number|null, "to": number|null, "add": [procedure], '
     '"remove": [procedure name], "note": string|null}] /* n lists occurrences; or every-nth: "CT every 3rd cycle from '
     'Cycle 3" = {"n": [], "every": 3, "from": 3} */},'),
    ('"include_if": [{"question": questionId, "in": [value]}] /* all must hold; [] = always */,',
     '"include_if": [condition] /* all must hold; [] = always */,'),
    ('"condition": string|null /* max 150 characters, this visit only */,',
     '"condition": string|null /* max 150 characters, this visit only */,\n'
     '    "min_gap": null | {"days": number, "after": visitId|null} /* at least this many days after the previous occurrence '
     '(after null) or after the latest occurrence of another visit ("at least 14 days between doses") */,\n'
     '    "prep": null | {"fasting_hours": number|null, "notes": [string]} /* what the participant must do before the visit: fasting, '
     'medication to hold, bring diary */,\n'
     '    "asap_if_past": boolean /* true when the protocol says "or as soon as possible": a visit kept after participation ends '
     '(keeps_visits) whose date had already passed by then is due that day ("4 weeks after the last dose, or as soon as possible") */,\n'
     '    "arrive_before": null | {"visit": visitId, "hours": number} /* on a check-in or admission visit: "check in at least 12 '
     'hours prior to dosing" = {"visit": the dosing visit, "hours": 12}; the dosing visit with the same occurrence number is meant */,\n'
     '    "check": null | {"id": string, "text": string /* max 90 characters, a yes/no question with the protocol\'s threshold: '
     '"ANC ≥ 1.0 × 10^9/L and platelets ≥ 100 × 10^9/L?" */, "help": string|null, "recheck_days": number|null /* "re-check '
     'weekly" = 7 */, "max_wait_days": number|null /* longest the visit may wait for a Yes, from its planned date: "if not '
     'recovered within 14 days" = 14 */, "on_timeout": "stop"|"warn" /* "stop" only when the protocol says treatment stops then */, '
     '"stop_visits": [visitId] /* other visits that stop with it */, "source": string|null,\n'
     '       "measures": [measureId] /* values entered at this check */, "limits": [limit] /* all must hold for the visit to go '
     'ahead; the Yes/No is then worked out from the entered values. [] when the protocol gives no measurable limit */,\n'
     '       "dose_changes": [{"if": [limit] /* required: when this change applies; may also use {"held_days": {"op": ">", '
     '"value": 7}} = the visit waited more than 7 days */, "dose": doseId, "step": number|null /* -1 = one level lower */, '
     '"to": levelId|null, "stop": boolean /* the drug is discontinued */, "occurrence": number|null /* only the n-th time '
     'these conditions hold */, "and_later": boolean /* the n-th time and every later time */, "note": string|null}] '
     '/* applied to this occurrence and later doses */,\n'
     '       "actions": [{"if": [limit], "do": "give"|"reduce"|"skip"|"hold", "percent": number|null /* reduce: percent of the '
     'dose, this time only */, "dose": doseId|null, "note": string|null}] /* a dosing table for this visit, read top to bottom, '
     'first match wins: "Day 8: ANC >= 1500 and platelets >= 100000: 100%; ANC 1000-1499 or platelets 75000-99999: 50%; ANC < 1000 '
     'or platelets < 75000: hold" = give / reduce 50 / skip (a Day 8 or Day 15 dose that is omitted, not made up, is "skip"; '
     '"hold" only when the dose waits and is given later). Every row needs its limits; [] when the protocol has no such table */} '
     '/* the visit goes ahead only if this is '
     'met on the day, asked at every occurrence: a "No" holds it until a "Yes" */,'),
    ('  "visits": [visit],\n  "events": [',
     '  "measures": [{"id": string, "label": string /* "ANC", "Peripheral neuropathy" */, "unit": string|null /* "× 10^9/L", '
     '"× ULN" */, "kind": "value"|"grade" /* grade = CTCAE grade 0-5 */, "worse": "lower"|"higher"|null /* which way is '
     'worse: ANC lower, creatinine higher; null if unclear */, "baseline": string|null /* what the protocol calls baseline: '
     '"Screening value" */}] /* values the protocol\'s dosing or hold limits use; [] if none */,\n'
     '  "doses": [{"id": string, "label": string /* drug name */, "levels": [{"id": string /* "0", "-1", "-2" */, "label": string '
     '/* "100 mg", "75 mg" */, "drop_visits": [visitId] /* visits of this drug not given at this level: "second reduction: '
     '800 mg/m2 on Day 1 only (Day 8 omitted)" = the Day 8 visit */}] /* highest dose first, as the dose-modification table lists them */, "start": levelId|null '
     '/* starting level; null if not stated */, "visits": [visitId] /* visits where it is given */, "below_lowest": '
     '"stop"|"warn" /* "stop" only when the protocol says a reduction below the lowest level discontinues the drug */, '
     '"stop_visits": [visitId] /* other visits that stop with the drug */, "source": string|null}] /* [] if the protocol '
     'has no dose levels */,\n'
     '  "visits": [visit],\n  "events": ['),
    ('and procedure = string | {"name": string, "if": [{"question": questionId, "in": [value]}]}',
     'and procedure = string | {"name": string, "if": [condition], "need": "required"|"if_indicated"|"optional" '
     '/* "if clinically indicated", "at investigator discretion", "per local practice" = if_indicated */, '
     '"reuse_within_days": number|null /* "results obtained within 72 h may be used" = 3 */, '
     '"within_before": null | {"days": number} | {"hours": number} /* this assessment\'s own limit before the visit when it differs '
     'from the visit window: "pregnancy test within 3 days before dosing" = {"days": 3}, "labs within 72 h" = {"hours": 72} */, '
     '"source": string|null}\n'
     'and condition = {"question": questionId, "in": [value]} | {"question": questionId, "op": "="|"!="|"<"|"<="|">"|">=", '
     '"value": number|string} /* age >= 65, weight < 50 */ | {"any": [condition]} /* at least one */ | {"not": condition} '
     '| {"event": eventId, "happened": boolean, "before": null | {"visit": visitId, "n": number|null} | {"anchor": anchorId}} '
     '/* logged on or before that visit ("after confirmed progression"); with before: logged before that visit or date ("patients '
     'who stop treatment before Week 12" = {"event": "stop_tx", "happened": true, "before": {"visit": "w12"}}) */ '
     '| {"visit": visitId, "n": number|null, "status": "done"|"missed"|"not_done"} /* "if C1D1 was missed" */'),
    ('"source": string|null /* "Section 3.6.2.1.8, p. 74; SoA footnote 13" */}],',
     '"source": string|null /* "Section 3.6.2.1.8, p. 74; SoA footnote 13" */,\n'
     '     "set_answers": null | {questionId: value} /* answers that change from the trigger date on: crossover to Arm B = {"arm": "B"} */,\n'
     '     "pause": null | {"visits": [visitId], "resume": anchorId|null, "mode": "shift"|"skip"} /* treatment interruption: visits '
     'stop from the trigger until the resume anchor\'s date; "shift" moves later visits by the pause length, "skip" drops the missed '
     'ones */,\n'
     '     "dose_changes": [{"dose": doseId, "step": number|null, "to": levelId|null, "stop": boolean}] /* dose level changes '
     'from the trigger date on: grade 3 AE logged = {"dose": "drug_x", "step": -1} */}],'),
])

# A visit check's limit on entered values (used in "limits" and in dose_changes "if").
LIMIT_SCHEMA = (
    'and limit = {"measure": measureId, "op": "<"|"<="|">"|">=", "value": number} /* ANC >= 1.0 */ '
    '| {"measure": measureId, "op": ..., "baseline_factor": number} /* creatinine <= 1.5 x baseline; "ANC fell by more than 50% '
    'from baseline" fails when ANC < 0.5 x baseline */ | {"any": [limit]} /* "grade <= 1 or back to baseline" */; a measure limit '
    'may add "of": "latest" (default, the value entered now) | "worst_here" (the worst value entered for this visit, e.g. during '
    'the hold) | "worst_so_far" (the worst value entered for the participant so far), and "for_days": number when the protocol '
    'says how long it lasted ("grade 4 neutropenia lasting 7 days or more" = {"measure": "anc", "op": "<", "value": 500, '
    '"for_days": 7}; only with "of": "latest").')

# Kit instructions that made the reviewer decide things the participant's own dates settle.
RULES = _extend(_KIT_RULES, [
    ('If screening is a range, make Day 1 relative with a typical offset, set_by_visit = the Day 1 visit, and add a review_question about the offset.',
     'When the protocol gives a range for a date ("screening within 28 days before Day 1", "surgery 3-6 weeks after the last dose"), '
     'give that anchor its range and a typical offset_days for planning; it is entered per participant, so never ask a review_question '
     'about the gap. Day 1 keeps set_by_visit = the Day 1 visit.'),
    ('count = how many to show ahead (default 6), and a review_question about the count.',
     'count = how many to show ahead (default 6); never ask a review_question about that count.'),
    ('- review_questions: up to 6, only where the protocol leaves a real choice or is ambiguous (ranges, "at least", conflicting table and text, open-ended counts).',
     '- review_questions: at most 3, only where the protocol is ambiguous or contradicts itself AND the answer changes visit dates or which '
     'visits/assessments happen (conflicting table and text, an unclear window or interval). Never ask about a date range (use range), '
     'how many open-ended visits to show, or anything the protocol states.'),
])


GRID_LEGEND = (
    'Schedule tables found by their ruled cells are given as grids ("[[Page n · schedule table as a grid]]"): '
    'one table row per line, cells separated by "|", a blank cell is empty, "←" is part of the merged cell '
    'to its left and "↑" part of the merged cell above (a header spanning several visits, or a label that wraps '
    "onto the next line). Any mark in a cell (X, ○, ✓, •, a symbol or a footnote letter) means that column's "
    "visit includes that row.")

RULE_CONDITIONS = (
    'A rule may also have "if": [condition] (all must hold); it then acts only in the case they describe. '
    'A condition is one of: {"question": questionId, "in": [value]} (a participant, site or decision answer); '
    '{"event_question": questionId, "in": [value]} (an answer given when the triggering event is logged: add '
    'that question, e.g. the grade, to the event); {"timing": {"anchor": anchorId} or {"visit": visitId, "n": '
    'occurrence|null}, "min_days": number|null, "max_days": number|null} (days from that date to the trigger '
    'date, inclusive: before Cycle 3 = {"visit": "c3", "max_days": -1}, on or after = {"visit": "c3", '
    '"min_days": 0}); {"occurrence": n} (only the n-th time the event is logged).')

# Added after the kit's rules; ``_prompt_text`` itself stays identical to the kit.
TABLE_RULES = (
    f"- {GRID_LEGEND}\n"
    '- Also return a top-level "schedule_table_pages": [number]: the [[Page n]] numbers of every page holding any '
    "part of a schedule of activities/assessments, visit flow chart or sampling-time table, including "
    "continuation pages and pages carrying only that table's footnotes.\n"
    f"- {RULE_CONDITIONS} When the protocol gives different consequences for the same event depending on when "
    "it happens, what is recorded with it, who the participant is, or how many times it happened, write one "
    'rule per branch, each with an "if", and make the branches exclude each other. Never write two rules for '
    'the same trigger without conditions that tell them apart. "allowed" only says when the trigger may '
    "happen at all (a date outside it is an error); it never chooses between branches. A rule's answer "
    'conditions may also use "op", "any" and "not" like visit conditions.\n'
    "- Write what the protocol states with the matching field instead of prose: a changing interval (stages), a "
    "time limit (max_days), an allowed delay (max_delay_days, delay_effect), a minimum gap (min_gap), preparation "
    "(prep), assessments done only if clinically indicated (need), crossover (rule set_answers), treatment "
    "interruption and resumption (rule pause, with an ask anchor for the resume date). When the protocol gives no "
    "number of repeats, leave count null; never invent one.\n"
    "- A dose or visit that may go ahead only when something measured that day meets a limit (ANC, platelets, "
    "creatinine, recovery of a toxicity to grade 1 or less) gets a visit check: one yes/no question with the "
    "protocol's limits in its text, asked at every occurrence. Combine the limits for the same visit into one "
    "question. recheck_days, max_wait_days and on_timeout \"stop\" only when the protocol states them; otherwise "
    "null, null and \"warn\".\n"
    "- When the protocol gives those limits as numbers, also add each value to top-level \"measures\" and write the "
    "check's \"limits\" so the answer is worked out from the values staff enter (relative to baseline with "
    "baseline_factor). Put a toxicity grade in measures with kind \"grade\". Leave limits [] when no number is given.\n"
    "- When the protocol has dose levels (a dose-modification table), list them in top-level \"doses\". Dose reductions "
    "or discontinuation that follow from a check go in that check's \"dose_changes\" with their conditions (the "
    "value or grade, \"worst_here\" for the worst during the hold, held_days for how long recovery took, occurrence for "
    "\"second occurrence\"). Ones that follow a logged event (an adverse event with its grade) go in a rule's "
    "\"dose_changes\". Never invent a level, a step or a condition the protocol does not state.\n"
    f"- {LIMIT_SCHEMA}\n"
    "- A visit counted from the last dose (or last visit) of several visits, \"30 days after the last study drug "
    "administration\", counts from an anchor of kind \"last_visit\" whose last_of lists those visits, never from one "
    "period's or cycle's dose. When the protocol says it still happens for patients who stop early, list it in "
    "keeps_visits of each event (or stop_all rule) that ends participation, except death.\n"
    "- An assessment the protocol limits to participants who meet stated criteria found during the study (a screening "
    "lab result, a risk factor, a positive test) gets a decision question asked at the visit where those criteria are "
    "known (scope \"decision\", ask_at that visit): kind \"number\" for a measured value (eosinophil count in x ULN), "
    "\"boolean\" for a yes/no criterion, with the criteria listed in help; the assessment's \"if\" uses them, e.g. "
    "[{\"question\": \"eos_uln\", \"op\": \">\", \"value\": 2}, {\"question\": \"parasite_risk\", \"in\": [\"yes\"]}]. Use need "
    "\"if_indicated\" only when the protocol leaves it to the investigator's judgment.\n"
    "- Dosing tables that decide what happens to one dose from that day's values (full dose / reduced this time / omitted / "
    "delayed) go in the check's \"actions\". Reductions that last for later doses stay in dose_changes. A reduction that "
    "removes a visit (\"omit Day 8\") is a dose level with drop_visits.\n"
    "- Rechecks until something recovers or resolves (\"CrCl weekly until >= 50; discontinue if still < 50 after 4 weeks\", "
    "\"follow the AE until resolved\") are a repeating visit with until_check and a check holding the limit, with "
    "max_wait_days and on_timeout \"stop\" when the protocol says what happens if it does not recover. When the recheck starts "
    "with a finding, put that visit on the event that records it.\n"
    "- Samples a number of hours after a dose that fall on later days (24 h, 48 h post-dose) stay timepoints of the dose visit "
    "(offset_min 1440, 2880); the app dates them. Never list them again on the later day's visit.")


def _with_note(note: str) -> str:
    return f"{note}\n\n" if note else ""


def build_prompt(text: str, symbol_note: str = "") -> str:
    return (
        "You are a clinical trial protocol analyst. Below is text extracted from a clinical study "
        "protocol PDF, with page markers like [[Page 12]]. Build a reusable, participant-independent "
        "model of everything needed to run any participant's visit schedule: anchors, every scheduled "
        "visit with timing and window, which arm or condition it applies to, intra-day time points, "
        "the events that add or stop visits, the rules for what happens to the schedule if something "
        "happens, the decisions taken during the study, and the questions to ask the site.\n\n"
        f"Reply with ONLY one JSON object, no prose, of this shape:\n{SCHEMA}\n\n{RULES}\n{TABLE_RULES}\n\n"
        f"{_with_note(symbol_note)}PROTOCOL TEXT:\n{text}"
    )


def _js(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def anchor_days(st: dict[str, Any]) -> dict[str, float | None]:
    """Each anchor's day counted from the start anchor (None for a date entered later)."""
    anchors = {a["id"]: a for a in st["anchors"]}
    days: dict[str, float | None] = {}

    def day(anchor_id: str, seen: frozenset = frozenset()) -> float | None:
        if anchor_id in days:
            return days[anchor_id]
        a = anchors.get(anchor_id)
        if a is None or anchor_id in seen:
            return None
        if a["kind"] == "start":
            value: float | None = 0
        elif a["kind"] == "relative" and a.get("relative_to"):
            base = day(a["relative_to"], seen | {anchor_id})
            value = None if base is None else base + (a.get("offset_days") or 0)
        else:
            value = None
        days[anchor_id] = value
        return value

    for anchor_id in anchors:
        day(anchor_id)
    return days


def describe_anchors(st: dict[str, Any]) -> str:
    """One line per anchor saying where it is: "week_8_post = week_1 + 49 days (day 49 after the start)"."""
    days = anchor_days(st)
    lines = []
    for a in st["anchors"]:
        if a["kind"] == "start":
            where = "the start anchor (day 0)"
        elif a["kind"] == "relative" and a.get("relative_to"):
            offset = a.get("offset_days") or 0
            where = f"= {a['relative_to']} {'+' if offset >= 0 else '−'} {abs(offset)} days"
            if days.get(a["id"]) is not None:
                where += f" (day {days[a['id']]:g} after the start)"
        else:
            where = "a date entered when it happens (not known in advance)"
        if a.get("set_by_visit"):
            where += f"; fixed by the actual date of visit {a['set_by_visit']}"
        lines.append(f"- {a['id']} ({a.get('label') or a['id']}): {where}")
    return "\n".join(lines)


def build_review_prompt(st: dict[str, Any], table_text: str, img_pages: list[int], symbol_note: str = "") -> str:
    days = anchor_days(st)
    compact = []
    for v in st["visits"]:
        item = {k: v.get(k) for k in (
            "id", "code", "name", "anchor", "follows", "offset_days", "window_before",
            "window_after", "repeat", "procedures", "timepoints", "condition")}
        base = days.get(v.get("anchor") or "")
        if base is not None and not v.get("follows"):
            # Where the visit really falls, so a "Week 8" visit is not moved by another 8 weeks.
            item["day_from_start"] = base + (v.get("offset_days") or 0)
        compact.append(item)
    questions = []
    for q in st["questions"]:
        item: dict[str, Any] = {"id": q["id"], "kind": q["kind"]}
        if q.get("options"):
            item["options"] = [o["value"] for o in q["options"]]
        questions.append(item)
    images = (
        f" (attached as page images: page {', '.join(str(p) for p in img_pages)}; "
        "trust the images over the text when they disagree)" if img_pages else "")
    anchors = ", ".join(a["id"] for a in st["anchors"])
    events = ", ".join(e["id"] for e in st.get("events", [])) or "none"
    rules = [
        {k: r.get(k) for k in ("id", "label", "when", "if", "stop_visits", "stop_all", "blocks", "allowed")}
        for r in st.get("rules", [])
    ]
    return (
        f"You are checking a clinical trial visit-schedule model against the protocol's schedule tables{images}. "
        f"{GRID_LEGEND} Other table pages keep their column layout: a mark belongs to the visit column it "
        "sits under.\n\n"
        "For EVERY visit in the model:\n"
        '1. "procedures" must list every row marked for that visit\'s column and nothing the column leaves blank. '
        "A footnote that limits a row to some participants or sites becomes "
        '{"name": ..., "if": [{"question": id, "in": [value]}]} using these questions: '
        f"{_js(questions)}.\n"
        "2. For a repeating visit, an assessment marked only for some occurrences must be in repeat.overrides "
        '{"n": [values], "add": [...], "remove": [], "note": null}, not in procedures.\n'
        "3. Check offset_days, windows and, where a sampling table exists, the timepoints "
        "(label, offset_min, windows in minutes). offset_days counts from the visit's OWN anchor (or the visit "
        "it follows), not from the study start. The anchors are:\n"
        f"{describe_anchors(st)}\n"
        "   day_from_start = the anchor's own day + offset_days. Compare day_from_start (not offset_days) with "
        "the timing the table gives. To correct a visit, give offset_days measured from its anchor: a Week 8 "
        "visit on an anchor that is already day 49 keeps offset 0, not 56.\n"
        "4. A repeat that runs until the final or end-of-study visit, or until progression, needs "
        f"repeat.until_anchor. Anchor ids: {anchors}.\n"
        '5. "condition" at most 150 characters and visit-specific.\n'
        "6. Footnotes and notes under the tables that say visits stop, are replaced, end or are not repeated when "
        'something happens ("participants who receive Injection 2 do not complete further Period 1 visits") must be '
        f"covered by a rule. Current rules: {_js(rules)}. Add each missing one to rules_add as "
        '{"id", "label", "when": {"anchor": anchorId} | {"event": eventId}, "if": [condition], "stop_visits": [visitId], '
        '"stop_all": boolean, "same_day": "keep"|"cancel", "starts": anchorId|null, "blocks": [ruleId], '
        '"allowed": null | {"relative_to": anchorId, "min_days", "max_days", "not_on_visit": visitId|null}, '
        f'"effect", "source"}}. Event ids: {events}. {RULE_CONDITIONS} A rule that acts only in one case (before or '
        'after a visit, a given grade, an arm) needs its "if"; two rules for the same trigger must have conditions '
        "that tell them apart.\n\n"
        "Reply with ONLY one JSON object:\n"
        '{"corrections": [{"id": visitId, "procedures": [...], "overrides": [...], "timepoints": [...], '
        '"until_anchor": anchorId, "anchor": anchorId, "follows": visitId|null, "offset_days": number, '
        '"window_before": number, "window_after": number, "repeat_count": number, "every_days": number, '
        '"condition": string|null, "reason": string (one short sentence citing the table)} '
        '/* include only changed fields; offset_days counts from the anchor (or followed visit) in the same '
        'correction, so give "anchor" too whenever the offset is measured from a different anchor */], '
        '"rules_add": [rule], "notes": [string]}\n'
        "Only these fields can be changed. Anything else that looks wrong goes in notes, not in a correction.\n"
        'Use "corrections": [] and "rules_add": [] when everything matches.\n\n'
        f"MODEL VISITS:\n{_js(compact)}\n\n"
        f"{_with_note(symbol_note)}SCHEDULE TABLE TEXT:\n{table_text}"
    )
