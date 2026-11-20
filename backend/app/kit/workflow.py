"""Versioned, declarative workflow format for the kit scheduling engine.

Each block owns one engine operation and its protocol-specific parameters. The
compiler is lossless: the reviewed blocks supply the model evaluated for every
participant. Edges describe prerequisites and effects of those same operations.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
from typing import Any

from .patterns import blockers


COLLECTIONS = {
    "anchor": "anchors", "question": "questions", "visit": "visits", "event": "events",
    "rule": "rules", "pattern": "pattern_rules", "measure": "measures", "dose": "doses",
    "phase": "phases", "study_event": "study_events",
}


def fingerprint(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _prompt(item: dict, kind: str, roles: list[str]) -> dict:
    return {"text": item.get("text") or item.get("question") or item.get("label"),
            "type": kind, "options": item.get("options") or [], "roles": roles}


def _days(n: Any) -> str:
    if isinstance(n, (int, float)) and n and n % 7 == 0:
        return f"{int(n) // 7} week{'s' if int(n) != 7 else ''}"
    return f"{n} day{'s' if n != 1 else ''}"


def _cond_text(c: dict) -> str:
    if "in" in c:
        return "finding is " + " or ".join(str(x) for x in c["in"])
    return f"finding {c['op']} {c['value']}"


def adaptive_summary(item: dict, ad: dict) -> list[str]:
    """The history-based interval as the study team reviews it: condition, inputs, timing, outputs, branches."""
    finding = (ad.get("finding") or {}).get("label") or "the finding"
    rec = ad.get("recommendation")
    lines = [
        f"Condition: from occurrence {ad['decide_from_n']}, the {finding.lower()} confirmed at each visit sets the next interval.",
        "Inputs: " + "; ".join(x for x in [
            f"{finding} ({', '.join((ad.get('finding') or {}).get('roles') or [])})",
            f"{rec['label']} in units of {_days(rec['unit_days'])} ({', '.join(rec['roles'])})" if rec else None,
            "the interval that led to the previous visit and the history of earlier visits"] if x),
        f"Timing: next target = previous visit's {ad.get('basis') or '(not stated)'} date + interval; window "
        f"-{item.get('window_before')}/+{item.get('window_after')} days; interval between "
        f"{_days(ad.get('min_days'))} and {_days(ad.get('max_days'))}; first decision from {_days(ad.get('start_days'))}.",
    ]
    for st in ad.get("steps") or []:
        effect = (f"set to {_days(st['set_days'])}" if "set_days" in st else "keep the interval" if st.get("keep")
                  else f"{'extend' if st['add_days'] > 0 else 'shorten'} by {_days(abs(st['add_days']))}")
        lines.append(f"Branch: {_cond_text(st['if'])}{' after an extension' if st.get('last') == 'extended' else ''} -> {effect}.")
    if ad.get("breakpoint"):
        bp = ad["breakpoint"]
        sign = "+" if bp["cap_offset_days"] >= 0 else "-"
        which = "reached by an extension " if bp.get("count_activity_at") == "extended_intervals" else ""
        lines.append(f"Breakpoint: after {bp['after_extensions']} extension attempts, an extension is capped at the longest "
                     f"interval {which}that ended with activity {sign} {_days(abs(bp['cap_offset_days']))}; "
                     "the cap is recorded when first reached and keeps applying.")
    lines.append("Precedence: " + {
        "calculated": "the calculated interval books the visit" + ("; a different recommendation is shown" if rec else ""),
        "recommendation": "the recommendation books the visit; it waits until the recommendation is recorded",
        "recommendation_if_given": ("the calculated interval books the visit until the recommendation is recorded; "
                                    "then the recommendation replaces it"),
        "shorter": "the shorter of the calculated interval and the recommendation",
        "longer": "the longer of the two, within the limits",
        "review": "when they differ, the investigator chooses one of the two before the visit is booked",
    }.get(ad.get("precedence"), "NOT STATED - the interval cannot run until this is set"))
    lines.append("Outputs: the next occurrence's target and window, or a pending booking naming the missing input.")
    if ad.get("blocked"):
        lines.append(f"Blocked: {ad['blocked']}.")
    return lines


def generated_summary(item: dict) -> list[str]:
    """A generated calculation as the study team reviews it: what it reads, what it produces, its logic and its tests."""
    from .calc import render
    status = item.get("generated") or {}
    kind = (item.get("output") or {}).get("kind")
    lines = [
        f"Generated calculation {item['pattern']} (calc v{status.get('calc_version', 1)}"
        + (f", content {status['sha256'][:12]})" if status.get("sha256") else ")"),
        "Inputs: " + (", ".join(f"{x['fact']} {x.get('id', '')}".strip() for x in item.get("inputs") or []) or "none"),
        "Output: " + ("the dates of every occurrence of " + ", ".join(item.get("visits") or []) if kind == "occurrences"
                      else "a new date for each occurrence of " + ", ".join(item.get("visits") or [])),
        "Logic: " + render(item.get("program")),
    ]
    for t in status.get("tests") or []:
        lines.append(f"Test {'passes' if t['passed'] else 'FAILS'}: {t['name']}")
    if item.get("blocked"):
        lines.append(f"Blocked: {item['blocked']}.")
    return lines


def study_event_summary(item: dict, phases: dict[str, dict]) -> list[str]:
    lines = [f"Condition: confirmed once by {' / '.join(item['confirm_roles'])} with the date it actually happened "
             "(stored apart from the confirmation time).",
             "Inputs: each participant's phase on that date, read from recorded visits and events"
             + (", and " + ", ".join(f["label"] for f in item["fields"]) if item["fields"] else "") + "."]
    for a in item["actions"]:
        if "phase" in a["audience"]:
            who = f"participants in {phases.get(a['audience']['phase'], {}).get('label', a['audience']['phase']).lower()}"
        else:
            who = f"the participant named in '{a['audience']['field']}'"
        what = [f"supersede pending {', '.join(a['supersede_visits'])}" if a["supersede_visits"] else None,
                f"add {a['add_visit']['name']} dated by the site's booking of the next available date" if a.get("add_visit") else None]
        lines.append(f"Branch: {who} -> " + " and ".join(x for x in what if x) + ".")
    if item.get("review_phases"):
        lines.append("Review: participants in " + ", ".join(item["review_phases"]) + " are listed without a change.")
    lines.append("Outputs: one stored effect per participant (applied, excluded, to resolve or not matched), delivered "
                 "with their calendar; completed visits are never replaced.")
    if item.get("blocked"):
        lines.append(f"Blocked: {item['blocked']}.")
    return lines


def build_workflow(model: dict, version: int, resolutions: dict | None = None) -> dict:
    """Build blocks from a normalized model, preserving operation order and source evidence."""
    blocks = []
    scheduled = {v["id"] for v in model.get("visits", [])}
    event_visits = {v["id"]: f"event_visit:{e['id']}:{v['id']}"
                    for e in model.get("events", []) for v in e["visits"]}

    def visit_ref(vid):
        return f"visit:{vid}" if vid in scheduled else event_visits.get(vid, f"visit:{vid}")

    def add(kind, item, owner=None):
        id_ = f"{kind}:{item['id']}" if owner is None else f"{kind}:{owner}:{item['id']}"
        refs, paths, prompts = [], [], []
        trigger = "When prerequisites are available"
        scope = "patient"

        def ref(target, value, relation, *, basis=None, output=False):
            if value:
                block = visit_ref(value) if target == "visit" else f"{target}:{value}"
                edge = {"block": block, "relation": relation}
                if basis:
                    edge["basis"] = basis
                edges = paths if output else refs
                if edge not in edges:
                    edges.append(edge)

        def condition_refs(value):
            if isinstance(value, list):
                for part in value:
                    condition_refs(part)
            if isinstance(value, dict):
                ref("question", value.get("question"), "Answer")
                ref("visit", value.get("visit"), "Visit history", basis="actual")
                ref("event", value.get("event"), "Event history", basis="actual")
                ref("anchor", value.get("anchor"), "Protocol date")
                ref("measure", value.get("measure"), "Recorded value", basis="actual")
                for part in value.values():
                    if isinstance(part, (dict, list)):
                        condition_refs(part)

        if kind == "anchor":
            scope = item.get("scope") or scope
            ref("anchor", item.get("relative_to"), "Count from", basis=item.get("date_basis"))
            ref("anchor", (item.get("range") or {}).get("relative_to"), "Interval bound")
            ref("visit", item.get("set_by_visit"), "Date set by visit", basis="actual")
            for vid in item.get("last_of") or []:
                ref("visit", vid, "Last scheduled or completed visit", basis="planned or actual")
            for event in model["events"]:
                if event.get("sets_anchor") == item["id"]:
                    ref("event", event["id"], "Date set by event", basis="actual")
            if item["kind"] == "start" or (item["kind"] == "ask" and not refs):
                trigger = "Date entered"
                prompts.append(_prompt(item, "date", ["pi"] if scope == "study" else ["pi", "crc"]))
            elif item["kind"] == "ask" and not any(
                edge["relation"] in ("Date set by visit", "Date set by event") for edge in refs
            ):
                prompts.append(_prompt(item, "date", ["pi"] if scope == "study" else ["pi", "crc"]))
        elif kind == "question":
            at = item.get("ask_at") or {}
            ref("visit", at.get("visit"), "Asked at")
            ref("anchor", at.get("anchor"), "Asked at")
            scope = "study" if item["scope"] == "study" else "patient"
            trigger = {"study": "During review", "decision": "When the decision is due"}.get(item["scope"], "At enrollment")
            prompts.append(_prompt(item, item["kind"], item.get("answer_roles")
                                   or (["pi", "crc"] if scope == "patient" else ["sponsor", "cro", "pi"])))
        elif kind in ("visit", "event_visit"):
            if owner:
                ref("anchor", item.get("count_from"), "Count from")
                ref("event", owner, "Event date", basis="actual")
            elif item.get("follows"):
                ref("visit", item["follows"], "Follow visit", basis="planned or actual")
            else:
                ref("anchor", item.get("anchor"), "Count from")
            ref("anchor", (item.get("repeat") or {}).get("until_anchor"), "Repeat ends at")
            ref("question", (item.get("repeat") or {}).get("count_question"), "Repeat count")
            ref("visit", (item.get("min_gap") or {}).get("after"), "Minimum interval", basis="planned or actual")
            ref("visit", (item.get("arrive_before") or {}).get("visit"), "Check in before", output=True)
            condition_refs([item.get("include_if"), item.get("procedures"), item.get("timepoints"),
                            item.get("instructions"), (item.get("repeat") or {}).get("overrides"),
                            (item.get("check") or {}).get("limits")])
            if item.get("check"):
                prompts.append(_prompt(item["check"], "boolean", ["pi", "crc"]))
            if item.get("timepoints"):
                prompts.append({"type": "time", "text": "Record the dose time", "roles": ["pi", "crc"], "options": []})
            ad = (item.get("repeat") or {}).get("adaptive")
            if ad:
                refs.append({"block": id_, "relation": "Previous occurrence: interval, confirmed finding and history",
                             "basis": ad.get("basis") or "not stated"})
                if ad.get("finding"):
                    prompts.append({"type": ad["finding"]["kind"], "text": f"Confirm {ad['finding']['label'].lower()} at each visit",
                                    "roles": ad["finding"]["roles"], "options": []})
                if ad.get("recommendation"):
                    prompts.append({"type": "number", "text": f"Record the {ad['recommendation']['label'].lower()}",
                                    "roles": ad["recommendation"]["roles"], "options": []})
                if ad.get("precedence") == "review":
                    prompts.append({"type": "choice", "text": "Choose the interval when the calculation and the recommendation differ",
                                    "roles": ["pi"], "options": []})
            for c in item.get("combine_with") or []:
                ref("visit", c["visit"], f"Combined when inside its window ({c['date']} date)", output=True)
        elif kind == "event":
            trigger = "Event recorded"
            roles = item.get("record_roles") or ["pi", "crc"]
            prompts = [{"type": "date", "text": f"When did {item['label']} occur?", "roles": roles, "options": []}]
            prompts.extend(_prompt(q, q["kind"], q.get("answer_roles") or roles) for q in item["questions"])
            ref("anchor", item.get("sets_anchor"), "Set date", output=True)
            for v in item["visits"]:
                paths.append({"block": f"event_visit:{item['id']}:{v['id']}", "relation": "Add visit when applicable"})
        elif kind == "rule":
            condition_refs([item.get("when"), item.get("if")])
            ref("anchor", item.get("starts"), "Open schedule path", output=True)
            ref("anchor", (item.get("pause") or {}).get("resume"), "Resume at")
            for v in item.get("stop_visits") or []:
                ref("visit", v, "Stop future occurrences", output=True)
            for r in item.get("blocks") or []:
                ref("rule", r, "Exclude rule", output=True)
            for q in item.get("set_answers") or {}:
                ref("question", q, "Set answer", output=True)
        elif kind in ("pattern", "dose"):
            produces = kind == "pattern" and (item.get("output") or {}).get("kind") == "occurrences"
            for v in item.get("visits") or []:
                ref("visit", v, "Calculates the occurrences of" if produces else "Apply at visit", output=True)
            for x in item.get("inputs") or [] if kind == "pattern" else []:
                target = {"visit": "visit", "anchor": "anchor", "answer": "question", "event_dates": "event"}.get(x.get("fact"))
                if target:
                    ref(target, x.get("id"), "Generated calculation reads", basis="planned or actual" if target == "visit" else None)
            if kind == "pattern" and item.get("program") is not None:
                trigger = "Generated calculation"
        elif kind == "phase":
            trigger = "Read from recorded facts"
            for target in ("visit", "event", "anchor"):
                ref(target, item["starts"].get(target), "Phase starts", basis="actual")
                for end in item["ends"]:
                    ref(target, end.get(target), "Phase ends", basis="actual")
        elif kind == "study_event":
            scope, trigger = "study", "Confirmed by the study team"
            prompts.append({"type": "date", "text": f"When did this happen: {item['label']}?",
                            "roles": item["confirm_roles"], "options": []})
            prompts.extend({"type": f["kind"], "text": f["label"], "roles": item["confirm_roles"], "options": []}
                           for f in item["fields"])
            for a in item["actions"]:
                ref("phase", a["audience"].get("phase"), "Audience on the effective date", basis="actual")
                for v in a["supersede_visits"]:
                    ref("visit", v, "Supersede pending occurrences", output=True)
                if a.get("add_visit"):
                    prompts.append({"type": "date", "text": f"Book the next available date for {a['add_visit']['name']}",
                                    "roles": ["pi", "crc"], "options": []})
        source = item.get("source")
        if not source and kind == "event":
            source = (item.get("check") or {}).get("evidence")
        block = {
            "id": id_, "kind": kind, "owner": owner, "version": version,
            "label": item.get("name") or item.get("label") or item["id"], "scope": scope,
            "trigger": trigger, "inputs": refs, "paths": paths, "prompts": prompts,
            "source": {"reference": source, "page": item.get("page"), "quote": item.get("quote")},
            "operation": deepcopy(item),
        }
        if kind in ("visit", "event_visit"):
            prefix = f"ev.{{event_key}}.{item['id']}" if owner else item["id"]
            block["outputs"] = [{"kind": "visit", "key": f"{prefix}#{{occurrence}}"},
                                *({"kind": "activity", "label": p["name"], "source": p.get("source")}
                                  for p in item["procedures"]),
                                *({"kind": "timed_activity", "label": p["label"], "source": p.get("source")}
                                  for p in item["timepoints"])]
        elif kind == "anchor":
            block["outputs"] = [{"kind": "date", "key": item["id"]}]
        elif kind == "question":
            block["outputs"] = [{"kind": "answer", "key": item["id"]}]
        if kind == "event":
            del block["operation"]["visits"]
        ad = (item.get("repeat") or {}).get("adaptive") if kind == "visit" else None
        if ad:
            block["summary"] = adaptive_summary(item, ad)
            block["source"] = {"reference": ad.get("source") or source, "page": ad.get("page") or item.get("page"),
                               "quote": ad.get("quote") or item.get("quote")}
        elif kind == "pattern" and item.get("program") is not None:
            block["summary"] = generated_summary(item)
        elif kind == "study_event":
            block["summary"] = study_event_summary(item, {p["id"]: p for p in model.get("phases") or []})
            block["outputs"] = [{"kind": "study_effect", "label": a["label"]} for a in item["actions"]]
        blocks.append(block)

    for kind, collection in COLLECTIONS.items():
        for item in model.get(collection) or []:
            add(kind, item)
            if kind == "event":
                for v in item["visits"]:
                    add("event_visit", v, item["id"])
    resolutions = resolutions or {}
    issues = [{**point, "resolution": resolutions.get(point["id"]),
               "blocks": [b["id"] for b in blocks if b["operation"]["id"] in point["affects"]]}
              for point in model.get("open_points") or []]
    errors = validate_blocks(blocks)
    errors += [f"{b['label']}: {b.get('problem') or 'Unsupported scheduling operation'}" for b in blockers(model)]
    metadata = {k: deepcopy(v) for k, v in model.items() if k not in COLLECTIONS.values()}
    return {"schema": 1, "version": version, "blocks": blocks, "metadata": metadata,
            "sections": [c for c in COLLECTIONS.values() if c in model], "issues": issues, "errors": errors}


def validate_blocks(blocks: list[dict]) -> list[str]:
    errors = []
    ids = Counter(b["id"] for b in blocks)
    relative = {b["id"]: "anchor:" + str(b["operation"].get("relative_to")) for b in blocks
                if b["kind"] == "anchor" and b["operation"].get("kind") == "relative"
                and b["operation"].get("relative_to")}
    for node in relative:
        seen = set()
        while node in relative and node not in seen:
            seen.add(node)
            node = relative[node]
        if node in seen:
            errors.append(f"Protocol dates form a circular dependency at {node}.")
    if sum(b["kind"] == "anchor" and b["operation"].get("kind") == "start" for b in blocks) != 1:
        errors.append("The workflow needs exactly one starting date.")
    if not any(b["kind"] in ("visit", "event_visit") for b in blocks):
        errors.append("The workflow has no visits.")
    for b in blocks:
        item = b["operation"]
        label = b["label"]
        if not re.fullmatch(r"[\w.-]{1,80}", item["id"]) or item["id"] in ("__proto__", "constructor", "prototype"):
            errors.append(f"{label}: use a stable ID without spaces or separators.")
        if ids[b["id"]] != 1:
            errors.append(f"{label}: duplicate block ID {b['id']}.")
        for edge in b["inputs"] + b["paths"]:
            if edge["block"] not in ids:
                errors.append(f"{label}: {edge['relation'].lower()} references {edge['block']}, which is missing.")
        if b["kind"] == "question" and item["kind"] == "choice" and not item["options"]:
            errors.append(f"{label}: the choice has no allowed answers.")
        if b["kind"] == "anchor" and item["kind"] == "relative" and not b["inputs"]:
            errors.append(f"{label}: give the date a reference or make it an entered date.")
        if b["kind"] == "anchor" and item.get("date_basis") == "confirmed" and (
            not item.get("range") or item.get("set_by_visit")
            or any(edge["relation"] == "Date set by event" for edge in b["inputs"])
        ):
            errors.append(f"{label}: a confirmed booking needs an interval and must be entered by staff.")
        if b["kind"] in ("visit", "event_visit"):
            for field in ("offset_days", "window_before", "window_after"):
                value = item.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or (field != "offset_days" and value < 0):
                    errors.append(f"{label}: invalid {field.replace('_', ' ')}.")
            repeat = item.get("repeat")
            if repeat:
                if repeat.get("every_days", 0) <= 0:
                    errors.append(f"{label}: repeating visits need a positive interval.")
                count = repeat.get("count")
                if count is not None and (not 1 <= count <= 200 or int(count) != count):
                    errors.append(f"{label}: the engine supports explicit counts of 1 to 200 occurrences.")
                if (not repeat.get("count") and not repeat.get("count_question") and not repeat.get("until_anchor")
                        and not repeat.get("until_check") and not repeat.get("max_days") and not repeat.get("open_ended")):
                    errors.append(f"{label}: specify when the repeat ends or use an explicit rolling preview.")
    return list(dict.fromkeys(errors))


def compile_workflow(workflow: dict) -> dict:
    if workflow["schema"] != 1:
        raise ValueError("Unsupported workflow format")
    model = deepcopy(workflow["metadata"])
    model.update({key: [] for key in workflow["sections"]})
    events = {}
    for block in workflow["blocks"]:
        kind = block["kind"]
        operation = deepcopy(block["operation"])
        if kind == "event_visit":
            events[block["owner"]]["visits"].append(operation)
        else:
            if kind == "event":
                operation["visits"] = []
                events[operation["id"]] = operation
            model[COLLECTIONS[kind]].append(operation)
    return model


def approval_problems(workflow: dict) -> list[str]:
    return workflow["errors"] + [p["text"] for p in workflow["issues"]
                                 if not p.get("resolution") or p["kind"] in ("cannot_represent", "cleanup")]


def saved_model(doc: dict) -> dict:
    if workflow := doc.get("workflow"):
        if doc.get("workflow_sha256") != fingerprint(workflow):
            raise ValueError("The approved workflow has changed.")
        return compile_workflow(workflow)
    return doc["model"]
