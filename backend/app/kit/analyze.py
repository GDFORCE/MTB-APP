"""Steps 3-6 of the kit: two model requests, then clean-up.

``analyze_protocol`` mirrors the kit's analyzeProtocol: request 1 builds the
model, request 2 cross-checks every visit against the schedule tables, then the
review questions are sorted and the recommended answers preselected.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .llm import AnalysisError
from .normalize import arr, build_template_model, default_review_answers, normalize
from .prompts import build_prompt, build_review_prompt

StageCallback = Callable[[str, str, Any], None]
SAMPLES = Path(__file__).parent / "samples"


def apply_corrections(st: dict[str, Any], res: Any) -> int:
    by_id = {v["id"]: v for v in st["visits"]}
    anchor_ids = {a["id"] for a in st["anchors"]}
    log: list[str] = []
    for c in arr((res or {}).get("corrections") if isinstance(res, dict) else None):
        v = by_id.get(str((c or {}).get("id"))) if isinstance(c, dict) else None
        if not v:
            continue
        if isinstance(c.get("procedures"), list):
            v["procedures"] = c["procedures"]
        if isinstance(c.get("timepoints"), list):
            v["timepoints"] = c["timepoints"]
        if isinstance(c.get("overrides"), list) and v.get("repeat"):
            v["repeat"]["overrides"] = c["overrides"]
        if c.get("until_anchor") and v.get("repeat") and c["until_anchor"] in anchor_ids:
            v["repeat"]["until_anchor"] = c["until_anchor"]
        for key in ("offset_days", "window_before", "window_after"):
            value = c.get(key)
            if value is not None and value != "" and not isinstance(value, bool):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                number = int(number) if number == int(number) else number
                v[key] = number if key == "offset_days" else abs(number)
        if "condition" in c:
            v["condition"] = str(c["condition"]) if c["condition"] else None
        log.append(f"{v['code']}: {c.get('reason') or 'corrected against the schedule table'}")
    for n in arr((res or {}).get("notes") if isinstance(res, dict) else None):
        if n and str(n) not in st["notes"]:
            st["notes"].append(str(n))
    st["review"] = log
    return len(log)


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
    scanned = prepared.get("scanned", False)
    on_stage("model", "run", "thinking")
    scan_images = prepared.get("scanImages") if scanned else []
    try:
        raw = client.json(build_prompt(prepared["text"]), images=scan_images, on_text=on_text)
    except AnalysisError as error:
        # The whole protocol was too large for the model: fall back to the kit's page selection.
        if error.code != "prompt_too_large" or not prepared.get("fullText") or not prepared.get("selectedText"):
            raise
        warnings.append("The protocol was too long to send whole, so only its most relevant pages were analysed.")
        raw = client.json(build_prompt(prepared["selectedText"]), images=scan_images, on_text=on_text)
    try:
        model = normalize(raw)
    except ValueError as error:
        raise AnalysisError("bad_model", "The protocol model was missing visits.") from error
    on_stage("model", "done", f"{len(model['visits'])} visits · {len(model['events'])} events")

    on_stage("verify", "run", "comparing each visit column")
    try:
        table_images = prepared.get("tableImages") or []
        images = table_images[:5] if table_images else (prepared.get("scanImages") or [] if scanned else [])
        img_pages = [i["page"] for i in table_images[:5]]
        res = client.json(build_review_prompt(model, prepared["tableText"], img_pages), images=images)
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

    answers = default_review_answers(model)
    counts = _counts(model, prepared.get("tablePages", []))
    on_stage("check", "done", counts)
    return {
        "baseModel": model, "model": build_template_model(model, answers), "reviewAnswers": answers,
        "corrections": model.get("review"), "counts": counts, "warnings": warnings,
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
    answers = default_review_answers(base)
    counts = _counts(base, [])
    on_stage("check", "done", counts)
    return {"baseModel": base, "model": build_template_model(base, answers), "reviewAnswers": answers,
            "corrections": [], "counts": counts, "warnings": ["Sample model: no document was analysed."]}
