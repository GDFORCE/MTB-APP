"""The draft Visit Schedule a reviewer reads, edits, exports and approves.

Design doc "Required user-facing output: Visit Schedule" and "Review and
activation contract"; requirements UI-01..UI-12 and RV-01..RV-05.

Everything here is DERIVED from the canonical schedule and the validator. It
never evaluates a patient, never invents a date and never decides a clinical
meaning the rules do not state:

* ``build_review_view`` turns the canonical rules plus a fresh validation run
  into the ``ScheduleReviewView`` contract (rows, shared questions,
  document-level issues, backend approval eligibility).
* ``review_revision`` / ``semantic_fingerprint`` identify the draft a reviewer
  looked at. Both are content hashes, so ANY write path (this module, the older
  correction routes, a future extraction retry) changes them without a
  dedicated revision column - a stale edit or approval is detected no matter
  which route changed the draft.
* ``ReviewWorkflow`` applies typed semantic edits, Add Visit, shared-question
  resolutions and whole-version approval, each guarded by an expected
  revision. Every write records who, why and what changed, and every reviewer
  statement is kept as REVIEWER_DECISION evidence - distinct from model
  interpretation (RV-02).
* ``export_json`` / ``export_csv`` carry every rule detail with provenance and
  the draft/approved label (UI-09).

Formatting is done from each rule's JSON form, one function per operator. An
operator this module does not know (for example one added by the constraint
work) is still listed, with an explicit "summary not available" label and its
full rule JSON, so nothing is silently dropped from review or export.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol
from uuid import UUID, uuid4

from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, ScheduleStatus, UniversalSchedule, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator


class ReviewVersion(Protocol):
    status: str
    metadata_json: dict[str, Any]


class ReviewRun(Protocol):
    id: UUID
    status: str
    provider: str | None
    model_name: str | None
    trace: dict[str, Any]
    error_details: dict[str, Any] | None

NON_ACKNOWLEDGEABLE = {"UNSUPPORTED_OPERATOR", "DEPENDS_ON_UNSUPPORTED_OPERATOR"}

REVIEW_VIEW_VERSION = "mtb.review-view.v1"
EXPORT_SCHEMA_VERSION = "mtb.visit-schedule-export.v1"
REVIEWER_EVIDENCE_TYPE = "REVIEWER_DECISION"

IMMUTABLE_STATUSES = {
    ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value, ScheduleStatus.ARCHIVED.value,
    ScheduleStatus.CURRENT_FOR_NEW_ENROLLMENT.value, ScheduleStatus.HISTORICAL_ACTIVE.value,
    ScheduleStatus.RETIRED_FOR_NEW_ENROLLMENT.value,
}

#: Extraction findings that mean part of the document was never turned into a
#: schedule. They cannot be "acknowledged" away (existing service rule).
COVERAGE_FAILURE_CODES = {"SCHEDULE_OPTION_FAILED", "SCHEDULE_DISCOVERY_FAILED"}

WINDOW_ISSUE_CODES = {"AMBIGUOUS_WINDOW", "WINDOW_NOT_APPLIED"}

#: Agent A's extraction accountability ledger (``uctsm-extraction-accountability.v1``).
UNCOMPILED_DISPOSITIONS = {"PENDING_INTERPRETATION", "UNSUPPORTED", "INVALID"}
DISPOSITION_LABELS = {
    "COMPILED": "Compiled into a schedule rule",
    "DUPLICATE_OF": "Duplicate of another statement",
    "PENDING_INTERPRETATION": "Waiting for interpretation",
    "UNSUPPORTED": "Not executable yet (unsupported rule)",
    "OUT_OF_SCOPE_NON_SCHEDULE": "Not a schedule rule",
    "INVALID": "Invalid extracted item",
}
TIMING_REVIEW_TYPES = {"UNRESOLVED", "APPROXIMATE", "PROTOCOL_DEFINED"}
SPAN_TIMING_TYPES = {"RANGE", "WITHIN", "NO_LATER_THAN"}
UNRESOLVED_INTERPRETATION = {"AMBIGUOUS", "CONFLICTING", "UNRESOLVED"}

#: Event fields this module formats explicitly. Any OTHER non-empty field on an
#: event (a new rule family) is still surfaced as an additional constraint.
_HANDLED_EVENT_FIELDS = {
    "id", "code", "protocol_label", "display_name", "normalized_name", "event_type",
    "epoch_id", "sequence_number", "timing", "window", "applicability", "conditions",
    "dependencies", "dependency_mode", "visit_mode", "allowed_visit_modes", "activation",
    "recurrence", "recurrence_plan", "conditional_actions", "qualifiers", "confinement",
    "activities", "evidence_refs", "interpretation_status", "requires_review", "metadata",
    "constraints", "calendar_recurrence", "track_id", "track_scope",
}
#: Metadata keys that are bookkeeping, not rules.
_BOOKKEEPING_METADATA = {
    "origin", "rationale", "review_state", "created_by", "created_at", "last_edit",
    "source_fact_ids", "fact_ids", "extraction", "legacy_id", "legacy_visit_id",
    "constraint_sources",
}


class StaleRevisionError(Exception):
    """The draft changed after the caller read it (RV-05, AT-15)."""

    def __init__(self, expected: str, current: str):
        super().__init__(
            "This draft changed since you loaded it. Reload the Visit Schedule, "
            "check what changed and try again.")
        self.expected = expected
        self.current = current


class ApprovalBlockedError(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("Approval is blocked: " + "; ".join(reasons))
        self.reasons = reasons


# --- identity of a draft ----------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sorted_by_id(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda item: str(item.get("id") if isinstance(item, dict) else item))


def _rules_payload(schedule: UniversalSchedule, *, with_provenance: bool) -> dict[str, Any]:
    data = schedule.model_dump(mode="json", by_alias=True)
    metadata = dict(data.get("schedule_metadata") or {})
    for volatile in ("status", "effective_from"):
        metadata.pop(volatile, None)
    events = []
    for event in data.get("events") or []:
        event = dict(event)
        event["activities"] = _sorted_by_id(list(event.get("activities") or []))
        if not with_provenance:
            event.pop("evidence_refs", None)
            for activity in event["activities"]:
                activity.pop("evidence_refs", None)
        events.append(event)
    payload: dict[str, Any] = {
        "schema_version": data.get("schema_version"),
        "schedule_metadata": metadata,
        "events": _sorted_by_id(events),
    }
    for key in ("epochs", "arms", "cohorts", "populations", "dimensions", "anchors",
                "conditional_definitions", "repeat_blocks", "confinement_episodes"):
        items = list(data.get(key) or [])
        if not with_provenance:
            items = [{k: v for k, v in item.items() if k != "evidence_refs"} for item in items]
        payload[key] = _sorted_by_id(items)
    # Fields another package adds to UniversalSchedule are part of the meaning.
    for key, value in data.items():
        if key not in payload and key not in {
            "schedule_version_id", "created_at", "evidence", "claim_evidence", "validation_issues",
        }:
            payload[key] = value
    if with_provenance:
        payload["evidence"] = _sorted_by_id(list(data.get("evidence") or []))
        payload["claim_evidence"] = sorted(
            data.get("claim_evidence") or [], key=_canonical)
    return payload


def semantic_fingerprint(schedule: UniversalSchedule) -> str:
    """Hash of the executable meaning only (rules, not evidence or review state)."""
    return hashlib.sha256(_canonical(_rules_payload(schedule, with_provenance=False)).encode()).hexdigest()


def _footnote_findings(issues) -> list[dict[str, Any]]:
    """Open "footnote not interpreted" findings, one entry per footnote (a footnote that marks
    several visits raises one finding per visit; resolving the footnote resolves them together)."""
    grouped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if issue.issue_code != "FOOTNOTE_NOT_INTERPRETED" or issue.status.value != "OPEN":
            continue
        note = (issue.details or {}).get("footnote") or {}
        key = str(note.get("statement_id") or issue.id)
        entry = grouped.setdefault(key, {
            "footnote_id": key, "label": note.get("label"), "table": note.get("table"),
            "page": note.get("page"), "text": note.get("text"), "verification": note.get("verification"),
            "message": issue.message, "issue_ids": []})
        entry["issue_ids"].append(str(issue.id))
    return sorted(grouped.values(), key=lambda item: (item["page"] or 0, str(item["label"] or "")))

def review_revision(schedule: UniversalSchedule) -> str:
    """Hash of everything a reviewer signs off: rules, evidence, review state.

    Derived validator findings are excluded - they are recomputed from the
    rules - but extraction findings and their resolution status are included,
    because resolving one is a review decision.
    """
    payload = _rules_payload(schedule, with_provenance=True)
    payload["extraction_findings"] = sorted(
        [str(issue.id), issue.status.value]
        for issue in schedule.validation_issues
        if issue.details.get("origin") == "EXTRACTION"
    )
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:32]


# --- one formatter per operator ------------------------------------------------------


def _amount(amount: dict[str, Any] | None, *, absolute: bool = True) -> str:
    if not amount:
        return "an unstated amount"
    value = amount.get("value")
    unit = str(amount.get("unit") or "").lower()
    number = abs(value) if absolute and isinstance(value, (int, float)) else value
    plural = "" if number == 1 else "s"
    return f"{number} {unit}{plural}"


class _Names:
    """Human names for anchor / event / activity references."""

    def __init__(self, schedule: UniversalSchedule):
        self.anchors = {item.code: item for item in schedule.anchors}
        self.events = {item.code: item for item in schedule.events}
        self.processes = {item.code: item for item in getattr(schedule, "processes", None) or []}
        self.tracks = {str(item.id): item for item in schedule.tracks}
        self.holiday_calendars = {item.code for item in getattr(schedule, "holiday_calendars", None) or []}
        self.conditional_definitions = list(schedule.conditional_definitions)
        self.cycle_plans = {
            str(block.cycle_plan.code): block for block in schedule.repeat_blocks
            if block.cycle_plan is not None
        }

    def is_cycle_anchor(self, code: str) -> bool:
        return any(re.fullmatch(rf"{re.escape(plan)}_[1-9][0-9]*_START", code)
                   for plan in self.cycle_plans)

    def reference(self, reference: dict[str, Any] | None) -> str:
        if not reference:
            return "an unstated reference"
        kind = reference.get("kind")
        if kind == "ANCHOR":
            code = str(reference.get("code"))
            anchor = self.anchors.get(code)
            if anchor is not None:
                name = anchor.display_name
            elif self.is_cycle_anchor(code):
                match = re.match(r"^(.*)_([0-9]+)_START$", code)
                name = f"start of cycle {match.group(2)}" if match else code
            else:
                name = f"{code} (anchor not defined)"
            if reference.get("accepts") == "PLANNED_OR_ACTUAL":
                return f"{name} (planned or actual)"
            return name
        if kind == "EVENT":
            code = str(reference.get("event_code"))
            event = self.events.get(code)
            name = event.display_name if event else f"{code} (visit not defined)"
            occurrence = reference.get("occurrence")
            if occurrence == "FIRST":
                name = f"the first {name}"
            elif occurrence == "LAST":
                name = f"the last {name}"
            basis = reference.get("temporal_basis")
            labels = {"ORIGINAL_PLANNED": "original planned date", "CURRENT_PLANNED": "current planned date",
                      "ACTUAL_VISIT": "actual visit date", "ACTUAL_TREATMENT": "actual treatment date",
                      "MANUAL_ANCHOR": "manually entered date"}
            if basis == "ACTUAL_OR_PLANNED":
                fallback = labels.get(reference.get("fallback_basis"), "unstated fallback")
                return f"actual date of {name}, otherwise its {fallback}"
            return f"{labels[basis]} of {name}" if basis in labels else name
        if kind == "ACTIVITY":
            return f"{reference.get('activity_code')} in the same visit"
        return f"a {kind or 'unknown'} reference"


def _relative(amount: dict[str, Any] | None, reference: str) -> str:
    value = (amount or {}).get("value")
    if value == 0:
        return f"At {reference}"
    direction = "before" if isinstance(value, (int, float)) and value < 0 else "after"
    return f"{_amount(amount)} {direction} {reference}"


def describe_timing(timing: dict[str, Any] | None, names: _Names) -> tuple[str, bool]:
    """(summary, supported). ``supported`` False means no formatter exists."""
    if not timing:
        return "Timing not stated", True
    kind = timing.get("type")
    if kind == "ABSOLUTE":
        return f"On {timing.get('value')} (fixed calendar date)", True
    if kind == "OFFSET":
        return _relative(timing.get("offset"), names.reference(timing.get("reference"))), True
    if kind == "PROTOCOL_DAY":
        return f"Day {timing.get('day')} from {names.reference(timing.get('reference'))}", True
    if kind == "RANGE":
        reference = names.reference(timing.get("reference"))
        start, end = timing.get("start") or {}, timing.get("end") or {}
        low, high = start.get("value"), end.get("value")
        unit = str(start.get("unit") or "").lower()
        if isinstance(low, int) and isinstance(high, int):
            if low >= 0:
                return f"Between {low} and {high} {unit}s after {reference}", True
            if high <= 0:
                return f"Between {abs(high)} and {abs(low)} {unit}s before {reference}", True
            return f"From {abs(low)} {unit}s before to {high} {unit}s after {reference}", True
        return f"A range relative to {reference}", True
    if kind == "NOMINAL_WITH_WINDOW":
        return describe_timing(timing.get("nominal"), names)
    if kind == "WITHIN":
        direction = "before" if timing.get("direction") == "BEFORE" else "after"
        return (f"Within {_amount(timing.get('duration'))} {direction} "
                f"{names.reference(timing.get('reference'))}"), True
    if kind == "NO_LATER_THAN":
        duration = timing.get("duration") or {}
        direction = "before" if (duration.get("value") or 0) < 0 else "after"
        return (f"No later than {_amount(duration)} {direction} "
                f"{names.reference(timing.get('reference'))}"), True
    if kind == "APPROXIMATE":
        text, _ = describe_timing({"type": "OFFSET", "reference": timing.get("reference"),
                                   "offset": timing.get("offset")}, names)
        return f"About {text[0].lower()}{text[1:]} (approximate — needs review)", True
    if kind == "TRIGGERED":
        trigger = names.reference(timing.get("trigger"))
        after = timing.get("timing_after_trigger") or {}
        if after.get("type") == "WITHIN":
            direction = "before" if after.get("direction") == "BEFORE" else "after"
            return f"Within {_amount(after.get('duration'))} {direction} {trigger}", True
        if after.get("type") == "NO_LATER_THAN":
            return f"No later than {_amount(after.get('duration'))} after {trigger}", True
        return _relative(after.get("offset"), trigger), True
    if kind == "CYCLE_DAY":
        cycle = timing.get("cycle") or {}
        plan = timing.get("cycle_reference")
        if cycle.get("type") == "NUMBER":
            return f"Cycle {cycle.get('number')} Day {timing.get('day')} ({plan})", True
        return f"Day {timing.get('day')} of each cycle ({plan})", True
    if kind == "PROTOCOL_DEFINED":
        return "Defined in protocol wording — needs review", True
    if kind == "UNRESOLVED":
        return "Timing needs review", True
    if kind == "CONSTRAINED":
        tree = _constraint_tree(timing.get("constraint"), "timing/constraint", names)
        text = str((tree or {}).get("summary") or "Restriction set")
        return text[:1].upper() + text[1:], not _tree_unsupported(tree)
    return f"{kind or 'Unknown'} rule — summary not available; see full details", False


def _rename_references(tree: dict[str, Any], names: "_Names") -> dict[str, Any]:
    """B's formatter names a reference by its title-cased code ("Rt End"); show
    the protocol's own display name instead ("RT end"). Text only - ids, paths
    and references are untouched."""
    table = {code.replace("_", " ").title(): item.display_name
             for code, item in {**names.events, **names.anchors}.items()}
    table = {old: new for old, new in table.items() if old != new}
    if not table:
        return tree
    pattern = re.compile("|".join(re.escape(key) for key in sorted(table, key=len, reverse=True)))

    def visit(node: dict[str, Any]) -> dict[str, Any]:
        node = dict(node)
        for key in ("summary", "value_summary"):
            if isinstance(node.get(key), str):
                node[key] = pattern.sub(lambda match: table[match.group(0)], node[key])
        node["items"] = [visit(child) for child in node.get("items") or []]
        return node

    return visit(tree)


def _constraint_tree(node: Any, path: str, names: "_Names | None" = None) -> dict[str, Any] | None:
    """B's ``constraint_view`` for a constraint given as JSON (one formatter)."""
    from pydantic import TypeAdapter

    from app.domain.schedule.projection import constraint_view
    from app.domain.schedule.timing import Constraint

    if not node:
        return None
    try:
        typed = TypeAdapter(Constraint).validate_python(node)
    except Exception:
        return {"id": path, "path": path, "type": str((node or {}).get("type") if isinstance(node, dict) else "?"),
                "summary": "Restriction MTB cannot read — see full details", "executable": False,
                "evidence_refs": [], "items": [], "invalid": True}
    tree = constraint_view(typed, path)
    return _rename_references(tree, names) if names is not None else tree


def _walk_tree(tree: dict[str, Any] | None):
    if not tree:
        return
    yield tree
    for child in tree.get("items") or []:
        yield from _walk_tree(child)


def _tree_unsupported(tree: dict[str, Any] | None) -> bool:
    return any(node.get("executable") is False for node in _walk_tree(tree))


def _condition_unsupported(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "UNSUPPORTED" and "source_operator" in value:
            return True
        return any(_condition_unsupported(item) for item in value.values())
    if isinstance(value, list):
        return any(_condition_unsupported(item) for item in value)
    return False


def describe_termination(termination: dict[str, Any] | None, names: _Names) -> str:
    if not termination:
        return "no stated end"
    kind = termination.get("type")
    if kind == "COUNT":
        return f"for {termination.get('count')} occurrences"
    if kind == "DATE":
        return f"until {termination.get('date') or termination.get('termination_date')}"
    if kind == "EVENT":
        event = names.events.get(str(termination.get("event_code")))
        return f"until {event.display_name if event else termination.get('event_code')}"
    if kind == "CONDITION":
        return f"until {describe_condition(termination.get('condition'))}"
    if kind == "HORIZON":
        return "with no stated end (continues while on study)"
    if kind == "RELATIVE":
        boundary = "inclusive" if termination.get("inclusive") else "exclusive"
        return (f"until {_relative(termination.get('end_offset'), names.reference(termination.get('end_reference'))).lower()}"
                f" ({boundary})")
    return f"until a {kind} boundary (see details)"


WEEKDAY_NAMES = {"MON": "Mon", "TUE": "Tue", "WED": "Wed", "THU": "Thu", "FRI": "Fri",
                 "SAT": "Sat", "SUN": "Sun", "MONDAY": "Mon", "TUESDAY": "Tue", "WEDNESDAY": "Wed",
                 "THURSDAY": "Thu", "FRIDAY": "Fri", "SATURDAY": "Sat", "SUNDAY": "Sun"}
NEEDS_REVIEW = "Needs review"


def describe_calendar_recurrence(rule: dict[str, Any], names: _Names) -> dict[str, Any]:
    """Weekday series (WP3): days, count and count basis, holiday and missed-session
    policies. An unstated policy is shown as Needs review, never defaulted."""
    days = ", ".join(WEEKDAY_NAMES.get(str(day).upper(), str(day)) for day in rule.get("weekdays") or [])
    every = rule.get("interval_weeks") or 1
    start = names.reference(rule.get("start_reference"))
    if rule.get("start_offset"):
        start = _relative(rule.get("start_offset"), start).lower()
    termination = rule.get("termination") or {}
    counted = termination.get("type") == "COUNT"
    basis = rule.get("count_basis")
    if counted:
        noun = {"SLOTS": "scheduled sessions", "DELIVERED": "delivered sessions"}.get(basis, "sessions")
        ending = f"for {termination.get('count')} {noun}"
    else:
        ending = describe_termination(termination, names)
    summary = f"Every {days}" + (f" (every {every} weeks)" if every > 1 else "") + f" from {start}, {ending}"
    holiday = rule.get("holiday_policy") or "NONE_STATED"
    missed = rule.get("missed_policy") or "NONE_STATED"
    policies = [
        {"key": "count_basis", "label": "What counts toward the total",
         "value": ({"SLOTS": "Scheduled slots — a missed session still counts",
                    "DELIVERED": "Delivered sessions — a missed session does not count"}.get(basis)
                   if basis else (NEEDS_REVIEW if counted else "Not applicable (not a counted series)")),
         "status": "STATED" if basis else ("NEEDS_REVIEW" if counted else "NOT_APPLICABLE")},
        {"key": "holiday_policy", "label": "On a holiday",
         "value": {"SKIP": "Skip the session", "SHIFT_NEXT_BUSINESS_DAY": "Move to the next business day"}.get(
             holiday, NEEDS_REVIEW) + (
                 (f" (calendar {rule.get('holiday_calendar_ref')}"
                  + ("" if rule.get("holiday_calendar_ref") in names.holiday_calendars
                     else ", supplied per site — not attached to this version") + ")")
                 if rule.get("holiday_calendar_ref") and holiday != "NONE_STATED" else ""),
         "status": "NEEDS_REVIEW" if holiday == "NONE_STATED" else "STATED"},
        # The engine replaces a missed session whenever the count is of DELIVERED
        # sessions, whatever the missed policy says (calendar_recurrence.py), so an
        # unstated policy is then determined by the count basis, not open.
        {"key": "missed_policy", "label": "When a session is missed",
         "value": ({"APPEND_REPLACEMENT": "Add a replacement session at the end",
                    "NO_REPLACEMENT": "No replacement session"}.get(missed)
                   or ("A replacement session is added — implied by counting delivered sessions"
                       if basis == "DELIVERED" else NEEDS_REVIEW)),
         "status": ("STATED" if missed != "NONE_STATED" else
                    "IMPLIED" if basis == "DELIVERED" else "NEEDS_REVIEW")},
    ]
    return {
        "type": "CALENDAR", "summary": summary, "rule": rule, "policies": policies,
        "needs_review": [item["label"] for item in policies if item["status"] == "NEEDS_REVIEW"],
    }


def describe_recurrence(event: dict[str, Any], names: _Names) -> dict[str, Any] | None:
    if event.get("calendar_recurrence"):
        return describe_calendar_recurrence(event["calendar_recurrence"], names)
    rule = event.get("recurrence")
    plan = event.get("recurrence_plan")
    if rule:
        start = names.reference(rule.get("start_reference"))
        if rule.get("start_offset"):
            start = _relative(rule.get("start_offset"), start).lower()
        summary = (f"Every {_amount(rule.get('interval'))} from {start}, "
                   f"{describe_termination(rule.get('termination'), names)}")
        if rule.get("include_start") is False:
            summary += "; the start itself is not a visit"
        basis, basis_status = rule.get("basis"), rule.get("basis_status")
        basis_text = {
            "FIRST_RESOLVED_OCCURRENCE": "each repeat counted from the first occurrence",
            "PREVIOUS_PLANNED_OCCURRENCE": "each repeat counted from the previous planned occurrence",
            "PREVIOUS_ACTUAL_OCCURRENCE": "each repeat counted from the previous actual "
                                          + ("treatment" if rule.get("actual_source") == "ACTUAL_TREATMENT"
                                             else "visit"),
            "FIXED_ANCHOR_SERIES": "fixed series counted from its anchor",
        }
        if basis_status == "UNCONFIRMED":
            summary += "; what each repeat counts from: Needs review"
        elif basis:
            summary += f"; {basis_text.get(basis, basis)}"
        return {"type": rule.get("type", "INTERVAL"), "summary": summary, "rule": rule,
                "basis": basis, "basis_status": basis_status or ("LEGACY_UNSTATED" if not basis else "STATED")}
    if plan:
        start = names.reference(plan.get("start_reference"))
        parts = []
        for segment in plan.get("segments") or []:
            text = f"every {_amount(segment.get('interval'))}"
            if segment.get("count"):
                text += f" for {segment['count']} visits"
            elif segment.get("end_offset"):
                edge = segment.get("end_inclusive")
                boundary = "" if edge is None else (" (inclusive)" if edge else " (exclusive)")
                text += f" until {_amount(segment['end_offset'])} after {start}{boundary}"
            parts.append(text)
        summary = f"From {start}: " + ", then ".join(parts)
        if plan.get("termination"):
            summary += f"; {describe_termination(plan.get('termination'), names)}"
        return {"type": "SEGMENTED", "summary": summary, "rule": plan}
    return None


def describe_condition(condition: dict[str, Any] | None) -> str:
    if not condition:
        return "an unstated condition"
    kind = condition.get("type")

    def operand(value: dict[str, Any] | None) -> str:
        if not value:
            return "?"
        return str(value.get("field") if value.get("source") == "FIELD" else value.get("value"))

    if kind == "COMPARISON":
        symbol = {"EQUALS": "=", "NOT_EQUALS": "≠", "GT": ">", "GTE": "≥", "LT": "<",
                  "LTE": "≤"}.get(str(condition.get("operator")), condition.get("operator"))
        return f"{operand(condition.get('left'))} {symbol} {operand(condition.get('right'))}"
    if kind == "MEMBERSHIP":
        word = "is one of" if condition.get("operator") == "IN" else "is not one of"
        return f"{operand(condition.get('value'))} {word} {', '.join(map(str, condition.get('values') or []))}"
    if kind == "EXISTS":
        return f"{condition.get('field')} is {'recorded' if condition.get('exists', True) else 'not recorded'}"
    if kind in {"AND", "OR"}:
        joiner = " and " if kind == "AND" else " or "
        return "(" + joiner.join(describe_condition(item) for item in condition.get("conditions") or []) + ")"
    if kind == "NOT":
        return f"not {describe_condition(condition.get('condition'))}"
    if kind == "UNSUPPORTED":
        return f"a condition MTB cannot execute yet ({condition.get('source_operator')})"
    return f"{kind or 'unknown'} condition (see details)"


def describe_applicability(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "All participants"
    parts = []
    for index, rule in enumerate(rules):
        dimension = str(rule.get("dimension") or "").replace("_", " ").title()
        operator = rule.get("operator", "IN")
        values = ", ".join(map(str, rule.get("values") or []))
        if operator == "IN":
            text = f"{dimension}: {values}"
        elif operator == "NOT_IN":
            text = f"{dimension}: not {values}"
        else:
            text = f"{rule.get('field') or dimension} {operator} {values}"
        if rule.get("condition"):
            text += f" when {describe_condition(rule['condition'])}"
        if index:
            parts.append(("or " if rules[index - 1].get("combinator") == "OR" else "and ") + text)
        else:
            parts.append(text)
    return "; ".join(parts)


def _window_text(window: dict[str, Any]) -> str:
    before, after = window.get("before") or {}, window.get("after") or {}
    unit = str(before.get("unit") or after.get("unit") or "").lower()
    b, a = before.get("value", 0), after.get("value", 0)
    if b == a:
        return f"±{a} {unit}{'' if a == 1 else 's'}" + (" (exact day stated)" if a == 0 else "")
    return f"−{b} / +{a} {unit}s"


# --- the view ------------------------------------------------------------------------


def _issue_ref(issue: ValidationIssue) -> str:
    """Stable identity for an issue across fresh validator runs."""
    if issue.details.get("origin") == "EXTRACTION":
        return str(issue.id)
    key = _canonical([issue.issue_code, issue.entity_type, str(issue.entity_id),
                      issue.message, issue.details])
    return "v-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def _issue_heading(code: str) -> str:
    return code.replace("_", " ").capitalize()


def _collect_references(value: Any, kind: str, key: str) -> set[str]:
    """Every reference of one kind anywhere in a rule's JSON (operator-agnostic)."""
    found: set[str] = set()
    if isinstance(value, dict):
        if value.get("kind") == kind and value.get(key):
            found.add(str(value[key]))
        for item in value.values():
            found |= _collect_references(item, kind, key)
    elif isinstance(value, list):
        for item in value:
            found |= _collect_references(item, kind, key)
    return found


def _citations(issue: ValidationIssue) -> list[dict[str, Any]]:
    citations = issue.details.get("citations")
    if not isinstance(citations, list):
        return []
    result = []
    for item in citations:
        if isinstance(item, dict):
            result.append({
                "page_number": item.get("page") or item.get("page_number"),
                "quote": item.get("quote") or item.get("text") or item.get("source_text"),
            })
    return result


def _ledger_coverage(accountability: dict[str, Any]) -> dict[str, Any]:
    """Coverage gate from the extraction accountability ledger (RV-01, IN-03, IR-05).

    INCOMPLETE coverage or any uncompiled statement blocks approval. NOT_ASSESSED
    (facts that did not come through the document reader) makes no coverage
    claim: it is shown, but only uncompiled statements block.
    """
    summary = accountability.get("summary") or {}
    coverage = accountability.get("coverage") or {}
    status = str(summary.get("coverage_status") or coverage.get("status") or "UNKNOWN").upper()
    statements = accountability.get("statements") or []
    uncompiled = summary.get("uncompiled_statements")
    if not isinstance(uncompiled, int):
        uncompiled = sum(1 for item in statements if item.get("disposition") in UNCOMPILED_DISPOSITIONS)
    gaps = coverage.get("gaps") or []
    reasons = []
    if status not in {"COMPLETE", "NOT_ASSESSED"}:
        reasons.append(f"Source coverage is {status.lower()} ({len(gaps)} gap(s))")
    if uncompiled:
        reasons.append(f"{uncompiled} protocol statement(s) are not compiled into executable rules")
    blocking = bool(reasons)
    if blocking:
        message = "; ".join(reasons) + ", so approval is blocked."
    elif status == "NOT_ASSESSED":
        message = ("Source coverage was not assessed for this draft (its facts did not come "
                   "through the document reader); every recorded statement is compiled.")
    else:
        message = "Source coverage is complete and every schedule statement is compiled."
    return {"status": status, "blocking": blocking, "ledger": coverage, "message": message,
            "uncompiled_statements": uncompiled, "gaps": len(gaps), "from_ledger": True}


def _coverage_hooks(schedule: UniversalSchedule, version: ReviewVersion | None,
                    run: ReviewRun | None,
                    accountability: dict[str, Any] | None = None) -> dict[str, Any]:
    """Source coverage / requirement disposition.

    The extraction accountability ledger is authoritative when present. The
    older locations below are fallbacks; with none of them, coverage is
    honestly reported NOT_RECORDED (a visible, non-blocking document issue),
    never as complete.
    """
    if isinstance(accountability, dict) and accountability:
        return _ledger_coverage(accountability)
    candidates: list[Any] = []
    if run is not None:
        trace = run.trace or {}
        candidates += [trace.get("source_coverage"), trace.get("coverage")]
    if version is not None:
        metadata = version.metadata_json or {}
        candidates += [metadata.get("source_coverage"), metadata.get("coverage")]
    for extension in schedule.schedule_metadata.extensions:
        if str(extension.get("type", "")).upper() in {"SOURCE_COVERAGE", "COVERAGE_LEDGER"}:
            candidates.append(extension)
    for attribute in ("source_coverage", "coverage", "coverage_ledger"):
        value = getattr(schedule, attribute, None)
        if value is not None:
            candidates.append(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
    ledger = next((item for item in candidates if isinstance(item, dict) and item), None)
    if ledger is None:
        return {"status": "NOT_RECORDED", "blocking": False, "ledger": None,
                "message": "Source coverage was not recorded for this draft, so MTB cannot "
                           "show which protocol pages and statements were read."}
    status = str(ledger.get("status") or ledger.get("coverage_status") or "UNKNOWN").upper()
    unresolved = ledger.get("undispositioned") or ledger.get("unreviewed") or ledger.get("missing") or []
    complete = status in {"COMPLETE", "REVIEWED", "ACCEPTED"} and not unresolved
    return {
        "status": status, "blocking": not complete, "ledger": ledger,
        "message": ("Source coverage is complete." if complete else
                    f"Source coverage is {status.lower()}: required material has not been "
                    "read or dispositioned, so approval is blocked."),
    }


def _extraction_status(schedule: UniversalSchedule, run: ReviewRun | None,
                       issues: list[ValidationIssue]) -> dict[str, Any]:
    failures = [item for item in issues if item.issue_code in COVERAGE_FAILURE_CODES]
    if run is not None:
        status = str(run.status or "UNKNOWN").upper()
        partial = bool(failures) or status in {"PARTIAL", "INCOMPLETE"}
        return {
            "status": "PARTIAL" if partial and status not in {"FAILED"} else status,
            "run_id": str(run.id), "provider": run.provider, "model": run.model_name,
            "reason": (run.error_details or {}).get("message") if run.error_details else (
                failures[0].message if failures else None),
            "retry_available": partial or status == "FAILED",
        }
    if failures:
        return {"status": "PARTIAL", "run_id": None, "reason": failures[0].message,
                "retry_available": True}
    if not schedule.events:
        return {"status": "EMPTY", "run_id": None, "reason": "No visits were extracted.",
                "retry_available": True}
    if not schedule.evidence:
        return {"status": "NOT_RECORDED", "run_id": None, "reason": None, "retry_available": False}
    return {"status": "EXTRACTED", "run_id": None, "reason": None, "retry_available": False}


def build_review_view(
    schedule: UniversalSchedule,
    *,
    version: ReviewVersion | None = None,
    run: ReviewRun | None = None,
    issues: list[ValidationIssue] | None = None,
    accountability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ScheduleReviewView contract for one draft (design doc)."""
    ledger_statements = [item for item in (accountability or {}).get("statements") or []
                         if isinstance(item, dict)]
    statement_by_id = {str(item.get("statement_id")): item for item in ledger_statements}
    if issues is None:
        issues = ScheduleValidator().validate(schedule)
    issues = [item for item in issues if item.status.value == "OPEN"]
    names = _Names(schedule)
    events = list(schedule.events)
    event_by_id = {item.id: item for item in events}
    activity_owner = {activity.id: event.id for event in events for activity in event.activities}
    qualifier_owner = {
        qualifier.id: event.id for event in events
        for qualifier in [*event.qualifiers, *(q for a in event.activities for q in a.qualifiers)]
    }
    event_json = {item.id: item.model_dump(mode="json", by_alias=True) for item in events}
    anchor_refs = {
        item.id: _collect_references(
            {key: value for key, value in event_json[item.id].items() if key != "metadata"},
            "ANCHOR", "code")
        for item in events
    }
    event_refs = {
        item.id: _collect_references(event_json[item.id], "EVENT", "event_code") for item in events
    }
    event_by_code = {item.code: item for item in events}
    statement_events: dict[str, set[UUID]] = {}
    for statement in ledger_statements:
        linked: set[UUID] = set()
        for ref in statement.get("rule_refs") or []:
            entity = str(ref.get("entity_id") or "")
            for event in events:
                if entity and (entity == str(event.id)
                               or entity in {str(a.id) for a in event.activities}):
                    linked.add(event.id)
            for code in (ref.get("event_code"),
                         ref.get("code") if ref.get("entity_type") == "EVENT" else None):
                if code and code in event_by_code:
                    linked.add(event_by_code[code].id)
        statement_events[str(statement.get("statement_id"))] = linked
    evidence_by_id = {item.id: item for item in schedule.evidence}
    claims_by_entity: dict[UUID, list[Any]] = {}
    for claim in schedule.claim_evidence:
        claims_by_entity.setdefault(claim.claim_entity_id, []).append(claim)

    # --- attribute every issue: row, shared question, or document ----------------
    row_issues: dict[UUID, list[dict[str, Any]]] = {item.id: [] for item in events}
    questions: dict[str, dict[str, Any]] = {}
    document_issues: list[dict[str, Any]] = []
    known_anchor_codes = set(names.anchors)

    def issue_view(issue: ValidationIssue) -> dict[str, Any]:
        return {
            "id": _issue_ref(issue), "code": issue.issue_code,
            "heading": _issue_heading(issue.issue_code), "message": issue.message,
            "blocking": issue.blocking, "severity": issue.severity.value,
            "origin": "EXTRACTION" if issue.details.get("origin") == "EXTRACTION" else "VALIDATION",
            "entity_type": issue.entity_type,
            "entity_id": str(issue.entity_id) if issue.entity_id else None,
            "citations": _citations(issue),
            "statement_ids": _issue_statement_ids(issue),
            "acknowledgeable": not (issue.issue_code in NON_ACKNOWLEDGEABLE
                                    or issue.details.get("acknowledgeable") is False),
            "conflicting_constraints": [str(item) for item in issue.details.get("conflicting_constraints") or []],
        }

    def add_question(key: str, *, kind: str, title: str, issue: ValidationIssue,
                     affected: Iterable[UUID], **extra: Any) -> None:
        # Question ids travel in URL paths; constraint paths contain "/".
        key = re.sub(r"[^A-Za-z0-9:_.\-]", "~", key)
        question = questions.get(key)
        if question is None:
            question = questions[key] = {
                "id": key, "kind": kind, "title": title, "issues": [],
                "blocking": False, "affected_event_ids": set(), **extra,
            }
        view = issue_view(issue)
        if view["id"] not in {item["id"] for item in question["issues"]}:
            question["issues"].append(view)
        question["blocking"] = question["blocking"] or issue.blocking
        question["affected_event_ids"].update(affected)

    def events_using_anchor(code: str) -> list[UUID]:
        return [item.id for item in events if code in anchor_refs[item.id]]

    for issue in issues:
        code = issue.issue_code
        entity_id = issue.entity_id
        owner = None
        if entity_id is not None:
            if entity_id in event_by_id:
                owner = entity_id
            elif entity_id in activity_owner:
                owner = activity_owner[entity_id]
            elif entity_id in qualifier_owner:
                owner = qualifier_owner[entity_id]
        if code in NON_ACKNOWLEDGEABLE:
            # RL-12: a capability gap blocks the rule and everything dated from
            # it; it is never acknowledged away, so no resolution action exists.
            if code == "UNSUPPORTED_OPERATOR":
                keys = [f"unsupported:{issue.details.get('owner_type')}:{issue.details.get('owner_code')}:"
                        f"{issue.details.get('path')}"]
                codes = {str(item).split("/", 1)[0] for item in issue.details.get("affected_dependents") or []}
                codes.add(str(issue.details.get("owner_code") or "").split("/", 1)[0])
            else:
                keys = [f"unsupported:{item}" for item in issue.details.get("unsupported_sources") or []] or [
                    f"unsupported-dependent:{issue.details.get('event_code')}"]
                codes = {str(issue.details.get("event_code") or "")}
            affected = {event_by_code[item].id for item in codes if item in event_by_code}
            if owner:
                affected.add(owner)
            for key in keys:
                label = issue.details.get("source_operator") or issue.details.get("operator")
                add_question(
                    key, kind="UNSUPPORTED_OPERATOR",
                    title=(f"“{label}” cannot be executed by the scheduling engine yet"
                           if label else "A rule these visits depend on cannot be executed yet"),
                    issue=issue, affected=affected, actions=[], acknowledgeable=False,
                    explanation=("Acknowledging does not make it executable. Restate the rule with "
                                 "supported operators, or wait for engine support; every visit "
                                 "listed stays blocked until then."),
                )
            continue
        question_id = issue.details.get("question_id")
        if issue.details.get("shared_question") and question_id:
            anchor_code = str(issue.details.get("anchor_code") or "")
            affected = set(events_using_anchor(anchor_code)) if anchor_code else set()
            for statement_id in _issue_statement_ids(issue):
                affected |= statement_events.get(statement_id, set())
            if owner:
                affected.add(owner)
            candidates = [str(item) for item in issue.details.get("candidates") or []]
            known = {item.code for item in schedule.anchors}
            options = [{"code": item.code, "display_name": item.display_name}
                       for item in schedule.anchors if not candidates or item.code in candidates
                       or item.display_name in candidates] or [
                {"code": item.code, "display_name": item.display_name} for item in schedule.anchors]
            add_question(
                str(question_id), kind="ANCHOR_EQUIVALENCE" if code == "ANCHOR_NOT_CANONICAL" else "SHARED",
                title=(f"Is “{issue.details.get('anchor_name') or anchor_code}” the same date as one of the "
                       "protocol's reference dates, or a separate date?"
                       if code == "ANCHOR_NOT_CANONICAL" else issue.message),
                issue=issue, affected=affected,
                anchor={"code": anchor_code, "display_name": issue.details.get("anchor_name")},
                options=options,
                actions=(["REMAP_ANCHOR", "CONFIRM_ANCHOR" if anchor_code in known else "DEFINE_ANCHOR"]
                         if anchor_code else ["EDIT_RULE"]),
            )
            continue
        if code in {"AMBIGUOUS_BASELINE", "UNRESOLVED_SCHEDULE_ORIGIN"}:
            ambiguous = [item for item in schedule.anchors if item.status == "AMBIGUOUS"]
            affected = {event_id for anchor in ambiguous for event_id in events_using_anchor(anchor.code)}
            affected |= {item.id for item in events if item.timing.type == "PROTOCOL_DAY"}
            add_question(
                "origin", kind="SCHEDULE_ORIGIN",
                title="Which event is Day 1 / Day 0 of this schedule measured from?",
                issue=issue, affected=affected,
                options=[{"code": item.code, "display_name": item.display_name,
                          "status": item.status} for item in schedule.anchors],
                actions=["CHOOSE_ORIGIN"],
            )
            continue
        if issue.entity_type == "ANCHOR" and entity_id is not None:
            anchor = next((item for item in schedule.anchors if item.id == entity_id), None)
            if anchor is not None:
                add_question(
                    f"anchor:{anchor.code}", kind="ANCHOR",
                    title=f"Confirm what “{anchor.display_name}” means before visits count from it.",
                    issue=issue, affected=events_using_anchor(anchor.code),
                    anchor={"code": anchor.code, "display_name": anchor.display_name,
                            "status": anchor.status, "anchor_type": anchor.anchor_type},
                    actions=["CONFIRM_ANCHOR"],
                )
                continue
        if code == "UNKNOWN_ANCHOR":
            match = re.search(r"Unknown anchor '([^']+)'", issue.message)
            codes = {match.group(1)} if match else (
                (anchor_refs.get(owner, set()) - known_anchor_codes) if owner else set())
            for missing in sorted(codes) or ["?"]:
                add_question(
                    f"missing-anchor:{missing}", kind="MISSING_ANCHOR",
                    title=f"Visits count from “{missing}”, which the draft never defines.",
                    issue=issue, affected=events_using_anchor(missing) or ([owner] if owner else []),
                    anchor={"code": missing},
                    options=[{"code": item.code, "display_name": item.display_name}
                             for item in schedule.anchors],
                    actions=["DEFINE_ANCHOR", "REMAP_ANCHOR"],
                )
            continue
        if issue.entity_type == "REPEAT_BLOCK" and entity_id is not None:
            block = next((item for item in schedule.repeat_blocks if item.id == entity_id), None)
            if block is not None:
                add_question(
                    f"repeat-block:{block.code}", kind="REPEAT_BLOCK",
                    title=f"Resolve how the “{block.display_name}” series is dated.",
                    issue=issue,
                    affected=[item.id for item in events if item.code in set(block.event_codes)],
                    actions=["EDIT_RULE"],
                )
                continue
        if issue.entity_type == "CONDITION" and entity_id is not None:
            definition = next((item for item in schedule.conditional_definitions if item.id == entity_id), None)
            if definition is not None:
                targets = {action.target_code for action in definition.actions}
                if definition.trigger_event_code:
                    targets.add(definition.trigger_event_code)
                add_question(
                    f"condition:{definition.code}", kind="CONDITIONAL_RULE",
                    title=f"Resolve the conditional rule “{definition.display_name}”.",
                    issue=issue, affected=[item.id for item in events if item.code in targets],
                    actions=["EDIT_RULE"],
                )
                continue
        if issue.entity_type == "CONFINEMENT_EPISODE" and entity_id is not None:
            episode = next((item for item in schedule.confinement_episodes if item.id == entity_id), None)
            if episode is not None:
                codes = {episode.admission_event_code, episode.discharge_event_code, *episode.dose_event_codes}
                add_question(
                    f"confinement:{episode.code}", kind="CONFINEMENT",
                    title=f"Resolve the in-patient stay “{episode.display_name}”.",
                    issue=issue, affected=[item.id for item in events if item.code in codes],
                    actions=["EDIT_RULE"],
                )
                continue
        if issue.entity_type == "SCHEDULE" and isinstance(issue.details.get("event_ids"), list):
            affected_ids = {UUID(str(item)) for item in issue.details["event_ids"]}
            add_question(
                f"{code.lower()}:{_issue_ref(issue)}", kind="DEPENDENCY",
                title="These visits depend on each other in a loop; one must be restated.",
                issue=issue, affected=affected_ids & set(event_by_id), actions=["EDIT_RULE"],
            )
            continue
        if owner is not None:
            row_issues[owner].append(issue_view(issue))
            continue
        document_issues.append(issue_view(issue))

    # One anchor, one question: an unknown-anchor finding about a code that an
    # anchor-equivalence question already asks about is folded into it.
    for key in [key for key in questions if key.startswith("missing-anchor:")]:
        code = key.split(":", 1)[1]
        target = next((q for q in questions.values() if q["kind"] == "ANCHOR_EQUIVALENCE"
                       and (q.get("anchor") or {}).get("code") == code), None)
        if target is None:
            continue
        folded = questions.pop(key)
        seen = {item["id"] for item in target["issues"]}
        target["issues"] += [item for item in folded["issues"] if item["id"] not in seen]
        target["blocking"] = target["blocking"] or folded["blocking"]
        target["affected_event_ids"] |= folded["affected_event_ids"]

    coverage = _coverage_hooks(schedule, version, run, accountability)
    if coverage["status"] == "NOT_RECORDED" or coverage["blocking"]:
        document_issues.append({
            "id": "source-coverage",
            "code": ("UNCOMPILED_STATEMENTS"
                     if coverage["blocking"] and coverage["status"] in {"COMPLETE", "NOT_ASSESSED"}
                     else "SOURCE_COVERAGE_" + coverage["status"]),
            "heading": ("Protocol statements not compiled"
                        if coverage["blocking"] and coverage["status"] in {"COMPLETE", "NOT_ASSESSED"}
                        else "Source coverage"),
            "message": coverage["message"],
            "blocking": coverage["blocking"], "severity": "ERROR" if coverage["blocking"] else "WARNING",
            "origin": "COVERAGE", "entity_type": None, "entity_id": None, "citations": [],
        })

    event_questions: dict[UUID, list[str]] = {item.id: [] for item in events}
    for key, question in questions.items():
        for event_id in question["affected_event_ids"]:
            if event_id in event_questions:
                event_questions[event_id].append(key)

    # --- rows ----------------------------------------------------------------------
    conditional_targets: dict[str, list[Any]] = {}
    for definition in schedule.conditional_definitions:
        for action in definition.actions:
            conditional_targets.setdefault(action.target_code, []).append(definition)
    blocks_for_event: dict[str, list[Any]] = {}
    for block in schedule.repeat_blocks:
        for code in block.event_codes:
            blocks_for_event.setdefault(code, []).append(block)

    rows = []
    constraint_linked: set[str] = set()
    for event in events:
        data = event_json[event.id]
        timing = data.get("timing") or {}
        timing_text, timing_supported = describe_timing(timing, names)
        recurrence = describe_recurrence(data, names)
        activation_conditions = [describe_condition(item) for item in data.get("conditions") or []]
        definitions = conditional_targets.get(event.code, [])
        conditional = bool(activation_conditions or definitions or event.activation == "ON_DEMAND")
        compact = timing_text
        if recurrence:
            compact = f"{timing_text}; then {recurrence['summary'][0].lower()}{recurrence['summary'][1:]}"
        if event.activation == "ON_DEMAND":
            compact = f"When needed (on demand) — {timing_text[0].lower()}{timing_text[1:]}"
        elif definitions:
            compact = f"When {definitions[0].display_name}: {compact[0].lower()}{compact[1:]}"
        elif activation_conditions:
            compact = f"Only if {activation_conditions[0]}: {compact[0].lower()}{compact[1:]}"

        direct = row_issues[event.id]
        window_issue = any(
            item["code"] in WINDOW_ISSUE_CODES
            or (item["code"] == "CONFLICTING_SOURCE" and "window" in item["message"].lower())
            for item in direct
        )
        window_qualifier = any(
            q.category.value == "WINDOW" and not q.resolved for q in event.qualifiers)
        stated_note = (event.metadata or {}).get("window_as_stated")
        if window_issue or window_qualifier or stated_note:
            window_status, window_summary = "NEEDS_REVIEW", "Needs review"
        elif data.get("window"):
            window_status, window_summary = "STATED", _window_text(data["window"])
        elif timing.get("type") == "NOMINAL_WITH_WINDOW" and timing.get("window"):
            window_status, window_summary = "STATED", _window_text(timing["window"])
        elif timing.get("type") in SPAN_TIMING_TYPES or (
                timing.get("type") == "TRIGGERED"
                and (timing.get("timing_after_trigger") or {}).get("type") in {"WITHIN", "NO_LATER_THAN"}):
            window_status, window_summary = "NOT_APPLICABLE", "Not applicable — timing is a range"
        elif event.activation == "ON_DEMAND":
            window_status, window_summary = "NOT_APPLICABLE", "Not applicable — on demand"
        else:
            window_status, window_summary = "NOT_STATED", "Not stated"

        constraints = _row_constraints(event, data, names, timing_text, timing_supported,
                                       recurrence, window_status, window_summary, stated_note,
                                       blocks_for_event.get(event.code, []), definitions)
        trees = []
        if timing.get("type") == "CONSTRAINED":
            trees.append(("timing", _constraint_tree(timing.get("constraint"), f"{event.code}/timing/constraint", names)))
        if data.get("constraints"):
            trees.append(("visit", _constraint_tree(data["constraints"], f"{event.code}/constraints", names)))
        for activity in data.get("activities") or []:
            if activity.get("constraints"):
                trees.append((f"assessment {activity.get('display_name')}", _constraint_tree(
                    activity["constraints"], f"{event.code}/{activity.get('code') or activity.get('id')}/constraints", names)))
        conflicting = {item for issue in direct for item in issue["conflicting_constraints"]}
        # Compiler v5: which statement each constraint came from.
        source_links: dict[str, list[dict[str, Any]]] = {}
        source_fact_ids: list[str] = []
        for owner_metadata in [data.get("metadata") or {}, *((a.get("metadata") or {}) for a in data.get("activities") or [])]:
            for link in owner_metadata.get("constraint_sources") or []:
                if not isinstance(link, dict) or not link.get("fact_id"):
                    continue
                source_fact_ids.append(str(link["fact_id"]))
                for constraint_id in link.get("constraint_ids") or []:
                    source_links.setdefault(str(constraint_id), []).append(link)

        def node_sources(node: dict[str, Any]) -> list[dict[str, Any]]:
            found = []
            for link in source_links.get(str(node.get("id")), []) + (
                    source_links.get(str(node.get("path")), []) if node.get("path") != node.get("id") else []):
                statement = statement_by_id.get(str(link["fact_id"]))
                found.append({"relation": link.get("relation"),
                              **(_statement_view(statement) if statement else
                                 {"statement_id": str(link["fact_id"]), "missing": True})})
            return found

        for scope, tree in trees:
            for node in _walk_tree(tree):
                constraints.append({
                    "id": f"{event.id}:{node['path']}", "kind": "CONSTRAINT_" + str(node.get("type")),
                    "summary": (f"{scope}: " if scope != "visit" and scope != "timing" else "")
                    + str(node.get("summary")),
                    "rule": {key: value for key, value in node.items() if key != "items"},
                    "in_compact_summary": scope in {"timing", "visit"} and node is tree,
                    "path": node["path"], "supported": node.get("executable") is not False,
                    "constraint_id": node.get("id"),
                    "source_statements": node_sources(node),
                    "conflicting": node.get("id") in conflicting or node.get("path") in conflicting,
                })
        if data.get("constraints"):
            # Every bound belongs in the one-line Timing, whichever field carries it.
            extra = next((tree for scope, tree in trees if scope == "visit"), None)
            if extra and str(extra["summary"]).lower() not in compact.lower():
                joiner = " and " if timing.get("type") == "CONSTRAINED" else "; also "
                compact = f"{compact}{joiner}{extra['summary']}"
        from app.domain.schedule.manual_handling import approved_manual
        manual_only = approved_manual(event)
        unsupported = not manual_only and (any(_tree_unsupported(tree) for _, tree in trees)
                       or _condition_unsupported(data.get("conditions"))
                       or _condition_unsupported([a.get("conditions") for a in data.get("activities") or []])
                       or _condition_unsupported(data.get("applicability"))
                       or any(questions[key]["kind"] == "UNSUPPORTED_OPERATOR" for key in event_questions[event.id])
                       or any(item["code"] in NON_ACKNOWLEDGEABLE for item in direct))
        linked_facts = set(source_fact_ids)
        constraint_linked |= linked_facts
        row_statements = [
            _statement_view(item) for item in ledger_statements
            if event.id in statement_events.get(str(item.get("statement_id")), set())
            or str(item.get("statement_id")) in linked_facts
        ]
        # Pre-v5 drafts only: extra restrictions stored as metadata, not constraints.
        restrictions = [item for item in (event.metadata or {}).get("additional_restrictions") or []
                        if isinstance(item, dict)]
        pending_reasons = []
        if unsupported:
            pending_reasons.append("Uses a rule the scheduling engine cannot execute yet")
        if recurrence and recurrence.get("needs_review"):
            pending_reasons.append("Repetition policy not stated: " + ", ".join(recurrence["needs_review"]).lower())
        if any(item["uncompiled"] for item in row_statements):
            pending_reasons.append("A protocol statement about this visit is not compiled")
        if any(str(item.get("status", "")).upper() != "COMPILED" for item in restrictions):
            pending_reasons.append("An additional restriction is not compiled")
        if any(item["blocking"] for item in direct):
            pending_reasons.append("Has unresolved blocking findings")
        if any(questions[key]["blocking"] for key in event_questions[event.id]):
            pending_reasons.append("Waiting on a shared question")
        timing_type = timing.get("type")
        if not manual_only and (timing_type == "UNRESOLVED" or not timing_supported or (
                timing_type in TIMING_REVIEW_TYPES and timing.get("requires_review", True)
                and event.activation != "ON_DEMAND")):
            pending_reasons.append("Timing needs review")
        if window_status == "NEEDS_REVIEW":
            pending_reasons.append("Window needs review")
        if event.requires_review or event.interpretation_status.value in UNRESOLVED_INTERPRETATION:
            pending_reasons.append("Marked for review during interpretation")
        if any(a.requires_review or a.interpretation_status.value in UNRESOLVED_INTERPRETATION
               for a in event.activities):
            pending_reasons.append("An assessment is marked for review")
        if any(not q.resolved for q in [*event.qualifiers, *(q for a in event.activities for q in a.qualifiers)]):
            pending_reasons.append("A footnote has not been resolved")

        evidence_ids = list(dict.fromkeys([
            *event.evidence_refs,
            *(ref for activity in event.activities for ref in activity.evidence_refs),
            *(ref for q in event.qualifiers for ref in q.evidence_refs),
            *(claim.evidence_id for claim in claims_by_entity.get(event.id, [])),
            *(claim.evidence_id for activity in event.activities
              for claim in claims_by_entity.get(activity.id, [])),
        ]))
        evidence = []
        for ref in evidence_ids:
            item = evidence_by_id.get(ref)
            if item is None:
                evidence.append({"id": str(ref), "missing": True})
                continue
            evidence.append({
                "id": str(item.id), "evidence_type": item.evidence_type,
                "is_reviewer_decision": item.evidence_type == REVIEWER_EVIDENCE_TYPE,
                "page_number": item.page_number, "section_title": item.section_title,
                "table_title": item.table_title, "quote": item.source_text,
                "claims": sorted({claim.claim_type for claim in schedule.claim_evidence
                                  if claim.evidence_id == item.id
                                  and claim.claim_entity_id in {event.id, *activity_owner_ids(event)}}),
            })

        metadata = event.metadata or {}
        if manual_only:
            compact = "Manual scheduling required; no automatic protocol target"
        rows.append({
            "id": str(event.id),
            "code": event.code,
            "rule_refs": (
                [{"entity_type": "EVENT", "id": str(event.id), "code": event.code}]
                + [{"entity_type": "ACTIVITY", "id": str(a.id), "code": a.code} for a in event.activities]
                + [{"entity_type": "REPEAT_BLOCK", "id": str(b.id), "code": b.code}
                   for b in blocks_for_event.get(event.code, [])]
                + [{"entity_type": "CONDITION", "id": str(d.id), "code": d.code} for d in definitions]
            ),
            "visit_name": event.display_name,
            "protocol_label": event.protocol_label,
            "event_type": event.event_type,
            "timing_summary": compact,
            "timing_supported": timing_supported,
            "window_summary": window_summary,
            "window_status": window_status,
            "applicability_summary": describe_applicability(data.get("applicability") or []),
            "review_status": "PENDING" if pending_reasons else "READY",
            "pending_reasons": pending_reasons,
            "issue_refs": [item["id"] for item in direct],
            "question_refs": sorted(event_questions[event.id]),
            "issues": direct,
            "group": ("CONDITIONAL" if conditional else "REPEATING" if recurrence else "SCHEDULED"),
            "origin": metadata.get("origin") or "EXTRACTED",
            "unsupported": unsupported,
            "details": {
                "rule_handling": metadata.get("rule_handling"),
                "constraint_trees": [{"scope": scope, "tree": tree} for scope, tree in trees if tree],
                "processes": [process_summary(item, names) for item in names.processes.values()
                              if event.code in (item.event_codes or [])],
                "activities": [_activity_view(activity, names) for activity in event.activities],
                "anchors": _anchor_views(data, names),
                "recurrence": recurrence,
                "constraints": constraints,
                "evidence": evidence,
                "statements": row_statements,
                "additional_restrictions": [_restriction_view(item) for item in restrictions],
                "visit_mode": event.visit_mode,
                "allowed_visit_modes": list(event.allowed_visit_modes),
                "activation": event.activation,
                "manual": ({"origin": metadata.get("origin"), "rationale": metadata.get("rationale"),
                            "review_state": metadata.get("review_state"),
                            "created_by": metadata.get("created_by"),
                            "created_at": metadata.get("created_at")}
                           if metadata.get("origin") == "MANUAL" else None),
            },
        })

    shared = []
    event_name = {str(item.id): item.display_name for item in events}
    for question in questions.values():
        affected = sorted(str(item) for item in question.pop("affected_event_ids"))
        question["affected_row_ids"] = affected
        question["affected_rows"] = [{"id": item, "visit_name": event_name.get(item, item)} for item in affected]
        question["issue_refs"] = [item["id"] for item in question["issues"]]
        shared.append(question)
    shared.sort(key=lambda item: (not item["blocking"], item["id"]))

    blocking_refs = {item["id"] for row in rows for item in row["issues"] if item["blocking"]}
    blocking_refs |= {item["id"] for q in shared for item in q["issues"] if item["blocking"]}
    blocking_refs |= {item["id"] for item in document_issues if item["blocking"]}
    warning_refs = {item["id"] for row in rows for item in row["issues"] if not item["blocking"]}
    warning_refs |= {item["id"] for q in shared for item in q["issues"] if not item["blocking"]}
    warning_refs |= {item["id"] for item in document_issues if not item["blocking"]}

    status = (version.status if version is not None else schedule.schedule_metadata.status.value)
    pending_rows = sum(1 for row in rows if row["review_status"] == "PENDING")
    reasons: list[str] = []
    if status in IMMUTABLE_STATUSES:
        reasons.append(f"This version is {status.lower().replace('_', ' ')} and can no longer be approved.")
    if not rows:
        reasons.append("The draft has no visits.")
    if blocking_refs:
        reasons.append(f"{len(blocking_refs)} blocking finding(s) must be resolved.")
    if pending_rows:
        reasons.append(f"{pending_rows} visit row(s) still need review.")
    if coverage["blocking"]:
        reasons.append(coverage["message"])

    extraction = _extraction_status(schedule, run, issues)
    linked_ids = {key for key, value in statement_events.items() if value} | constraint_linked
    unlinked = [_statement_view(item) for item in ledger_statements
                if str(item.get("statement_id")) not in linked_ids]
    source_coverage = _source_coverage_view(accountability, coverage, unlinked)
    return {
        "view_version": REVIEW_VIEW_VERSION,
        "draft_version_id": str(schedule.schedule_version_id),
        "schedule_name": schedule.schedule_metadata.name,
        "version_number": schedule.schedule_metadata.version_number,
        "status": status,
        "revision": review_revision(schedule),
        "semantic_fingerprint": semantic_fingerprint(schedule),
        "extraction_status": extraction,
        "source_coverage_status": {k: v for k, v in coverage.items() if k != "ledger"},
        "source_coverage": source_coverage,
        "statement_summary": source_coverage["statements"] if source_coverage else None,
        "summary": {
            "displayed_rows": len(rows),
            "pending_rows": pending_rows,
            "ready_rows": len(rows) - pending_rows,
            "blocking_issues": len(blocking_refs),
            "warnings": len(warning_refs),
            "shared_questions": len(shared),
            "open_shared_questions": sum(1 for item in shared if item["blocking"]),
            "document_level_issues": len(document_issues),
        },
        "groups": [
            {"id": group, "label": label,
             "row_ids": [row["id"] for row in rows if row["group"] == group]}
            for group, label in (("SCHEDULED", "Scheduled visits"), ("REPEATING", "Repeating visits"),
                                 ("CONDITIONAL", "Visits when a condition is met"))
            if any(row["group"] == group for row in rows)
        ],
        "rows": rows,
        "shared_review_questions": shared,
        "document_level_issues": document_issues,
        "footnote_findings": _footnote_findings(issues),
        "can_approve": not reasons,
        "approval_blockers": reasons,
        "processes": [process_summary(item, names) for item in names.processes.values()],
        "tracks": [
            {"code": item.code, "track_type": item.track_type, "display_name": item.display_name,
             "anchor_code": item.anchor_code,
             "rebase_policy": item.rebase_policy.value if item.rebase_policy else None,
             "process_code": item.process_code, "stoppable": item.stoppable,
             "review_state": item.review_state,
             "events": [event.code for event in schedule.events if event.track_id == item.id]}
            for item in getattr(schedule, "tracks", None) or []
        ],
        "track_links": [
            {"source_track_code": item.source_track_code, "target_track_code": item.target_track_code,
             "relation": item.relation, "review_state": item.review_state}
            for item in getattr(schedule, "track_links", None) or []
        ],
        "track_membership": {
            event.code: {"scope": event.track_scope,
                         "track_code": next((track.code for track in schedule.tracks
                                             if track.id == event.track_id), None)}
            for event in schedule.events
        } if getattr(schedule, "tracks", None) or any(
            event.track_scope for event in schedule.events) else {},
        "holiday_calendars": [
            {"code": item.code, "version": item.version, "description": item.description,
             "business_weekdays": [getattr(day, "value", day) for day in item.business_weekdays],
             "dates": [value.isoformat() for value in item.dates], "date_count": len(item.dates)}
            for item in getattr(schedule, "holiday_calendars", None) or []
        ],
        "anchors": [
            {"code": item.code, "display_name": item.display_name, "status": item.status,
             "anchor_type": item.anchor_type,
             "is_origin": (item.derivation_rule or {}).get("role") == "SCHEDULE_ORIGIN"}
            for item in schedule.anchors
        ],
    }


def _issue_statement_ids(issue: ValidationIssue) -> list[str]:
    ids: list[str] = []
    for key in ("fact_ids", "statement_ids"):
        value = issue.details.get(key)
        if isinstance(value, list):
            ids += [str(item) for item in value]
    for key in ("fact_id", "statement_id"):
        if issue.details.get(key):
            ids.append(str(issue.details[key]))
    return list(dict.fromkeys(ids))


def _statement_view(statement: dict[str, Any]) -> dict[str, Any]:
    disposition = str(statement.get("disposition") or "UNKNOWN")
    citations = [
        {"page": item.get("page"), "section": item.get("section"), "table": item.get("table"),
         "quote": item.get("quote"), "verification": item.get("verification") or "NOT_CHECKED"}
        for item in statement.get("citations") or [] if isinstance(item, dict)
    ]
    return {
        "statement_id": str(statement.get("statement_id")),
        "what": statement.get("what"),
        "disposition": disposition,
        "disposition_label": DISPOSITION_LABELS.get(disposition, disposition.replace("_", " ").lower()),
        "uncompiled": disposition in UNCOMPILED_DISPOSITIONS,
        "reason": statement.get("reason"),
        "operator": statement.get("operator"),
        "evidence_state": statement.get("evidence_state"),
        "citations": citations,
        "rule_refs": statement.get("rule_refs") or [],
        "unknown_fields": statement.get("unknown_fields") or {},
        "blocking_issue_codes": statement.get("blocking_issue_codes") or [],
    }


def _restriction_view(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "UNKNOWN").upper()
    operator = item.get("required_operator")
    timing = item.get("timing") if isinstance(item.get("timing"), dict) else None
    detail = f" ({timing.get('type', '').lower().replace('_', ' ')} timing)" if timing else ""
    summary = (f"Additional restriction{detail} — "
               + ("compiled" if status == "COMPILED" else
                  f"not compiled{f', needs {operator}' if operator else ''}; applies together with the timing"))
    return {
        "fact_id": item.get("fact_id"), "status": status, "relation": item.get("relation"),
        "required_operator": operator, "summary": summary, "timing": timing,
        "citations": [{"page": c.get("page"), "quote": c.get("quote"),
                       "verification": c.get("verification") or "NOT_CHECKED"}
                      for c in item.get("citations") or [] if isinstance(c, dict)],
    }


def _source_coverage_view(accountability: dict[str, Any] | None, coverage_gate: dict[str, Any],
                          unlinked: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Document-level Source coverage panel (IN-03, IR-05)."""
    if not isinstance(accountability, dict) or not accountability:
        return None
    coverage = accountability.get("coverage") or {}
    summary = accountability.get("summary") or {}
    statements = [item for item in accountability.get("statements") or [] if isinstance(item, dict)]
    dispositions = summary.get("dispositions") or {}
    if not dispositions:
        for item in statements:
            dispositions[item.get("disposition")] = dispositions.get(item.get("disposition"), 0) + 1
    references = [item for item in coverage.get("references") or [] if isinstance(item, dict)]
    return {
        "schema": accountability.get("schema"),
        "status": coverage_gate["status"],
        "blocking": coverage_gate["blocking"],
        "message": coverage_gate["message"],
        "counts": coverage.get("counts") or {},
        "gaps": [{"unit_id": item.get("unit_id"), "page": item.get("page"), "state": item.get("state"),
                  "rationale": item.get("rationale")} for item in coverage.get("gaps") or []],
        "unaccounted_units": coverage.get("unaccounted_units") or [],
        "missing_references": [item for item in references
                               if str(item.get("state", "")).upper() in {"MISSING", "UNRESOLVED"}],
        "notes": coverage.get("notes") or [],
        "limits": coverage.get("limits") or {},
        "document": accountability.get("document"),
        "versions": accountability.get("versions"),
        "statements": {
            "total": summary.get("statements", len(statements)),
            "dispositions": dispositions,
            "uncompiled": coverage_gate.get("uncompiled_statements", 0),
            "not_linked_to_a_visit": unlinked,
        },
        "anchors": accountability.get("anchors") or {},
    }


def activity_owner_ids(event: Event) -> set[UUID]:
    return {activity.id for activity in event.activities}


def _activity_view(activity: Any, names: _Names) -> dict[str, Any]:
    data = activity.model_dump(mode="json", by_alias=True)
    timing_text = describe_timing(data["timing"], names)[0] if data.get("timing") else None
    return {
        "id": str(activity.id), "code": activity.code, "name": activity.display_name,
        "activity_type": activity.activity_type, "requiredness": activity.requiredness.value,
        "timing_summary": timing_text,
        "conditions": [describe_condition(item) for item in data.get("conditions") or []],
        "applicability_summary": describe_applicability(data.get("applicability") or []),
        "footnotes": [{"marker": q.marker, "text": q.text, "category": q.category.value,
                       "resolved": q.resolved} for q in activity.qualifiers],
        "needs_review": activity.requires_review
        or activity.interpretation_status.value in UNRESOLVED_INTERPRETATION,
    }


def _anchor_views(data: dict[str, Any], names: _Names) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}

    def visit(value: Any, role: str) -> None:
        if isinstance(value, dict):
            if value.get("kind") == "ANCHOR" and value.get("code"):
                code = str(value["code"])
                anchor = names.anchors.get(code)
                seen.setdefault(code, {
                    "code": code,
                    "display_name": anchor.display_name if anchor else code,
                    "defined": anchor is not None or names.is_cycle_anchor(code),
                    "status": anchor.status if anchor else ("CYCLE" if names.is_cycle_anchor(code) else "UNDEFINED"),
                    "accepts": value.get("accepts", "ACTUAL"),
                    "roles": [],
                })
                if role not in seen[code]["roles"]:
                    seen[code]["roles"].append(role)
            for item in value.values():
                visit(item, role)
        elif isinstance(value, list):
            for item in value:
                visit(item, role)

    visit(data.get("timing"), "timing")
    visit(data.get("recurrence"), "repetition")
    visit(data.get("recurrence_plan"), "repetition")
    visit(data.get("activities"), "assessment timing")
    return list(seen.values())


PROCESS_ACTION_WORDS = {
    "HOLD_PROCESS": "Holds {name} only", "RESUME_PROCESS": "Resumes {name}",
    "STOP_PROCESS": "Stops {name} only", "COMPLETE_PROCESS": "Completes {name}",
}
HOLD_RESUME_LABELS = {
    "NOMINAL": "Resume on the original dates (missed ones are not moved)",
    "SHIFT_BY_HOLD": "Shift later dates by the length of the hold",
    "RESTART_FROM_RESUME": "Restart the series from the resume date",
    "WAIT_FOR_DECISION": "Wait for an explicit decision before rescheduling",
    "NONE_STATED": "Not stated — needs review before a hold can be applied",
}


def describe_action(action: dict[str, Any], names: _Names) -> str:
    kind = str(action.get("action_type") or "")
    target = str(action.get("target_code") or "")
    if kind in PROCESS_ACTION_WORDS:
        process = names.processes.get(target)
        return PROCESS_ACTION_WORDS[kind].format(name=process.display_name if process else target)
    event = names.events.get(target)
    return f"{kind.replace('_', ' ').lower()} {event.display_name if event else target}"


def process_actions_for(event_code: str, names: _Names) -> list[tuple[str, int, dict[str, Any]]]:
    """Process-scoped actions (anywhere in the schedule) whose process includes this visit."""
    member_of = {code for code, process in names.processes.items() if event_code in (process.event_codes or [])}
    if not member_of:
        return []
    found = []
    for owner in names.events.values():
        for index, action in enumerate(owner.conditional_actions):
            if action.action_type in PROCESS_ACTION_WORDS and action.target_code in member_of:
                found.append((f"visit {owner.code}", index, action.model_dump(mode="json")))
    for definition in names.conditional_definitions:
        for index, action in enumerate(definition.actions):
            if action.action_type in PROCESS_ACTION_WORDS and action.target_code in member_of:
                payload = action.model_dump(mode="json")
                payload.setdefault("condition", definition.condition.model_dump(mode="json"))
                found.append((f"rule {definition.code}", index, payload))
    return found


def process_summary(process: Any, names: _Names) -> dict[str, Any]:
    policy = getattr(process.hold_resume_policy, "value", process.hold_resume_policy)
    return {
        "code": process.code, "display_name": process.display_name,
        "kind": getattr(process.kind, "value", process.kind),
        "event_codes": list(process.event_codes or []),
        "visits": [names.events[code].display_name for code in process.event_codes or [] if code in names.events],
        "hold_resume_policy": policy,
        "hold_resume_label": HOLD_RESUME_LABELS.get(policy, str(policy)),
        "hold_resume_needs_review": policy == "NONE_STATED",
        "resume_trigger": process.resume_trigger,
        "resume_trigger_label": {"EXPLICIT_RESUME": "Resumes only when a resume is recorded",
                                 "CONDITION_RESOLUTION": "Resumes when the holding condition resolves",
                                 None: "Not stated"}.get(process.resume_trigger, str(process.resume_trigger)),
    }


def _row_constraints(
    event: Event, data: dict[str, Any], names: _Names, timing_text: str, timing_supported: bool,
    recurrence: dict[str, Any] | None, window_status: str, window_summary: str,
    stated_note: Any, blocks: list[Any], definitions: list[Any],
) -> list[dict[str, Any]]:
    """Every restriction the row carries, including those the compact row omits (UI-03)."""
    items: list[dict[str, Any]] = []

    def add(kind: str, summary: str, rule: Any, *, in_summary: bool, path: str,
            supported: bool = True) -> None:
        items.append({
            "id": f"{event.id}:{path}", "kind": kind, "summary": summary, "rule": rule,
            "in_compact_summary": in_summary, "path": path, "supported": supported,
        })

    add("TIMING", timing_text, data.get("timing"), in_summary=True, path="timing",
        supported=timing_supported)
    if data.get("window"):
        add("WINDOW", f"Allowed window {_window_text(data['window'])}", data["window"],
            in_summary=True, path="window")
    elif window_status in {"NOT_STATED", "NOT_APPLICABLE"}:
        add("WINDOW", window_summary, None, in_summary=True, path="window")
    if stated_note:
        add("WINDOW_AS_STATED", "Protocol states a window MTB could not apply: "
            + str((stated_note or {}).get("reason") if isinstance(stated_note, dict) else stated_note),
            stated_note, in_summary=False, path="metadata.window_as_stated")
    if recurrence:
        add("REPETITION", recurrence["summary"], recurrence["rule"], in_summary=True,
            path=("calendar_recurrence" if data.get("calendar_recurrence")
                  else "recurrence_plan" if data.get("recurrence_plan") else "recurrence"))
        for policy in recurrence.get("policies") or []:
            add("CALENDAR_POLICY", f"{policy['label']}: {policy['value']}", policy,
                in_summary=False, path=f"calendar_recurrence.{policy['key']}")
    for index, rule in enumerate(data.get("applicability") or []):
        add("APPLICABILITY", describe_applicability([rule]), rule, in_summary=False,
            path=f"applicability[{index}]")
    for index, condition in enumerate(data.get("conditions") or []):
        add("CONDITION", f"Only if {describe_condition(condition)}", condition, in_summary=index == 0,
            path=f"conditions[{index}]")
    for index, dependency in enumerate(data.get("dependencies") or []):
        source = names.events.get(dependency.get("source_event_code"))
        source_name = source.display_name if source else dependency.get("source_event_code")
        selector = dependency.get("source_occurrence", "ANY")
        selected = {"ANY": "any resolved or completed occurrence", "FIRST": "the first occurrence",
                    "LAST": "the last generated occurrence", "CURRENT": "the legacy current (last generated) occurrence"}.get(selector)
        if selector == "NUMBER":
            selected = f"occurrence {dependency['source_occurrence_number'] + 1}"
        condition = dependency.get("condition")
        add("DEPENDENCY", f"{dependency.get('dependency_type', '').title()} dependency on {selected} of {source_name}"
            + (f"; applies only if {describe_condition(condition)}" if condition else ""),
            dependency, in_summary=False, path=f"dependencies[{index}]")
    if data.get("dependency_mode"):
        add("DEPENDENCY_MODE", {
            "NOMINAL": "Dated from the planned schedule, not from when the previous visit happened",
            "ACTUAL_PREVIOUS_EVENT": "Requires the selected source's actual date stated by the timing rule",
            "MANUAL": "A site user enters the date",
            "UNCLEAR": "How this visit is dated relative to earlier visits is unclear — needs review",
        }.get(data["dependency_mode"], data["dependency_mode"]), data["dependency_mode"],
            in_summary=False, path="dependency_mode")
    if event.track_scope == "GLOBAL":
        add("TRACK", "Study-wide visit, outside every track", {"scope": "GLOBAL"}, in_summary=False, path="track_scope")
    elif event.track_id is not None:
        track = names.tracks.get(str(event.track_id))
        add("TRACK", f"Member of the {track.display_name} track ({track.code})" if track
            else "Track is not defined in this version", track.model_dump(mode="json") if track else None,
            in_summary=False, path="track_id", supported=track is not None)
    from app.domain.schedule.manual_handling import approved_manual, handling
    if approved_manual(event):
        rule = handling(event)
        items[0]["summary"] = "Original timing is not executable; approved manual workflow applies"
        add("MANUAL_HANDLING", f"Manual scheduling required; no automatic target. {rule.policy_reference}. {rule.reason}",
            rule.model_dump(mode="json"), in_summary=False, path="metadata.rule_handling")
    if data.get("visit_mode") or data.get("allowed_visit_modes"):
        modes = data.get("allowed_visit_modes") or [data.get("visit_mode")]
        add("VISIT_MODE", "Attend by " + " or ".join(str(m).lower().replace("_", " ") for m in modes),
            {"visit_mode": data.get("visit_mode"), "allowed_visit_modes": data.get("allowed_visit_modes")},
            in_summary=False, path="visit_mode")
    else:
        add("VISIT_MODE", "How the patient attends is not stated", None, in_summary=False,
            path="visit_mode")
    if data.get("activation") == "ON_DEMAND":
        add("ACTIVATION", "Only when needed (never automatically due)", "ON_DEMAND",
            in_summary=True, path="activation")
    for index, action in enumerate(data.get("conditional_actions") or []):
        add("CONDITIONAL_ACTION",
            f"{describe_action(action, names)} when {describe_condition(action.get('condition'))}",
            action, in_summary=False, path=f"conditional_actions[{index}]")
    # WP3: a process-scoped action elsewhere in the schedule that changes this visit.
    for source, index, action in process_actions_for(event.code, names):
        add("PROCESS_ACTION",
            f"{describe_action(action, names)} when {describe_condition(action.get('condition'))} ({source})",
            action, in_summary=False, path=f"process_actions.{source}[{index}]")
    for definition in definitions:
        add("CONDITIONAL_RULE", f"Activated by rule “{definition.display_name}” when "
            f"{describe_condition(definition.condition.model_dump(mode='json'))}",
            definition.model_dump(mode="json", by_alias=True), in_summary=True,
            path=f"conditional_definitions.{definition.code}")
    for block in blocks:
        add("REPEAT_BLOCK", f"Part of the “{block.display_name}” series "
            f"({str(block.dependency_mode.value).replace('_', ' ').lower()} dating)",
            block.model_dump(mode="json", by_alias=True), in_summary=False,
            path=f"repeat_blocks.{block.code}")
    if data.get("confinement"):
        add("CONFINEMENT", "In-patient stay rule", data["confinement"], in_summary=False,
            path="confinement")
    for index, qualifier in enumerate(data.get("qualifiers") or []):
        state = "resolved" if qualifier.get("resolved") else "unresolved"
        add("FOOTNOTE", f"Footnote {qualifier.get('marker') or ''} ({str(qualifier.get('category')).lower()}, "
            f"{state}): {qualifier.get('text')}".replace("  ", " "), qualifier, in_summary=False,
            path=f"qualifiers[{index}]")
    for a_index, activity in enumerate(data.get("activities") or []):
        if activity.get("timing"):
            text, supported = describe_timing(activity["timing"], names)
            add("ASSESSMENT_TIMING", f"{activity.get('display_name')}: {text}", activity["timing"],
                in_summary=False, path=f"activities[{a_index}].timing", supported=supported)
        for c_index, condition in enumerate(activity.get("conditions") or []):
            add("ASSESSMENT_CONDITION", f"{activity.get('display_name')} only if {describe_condition(condition)}",
                condition, in_summary=False, path=f"activities[{a_index}].conditions[{c_index}]")
        for r_index, rule in enumerate(activity.get("applicability") or []):
            add("ASSESSMENT_APPLICABILITY", f"{activity.get('display_name')}: {describe_applicability([rule])}",
                rule, in_summary=False, path=f"activities[{a_index}].applicability[{r_index}]")
        for q_index, qualifier in enumerate(activity.get("qualifiers") or []):
            state = "resolved" if qualifier.get("resolved") else "unresolved"
            add("FOOTNOTE", f"{activity.get('display_name')} footnote {qualifier.get('marker') or ''} "
                f"({state}): {qualifier.get('text')}".replace("  ", " "), qualifier, in_summary=False,
                path=f"activities[{a_index}].qualifiers[{q_index}]")
        for key, value in activity.items():
            if key not in {"id", "code", "protocol_label", "display_name", "activity_type", "requiredness",
                           "sequence_number", "timing", "conditions", "applicability", "qualifiers",
                           "metadata", "interpretation_status", "requires_review", "evidence_refs",
                           "constraints"} and value:
                add("ADDITIONAL_RULE", f"{activity.get('display_name')}: additional {key.replace('_', ' ')} "
                    "rule — summary not available; see full details", value, in_summary=False,
                    path=f"activities[{a_index}].{key}", supported=False)
    # Hook: a composable constraint set (WP2) or any other new rule family on
    # the event. Listed, never dropped - an unknown restriction still blocks
    # nothing silently and reaches review and export.
    for key, value in data.items():
        if key in _HANDLED_EVENT_FIELDS or not value:
            continue
        rules = value if isinstance(value, list) else [value]
        for index, rule in enumerate(rules):
            summary = rule.get("summary") if isinstance(rule, dict) and isinstance(rule.get("summary"), str) else None
            add("ADDITIONAL_RULE", summary or f"Additional {key.replace('_', ' ')} rule — summary not "
                "available; see full details", rule, in_summary=False,
                path=f"{key}[{index}]" if isinstance(value, list) else key, supported=bool(summary))
    for index, restriction in enumerate((data.get("metadata") or {}).get("additional_restrictions") or []):
        if isinstance(restriction, dict):
            add("ADDITIONAL_RESTRICTION", _restriction_view(restriction)["summary"], restriction,
                in_summary=False, path=f"metadata.additional_restrictions[{index}]",
                supported=str(restriction.get("status", "")).upper() == "COMPILED")
    for key, value in (data.get("metadata") or {}).items():
        if (key in _BOOKKEEPING_METADATA or key in {"window_as_stated", "additional_restrictions"}
                or value in (None, "", [], {})):
            continue
        if key in {"constraints", "constraint_set", "additional_constraints"}:
            rules = value if isinstance(value, list) else [value]
            for index, rule in enumerate(rules):
                add("ADDITIONAL_RULE", (rule.get("summary") if isinstance(rule, dict) else None)
                    or f"Additional constraint — see full details", rule, in_summary=False,
                    path=f"metadata.{key}[{index}]", supported=False)
    return items


# --- export -----------------------------------------------------------------------


def _label(status: str) -> str:
    if status == ScheduleStatus.APPROVED.value:
        return "APPROVED"
    if status in IMMUTABLE_STATUSES:
        return status
    return "DRAFT — NOT APPROVED FOR PATIENT USE"


def export_json(schedule: UniversalSchedule, view: dict[str, Any], provenance: dict[str, Any],
                accountability: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "export_schema": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "label": _label(view["status"]),
        "status": view["status"],
        "schedule_version_id": view["draft_version_id"],
        "version_number": view["version_number"],
        "revision": view["revision"],
        "semantic_fingerprint": view["semantic_fingerprint"],
        "provenance": provenance,
        "summary": view["summary"],
        "can_approve": view["can_approve"],
        "approval_blockers": view["approval_blockers"],
        "rows": view["rows"],
        "shared_review_questions": view["shared_review_questions"],
        "document_level_issues": view["document_level_issues"],
        "source_coverage_status": view["source_coverage_status"],
        "extraction_status": view["extraction_status"],
        "source_coverage": view.get("source_coverage"),
        "processes": view.get("processes") or [],
        "holiday_calendars": view.get("holiday_calendars") or [],
        # The extraction accountability ledger, unabridged.
        "accountability": accountability,
        # The complete canonical rules: whatever the rows could not flatten is here.
        "canonical_schedule": schedule.model_dump(mode="json", by_alias=True),
    }


def export_csv(view: dict[str, Any], provenance: dict[str, Any],
               accountability: dict[str, Any] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["MTB Visit Schedule export", EXPORT_SCHEMA_VERSION])
    writer.writerow(["Label", _label(view["status"])])
    writer.writerow(["Schedule", view["schedule_name"], "Version", view["version_number"]])
    writer.writerow(["Revision", view["revision"], "Semantic fingerprint", view["semantic_fingerprint"]])
    writer.writerow(["Protocol version", provenance.get("protocol_version_label"),
                     "Document", provenance.get("document_name"), "Document hash", provenance.get("document_hash")])
    writer.writerow(["Exported at", datetime.now(timezone.utc).isoformat(),
                     "Approved at", provenance.get("approved_at") or ""])
    writer.writerow(["Note", "Summary rows are abbreviated. Every rule, constraint, evidence reference and "
                     "question is listed in the DETAIL APPENDIX below; the JSON export carries the full rules."])
    writer.writerow([])
    writer.writerow(["SUMMARY"])
    writer.writerow(["row_id", "review_status", "visit_name", "timing", "window", "applies_to",
                     "assessments", "constraints", "open_findings", "evidence_pages"])
    for row in view["rows"]:
        details = row["details"]
        pages = sorted({str(item.get("page_number")) for item in details["evidence"] if item.get("page_number")})
        writer.writerow([
            row["id"], row["review_status"], row["visit_name"], row["timing_summary"],
            row["window_summary"], row["applicability_summary"],
            "; ".join(item["name"] for item in details["activities"]),
            len(details["constraints"]), len(row["issue_refs"]) + len(row["question_refs"]),
            ", ".join(pages),
        ])
    writer.writerow([])
    writer.writerow(["DETAIL APPENDIX"])
    writer.writerow(["row_id", "detail_type", "detail_id", "kind", "summary", "in_compact_summary",
                     "page", "rule_json"])
    for row in view["rows"]:
        details = row["details"]
        for activity in details["activities"]:
            writer.writerow([row["id"], "ASSESSMENT", activity["id"], activity["requiredness"],
                             activity["name"] + (f" — {activity['timing_summary']}" if activity["timing_summary"] else ""),
                             "", "", json.dumps(activity, default=str)])
        for anchor in details["anchors"]:
            writer.writerow([row["id"], "ANCHOR", anchor["code"], anchor["accepts"], anchor["display_name"],
                             "", "", json.dumps(anchor, default=str)])
        for constraint in details["constraints"]:
            writer.writerow([row["id"], "CONSTRAINT", constraint["id"], constraint["kind"], constraint["summary"],
                             "yes" if constraint["in_compact_summary"] else "no", "",
                             json.dumps(constraint["rule"], default=str)])
        for evidence in details["evidence"]:
            writer.writerow([row["id"], "EVIDENCE", evidence["id"], evidence.get("evidence_type"),
                             evidence.get("quote") or "", "", evidence.get("page_number") or "",
                             json.dumps(evidence, default=str)])
        for issue in row["issues"]:
            writer.writerow([row["id"], "FINDING", issue["id"], issue["code"], issue["message"], "", "",
                             json.dumps(issue, default=str)])
    for question in view["shared_review_questions"]:
        writer.writerow(["; ".join(question["affected_row_ids"]), "SHARED_QUESTION", question["id"],
                         question["kind"], question["title"], "", "", json.dumps(question, default=str)])
    for issue in view["document_level_issues"]:
        writer.writerow(["(document)", "DOCUMENT_ISSUE", issue["id"], issue["code"], issue["message"],
                         "", "", json.dumps(issue, default=str)])
    for row in view["rows"]:
        for restriction in row["details"].get("additional_restrictions") or []:
            writer.writerow([row["id"], "ADDITIONAL_RESTRICTION", restriction.get("fact_id"),
                             restriction["status"], restriction["summary"], "no", "",
                             json.dumps(restriction, default=str)])
    writer.writerow([])
    writer.writerow(["PROCESSES APPENDIX"])
    writer.writerow(["process_code", "name", "kind", "visits", "hold_resume_policy", "resume_trigger", "rule_json"])
    for process in view.get("processes") or []:
        writer.writerow([process["code"], process["display_name"], process["kind"], "; ".join(process["visits"]),
                         process["hold_resume_label"], process["resume_trigger_label"],
                         json.dumps(process, default=str)])
    writer.writerow([])
    writer.writerow(["HOLIDAY CALENDARS APPENDIX"])
    writer.writerow(["code", "version", "business_weekdays", "dates"])
    for calendar in view.get("holiday_calendars") or []:
        writer.writerow([calendar["code"], calendar["version"], " ".join(calendar["business_weekdays"]),
                         "; ".join(calendar["dates"])])
    writer.writerow([])
    writer.writerow(["SOURCE COVERAGE APPENDIX"])
    if not accountability:
        writer.writerow(["status", view["source_coverage_status"]["status"],
                         view["source_coverage_status"]["message"]])
    else:
        coverage = accountability.get("coverage") or {}
        writer.writerow(["schema", accountability.get("schema"), "status", coverage.get("status"),
                         "document_hash", (accountability.get("document") or {}).get("hash")
                         or coverage.get("document_hash")])
        writer.writerow(["counts", json.dumps(coverage.get("counts") or {})])
        writer.writerow(["unit_id", "page", "state", "rationale", "text_status"])
        for unit in coverage.get("units") or []:
            writer.writerow([unit.get("unit_id"), unit.get("page"), unit.get("state"),
                             unit.get("rationale"), unit.get("text_status")])
        writer.writerow(["reference_kind", "label", "state", "required", "from_pages", "text"])
        for reference in coverage.get("references") or []:
            writer.writerow([reference.get("kind"), reference.get("label"), reference.get("state"),
                             reference.get("required"), json.dumps(reference.get("from_pages")),
                             reference.get("text")])
        writer.writerow([])
        writer.writerow(["STATEMENTS APPENDIX"])
        writer.writerow(["statement_id", "disposition", "what", "reason", "operator", "evidence_state",
                         "pages", "quotes", "verification", "rule_refs_json", "unknown_fields_json"])
        for statement in accountability.get("statements") or []:
            citations = statement.get("citations") or []
            writer.writerow([
                statement.get("statement_id"), statement.get("disposition"), statement.get("what"),
                statement.get("reason"), statement.get("operator"), statement.get("evidence_state"),
                "; ".join(str(c.get("page")) for c in citations if c.get("page") is not None),
                " | ".join(str(c.get("quote")) for c in citations if c.get("quote")),
                "; ".join(str(c.get("verification")) for c in citations),
                json.dumps(statement.get("rule_refs") or [], default=str),
                json.dumps(statement.get("unknown_fields") or {}, default=str),
            ])
    return buffer.getvalue()


