"""Steps 3-6 of the kit: build, cross-check and clean up the protocol model.

``analyze_protocol`` mirrors the kit's analyzeProtocol: request 1 builds the
model, request 2 cross-checks every visit against the schedule tables, request 3
reads the whole protocol for what logging each event changes (kit/events.py),
then the review questions are sorted and the recommended answers preselected.
Oversized protocols are summarized section by section before model building.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .events import run_event_check
from .llm import AnalysisError
from .normalize import arr, build_template_model, default_review_answers, normalize
from .pdf_input import cross_check_material, hidden_pages_note
from .prompts import build_prompt, build_review_prompt
from .summarize import analyze_from_summaries
from .tables import read_borderless_tables

StageCallback = Callable[[str, str, Any], None]
SAMPLES = Path(__file__).parent / "samples"


def _number(value: Any) -> float | int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else number


def _names(items: Any) -> list[str]:
    out = []
    for p in arr(items):
        name = p.get("name") if isinstance(p, dict) else p
        if name:
            out.append(str(name))
    return out


def _snapshot(v: dict[str, Any]) -> dict[str, Any]:
    r = v.get("repeat") or {}
    return {
        "anchor": v.get("anchor"), "follows": v.get("follows"), "offset_days": v.get("offset_days"),
        "window_before": v.get("window_before"), "window_after": v.get("window_after"),
        "repeat_count": r.get("count"), "every_days": r.get("every_days"), "until_anchor": r.get("until_anchor"),
        "procedures": _names(v.get("procedures")), "timepoints": json.dumps(v.get("timepoints"), sort_keys=True, default=str),
        "overrides": json.dumps(r.get("overrides"), sort_keys=True, default=str), "condition": v.get("condition"),
    }


def _changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What actually changed, readable: "offset_days 56 → 854", "procedures +ECG −Labs"."""
    out = []
    for key, old in before.items():
        new = after[key]
        if old == new:
            continue
        if key == "procedures":
            added = [n for n in new if n not in old]
            removed = [n for n in old if n not in new]
            out.append("procedures " + " ".join([*(f"+{n}" for n in added), *(f"−{n}" for n in removed)]))
        elif key in ("timepoints", "overrides", "condition"):
            out.append(f"{key} updated")
        else:
            out.append(f"{key} {old} → {new}")
    return out


def apply_corrections(st: dict[str, Any], res: Any) -> int:
    """Apply the cross-check's corrections. Only what really changed is logged (with before →
    after); a correction that could not be applied is kept in the notes, never claimed as done.
    A visit's timing (anchor, follows, offset) changes as one unit: an offset is never applied
    with an anchor or followed visit that does not exist."""
    by_id = {v["id"]: v for v in st["visits"]}
    anchor_ids = {a["id"] for a in st["anchors"]}
    log: list[str] = []
    unapplied: list[str] = []
    for c in arr((res or {}).get("corrections") if isinstance(res, dict) else None):
        v = by_id.get(str((c or {}).get("id"))) if isinstance(c, dict) else None
        if not v:
            if isinstance(c, dict) and c.get("id"):
                unapplied.append(f"{c['id']} (no such visit): {c.get('reason') or 'correction'}")
            continue
        reason = c.get("reason") or "corrected against the schedule table"
        before = _snapshot(v)
        problems: list[str] = []
        anchor = c.get("anchor")
        if anchor is not None and anchor not in anchor_ids:
            problems.append(f"unknown anchor {anchor!r}")
        follows = c.get("follows", v.get("follows"))
        if follows and (follows not in by_id or follows == v["id"]):
            problems.append(f"unknown visit to follow {follows!r}")
        if not problems:
            if anchor is not None:
                v["anchor"] = anchor
            if "follows" in c:
                v["follows"] = follows or None
            offset = _number(c.get("offset_days"))
            if offset is not None:
                v["offset_days"] = offset
        for key in ("window_before", "window_after"):
            number = _number(c.get(key))
            if number is not None:
                v[key] = abs(number)
        if isinstance(c.get("procedures"), list):
            v["procedures"] = c["procedures"]
        if isinstance(c.get("timepoints"), list):
            v["timepoints"] = c["timepoints"]
        repeat = v.get("repeat")
        if repeat:
            if isinstance(c.get("overrides"), list):
                repeat["overrides"] = c["overrides"]
            if c.get("until_anchor") in anchor_ids:
                repeat["until_anchor"] = c["until_anchor"]
            count = _number(c.get("repeat_count"))
            if count is not None and count >= 1 and count == int(count):
                repeat["count"] = int(count)
            every = _number(c.get("every_days"))
            if every is not None and every > 0:
                repeat["every_days"] = every
        if "condition" in c:
            v["condition"] = str(c["condition"]) if c["condition"] else None
        changes = _changes(before, _snapshot(v))
        if changes:
            log.append(f"{v['code']}: {reason} ({'; '.join(changes)})")
        if problems:
            unapplied.append(f"{v['code']}: timing left unchanged ({', '.join(problems)}): {reason}")
        elif not changes:
            unapplied.append(f"{v['code']}: {reason}")
    if unapplied:
        st["notes"].append("The cross-check suggested these but they were not applied; review them against "
                           "the protocol: " + " | ".join(unapplied))
    rule_ids = {r.get("id") for r in arr(st.get("rules")) if isinstance(r, dict)}
    for r in arr((res or {}).get("rules_add") if isinstance(res, dict) else None):
        if isinstance(r, dict) and r.get("id") and r["id"] not in rule_ids:
            st.setdefault("rules", []).append(r)
            rule_ids.add(r["id"])
            log.append(f"Rule added: {r.get('label') or r['id']}")
    for n in arr((res or {}).get("notes") if isinstance(res, dict) else None):
        if n and str(n) not in st["notes"]:
            st["notes"].append(str(n))
    st["review"] = log
    return len(log)


def model_table_pages(model: dict[str, Any]) -> list[int]:
    """Take the model's own list of schedule-table pages off the model (it is input to the
    cross-check, not part of the schedule)."""
    pages = model.pop("schedule_table_pages", None)
    out = []
    for n in arr(pages):
        try:
            if not isinstance(n, bool) and float(n) == int(float(n)):
                out.append(int(float(n)))
        except (TypeError, ValueError):
            continue
    return out


def _counts(model: dict[str, Any], table_pages: list[int]) -> dict[str, Any]:
    return {
        "review": len(model["review_questions"]),
        "participant": sum(1 for q in model["questions"] if q["scope"] == "participant")
        + sum(1 for a in model["anchors"] if a["kind"] == "ask"),
        "decisions": sum(1 for q in model["questions"] if q["scope"] == "decision"),
        "events": len(model["events"]),
        "tablePages": table_pages,
    }


def analyze_protocol(
    prepared: dict[str, Any], client: Any, *, on_stage: StageCallback,
    on_text: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    if prepared.get("hiddenPages"):
        warnings.append(hidden_pages_note(prepared["hiddenPages"]))
    scanned = prepared.get("scanned", False)
    scan_images = prepared.get("scanImages") if scanned else []
    # Symbols the text cannot spell, shown as printed (see pdf_input.SymbolLegend).
    legend = prepared.get("symbolLegend") or {}
    legend_note = legend.get("note") or ""
    legend_images = [legend["image"]] if legend.get("image") else []

    def build(text: str) -> str:
        return build_prompt(text, legend_note)

    # Tables without cell borders are read from their page images first, so the model
    # build below sees them as clean grids (see kit/tables.py).
    table_review = {"flags": [], "notes": [], "warnings": []}
    if not scanned:
        table_review = read_borderless_tables(prepared, client, on_stage=on_stage, symbol_note=legend_note,
                                              legend_images=legend_images)
        warnings.extend(table_review["warnings"])

    on_stage("model", "run", "thinking")
    try:
        raw = client.json(build(prepared["text"]), images=[*(scan_images or []), *legend_images], on_text=on_text)
    except AnalysisError as error:
        if error.code != "prompt_too_large" or scanned:
            raise
        # Summarize the complete source, never only the keyword-selected pages.
        source = "\n\n".join(prepared.get("pageTexts") or []) or prepared["text"]
        raw = analyze_from_summaries(source, client, build, on_stage=on_stage, on_text=on_text,
                                     images=legend_images)
        warnings.append(
            "The complete protocol exceeded the model input limit. Analysis used scheduling summaries "
            "from every extracted section, with the original schedule tables retained for cross-checking.")
    try:
        model = normalize(raw)
    except ValueError as error:
        raise AnalysisError("bad_model", "The protocol model was missing visits.") from error
    if warnings:
        model["notes"].extend(warnings)
    # Rows of a borderless table whose reading still disagrees with the page are asked about
    # alongside the protocol-reading questions.
    model["notes"].extend(table_review["notes"])
    model["review_questions"].extend(table_review["flags"])
    on_stage("model", "done", f"{len(model['visits'])} visits · {len(model['events'])} events")

    on_stage("verify", "run", "comparing each visit column")
    material = cross_check_material(prepared, model_table_pages(model))
    try:
        images = material["images"] or (prepared.get("scanImages") or [] if scanned else [])
        img_pages = [i["page"] for i in material["images"]]
        res = client.json(build_review_prompt(model, material["text"], img_pages, legend_note),
                          images=[*images, *legend_images])
        n = apply_corrections(model, res)
        model = normalize(model)
        on_stage("verify", "done", f"{n} correction{'s' if n != 1 else ''} applied" if n else "all visits match")
    except AnalysisError as error:
        if error.code in ("no_credits", "auth", "no_api_key"):
            raise
        model["review"] = None  # None = the check did not run (vs [] = ran, nothing to fix)
        model["notes"].append(
            "The cross-check against the schedule table could not run. Review procedures against the protocol table.")
        warnings.append(f"Cross-check skipped: {error}")
        on_stage("verify", "done", "skipped")

    # What logging each event (pregnancy, withdrawal, death…) changes, read on its own.
    try:
        run_event_check(prepared, model, client, on_stage=on_stage, symbol_note=legend_note,
                        images=legend_images)
        model = normalize(model)
    except AnalysisError as error:
        if error.code in ("no_credits", "auth", "no_api_key"):
            raise
        model["event_check"] = {"ran": False, "changes": [], "events": []}
        model["notes"].append(
            "The events check could not run. Check what logging each event (pregnancy, withdrawal, death, "
            "serious adverse events…) changes against the protocol.")
        warnings.append(f"Events check skipped: {error}")
        on_stage("events", "done", "skipped")

    answers = default_review_answers(model)
    counts = _counts(model, material["pages"])
    on_stage("check", "done", counts)
    return {
        "baseModel": model, "model": build_template_model(model, answers), "reviewAnswers": answers,
        "corrections": model.get("review"), "eventCheck": model.get("event_check"), "counts": counts,
        "warnings": warnings,
    }


def sample_analysis(name: str, on_stage: StageCallback) -> dict[str, Any]:
    """A shipped sample model through the same stages, for UI work without model credits."""
    path = SAMPLES / f"{name}.json"
    if not name.isalpha() or not path.is_file():
        raise AnalysisError("not_found", f"No sample named {name!r}.")
    sample = json.loads(path.read_text(encoding="utf-8"))
    on_stage("model", "run", "sample model")
    time.sleep(1.5)
    base = normalize(sample["model"])
    base["review"] = []
    on_stage("model", "done", f"{len(base['visits'])} visits · {len(base['events'])} events")
    on_stage("verify", "run", "sample model")
    time.sleep(1.2)
    on_stage("verify", "done", "sample model, not cross-checked")
    on_stage("events", "done", "sample model, not checked")
    answers = default_review_answers(base)
    counts = _counts(base, [])
    on_stage("check", "done", counts)
    return {"baseModel": base, "model": build_template_model(base, answers), "reviewAnswers": answers,
            "corrections": [], "counts": counts, "warnings": ["Sample model: no document was analysed."]}
