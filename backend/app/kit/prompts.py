"""The two prompts sent to the model, identical to the kit's src/ai/prompts.js."""

from __future__ import annotations

import json
from typing import Any

from ._prompt_text import RULES, SCHEMA


def build_prompt(text: str) -> str:
    return (
        "You are a clinical trial protocol analyst. Below is text extracted from a clinical study "
        "protocol PDF, with page markers like [[Page 12]]. Build a reusable, participant-independent "
        "model of everything needed to run any participant's visit schedule: anchors, every scheduled "
        "visit with timing and window, which arm or condition it applies to, intra-day time points, "
        "the events that add or stop visits, the decisions taken during the study, and the questions "
        "to ask the site.\n\n"
        f"Reply with ONLY one JSON object, no prose, of this shape:\n{SCHEMA}\n\n{RULES}\n\n"
        f"PROTOCOL TEXT:\n{text}"
    )


def _js(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def build_review_prompt(st: dict[str, Any], table_text: str, img_pages: list[int]) -> str:
    compact = [
        {k: v.get(k) for k in (
            "id", "code", "name", "anchor", "follows", "offset_days", "window_before",
            "window_after", "repeat", "procedures", "timepoints", "condition")}
        for v in st["visits"]
    ]
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
    return (
        f"You are checking a clinical trial visit-schedule model against the protocol's schedule tables{images}. "
        'The table text keeps the column layout: an "X" belongs to the visit column it sits under.\n\n'
        "For EVERY visit in the model:\n"
        '1. "procedures" must list every row marked for that visit\'s column and nothing the column leaves blank. '
        "A footnote that limits a row to some participants or sites becomes "
        '{"name": ..., "if": [{"question": id, "in": [value]}]} using these questions: '
        f"{_js(questions)}.\n"
        "2. For a repeating visit, an assessment marked only for some occurrences must be in repeat.overrides "
        '{"n": [values], "add": [...], "remove": [], "note": null}, not in procedures.\n'
        "3. Check offset_days, windows and, where a sampling table exists, the timepoints "
        "(label, offset_min, windows in minutes).\n"
        "4. A repeat that runs until the final or end-of-study visit, or until progression, needs "
        f"repeat.until_anchor. Anchor ids: {anchors}.\n"
        '5. "condition" at most 150 characters and visit-specific.\n\n'
        "Reply with ONLY one JSON object:\n"
        '{"corrections": [{"id": visitId, "procedures": [...], "overrides": [...], "timepoints": [...], '
        '"until_anchor": anchorId, "offset_days": number, "window_before": number, "window_after": number, '
        '"condition": string|null, "reason": string (one short sentence citing the table)} '
        '/* include only changed fields */], "notes": [string]}\n'
        'Use "corrections": [] when everything matches.\n\n'
        f"MODEL VISITS:\n{_js(compact)}\n\n"
        f"SCHEDULE TABLE TEXT:\n{table_text}"
    )
