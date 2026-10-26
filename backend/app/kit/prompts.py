"""The two prompts sent to the model: the kit's src/ai/prompts.js, plus how schedule-table
grids are written and a request for the model to name the schedule-table pages."""

from __future__ import annotations

import json
from typing import Any

from ._prompt_text import RULES, SCHEMA


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
    "happen at all (a date outside it is an error); it never chooses between branches.")


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
