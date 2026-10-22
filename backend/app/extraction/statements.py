"""Statement disposition ledger (IR-05, RV-01, AT-04).

Built by the compiler after validation. Every statement the reader reported -
each valid fact, each raw item that failed validation, each duplicate removed
during reconciliation, each schedule-table footnote - gets exactly one
disposition (``ledger.DISPOSITIONS``) with the rules it compiled into or the
reason it did not.

It also enforces the rule that makes the ledger matter: an uncompiled
statement about an activity blocks readiness of every compiled rule about that
same activity. A compiled primary reading can never make a visit look ready
while an additional statement about it is pending, unsupported or invalid.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.domain.schedule.models import IssueStatus, Severity, UniversalSchedule, ValidationIssue
from app.domain.schedule.timing import UnresolvedTiming

from . import ledger as L
from .reader import quote_found

if TYPE_CHECKING:
    from .compiler import _Compiler

_ORIGIN = "EXTRACTION"
_BATCH = re.compile(r"^b(\d+)-")


def _code(name: str) -> str:
    from .compiler import code_for
    return code_for(name or "")


def _issue_fact_ids(issue: ValidationIssue) -> list[str]:
    details = issue.details or {}
    ids = list(details.get("fact_ids") or [])
    if details.get("fact_id"):
        ids.append(details["fact_id"])
    if details.get("statement_id"):
        ids.append(details["statement_id"])
    return ids


def _batch(statement_id: str | None) -> int | None:
    match = _BATCH.match(statement_id or "")
    return int(match.group(1)) if match else None


def _citations(fact) -> list[dict[str, Any]]:
    return [{"page": citation.page, "section": citation.section, "table": citation.table,
             "quote": citation.quote, "verification": citation.verification}
            for citation in fact.evidence]


def build_statement_ledger(compiler: "_Compiler", schedule: UniversalSchedule,
                           issues: list[ValidationIssue],
                           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ValidationIssue]]:
    """(statements, relations, extra blocking issues) for one compiled schedule."""
    accountability = compiler.document.accountability or {}
    reconciliation = accountability.get("reconciliation") or {}
    raw_items = accountability.get("raw_items") or {}
    unknown = accountability.get("unknown_fields") or {}
    relations = list(reconciliation.get("relations") or []) + list(compiler.relations)

    by_fact: dict[str, list[ValidationIssue]] = {}
    for issue in issues:
        for fact_id in _issue_fact_ids(issue):
            by_fact.setdefault(fact_id, []).append(issue)
    events_by_id = {str(event.id): event for event in schedule.events}

    statements: list[dict[str, Any]] = []
    for fact in compiler.input_facts:
        own_id = fact.id
        merged_into = compiler.merged_into.get(own_id)
        refs = list(compiler.rule_refs.get(merged_into or own_id, []))
        outcomes = list(compiler.outcomes.get(own_id, []))
        related = [item for item in relations if own_id in (item.get("subject"), item.get("object"))]
        own_issues = by_fact.get(own_id, [])
        blocking = [issue for issue in own_issues if issue.blocking and issue.status == IssueStatus.OPEN]

        candidates: list[tuple[str, str, str | None]] = list(outcomes)
        for issue in blocking:
            operator = L.UNSUPPORTED_ISSUE_CODES.get(issue.issue_code) or (issue.details or {}).get("operator")
            if issue.issue_code in L.UNSUPPORTED_ISSUE_CODES:
                candidates.append((L.UNSUPPORTED, f"{issue.issue_code}: {issue.message}", operator))
            else:
                candidates.append((L.PENDING_INTERPRETATION, f"{issue.issue_code}: {issue.message}", None))
        for entry in refs:
            event = events_by_id.get(entry.get("entity_id", ""))
            if entry["entity_type"] == "EVENT" and event is not None and isinstance(event.timing, UnresolvedTiming) \
                    and not any(item[0] == L.PENDING_INTERPRETATION for item in candidates):
                candidates.append((L.PENDING_INTERPRETATION, f"timing unresolved: {event.timing.reason}", None))
        duplicate_of = next((item["object"] for item in related
                             if item["type"] == "DUPLICATE" and item["subject"] == own_id), None)

        precedence = [L.UNSUPPORTED, L.PENDING_INTERPRETATION, L.OUT_OF_SCOPE_NON_SCHEDULE]
        chosen = next(((disposition, reason, operator) for rank in precedence
                       for disposition, reason, operator in candidates if disposition == rank), None)
        if chosen is None and duplicate_of is not None:
            chosen = (L.DUPLICATE_OF, "states the same rule as another statement", None)
        if chosen is None and refs:
            if all(entry["entity_type"] == "TRIAL_LEVEL_RULE" for entry in refs):
                chosen = (L.OUT_OF_SCOPE_NON_SCHEDULE,
                          f"{fact.scope.lower()}-level rule: recorded for the trial workflow, not a "
                          "patient visit", None)
            elif merged_into:
                chosen = (L.COMPILED, f"merged into the phased plan stated by {merged_into}", None)
            else:
                chosen = (L.COMPILED, "compiled into the rules listed", None)
        if chosen is None:
            chosen = (L.PENDING_INTERPRETATION, "not used by any compiled rule; a reviewer must interpret it",
                      None)
        disposition, reason, operator = chosen
        statements.append({
            "statement_id": own_id,
            "source": (fact.provenance or {}).get("source") or "FACT",
            "what": fact.what, "fact_kind": fact.kind, "scope": fact.scope,
            "statement_type": fact.statement_type or "SCHEDULE",
            "non_schedule_reason": fact.non_schedule_reason,
            "occurrence_context": fact.occurrence_context,
            "batch": _batch(own_id),
            "citations": _citations(fact),
            "evidence_state": L.evidence_state(_citations(fact)),
            "disposition": disposition, "reason": reason, "operator": operator,
            "duplicate_of": duplicate_of if disposition == L.DUPLICATE_OF else None,
            "rule_refs": refs,
            "relations": related,
            "unknown_fields": unknown.get(own_id, []),
            "issue_codes": sorted({issue.issue_code for issue in own_issues}),
            "blocking_issue_codes": sorted({issue.issue_code for issue in blocking}),
            "provenance": fact.provenance or {},
            "raw_item": raw_items.get(own_id),
        })

    for removed in reconciliation.get("duplicates_removed") or []:
        raw = raw_items.get(removed["statement_id"])
        statements.append({
            "statement_id": removed["statement_id"], "source": "DUPLICATE_DELIVERY",
            "what": raw.get("what") if isinstance(raw, dict) else None, "fact_kind": None,
            "batch": _batch(removed["statement_id"]), "citations": [], "evidence_state": None,
            "disposition": L.DUPLICATE_OF, "reason": "the same statement was delivered more than once",
            "operator": None, "duplicate_of": removed["duplicate_of"], "rule_refs": [],
            "relations": [item for item in relations if item.get("subject") == removed["statement_id"]],
            "unknown_fields": [], "issue_codes": [], "blocking_issue_codes": [], "provenance": {},
            "raw_item": raw,
        })
    for rejected in accountability.get("rejected_items") or []:
        raw = rejected.get("raw_item")
        statements.append({
            "statement_id": rejected["statement_id"], "source": "RAW_ITEM",
            "what": (raw.get("what") or raw.get("row")) if isinstance(raw, dict) else None,
            "fact_kind": rejected.get("kind"), "batch": rejected.get("batch"), "citations": [],
            "evidence_state": None, "disposition": L.INVALID,
            "reason": f"could not be read as a statement: {rejected.get('error')}", "operator": None,
            "duplicate_of": None, "rule_refs": [], "relations": [], "unknown_fields": [],
            "issue_codes": ["EXTRACTION_FACT_INVALID"] if rejected.get("kind") == "fact" else [],
            "blocking_issue_codes": ["EXTRACTION_FACT_INVALID"] if rejected.get("kind") == "fact" else [],
            "provenance": {}, "raw_item": raw,
        })

    extra: list[ValidationIssue] = []
    extra += _footnotes(compiler, schedule, accountability, statements, relations)
    extra += _uncompiled_blocks_same_activity(schedule, statements, issues + extra)
    return statements, relations, extra


def _new_issue(code: str, message: str, *, entity_type: str | None, entity_id, **details: Any) -> ValidationIssue:
    return ValidationIssue(issue_code=code, message=message, severity=Severity.ERROR, blocking=True,
                           entity_type=entity_type, entity_id=entity_id,
                           details={**details, "origin": _ORIGIN})


#: Wording that makes a footnote schedule-relevant. If ANY of it is present the model's "no effect"
#: proposal is refused and the footnote stays pending a person: timing and counts, conditions,
#: populations, required/optional status, cross references, or any numeric amount.
_RELEVANT = re.compile(
    r"\d|\b(before|after|prior|within|until|every|each|per|weekly|daily|monthly|annual\w*|day|days|week|weeks|"
    r"month|months|year|years|hour|hours|window|visit window|baseline|screening|"
    r"required|requires|require|must|mandatory|shall|only|except|unless|if|when|whenever|applicable|"
    r"eligible|female|male|women|men|subjects? who|patients? who|"
    r"see|refer|section|appendix|table|figure|schedule|repeat\w*|optional|not required|at least|no later|"
    r"up to|maximum|minimum|first|last|final|then|following|preceding|continue\w*)\b", re.IGNORECASE)


def schedule_relevant_wording(text: str) -> list[str]:
    """The schedule-relevant words found in a footnote (empty = nothing that could be a rule)."""
    return sorted({match.group(0).lower() for match in _RELEVANT.finditer(text or "")})


def _no_effect(note: dict[str, Any]) -> tuple[bool, str]:
    """Whether a footnote may be recorded as having no scheduling effect, and why / why not.

    The model only PROPOSES it. It is accepted when the footnote text is verified on its page, the
    model gave a reason, AND a deterministic screen finds no schedule-relevant wording. Anything
    else stays pending interpretation - an unstated rule is never assumed absent."""
    if note.get("proposed_effect") != "NONE":
        return False, "the reader did not propose that it has no scheduling effect"
    if note.get("verification") != "TEXT_MATCHED":
        return False, "the footnote text could not be verified on its page"
    if not note.get("proposed_effect_reason"):
        return False, "no reason was given for treating it as having no effect"
    found = schedule_relevant_wording(str(note.get("text") or ""))
    if found:
        return False, "it contains schedule-relevant wording (" + ", ".join(found[:6]) + ")"
    return True, str(note["proposed_effect_reason"])


def _footnotes(compiler, schedule: UniversalSchedule, accountability: dict[str, Any],
               statements: list[dict[str, Any]], relations: list[dict[str, Any]]) -> list[ValidationIssue]:
    """A schedule-table footnote is a statement too: it is interpreted by the
    facts that quote it, or it is pending - and a pending footnote on a marked
    cell blocks the visits that cell's activity is performed at."""
    by_id = {statement["statement_id"]: statement for statement in statements}
    rows: list[tuple[Any, dict[str, Any]]] = [
        (fact, fact.provenance) for fact in compiler.input_facts
        if (fact.provenance or {}).get("source") == "TABLE_CELLS"]
    issues: list[ValidationIssue] = []
    for note in accountability.get("footnotes") or []:
        interpreting = [fact for fact in compiler.input_facts
                        if (fact.provenance or {}).get("source") != "TABLE_CELLS"
                        and any(quote_found(citation.quote, note["text"]) or quote_found(note["text"], citation.quote)
                                for citation in fact.evidence)]
        for fact in interpreting:
            relations.append({"type": "INTERPRETED_BY", "subject": note["statement_id"], "object": fact.id,
                              "reason": "the statement quotes this footnote"})
        dispositions = [by_id[fact.id]["disposition"] for fact in interpreting if fact.id in by_id]
        if note.get("verification") == "REJECTED":
            disposition, reason = (L.PENDING_INTERPRETATION,
                                   "the footnote text reported by the reader is not on its page")
        elif any(item in {L.COMPILED, L.DUPLICATE_OF} for item in dispositions):
            disposition, reason = L.COMPILED, "interpreted by the statements listed in relations"
        elif dispositions:
            disposition, reason = dispositions[0], "the statements interpreting it are not compiled"
        else:
            disposition, reason = L.PENDING_INTERPRETATION, "no statement interprets this footnote"
            accepted, why = _no_effect(note)
            if accepted:
                disposition = L.NO_SCHEDULING_EFFECT
                reason = (f"read and recorded as having no scheduling effect: {why} "
                          "(no timing, condition, population or requirement wording; a reviewer can reopen it)")
            elif note.get("proposed_effect") == "NONE":
                reason = f"no statement interprets this footnote; 'no effect' was proposed but refused: {why}"
        affected: list[str] = []
        columns: dict[str, set[str]] = {}
        if note.get("label"):
            for fact, provenance in rows:
                if (provenance.get("table") or None) != (note.get("table") or None):
                    continue
                cells = [cell for cell in provenance.get("cells") or [] if note["label"] in cell.get("footnotes", [])]
                if cells:
                    affected.append(fact.id)
                    columns[fact.id] = {_code(cell["column"]) for cell in cells}
        statement = {
            "statement_id": note["statement_id"], "source": "FOOTNOTE", "what": note["text"][:120],
            "fact_kind": "footnote", "batch": note.get("batch"),
            "citations": [{"page": note["page"], "section": None, "table": note.get("table"),
                           "quote": note["text"], "verification": note.get("verification")}],
            "evidence_state": note.get("verification"), "disposition": disposition, "reason": reason,
            "operator": None, "duplicate_of": None, "rule_refs": [],
            "relations": [item for item in relations if item.get("subject") == note["statement_id"]],
            "unknown_fields": [], "issue_codes": [], "blocking_issue_codes": [],
            "provenance": {"label": note.get("label"), "table": note.get("table"), "cell_statements": affected,
                           "found_by": note.get("source"), "proposed_effect": note.get("proposed_effect"),
                           "proposed_effect_reason": note.get("proposed_effect_reason")},
            "raw_item": None,
        }
        statements.append(statement)
        if disposition not in L.UNCOMPILED:
            continue
        targets = []
        for fact_id in affected:
            # Only the visits of the annotated cells, not every visit of the row.
            wanted = {compiler.event_names.get(column, column) for column in columns[fact_id]}
            for entry in compiler.rule_refs.get(fact_id, []):
                event_code = entry.get("event_code") or ""
                event = compiler.events.get(event_code)
                matches = event_code in wanted or any(event_code.startswith(f"{item}_P") for item in wanted)
                if event is not None and matches and event not in targets:
                    targets.append(event)
        message = (f"Footnote {note.get('label') or ''} of {note.get('table') or 'the schedule table'} "
                   f"(page {note['page']}) is not interpreted: {reason}. The rows it annotates are not ready "
                   "until a reviewer interprets it.")
        statement["issue_codes"] = statement["blocking_issue_codes"] = ["FOOTNOTE_NOT_INTERPRETED"]
        for event in targets or [None]:
            if event is not None:
                event.requires_review = True
            issues.append(_new_issue("FOOTNOTE_NOT_INTERPRETED", message,
                                     entity_type="EVENT" if event is not None else None,
                                     entity_id=event.id if event is not None else None,
                                     fact_ids=[note["statement_id"]], footnote=note))
    return issues


def _uncompiled_blocks_same_activity(schedule: UniversalSchedule, statements: list[dict[str, Any]],
                                     issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """IR-05 / AT-04: an uncompiled statement about an activity blocks every
    compiled rule about that same activity, so none can read as ready."""
    extra: list[ValidationIssue] = []
    for statement in statements:
        if statement["disposition"] not in L.UNCOMPILED or statement["source"] == "FOOTNOTE":
            continue
        name = statement.get("what")
        if not name:
            continue
        code = _code(name)
        own_entities = {entry.get("entity_id") for entry in statement["rule_refs"]}
        for event in schedule.events:
            same_event = event.code == code or event.code.startswith(f"{code}_P")
            same_activity = any(activity.code == code for activity in event.activities)
            if not (same_event or same_activity) or str(event.id) in own_entities and same_event:
                continue
            already = any(issue.entity_id == event.id and issue.blocking and issue.status == IssueStatus.OPEN
                          and statement["statement_id"] in _issue_fact_ids(issue)
                          for issue in issues + extra)
            if already:
                continue
            event.requires_review = True
            extra.append(_new_issue(
                "UNCOMPILED_STATEMENT_FOR_ENTITY",
                f"{event.display_name!r} has a compiled rule, but another statement about "
                f"{name!r} is {statement['disposition'].replace('_', ' ').lower()} "
                f"({statement['reason']}). It is not ready until that statement is resolved.",
                entity_type="EVENT", entity_id=event.id, fact_ids=[statement["statement_id"]],
                statement_disposition=statement["disposition"], operator=statement.get("operator")))
            statement["blocking_issue_codes"] = sorted(set(statement["blocking_issue_codes"])
                                                       | {"UNCOMPILED_STATEMENT_FOR_ENTITY"})
    return extra
