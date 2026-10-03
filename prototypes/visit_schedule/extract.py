"""Protocol PDF → visit plan JSON via OpenAI."""
from __future__ import annotations

import base64
import json
import re
from typing import List, Tuple

import fitz  # PyMuPDF

KEYWORDS = [
    (r"schedule of (assessments|activities|events|evaluations|procedures)", 25),
    (r"study (flow ?chart|calendar)|time and events", 25),
    (r"\bvisit\s*\d+", 3), (r"\bday\s*-?\d+", 2), (r"\bweek\s*\d+", 2),
    (r"screening", 2), (r"window|±|\+/-", 3), (r"follow[- ]?up", 2),
    (r"end of (treatment|study)", 3), (r"cycle\s*\d+", 2), (r"randomi[sz]ation|baseline", 2),
]

MAX_TEXT_PAGES = 18
MAX_IMAGE_PAGES = 4


def read_pages(pdf_bytes: bytes) -> List[str]:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [p.get_text() for p in doc]


def pick_pages(pages: List[str]) -> List[int]:
    """Indices of pages most likely to hold the visit schedule, in document order."""
    scores = []
    for i, text in enumerate(pages):
        t = text.lower()
        s = sum(min(len(re.findall(p, t)), 10) * w for p, w in KEYWORDS)
        scores.append((s, i))
    ranked = [i for s, i in sorted(scores, reverse=True) if s > 0][:MAX_TEXT_PAGES]
    return sorted(ranked)


def page_images(pdf_bytes: bytes, indices: List[int]) -> List[str]:
    out = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i in indices:
            pix = doc[i].get_pixmap(dpi=110)
            out.append("data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode())
    return out


_int = {"type": ["integer", "null"]}
_acts = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["study_title", "day1_definition", "has_day_zero", "visits", "repeating", "notes"],
    "properties": {
        "study_title": {"type": ["string", "null"]},
        "day1_definition": {"type": ["string", "null"],
                            "description": "What the protocol calls Day 1, e.g. 'first dose' or 'randomisation'."},
        "has_day_zero": {"type": ["boolean", "null"]},
        "visits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "day", "window_before", "window_after", "activities", "note"],
            "properties": {
                "name": {"type": "string"},
                "day": {**_int, "description": "Study day. Convert weeks/months to days (Week 4 = Day 29 when "
                                               "Week 1 starts on Day 1). Null when event-driven."},
                "window_before": _int, "window_after": _int,
                "activities": _acts,
                "note": {"type": ["string", "null"]},
            }}},
        "repeating": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name_template", "number_start", "first_day", "every_days", "occurrences", "until_text",
                         "window_before", "window_after", "activities"],
            "properties": {
                "name_template": {"type": "string", "description": "Use {n} for the counter, e.g. 'Cycle {n} Day 1'."},
                "number_start": {"type": "integer"},
                "first_day": {"type": "integer"},
                "every_days": {"type": "integer"},
                "occurrences": {**_int, "description": "Null if the protocol gives no fixed count."},
                "until_text": {"type": ["string", "null"], "description": "e.g. 'until disease progression'."},
                "window_before": _int, "window_after": _int,
                "activities": _acts,
            }}},
        "notes": {"type": ["string", "null"]},
    },
}

PROMPT = """You extract the patient visit schedule from a clinical trial protocol.
Return every scheduled visit with its study day, visit window and main activities.
Rules:
- Use numbers exactly as written in the protocol. If a window or day is not stated, use null. Never invent values.
- Put regularly repeating visits (e.g. every 21-day cycle, every 12 weeks in follow-up) in `repeating`, not `visits`,
  and don't also list the individual repeats in `visits`.
- Visits tied to events (early termination, progression, unscheduled) get day = null.
- Keep activity names short (e.g. 'Vital signs', 'ECG', 'Labs')."""


def extract_plan(pdf_bytes: bytes, api_key: str, model: str) -> Tuple[dict, List[int]]:
    from openai import OpenAI

    pages = read_pages(pdf_bytes)
    picked = pick_pages(pages)
    if not picked:
        raise RuntimeError("No readable text found. The PDF may be a scan without text.")
    text = "\n\n".join(f"=== Page {i + 1} ===\n{pages[i]}" for i in picked)
    top = sorted(picked, key=lambda i: -len(re.findall(r"schedule of|visit|day\s*-?\d", pages[i].lower())))
    images = page_images(pdf_bytes, sorted(top[:MAX_IMAGE_PAGES]))

    content = [{"type": "text", "text": f"Protocol excerpts (pages most likely to contain the schedule):\n\n{text}"}]
    content += [{"type": "text", "text": "Page images of the schedule tables:"}]
    content += [{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in images]

    resp = OpenAI(api_key=api_key).chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": content}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "visit_plan", "strict": True, "schema": SCHEMA}},
    )
    msg = resp.choices[0].message
    if msg.refusal:
        raise RuntimeError(f"Model refused: {msg.refusal}")
    return json.loads(msg.content or "{}"), [i + 1 for i in picked]
