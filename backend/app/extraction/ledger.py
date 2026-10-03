"""Accountability ledgers for one protocol read (IN-02/03, IR-05).

Two linked ledgers make omissions inspectable. They do not prove that a model
found everything; they make sure nothing the pipeline saw, skipped or could
not read disappears silently.

* **Source coverage** - every page (source unit) of the uploaded document gets
  exactly one coverage state with a rationale, and every section / appendix
  reference found on a read page is resolved to pages or recorded as missing.
* **Statement disposition** - every statement the reader reported (valid
  facts, invalid raw items, SoA footnotes) gets exactly one disposition, with
  the rules it compiled into or the reason it did not.

Both are plain JSON (``dict``) so they can be stored on existing rows
(``ExtractionRun.trace``, the Mongo draft) and returned in API previews
unchanged. ``ACCOUNTABILITY_SCHEMA`` versions the shape.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

ACCOUNTABILITY_SCHEMA = "uctsm-extraction-accountability.v1"

# --- source coverage ---------------------------------------------------------------

INSPECTED = "INSPECTED"
NOT_SELECTED = "NOT_SELECTED"
UNREADABLE = "UNREADABLE"
MISSING_REFERENCE = "MISSING_REFERENCE"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
#: The page was sent to the model but no usable reply came back (malformed
#: twice, or the call failed); its statements may be missing.
READ_FAILED = "READ_FAILED"
#: The run stopped (transport failure / timeout) before this page was sent.
INTERRUPTED = "INTERRUPTED"

COVERAGE_STATES = (INSPECTED, NOT_SELECTED, UNREADABLE, MISSING_REFERENCE,
                   BUDGET_EXHAUSTED, READ_FAILED, INTERRUPTED)
#: Any unit in one of these states makes coverage INCOMPLETE.
INCOMPLETE_STATES = frozenset({UNREADABLE, MISSING_REFERENCE, BUDGET_EXHAUSTED, READ_FAILED,
                               INTERRUPTED})

#: "see Appendix C", "Annex 2", "Attachment B" - separately supplied material
#: a protocol commonly relies on. A missing one is required coverage.
_APPENDIX_REF = re.compile(
    r"\b(appendix|appendices|annex|attachment|supplement)\s+([A-Z]{1,2}\b|[IVX]{1,5}\b|\d{1,2}\b)",
    re.IGNORECASE)
#: "Section 10.2", "§ 7.1" - internal cross references.
_SECTION_REF = re.compile(r"(?:\bsections?\s+|§\s*)(\d{1,2}(?:\.\d{1,2}){0,4})\b", re.IGNORECASE)
#: Table-of-contents lines ("7.1 Treatment plan ........ 23") name sections
#: without being them.
_TOC_LINE = re.compile(r"(\.{3,}|\s{3,})\s*\d{1,4}\s*$")


def _kind(word: str) -> str:
    word = word.lower()
    if word.startswith("append"):
        return "APPENDIX"
    return word.upper()


def detect_references(text: str) -> list[dict[str, str]]:
    """Every appendix / section reference in a page's text, deduplicated."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for match in _APPENDIX_REF.finditer(text or ""):
        kind, label = _kind(match.group(1)), match.group(2).upper()
        found.setdefault((kind, label), {"kind": kind, "label": label, "text": match.group(0)})
    for match in _SECTION_REF.finditer(text or ""):
        label = match.group(1).rstrip(".")
        found.setdefault(("SECTION", label), {"kind": "SECTION", "label": label, "text": match.group(0)})
    return list(found.values())


def _heading_pattern(kind: str, label: str) -> re.Pattern[str]:
    escaped = re.escape(label)
    if kind == "SECTION":
        # "10.2 Follow-up", "Section 10.2: Follow-up", "10.2. FOLLOW-UP"
        return re.compile(rf"^\s*(?:section\s+)?{escaped}\.?\s+[A-Za-z(]", re.IGNORECASE | re.MULTILINE)
    words = {"APPENDIX": "appendix", "ANNEX": "annex", "ATTACHMENT": "attachment",
             "SUPPLEMENT": "supplement"}.get(kind, kind.lower())
    return re.compile(rf"^\s*{words}\s+{escaped}\b", re.IGNORECASE | re.MULTILINE)


def resolve_reference(kind: str, label: str, pages: Iterable[Any]) -> list[int]:
    """Pages whose text carries a heading for the referenced unit.

    A table-of-contents line naming the unit is not the unit itself.
    """
    pattern = _heading_pattern(kind, label)
    hits: list[int] = []
    for page in pages:
        for match in pattern.finditer(page.text or ""):
            line_end = (page.text or "").find("\n", match.start())
            line = (page.text or "")[match.start(): line_end if line_end >= 0 else None]
            if not _TOC_LINE.search(line):
                hits.append(page.page_number)
                break
    return hits


class CoverageLedger:
    """One coverage state per page, plus the reference inventory."""

    def __init__(self, pages: Iterable[Any], *, document_hash: str):
        self.document_hash = document_hash
        self.units: dict[int, dict[str, Any]] = {}
        for page in pages:
            text = page.text or ""
            self.units[page.page_number] = {
                "unit_id": f"page-{page.page_number}", "page": page.page_number,
                "text_status": page.text_status, "chars": len(text),
                "state": None, "rationale": None, "score": None, "batch": None,
                "references": [],
            }
        self.references: list[dict[str, Any]] = []
        self.limits: dict[str, Any] = {}
        self.usage: dict[str, Any] = {}
        self.notes: list[str] = []

    def set(self, page: int, state: str, rationale: str, **extra: Any) -> None:
        assert state in COVERAGE_STATES, state
        unit = self.units.get(page)
        if unit is None:
            return
        unit.update(state=state, rationale=rationale, **extra)

    def missing_reference(self, reference: dict[str, Any]) -> None:
        """A referenced unit that is not in the document is its own source unit."""
        unit_id = f"ref-{reference['kind'].lower()}-{reference['label']}"
        self.references.append(reference)
        if reference.get("state") == "MISSING" and reference.get("required"):
            self.units[unit_id] = {
                "unit_id": unit_id, "page": None, "text_status": None, "chars": 0,
                "state": MISSING_REFERENCE,
                "rationale": (f"{reference['kind'].title()} {reference['label']} is referenced on page(s) "
                              f"{', '.join(str(p) for p in reference['from_pages'])} "
                              f"({reference['text']!r}) but is not in the uploaded document."),
                "score": None, "batch": None, "references": [],
            }

    def gaps(self) -> list[dict[str, Any]]:
        return [unit for unit in self.units.values() if unit["state"] in INCOMPLETE_STATES]

    def to_dict(self) -> dict[str, Any]:
        counts = {state: 0 for state in COVERAGE_STATES}
        for unit in self.units.values():
            if unit["state"] is not None:
                counts[unit["state"]] += 1
        unaccounted = [unit["unit_id"] for unit in self.units.values() if unit["state"] is None]
        gaps = self.gaps()
        return {
            "status": "INCOMPLETE" if gaps or unaccounted else "COMPLETE",
            "document_hash": self.document_hash,
            "counts": counts,
            "gaps": [{"unit_id": unit["unit_id"], "page": unit["page"], "state": unit["state"],
                      "rationale": unit["rationale"]} for unit in gaps],
            "unaccounted_units": unaccounted,
            "units": sorted(self.units.values(),
                            key=lambda unit: (unit["page"] is None, unit["page"] or 0, unit["unit_id"])),
            "references": self.references,
            "limits": self.limits,
            "usage": self.usage,
            "notes": self.notes + [
                "NOT_SELECTED pages were excluded by deterministic keyword ranking; ranking "
                "prioritises reading but cannot prove an excluded page irrelevant.",
            ],
        }


# --- statement disposition ----------------------------------------------------------

COMPILED = "COMPILED"
DUPLICATE_OF = "DUPLICATE_OF"
PENDING_INTERPRETATION = "PENDING_INTERPRETATION"
UNSUPPORTED = "UNSUPPORTED"
OUT_OF_SCOPE_NON_SCHEDULE = "OUT_OF_SCOPE_NON_SCHEDULE"
INVALID = "INVALID"

DISPOSITIONS = (COMPILED, DUPLICATE_OF, PENDING_INTERPRETATION, UNSUPPORTED,
                OUT_OF_SCOPE_NON_SCHEDULE, INVALID)
#: A statement in one of these states is an uncompiled requirement: it blocks
#: readiness of every compiled rule about the same activity (IR-05, AT-04).
UNCOMPILED = frozenset({PENDING_INTERPRETATION, UNSUPPORTED, INVALID})

#: Relations recorded between statements (design s2). AND means both
#: restrictions apply to the same occurrence.
RELATION_TYPES = ("DUPLICATE", "AND", "CONFLICTS_WITH", "INTERPRETED_BY", "GROUPED_INTO")

#: Issue codes whose facts are a capability gap rather than a question.
UNSUPPORTED_ISSUE_CODES = {
    "UNSUPPORTED_TIMING": "TIMING_OPERATOR",
    "ADDITIONAL_RESTRICTION_NOT_COMPILED": "CONSTRAINT_CONJUNCTION",
}


def disposition_counts(statements: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in DISPOSITIONS}
    for statement in statements:
        counts[statement["disposition"]] = counts.get(statement["disposition"], 0) + 1
    return counts


def evidence_state(citations: list[dict[str, Any]]) -> str | None:
    """Fact-level evidence state: the weakest of its citations."""
    states = [citation.get("verification") for citation in citations]
    if not states or any(state is None for state in states):
        return None if not any(states) else "UNVERIFIED"
    if "REJECTED" in states:
        return "REJECTED"
    if "UNVERIFIED" in states:
        return "UNVERIFIED"
    return "TEXT_MATCHED"
