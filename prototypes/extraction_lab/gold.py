"""Explicit expected-field checks; a green schema is never an accuracy score."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["visit", "activity", "anchor", "condition", "milestone"] | None = None
    aliases: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[FactCheck] = Field(default_factory=list)
    required_issue_codes: list[str] = Field(default_factory=list)
    forbidden_visit_names: list[str] = Field(default_factory=list)
    minimum_table_cells: int = Field(default=0, ge=0)
    required_footnote_labels: list[str] = Field(default_factory=list)
    expected_schedule_options: int | None = Field(default=None, ge=0)

    def count(self) -> int:
        return (sum(1 + len(check.fields) for check in self.checks)
                + len(self.required_issue_codes) + len(self.forbidden_visit_names)
                + int(self.minimum_table_cells > 0) + len(self.required_footnote_labels)
                + int(self.expected_schedule_options is not None))


def normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def lookup(value, dotted: str):
    for part in dotted.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def same(actual, expected) -> bool:
    """Typed exact equality: bool is not int; list order remains meaningful."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def score(report: dict, expected: Expectations | None) -> dict:
    if expected is None or not expected.count():
        return {"status": "NOT_SCORED", "passed": 0, "total": 0, "checks": [],
                "note": "No reviewed expectations supplied. Successful extraction is not measured accuracy."}
    rows = []

    def check(label, actual, wanted, passed=None):
        rows.append({"check": label, "passed": same(actual, wanted) if passed is None else passed,
                     "expected": wanted, "actual": actual})

    facts = (report.get("facts") or {}).get("facts", [])
    used = set()
    for wanted in expected.checks:
        names = {normal(n) for n in [wanted.name, *wanted.aliases]}
        candidates = [(i, fact) for i, fact in enumerate(facts) if i not in used
                      and normal(fact.get("what", "")) in names
                      and (wanted.kind is None or fact.get("kind") == wanted.kind)]
        # Match duplicate names to the most specific field agreement, then
        # consume that occurrence so one fact cannot satisfy two expectations.
        candidates.sort(key=lambda pair: sum(same(lookup(pair[1], path), val)
                                             for path, val in wanted.fields.items()), reverse=True)
        match = candidates[0] if candidates else None
        if match:
            used.add(match[0])
        fact = match[1] if match else {}
        check(f"{wanted.name}: fact present", bool(match), True)
        for path, val in wanted.fields.items():
            check(f"{wanted.name}: {path}", lookup(fact, path), val, passed=bool(match) and same(lookup(fact, path), val))
    issues = {i["issue_code"] for r in report.get("results", []) for i in r.get("issues", [])}
    for code in expected.required_issue_codes:
        check(f"Required issue {code}", code in issues, True)
    visits = {normal(event["display_name"]) for result in report.get("results", [])
              for event in (result.get("schedule") or {}).get("events", [])}
    for name in expected.forbidden_visit_names:
        check(f"No generated visit {name}", normal(name) in visits, False)
    accountability = (report.get("facts") or {}).get("accountability", {})
    cells = [cell for fact in facts for cell in fact.get("provenance", {}).get("cells", [])]
    if expected.minimum_table_cells:
        check("Marked table cells", len(cells), expected.minimum_table_cells, len(cells) >= expected.minimum_table_cells)
    notes = accountability.get("footnotes", [])
    labels = {str(n.get("label")) for n in notes}
    for label in expected.required_footnote_labels:
        check(f"Footnote {label}", label in labels, True)
    if expected.expected_schedule_options is not None:
        check("Independent schedule count", len(report.get("results", [])), expected.expected_schedule_options)
    passed = sum(row["passed"] for row in rows)
    complete = report.get("coverage_status") == "COMPLETE" and report.get("status") == "COMPLETED"
    return {"status": "PASS" if passed == len(rows) and complete else "FAIL" if complete else "INCOMPLETE",
            "passed": passed, "total": len(rows), "checks": rows,
            "note": "These explicit checks cover selected fields, not universal protocol accuracy or clinical approval."}
