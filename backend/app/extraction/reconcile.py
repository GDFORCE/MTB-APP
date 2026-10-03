"""Whole-document reconciliation after every batch is read (IR-02..IR-04, AT-12).

The reader asks one question per page batch, so no single answer sees the
whole protocol. This deterministic pass (no model call) runs over ALL facts
from ALL batches before compiling:

1. **Duplicate delivery** - a fact delivered twice (overlapping batches, a
   retried reply) is kept once; the copy is recorded as DUPLICATE_OF with its
   own provenance, so it stays visible on the statement ledger.
2. **Canonical order** - facts are ordered by source position and meaning, not
   by reply order, so reordering batches or replies compiles to the same
   normalized rules.
3. **Anchor canonicalisation** - reference dates are matched against the
   overview's canonical anchor list. Pure wording variants ("registration",
   "the Registration", "date of registration") are merged onto the canonical
   spelling with provenance. A name that is NOT on the list ("study entry"
   when the list says "Registration") is never merged by guess: it becomes one
   shared blocking review question listing every statement that uses it.
4. **Occurrence identity** - two statements about one activity in different
   stated occurrence contexts ("pre-treatment" / "follow-up") are different
   occurrences, not a conflict, and are named apart.
5. **Visits with activities** - several activities the reader timed
   identically (typically one fact per drug given on "Day 1 of each cycle")
   become activities of ONE visit carrying that timing; the timing itself is
   unchanged.

Relations between statements (DUPLICATE, GROUPED_INTO) are recorded here;
AND / CONFLICTS_WITH are decided by the compiler over the same global groups.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .compiler import code_for
from .facts import CONDITION_PATTERNS, NON_PATIENT_SCOPES, DocumentFacts, ReaderIssue, ReferenceTo, ScheduleFact

RECONCILER_VERSION = "uctsm-fact-reconciler.v1"

_LEADING = re.compile(r"^(the|a|an|date of|day of|time of|the date of|the day of)\s+")
_TRAILING = re.compile(r"\s+(date|day)$")


def anchor_key(name: str) -> str:
    """Wording-insensitive key: case, punctuation, articles and a "date of"
    wrapper do not change which date is meant. Different words do."""
    text = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    previous = None
    while previous != text:
        previous = text
        text = _LEADING.sub("", text).strip()
        text = _TRAILING.sub("", text).strip()
    return text


def _semantic(fact: ScheduleFact, *, with_evidence: bool = True) -> str:
    data = fact.model_dump(mode="json", by_alias=True, exclude={
        "id", "conflict_group", "conflicts_with", "provenance", "evidence", "alternative_group"})
    data["what"] = code_for(fact.what)
    # Labels are chosen per reply (batch-prefixed); only their presence means anything.
    data["alternative"] = bool(fact.alternative_group)
    data["conflict"] = bool(fact.conflict_group or fact.conflicts_with)
    if with_evidence:
        data["evidence"] = sorted(
            [citation.page, re.sub(r"[^a-z0-9]+", " ", citation.quote.lower()).strip()]
            for citation in fact.evidence)
    return json.dumps(data, sort_keys=True, default=str)


def _is_event_fact(fact: ScheduleFact) -> bool:
    pattern = fact.timing.pattern if fact.timing else None
    if fact.kind == "condition" or pattern in CONDITION_PATTERNS or fact.scope in NON_PATIENT_SCOPES:
        return False
    if fact.statement_type == "NON_SCHEDULE":
        return False
    if fact.kind == "visit":
        return True
    if fact.kind == "activity":
        reference = fact.timing.reference if fact.timing else None
        return fact.timing is not None and not (reference and reference.kind == "ACTIVITY")
    return False


def _references(fact: ScheduleFact) -> list[tuple[str, ReferenceTo]]:
    """Every anchor-like reference a fact makes, with where it sits."""
    found: list[tuple[str, ReferenceTo]] = []
    timing = fact.timing
    if timing and timing.reference and timing.reference.kind in (None, "ANCHOR"):
        found.append(("timing.from", timing.reference))
    if fact.repeat and fact.repeat.start and fact.repeat.start.kind in (None, "ANCHOR"):
        found.append(("repeat.start", fact.repeat.start))
    if fact.repeat and fact.repeat.end and fact.repeat.end.reference \
            and fact.repeat.end.reference.kind in (None, "ANCHOR"):
        found.append(("repeat.end.reference", fact.repeat.end.reference))
    return found


def _timing_label(fact: ScheduleFact) -> str | None:
    timing = fact.timing
    if timing is None:
        return None
    ref = timing.reference.name if timing.reference else None
    if timing.pattern == "CYCLE_DAY" and timing.cycle and timing.day is not None:
        cycle = fact.block or timing.cycle.start_anchor
        number = f" {timing.cycle.number}" if timing.cycle.number else ""
        return f"Day {timing.day} of {cycle} cycle{number}"
    if timing.pattern == "PROTOCOL_DAY" and timing.day is not None:
        return f"Day {timing.day}" + (f" from {ref}" if ref else "")
    if timing.pattern == "OFFSET" and timing.offset and ref:
        amount = timing.offset
        side = "before" if amount.value < 0 else "after"
        return f"{abs(amount.value)} {amount.unit.lower()}(s) {side} {ref}"
    if timing.pattern == "REPEAT" and fact.repeat and ref:
        return f"Every {fact.repeat.interval.value} {fact.repeat.interval.unit.lower()}(s) from {ref}"
    return None


def reconcile(document: DocumentFacts) -> DocumentFacts:
    """Return a reconciled copy; the input is not modified."""
    accountability = dict(document.accountability or {})
    report: dict[str, Any] = {"version": RECONCILER_VERSION, "duplicates_removed": [], "anchor_aliases": [],
                              "unmatched_anchors": [], "canonical_anchors": [], "occurrence_splits": [],
                              "grouped_visits": [], "relations": []}
    issues = list(document.reader_issues)

    # 1. duplicate delivery -------------------------------------------------------
    kept: list[ScheduleFact] = []
    seen: dict[str, str] = {}
    for fact in document.facts:
        key = _semantic(fact)
        if key in seen:
            report["duplicates_removed"].append({"statement_id": fact.id, "duplicate_of": seen[key]})
            report["relations"].append({"type": "DUPLICATE", "subject": fact.id, "object": seen[key],
                                        "reason": "delivered twice with identical meaning and source"})
            continue
        seen[key] = fact.id
        kept.append(fact)

    # 3. anchors --------------------------------------------------------------------
    overview = (accountability.get("overview_names") or {})
    canonical: list[str] = [name for name in overview.get("anchor_names") or [] if str(name).strip()]
    if document.day_numbering.day_1_anchor:
        canonical.append(document.day_numbering.day_1_anchor)
    by_key: dict[str, str] = {}
    for name in canonical:
        by_key.setdefault(anchor_key(name), name)
    report["canonical_anchors"] = list(dict.fromkeys(canonical))
    other_names = {anchor_key(name) for name in overview.get("visit_names") or []}
    for fact in kept:
        if _is_event_fact(fact) or fact.kind in {"visit", "activity"}:
            other_names.add(anchor_key(fact.what))
        if fact.condition:
            other_names.add(anchor_key(fact.condition.name))
        if fact.kind == "condition" or (fact.timing and fact.timing.pattern in CONDITION_PATTERNS):
            other_names.add(anchor_key(fact.what))
        if fact.block:
            other_names.add(anchor_key(fact.block))
    for block in document.blocks:
        other_names.add(anchor_key(block.name))

    unmatched: dict[str, dict[str, Any]] = {}

    def canonicalise(name: str, fact: ScheduleFact, where: str) -> str:
        if not by_key or fact.statement_type == "NON_SCHEDULE" or fact.scope in NON_PATIENT_SCOPES:
            return name  # never becomes a patient rule, so it asks no anchor question
        key = anchor_key(name)
        target = by_key.get(key)
        if target is not None:
            if target != name:
                report["anchor_aliases"].append({"from": name, "to": target, "fact_id": fact.id, "where": where})
            return target
        if key in other_names or code_for(name) in {"DAY_1", "D1", "STUDY_DAY_1", "DAY_ONE"}:
            return name  # a visit / condition / block reference, resolved by the compiler
        entry = unmatched.setdefault(code_for(name), {"name": name, "anchor_code": code_for(name),
                                                      "fact_ids": [], "where": []})
        if fact.id not in entry["fact_ids"]:
            entry["fact_ids"].append(fact.id)
            entry["where"].append(where)
        return name

    reconciled: list[ScheduleFact] = []
    for fact in kept:
        provenance = dict(fact.provenance)
        update: dict[str, Any] = {}
        timing = fact.timing
        if timing is not None:
            timing_update: dict[str, Any] = {}
            if timing.reference is not None and timing.reference.kind in (None, "ANCHOR"):
                name = canonicalise(timing.reference.name, fact, "timing.from")
                if name != timing.reference.name:
                    timing_update["reference"] = timing.reference.model_copy(update={"name": name})
            if timing.cycle is not None:
                name = canonicalise(timing.cycle.start_anchor, fact, "timing.cycle.start_anchor") \
                    if anchor_key(timing.cycle.start_anchor) in by_key else timing.cycle.start_anchor
                if name != timing.cycle.start_anchor:
                    timing_update["cycle"] = timing.cycle.model_copy(update={"start_anchor": name})
            if timing_update:
                update["timing"] = timing.model_copy(update=timing_update)
        if fact.repeat is not None:
            repeat_update: dict[str, Any] = {}
            if fact.repeat.start is not None and fact.repeat.start.kind in (None, "ANCHOR"):
                name = canonicalise(fact.repeat.start.name, fact, "repeat.start")
                if name != fact.repeat.start.name:
                    repeat_update["start"] = fact.repeat.start.model_copy(update={"name": name})
            end = fact.repeat.end
            if end is not None and end.reference is not None and end.reference.kind in (None, "ANCHOR"):
                name = canonicalise(end.reference.name, fact, "repeat.end.reference")
                if name != end.reference.name:
                    repeat_update["end"] = end.model_copy(update={
                        "reference": end.reference.model_copy(update={"name": name})})
            if repeat_update:
                update["repeat"] = fact.repeat.model_copy(update=repeat_update)
        if fact.kind in {"anchor", "milestone"}:
            name = canonicalise(fact.what, fact, "anchor")
            if name != fact.what:
                provenance["original_what"] = fact.what
                update["what"] = name
        aliases = [alias for alias in report["anchor_aliases"] if alias["fact_id"] == fact.id]
        if aliases:
            provenance["anchor_aliases"] = aliases
        if provenance != fact.provenance:
            update["provenance"] = provenance
        reconciled.append(fact.model_copy(update=update) if update else fact)

    for entry in unmatched.values():
        question = f"anchor-equivalence-{entry['anchor_code'].lower()}"
        entry["question_id"] = question
        entry["candidates"] = report["canonical_anchors"]
        report["unmatched_anchors"].append(entry)
        issues.append(ReaderIssue(
            code="ANCHOR_NOT_CANONICAL",
            message=(f"{entry['name']!r} is used as a reference date but is not one of the protocol's "
                     f"reference dates ({', '.join(report['canonical_anchors'])}). Decide once whether it "
                     "is the same date as one of them or a separate date; every statement using it waits "
                     "for that answer."),
            details={"anchor_name": entry["name"], "anchor_code": entry["anchor_code"],
                     "fact_ids": entry["fact_ids"], "candidates": entry["candidates"],
                     "question_id": question, "shared_question": True}))
    if not by_key:
        report["notes"] = ["The overview gave no anchor list, so reference dates were not checked "
                           "against one."]

    # 4. occurrence identity --------------------------------------------------------
    contexts: dict[str, set[str]] = {}
    for fact in reconciled:
        if _is_event_fact(fact) and fact.occurrence_context:
            contexts.setdefault(code_for(fact.what), set()).add(fact.occurrence_context.strip().lower())
    split = {code for code, values in contexts.items() if len(values) > 1}
    staged: list[ScheduleFact] = []
    for fact in reconciled:
        if _is_event_fact(fact) and fact.occurrence_context and code_for(fact.what) in split:
            label = f"{fact.what} ({fact.occurrence_context.strip()})"
            report["occurrence_splits"].append({"fact_id": fact.id, "from": fact.what, "to": label})
            fact = fact.model_copy(update={"what": label, "provenance": {
                **fact.provenance, "original_what": fact.what, "occurrence_context_split": True}})
        staged.append(fact)

    # 5. visits with activities -----------------------------------------------------
    signature_groups: dict[str, list[ScheduleFact]] = {}
    for fact in staged:
        if fact.kind != "activity" or not _is_event_fact(fact) or fact.timing is None \
                or fact.timing.pattern == "UNRESOLVED" or fact.conflict_group or fact.conflicts_with \
                or fact.alternative_group:
            continue
        signature = json.dumps({
            "timing": fact.timing.model_dump(mode="json", by_alias=True),
            "repeat": fact.repeat.model_dump(mode="json", by_alias=True) if fact.repeat else None,
            "window": fact.window.model_dump(mode="json") if fact.window else None,
            "applies_to": fact.applies_to.model_dump(mode="json"), "block": fact.block,
            "scope": fact.scope, "schedule_option": fact.schedule_option,
            "context": fact.occurrence_context, "depends_on": fact.depends_on,
            "dependency_mode": fact.dependency_mode,
        }, sort_keys=True, default=str)
        signature_groups.setdefault(signature, []).append(fact)
    visit_codes = {code_for(fact.what) for fact in staged if fact.kind == "visit"}
    grouped: dict[str, str] = {}
    synthesized: list[ScheduleFact] = []
    for number, members in enumerate(signature_groups.values(), start=1):
        if len({code_for(fact.what) for fact in members}) < 2:
            continue
        label = _timing_label(members[0])
        if label is None or code_for(label) in visit_codes:
            continue
        visit_id = f"g{number}"
        first = members[0]
        visit = first.model_copy(update={
            "id": visit_id, "what": label, "kind": "visit", "code": None, "event_type": None,
            "activities": [fact.what for fact in members], "at_visits": [],
            "evidence": [citation for fact in members for citation in fact.evidence],
            "provenance": {"source": "GROUPED_ACTIVITIES", "fact_ids": [fact.id for fact in members],
                           "reason": "activities stated with identical timing are held at one visit"},
        })
        visit_codes.add(code_for(label))
        synthesized.append(visit)
        report["grouped_visits"].append({"visit_statement_id": visit_id, "label": label,
                                         "fact_ids": [fact.id for fact in members]})
        for fact in members:
            grouped[fact.id] = label
            report["relations"].append({"type": "GROUPED_INTO", "subject": fact.id, "object": visit_id,
                                        "reason": "identical stated timing"})
    final: list[ScheduleFact] = []
    for fact in staged:
        label = grouped.get(fact.id)
        if label is not None:
            fact = fact.model_copy(update={
                "timing": None, "repeat": None, "window": None, "at_visits": [label],
                "block": None, "depends_on": None, "dependency_mode": None,
                "provenance": {**fact.provenance, "grouped_into": label,
                               "timing_moved_to_visit": True}})
        final.append(fact)
    final.extend(synthesized)

    # 2. canonical order (last, so every rewrite above is order-independent) --------
    def order(fact: ScheduleFact):
        pages = [citation.page for citation in fact.evidence] or [0]
        return (min(pages), code_for(fact.what), fact.kind, _semantic(fact))
    final.sort(key=order)

    accountability["reconciliation"] = report
    return document.model_copy(update={"facts": final, "reader_issues": issues,
                                       "accountability": accountability})
