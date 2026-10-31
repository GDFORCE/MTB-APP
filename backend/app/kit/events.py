"""The events check: a separate model request that reads the whole protocol for what happens to
the schedule when something is logged (pregnancy, withdrawal, death, a serious adverse event…).

The model build covers events among everything else and sometimes names an event without saying
what it does. This pass looks only at events. For every event in the model and every item of a
fixed checklist it reports, with the protocol's own words and page, either what changes (visits
cancelled or added, tests added or dropped at later visits, the study ending, answers asked when
it is logged) or that the protocol states no schedule change, or that the protocol does not
mention it. The changes are merged into the model in the shapes the schedule engine already
applies; each quote is looked up in the PDF text so an invented one is flagged, never trusted
silently. Nothing is removed from the model unless the check names it with a reason.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable

from .llm import AnalysisError
from .normalize import _s, arr
from .prompts import RULE_CONDITIONS, SCHEMA, describe_anchors
from .summarize import MIN_CHUNK_CHARS, split_sections

StageCallback = Callable[[str, str, Any], None]

# Asked about in every protocol, so "the protocol does not mention it" is a recorded answer
# rather than a gap nobody looked at.
CHECKLIST: list[tuple[str, str]] = [
    ("pregnancy", "Pregnancy (of the participant, or of a partner if the protocol follows that)"),
    ("consent_withdrawal", "Withdrawal of consent"),
    ("treatment_discontinuation", "Study treatment stopped early while the participant stays in the study"),
    ("early_termination", "Early termination or discontinuation from the study"),
    ("death", "Death"),
    ("lost_to_follow_up", "Lost to follow-up"),
    ("serious_adverse_event", "Serious adverse event, adverse event of special interest or dose-limiting toxicity"),
    ("dose_interruption", "Dose interruption, hold or delay"),
    ("dose_change", "Dose reduction, escalation or change"),
    ("disease_progression", "Disease progression, relapse or recurrence"),
    ("overdose", "Overdose or medication error"),
    ("rescue_medication", "Rescue medication use"),
    ("unscheduled_visit", "Unscheduled visit"),
    ("screen_failure", "Screen failure or failing a re-check before dosing"),
]
CHECKLIST_IDS = {cid for cid, _ in CHECKLIST}
STATUSES = ("changes", "no_schedule_change", "not_in_protocol")
MAX_QUOTE = 400


def _js(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _model_view(st: dict[str, Any]) -> dict[str, Any]:
    """What the check needs to know about the model, with the ids it must reuse."""
    visits = []
    for v in st["visits"]:
        r = v.get("repeat") or {}
        item = {"id": v["id"], "code": v["code"], "name": v["name"], "phase": v.get("phase"),
                "anchor": v.get("anchor"), "offset_days": v.get("offset_days"),
                "procedures": [p["name"] for p in v.get("procedures", [])]}
        if r:
            item["repeat"] = {k: r.get(k) for k in ("every_days", "count", "open_ended", "until_anchor")}
        visits.append(item)
    questions = [{"id": q["id"], "scope": q["scope"], "kind": q["kind"],
                  "options": [o["value"] for o in q.get("options", [])]} for q in st["questions"]]
    return {"visits": visits, "questions": questions, "events": st.get("events", []), "rules": st.get("rules", [])}


def build_events_prompt(st: dict[str, Any], text: str, symbol_note: str = "", part: str = "") -> str:
    view = _model_view(st)
    checklist = "\n".join(f"- {cid}: {label}" for cid, label in CHECKLIST)
    scope = (f"\nYou are given {part} of the protocol text; report only what this part says. Use "
             '"not_in_protocol" for a checklist item this part does not mention.\n' if part else "")
    return (
        "You are a clinical trial protocol analyst checking ONE thing: what happens to a participant's "
        "visit schedule when an event is logged during the study. A visit-schedule model has already been "
        "built from this protocol; it is given below with its ids. Events in it are often incomplete: an "
        "event is named but logging it changes nothing. Read the WHOLE protocol text, including the "
        "synopsis, study design, visit descriptions, schedule-table footnotes, discontinuation and withdrawal "
        "sections, safety and adverse event reporting, pregnancy reporting, study stopping rules and the "
        f"appendices.{scope}\n\n"
        "Return exactly one entry for EVERY event already in the model, for EVERY checklist item below, and "
        "for every other event the protocol says adds, cancels, moves or replaces visits or assessments. When a "
        "checklist item is the same thing as a model event, give one entry with the model's event id and the "
        "checklist id in \"checklist\". A new event gets a new short snake_case id.\n"
        f"CHECKLIST:\n{checklist}\n\n"
        "For each entry:\n"
        '- status: "changes" when logging it changes visits, assessments or answers; "no_schedule_change" when '
        'the protocol mentions it but states no change to visits or assessments; "not_in_protocol" when the '
        "protocol never mentions it.\n"
        "- evidence: the passages that say so, each {\"page\": n, \"quote\": ...} where n is the [[Page n]] "
        f"number and quote is ONE continuous passage of at most {MAX_QUOTE} characters copied character for "
        "character from that page (no paraphrase, no joining passages with \"...\": give several items "
        'instead). Required for "changes" and "no_schedule_change"; [] for "not_in_protocol".\n'
        "- summary: at most 200 characters, plain words: what happens when it is logged.\n"
        "- visits: the COMPLETE list of visits logging it adds (follow-up contacts, end-of-treatment, "
        "pregnancy outcome calls…), in the visit shape below. offset_days counts from the event date (the "
        "visit's anchor is ignored); include_if may use the event's own questions. Keep the ids of the event's "
        "existing visits that are right; correct them in place; remove_visits lists, with a reason, any "
        "existing one the protocol contradicts.\n"
        "- rules: the COMPLETE list of rules this event triggers, in the rule shape below, when = "
        '{"event": thisEventId}: stop_visits = the visit ids that no longer happen after it (every '
        "occurrence still to come; scheduled visits or visits another event added), stop_all when participation ends, same_day, blocks, allowed, pause (dose "
        f"interruption), set_answers, and \"if\" for different branches. {RULE_CONDITIONS} Keep the ids of "
        "existing rules that are right; remove_rules lists, with a reason, any existing rule for this event "
        "the protocol contradicts. Two rules for the same event must have conditions that tell them apart.\n"
        "- add_tests: assessments done at the regular scheduled visits AFTER the event is logged that are not "
        'done otherwise: {"name": short assessment name, "visits": [visitId] or "all" (every later scheduled '
        'visit), "need": "required"|"if_indicated", "source": "p. n"}. ("Urine pregnancy test at every '
        'remaining visit" = {"name": "Urine pregnancy test", "visits": "all"}.)\n'
        "- remove_tests: assessments the regular visits no longer include after the event: {\"name\": the "
        "assessment exactly as it is named in the model's visit procedures, \"visits\": [visitId] or \"all\", "
        '"source"}.\n'
        "- questions: what the site is asked when it logs the event (grade, outcome, whether the participant "
        "continues), in the event question shape, when a branch depends on it.\n"
        "- ends_schedule: true only when participation ends (every later visit stops except the follow-up "
        "visits this event adds). keeps_visits: visit ids that still happen when it ends the schedule, only when the "
        "protocol says so (a safety follow-up 30 days after the last dose for patients who stop early; count that visit "
        'from an anchor of kind "last_visit" whose last_of lists the dosing visits, adding it in anchors_add if the model '
        "has none). Never for death. sets_anchor: an anchor id whose date is the event date, or null. "
        "repeatable: whether it can be logged more than once.\n"
        "- site_actions: things the protocol requires when it happens that are not visits or assessments, "
        'each at most 120 characters ("Report to the sponsor within 24 hours", "Stop dispensing study drug", '
        '"Unblind only if needed for treatment").\n'
        "Use only visit, anchor, question and rule ids that exist in the model or that you add here. A new date "
        "the protocol counts from (delivery date, end of treatment) goes in top-level anchors_add with kind "
        '"ask". Write only what the protocol states: never invent a visit, test, window or count; when the '
        "protocol is unclear, keep to what is certain and put the unclear point in top-level notes.\n\n"
        "Reply with ONLY one JSON object:\n"
        '{"events": [{"id": string, "label": string, "checklist": checklistId|null, "status": '
        '"changes"|"no_schedule_change"|"not_in_protocol", "summary": string, "evidence": [{"page": number, '
        '"quote": string}], "repeatable": boolean, "ends_schedule": boolean, "keeps_visits": [visitId], "sets_anchor": anchorId|null, '
        '"questions": [eventQuestion], "visits": [visit], "remove_visits": [{"id": visitId, "reason": string}], '
        '"rules": [rule], "remove_rules": [{"id": ruleId, "reason": string}], "add_tests": [...], '
        '"remove_tests": [...], "site_actions": [string]}], "anchors_add": [anchor], "notes": [string]}\n\n'
        "SHAPES (reference for visit, eventQuestion, rule, anchor and condition only; do not return a whole "
        f"model):\n{SCHEMA}\n\n"
        f"MODEL ANCHORS:\n{describe_anchors(st)}\n\n"
        f"MODEL SCHEDULED VISITS:\n{_js(view['visits'])}\n\n"
        f"MODEL QUESTIONS:\n{_js(view['questions'])}\n\n"
        f"MODEL EVENTS:\n{_js(view['events'])}\n\n"
        f"MODEL RULES:\n{_js(view['rules'])}\n\n"
        f"{symbol_note + chr(10) + chr(10) if symbol_note else ''}PROTOCOL TEXT:\n{text}"
    )


# Quotes are looked up in the PDF text, compared word by word so line breaks, hyphenation,
# ligatures and curly quotes in the extracted text do not make a real quote look invented.
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_PAGE = re.compile(r"^\[\[Page (\d+)[^\]]*\]\]", re.M)


def _words(text: str) -> str:
    text = unicodedata.normalize("NFKC", _s(text))
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # word broken across lines
    return " ".join(re.findall(r"[0-9a-z]+", text.lower()))


def page_words(page_texts: list[str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i, block in enumerate(page_texts):
        m = _PAGE.match(block or "")
        out[int(m.group(1)) if m else i + 1] = _words(_PAGE.sub("", block or "", count=1))
    return out


def verify_quote(page: Any, quote: Any, pages: dict[int, str]) -> dict[str, Any]:
    """{"page", "quote", "found": True|False|None, "found_on"}. None = could not be checked (no text
    layer); a quote found on another page is accepted with that page recorded."""
    try:
        n = int(float(page))
    except (TypeError, ValueError):
        n = None
    item: dict[str, Any] = {"page": n, "quote": _s(quote).strip()[:MAX_QUOTE]}
    if not pages:
        return {**item, "found": None}
    words = _words(item["quote"])
    if len(words.split()) < 4:
        return {**item, "found": False}
    if n in pages and words in pages[n]:
        return {**item, "found": True, "found_on": n}
    other = next((p for p, text in pages.items() if words in text), None)
    return {**item, "found": other is not None, **({"found_on": other} if other is not None else {})}


def _merge_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Reports from parts of a protocol too large for one request, merged per event: the parts'
    findings are combined, and an event is "changes" if any part found a change."""
    rank = {"not_in_protocol": 0, "no_schedule_change": 1, "changes": 2}
    merged: dict[str, dict[str, Any]] = {}
    out: dict[str, Any] = {"events": [], "anchors_add": [], "notes": []}
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        out["anchors_add"].extend(arr(rep.get("anchors_add")))
        out["notes"].extend(arr(rep.get("notes")))
        for e in arr(rep.get("events")):
            if not isinstance(e, dict) or not _s(e.get("id")):
                continue
            cur = merged.get(_s(e["id"]))
            if cur is None:
                merged[_s(e["id"])] = cur = json.loads(json.dumps(e))
                out["events"].append(cur)
                continue
            if rank.get(_s(e.get("status")), 2) > rank.get(_s(cur.get("status")), 2):
                cur["status"] = e.get("status")
            for key in ("evidence", "questions", "visits", "remove_visits", "rules", "remove_rules",
                        "add_tests", "remove_tests", "site_actions"):
                cur[key] = arr(cur.get(key)) + arr(e.get(key))
            if e.get("summary") and e.get("summary") != cur.get("summary"):
                cur["summary"] = " ".join(x for x in (_s(cur.get("summary")), _s(e["summary"])) if x)
            for key in ("ends_schedule", "repeatable"):
                cur[key] = bool(cur.get(key)) or bool(e.get(key))
            cur["sets_anchor"] = cur.get("sets_anchor") or e.get("sets_anchor")
            cur["checklist"] = cur.get("checklist") or e.get("checklist")
    return out


def _ask(client: Any, st: dict[str, Any], text: str, symbol_note: str, progress: Callable[[str], None],
         part: str = "", images: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    try:
        return [client.json(build_events_prompt(st, text, symbol_note, part), images=images or [])]
    except AnalysisError as error:
        if error.code != "prompt_too_large" or len(text) <= MIN_CHUNK_CHARS:
            raise
    progress("reading the protocol in parts")
    pieces = split_sections(text, max(MIN_CHUNK_CHARS, len(text) // 2))
    out: list[dict[str, Any]] = []
    for i, piece in enumerate(pieces, 1):
        out.extend(_ask(client, st, piece, symbol_note, progress, part=f"part {i} of {len(pieces)}", images=images))
    return out


def _key_name(name: Any) -> str:
    return " ".join(_s(name).lower().split())


def _has_event_cond(conds: list[Any], eid: str, happened: bool) -> bool:
    return any(isinstance(c, dict) and c.get("event") == eid and (c.get("happened") is not False) == happened
               for c in conds)


def _targets(st: dict[str, Any], visits: Any) -> list[dict[str, Any]]:
    if visits == "all" or visits == ["all"]:
        return list(st["visits"])
    ids = {_s(v) for v in arr(visits)}
    return [v for v in st["visits"] if v["id"] in ids]


def _apply_one(st: dict[str, Any], item: dict[str, Any], events: dict[str, dict[str, Any]], a_ids: set[str],
               pages: dict[int, str], log: list[str], notes: list[str]) -> dict[str, Any] | None:
    eid = _s(item.get("id")).strip()
    status = item.get("status") if item.get("status") in STATUSES else "changes"
    evidence = [verify_quote(e.get("page"), e.get("quote"), pages) for e in arr(item.get("evidence"))
                if isinstance(e, dict) and _s(e.get("quote")).strip()]
    checklist = item.get("checklist") if item.get("checklist") in CHECKLIST_IDS else (
        eid if eid in CHECKLIST_IDS else None)
    check = {"status": status, "summary": _s(item.get("summary")).strip()[:300], "evidence": evidence,
             "checklist": checklist}
    ev = events.get(eid)
    label = _s(item.get("label")).strip() or (ev or {}).get("label") or eid
    if status == "not_in_protocol":
        if ev is not None:
            ev["check"] = check
            notes.append(f"The events check found no mention of “{ev['label']}” in the protocol, although the "
                         "model has it as an event. Check it against the protocol.")
        return check
    if ev is None:
        ev = {"id": eid, "label": label, "repeatable": item.get("repeatable") is not False,
              "ends_schedule": False, "sets_anchor": None, "note": None, "questions": [], "visits": []}
        st["events"].append(ev)
        events[eid] = ev
        log.append(f"Event added: {label}")
    name = ev["label"]
    if isinstance(item.get("repeatable"), bool):
        ev["repeatable"] = item["repeatable"]
    if item.get("ends_schedule") is True and not ev.get("ends_schedule"):
        ev["ends_schedule"] = True
        log.append(f"{name}: now ends the participant's schedule")
    elif item.get("ends_schedule") is False and ev.get("ends_schedule"):
        notes.append(f"The events check reads “{name}” as not ending participation, but the model ends the "
                     "schedule on it. It still ends the schedule; check this against the protocol.")
    # Visits that still happen after it ends the schedule: added, never removed here.
    for vid in arr(item.get("keeps_visits")):
        if _s(vid) and _s(vid) not in arr(ev.get("keeps_visits")):
            ev["keeps_visits"] = [*arr(ev.get("keeps_visits")), _s(vid)]
            log.append(f"{name}: {_s(vid)} still happens after it")
    anchor = _s(item.get("sets_anchor"))
    if anchor in a_ids and anchor != ev.get("sets_anchor"):
        if ev.get("sets_anchor"):
            notes.append(f"The events check says “{name}” sets {anchor}, the model says {ev['sets_anchor']}. "
                         "The model's anchor is kept; check this against the protocol.")
        else:
            ev["sets_anchor"] = anchor
            log.append(f"{name}: its date now sets {anchor}")

    # Questions asked when it is logged: new ones added, same id replaced.
    q_by_id = {q.get("id"): i for i, q in enumerate(ev["questions"]) if isinstance(q, dict)}
    for q in arr(item.get("questions")):
        if isinstance(q, dict) and _s(q.get("id")) and q.get("text"):
            if _s(q["id"]) in q_by_id:
                ev["questions"][q_by_id[_s(q["id"])]] = q
            else:
                ev["questions"].append(q)
                q_by_id[_s(q["id"])] = len(ev["questions"]) - 1
                log.append(f"{name}: asks “{_s(q.get('label') or q['text'])}” when logged")

    # Visits it adds: matched to existing ones by id, then by code; removed only when named.
    removed = {_s(x.get("id")): _s(x.get("reason")) for x in arr(item.get("remove_visits")) if isinstance(x, dict)}
    for vid, reason in removed.items():
        old = next((v for v in ev["visits"] if v.get("id") == vid), None)
        if old is not None:
            ev["visits"].remove(old)
            log.append(f"{name}: visit {old.get('code') or vid} removed ({reason or 'contradicts the protocol'})")
    for i, v in enumerate(arr(item.get("visits"))):
        if not isinstance(v, dict) or not (v.get("name") or v.get("code")):
            continue
        v = dict(v)
        v["id"] = _s(v.get("id")) or f"{eid}_x{i + 1}"
        if v["id"] in removed:
            continue
        same = next((k for k, x in enumerate(ev["visits"]) if x.get("id") == v["id"]), None)
        if same is None and v.get("code"):
            same = next((k for k, x in enumerate(ev["visits"]) if x.get("code") == v["code"]), None)
        if same is None:
            ev["visits"].append(v)
            log.append(f"{name}: adds visit {v.get('code') or v.get('name')}")
        else:
            v["id"] = ev["visits"][same]["id"]
            ev["visits"][same] = {**ev["visits"][same], **v}

    # Rules it triggers: same id replaced, otherwise added; removed only when named.
    rules = st.setdefault("rules", [])
    drop = {_s(x.get("id")): _s(x.get("reason")) for x in arr(item.get("remove_rules")) if isinstance(x, dict)}
    for rid, reason in drop.items():
        old = next((r for r in rules if isinstance(r, dict) and r.get("id") == rid
                    and (r.get("when") or {}).get("event") == eid), None)
        if old is not None:
            rules.remove(old)
            log.append(f"{name}: rule “{old.get('label') or rid}” removed ({reason or 'contradicts the protocol'})")
    for i, r in enumerate(arr(item.get("rules"))):
        if not isinstance(r, dict):
            continue
        r = dict(r)
        r["id"] = _s(r.get("id")) or f"{eid}_rule{i + 1}"
        if r["id"] in drop:
            continue
        if not isinstance(r.get("when"), dict) or not (r["when"].get("event") or r["when"].get("anchor")):
            r["when"] = {"event": eid}
        k = next((k for k, x in enumerate(rules) if isinstance(x, dict) and x.get("id") == r["id"]), None)
        if k is None:
            rules.append(r)
            log.append(f"Rule added: {r.get('label') or r['id']}")
        else:
            rules[k] = r

    # Tests at later regular visits: an "after this event" condition, checked at each visit's date.
    for t in arr(item.get("add_tests")):
        if not isinstance(t, dict) or not _s(t.get("name")).strip():
            continue
        tname = _s(t["name"]).strip()
        hit = 0
        for v in _targets(st, t.get("visits")):
            same = [p for p in v["procedures"] if _key_name(p.get("name")) == _key_name(tname)]
            if any(not p.get("if") or _has_event_cond(arr(p.get("if")), eid, True) for p in same):
                continue  # already done at that visit
            proc: dict[str, Any] = {"name": tname, "if": [{"event": eid, "happened": True}]}
            if t.get("need") == "if_indicated":
                proc["need"] = "if_indicated"
            if t.get("source"):
                proc["source"] = _s(t["source"])
            v["procedures"].append(proc)
            hit += 1
        if hit:
            log.append(f"{name}: adds “{tname}” at {hit} later visit{'s' if hit != 1 else ''}")
        else:
            notes.append(f"The events check says “{name}” adds “{tname}”, but no visit it named could take it. "
                         "Check this against the protocol.")
    for t in arr(item.get("remove_tests")):
        if not isinstance(t, dict) or not _s(t.get("name")).strip():
            continue
        tname = _s(t["name"]).strip()
        hit = 0
        for v in _targets(st, t.get("visits")):
            for p in v["procedures"]:
                if _key_name(p.get("name")) == _key_name(tname) and not any(
                        isinstance(c, dict) and isinstance(c.get("not"), dict) and c["not"].get("event") == eid
                        for c in arr(p.get("if"))):
                    p["if"] = [*arr(p.get("if")), {"not": {"event": eid, "happened": True}}]
                    hit += 1
        if hit:
            log.append(f"{name}: stops “{tname}” at {hit} later visit{'s' if hit != 1 else ''}")
        else:
            notes.append(f"The events check says “{name}” stops “{tname}”, but no visit has an assessment by "
                         "that name. Check this against the protocol.")

    actions = [*arr(ev.get("site_actions"))]
    for a in arr(item.get("site_actions")):
        a = _s(a).strip()[:160]
        if a and a not in actions:
            actions.append(a)
    if actions:
        ev["site_actions"] = actions
    ev["check"] = check
    return check


def apply_event_check(st: dict[str, Any], res: Any, page_texts: list[str] | None) -> dict[str, Any]:
    """Merge the events check into the model (before it is normalized again). Returns
    {"ran": True, "changes": [...], "events": [{id, label, status, ...}]}."""
    res = res if isinstance(res, dict) else {}
    pages = page_words(page_texts or [])
    log: list[str] = []
    notes: list[str] = []
    a_ids = {a["id"] for a in st["anchors"]}
    for a in arr(res.get("anchors_add")):
        if isinstance(a, dict) and _s(a.get("id")) and _s(a["id"]) not in a_ids:
            # New dates are entered by the site, except "the last of these visits" (normalize checks last_of).
            kind = "last_visit" if a.get("kind") == "last_visit" and arr(a.get("last_of")) else "ask"
            st["anchors"].append({**a, "id": _s(a["id"]), "kind": kind, "optional": True})
            a_ids.add(_s(a["id"]))
            log.append(f"Date added: {_s(a.get('label')) or a['id']}")
    events = {e["id"]: e for e in st["events"]}
    model_events = list(events)
    reported: dict[str, dict[str, Any]] = {}  # event id -> its check, in reply order
    covered: set[str] = set()  # checklist items answered
    not_found: list[str] = []
    for item in arr(res.get("events")):
        if not isinstance(item, dict) or not _s(item.get("id")).strip():
            continue
        eid = _s(item["id"]).strip()
        if eid in reported:
            continue
        check = _apply_one(st, item, events, a_ids, pages, log, notes)
        if check is None:
            continue
        label = (events.get(eid) or {}).get("label") or _s(item.get("label")) or eid
        reported[eid] = {**check, "label": label}
        if check["checklist"]:
            covered.add(check["checklist"])
        if check["status"] == "not_in_protocol":
            if eid not in events:
                not_found.append(label)
            continue
        if not check["evidence"]:
            notes.append(f"The events check gave no protocol passage for “{label}”. Check it against the protocol.")
        for e in check["evidence"]:
            if e["found"] is False:
                notes.append(f"The passage quoted for “{label}” (p. {e['page']}) was not found in the PDF text: "
                             f"“{e['quote'][:120]}”. Check it against the protocol.")
        if check["status"] == "no_schedule_change":
            ev = events.get(eid) or {}
            has = [what for what, hit in (
                ("ends the schedule", ev.get("ends_schedule")),
                (f"adds {len(ev.get('visits') or [])} visit(s)", ev.get("visits")),
                ("has rules", any((r.get("when") or {}).get("event") == eid for r in arr(st.get("rules"))
                                  if isinstance(r, dict))),
            ) if hit]
            pg = ", ".join(str(e.get("found_on") or e["page"]) for e in check["evidence"] if e.get("page"))
            if has:
                notes.append(f"The events check says logging “{label}” changes nothing, but the model "
                             f"{', '.join(has)} for it. Those are kept; check this against the protocol"
                             f"{f' (p. {pg})' if pg else ''}.")
            else:
                notes.append(f"Logging “{label}” does not change the schedule, according to the protocol"
                             f"{f' (p. {pg})' if pg else ''}.")
    missed = [events[eid]["label"] for eid in model_events if eid not in reported]
    if missed:
        notes.append("The events check did not report on: " + ", ".join(missed) + ". Check what logging each "
                     "of these changes against the protocol.")
    unchecked = [label for cid, label in CHECKLIST if cid not in covered]
    if unchecked:
        notes.append("The events check did not say whether the protocol covers: " + "; ".join(unchecked) + ".")
    if not_found:
        notes.append("Not mentioned in the protocol, according to the events check: " + "; ".join(not_found) + ".")
    if not pages:
        notes.append("The quoted protocol passages could not be checked against the PDF text (no text layer).")
    for n in arr(res.get("notes")):
        notes.append(_s(n))
    for n in notes:
        if n and n not in st["notes"]:
            st["notes"].append(n)
    summary = [{"id": eid, **chk} for eid, chk in reported.items()]
    out = {"ran": True, "changes": log, "events": summary}
    st["event_check"] = out
    return out


def run_event_check(prepared: dict[str, Any], st: dict[str, Any], client: Any, *, on_stage: StageCallback,
                    symbol_note: str = "", images: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Ask, apply and report. None when it could not run (the reason is in the model's notes)."""
    on_stage("events", "run", "reading what each event changes")
    if prepared.get("scanned"):
        st["notes"].append("The events check needs a text layer and did not run on this scanned PDF. Check what "
                           "logging each event (pregnancy, withdrawal, death…) changes against the protocol.")
        st["event_check"] = {"ran": False, "changes": [], "events": []}
        on_stage("events", "done", "skipped (scanned PDF)")
        return None
    page_texts = prepared.get("pageTexts") or []
    text = "\n\n".join(page_texts) or prepared.get("text") or ""
    reports = _ask(client, st, text, symbol_note, lambda message: on_stage("events", "run", message), images=images)
    out = apply_event_check(st, reports[0] if len(reports) == 1 else _merge_reports(reports), page_texts)
    n = len(out["changes"])
    on_stage("events", "done", f"{len(out['events'])} events checked · "
                               f"{n} change{'s' if n != 1 else ''} applied" if n else
             f"{len(out['events'])} events checked · no changes")
    return out
