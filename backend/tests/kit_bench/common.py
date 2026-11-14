"""Helpers shared by the constructed protocol fixtures: dates, expectation rows and the
hand-written kit models (the same shapes the app saves)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def d(base: str, days: int = 0) -> str:
    """ISO date + whole days: d("2026-03-16", 28) == "2026-04-13"."""
    return (date.fromisoformat(base) + timedelta(days=days)).isoformat()


def win(target: str, before: int, after: int) -> list[str]:
    return [d(target, -before), d(target, after)]


def has(name: str, *words: Any, conditional: bool | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "any": list(words) or [name.lower()]}
    if conditional is not None:
        out["conditional"] = conditional
    return out


def ev(name: str, match: list[Any], on: str | None = "", window: Any = "", **over: Any) -> dict[str, Any]:
    """One expected visit. ``on`` None = on the schedule but waiting for a date; omitted = any date."""
    out: dict[str, Any] = {"name": name, "match": match}
    if on != "":
        out["date"] = on
    if window != "":
        out["window"] = window
    out.update(over)
    return out


# ── hand-written kit models ──────────────────────────────────────────────
def anchor(aid: str, kind: str, label: str, **over: Any) -> dict[str, Any]:
    return {"id": aid, "label": label, "kind": kind, "relative_to": None, "offset_days": 0, "is_day1": False,
            "day_zero": False, "set_by_visit": None, "question": None, "optional": kind == "ask", "estimates": [],
            "range": None, "note": None, **over}


def visit(vid: str, code: str, name: str, phase: str, anchor_id: str, offset: int, window: tuple[int, int] = (0, 0),
          procedures: list[Any] | None = None, **over: Any) -> dict[str, Any]:
    return {"id": vid, "code": code, "name": name, "phase": phase, "modality": "site", "anchor": anchor_id,
            "offset_days": offset, "window_before": window[0], "window_after": window[1], "follows": None,
            "follows_occurrence": None, "duration_days": None, "include_if": [], "repeat": None,
            "procedures": procedures or [], "timepoints": [], "condition": None, "source": None, **over}


def repeat(every: int, count: int | None, **over: Any) -> dict[str, Any]:
    return {"every_days": every, "count": count, "count_question": None, "start_n": 1, "label_pattern": None,
            "code_pattern": None, "chain": False, "open_ended": False, "until_anchor": None, "overrides": [], **over}


def question(qid: str, label: str, text: str, kind: str = "boolean", scope: str = "participant",
             options: list[tuple[str, str]] | None = None, **over: Any) -> dict[str, Any]:
    opts = [{"value": v, "label": lb} for v, lb in (options or ([("yes", "Yes"), ("no", "No")] if kind == "boolean" else []))]
    return {"id": qid, "label": label, "text": text, "scope": scope, "ask_at": None, "kind": kind, "options": opts,
            "help": "", "default": None, **over}


def event(eid: str, label: str, **over: Any) -> dict[str, Any]:
    return {"id": eid, "label": label, "repeatable": False, "ends_schedule": False, "sets_anchor": None, "note": None,
            "questions": [], "visits": [], **over}


def rule(rid: str, label: str, when: dict[str, str], **over: Any) -> dict[str, Any]:
    return {"id": rid, "label": label, "when": when, "stop_visits": [], "stop_all": False, "same_day": "keep",
            "starts": None, "blocks": [], "allowed": None, "effect": label, "source": None, "if": [],
            "set_answers": None, "pause": None, **over}


def proc(name: str, *conds: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {"name": name, "if": list(conds), **over}


def model(title: str, number: str, **parts: Any) -> dict[str, Any]:
    return {"protocol": {"title": title, "number": number, "sponsor": None, "phase": None, "indication": None,
                         "version": None, "design": "Constructed fixture."},
            "arms": [], "anchors": [], "questions": [], "visits": [], "events": [], "rules": [],
            "review_questions": [], "notes": [], **parts}
