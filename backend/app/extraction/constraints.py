"""Cited facts -> WP2 composable constraint nodes (``uctsm-constraints.v1``).

Used by the compiler when one occurrence carries more than one restriction
(AND), permitted alternatives (OR) or a requirement no executable operator
covers yet (UNSUPPORTED). Deterministic and conservative:

* A bound's ``inclusive`` flag is required by the language and is taken only
  from what the source states: the fact's ``lower_inclusive`` /
  ``upper_inclusive``, or unambiguous boundary wording in the fact's own quote
  ("at least", "no later than" = inclusive; "more than", "less than" =
  exclusive). Anything else raises ``NotExact`` and the statement stays an
  unresolved, blocking statement - never a guessed bound.
* A reference to a visit needs its occurrence (FIRST / LAST) stated.
* Every leaf carries the evidence ids of the statement it came from.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.domain.schedule.timing import (
    AllConstraint,
    AnchorReference,
    AnchorValue,
    AnyConstraint,
    AtConstraint,
    EventReference,
    EventValue,
    LiteralDateValue,
    LowerBoundConstraint,
    OccurrenceSelector,
    ShiftValue,
    TemporalAmount,
    UnsupportedConstraint,
    UpperBoundConstraint,
)

from .facts import UNSUPPORTED_PATTERNS, Duration, ScheduleFact

#: Unambiguous boundary wording, per side. (inclusive?, pattern)
_LOWER_WORDS = [
    (True, re.compile(r"\bat least\b|\bno (?:earlier|sooner) than\b|\bnot (?:earlier|sooner) than\b"
                      r"|\bminimum of\b|\bor more\b|\bor later\b", re.IGNORECASE)),
    (False, re.compile(r"(?<!no )(?<!not )\bmore than\b|\bgreater than\b|\blater than\b(?<!no later than)",
                       re.IGNORECASE)),
]
_UPPER_WORDS = [
    (True, re.compile(r"\bno later than\b|\bnot later than\b|\bat the latest\b|\bup to\b"
                      r"|\bno more than\b|\bnot more than\b|\bmaximum of\b|\bor less\b|\bor earlier\b",
                      re.IGNORECASE)),
    (False, re.compile(r"(?<!no )(?<!not )\bless than\b|\bfewer than\b|\bstrictly before\b", re.IGNORECASE)),
]

#: Wording that states a calendar rule no operator executes yet, even when the
#: reader squeezed it into a plain repeat or offset.
#: A weekday-only schedule ("5 days per week", "Monday to Friday"). Since WP3
#: it compiles to a calendar recurrence when the weekdays are named.
_WEEKDAY_WORDING = re.compile(
    r"\b\d\s*(?:days?|fractions?|sessions?|treatments?)\s*(?:per|a|each|/)\s*week\b"
    r"|\bmonday\s*(?:to|through|-|–)\s*friday\b|\bweekdays?\b|\bexcluding weekends?\b", re.IGNORECASE)

_UNSUPPORTED_WORDING = [
    ("BUSINESS_DAY_OFFSET", re.compile(r"\b(?:business|working)\s+days?\b", re.IGNORECASE)),
    ("HOLIDAY_ADJUSTMENT", re.compile(r"\b(?:public|bank|national)\s+holidays?\b|\bholidays?\b", re.IGNORECASE)),
]

BOUND_PATTERNS = {"WITHIN_BEFORE", "WITHIN_AFTER", "NO_LATER_THAN", "NO_EARLIER_THAN", "RANGE"}


class NotExact(Exception):
    """The statement cannot be written as constraint nodes without guessing."""


def _quotes(fact: ScheduleFact) -> str:
    return " ".join(citation.quote for citation in fact.evidence)


def stated_inclusive(fact: ScheduleFact, side: str) -> tuple[bool | None, str | None]:
    """(inclusive, how it was stated) for one side of a bound."""
    timing = fact.timing
    explicit = getattr(timing, f"{side}_inclusive", None) if timing else None
    if explicit is not None:
        return explicit, "stated by the reader"
    words = _LOWER_WORDS if side == "lower" else _UPPER_WORDS
    found = {inclusive for inclusive, pattern in words if pattern.search(_quotes(fact))}
    if len(found) == 1:
        return found.pop(), "boundary wording in the cited text"
    return None, None


def weekday_wording(fact: ScheduleFact) -> bool:
    return bool(_WEEKDAY_WORDING.search(_quotes(fact) + " " + fact.what))


def unsupported_wording(fact: ScheduleFact) -> list[str]:
    """Source operators named by the fact's own wording that nothing executes."""
    text = _quotes(fact) + " " + fact.what
    return [name for name, pattern in _UNSUPPORTED_WORDING if pattern.search(text)]


def _amount(duration: Duration, sign: int = 1) -> TemporalAmount:
    return TemporalAmount(value=sign * duration.value, unit=duration.unit)


class ConstraintBuilder:
    """Needs the compiler's reference resolver and evidence citer."""

    def __init__(self, resolve: Callable, cite: Callable[[ScheduleFact], list[str]]):
        self.resolve = resolve
        self.cite = cite

    def value(self, fact: ScheduleFact):
        timing = fact.timing
        if timing is None or timing.reference is None:
            raise NotExact("the statement does not say what it counts from")
        reference = self.resolve(timing.reference, fact)
        if isinstance(reference, AnchorReference):
            return AnchorValue(code=reference.code, accepts=reference.accepts)
        if isinstance(reference, EventReference):
            occurrence = timing.reference.occurrence
            if occurrence is None:
                raise NotExact(f"which occurrence of {timing.reference.name!r} is meant is not stated")
            return EventValue(event_code=reference.event_code, occurrence=OccurrenceSelector(type=occurrence),
                              accepts=getattr(reference, "accepts", "ACTUAL"))
        raise NotExact("the reference cannot be used in a combined restriction")

    @staticmethod
    def _shift(value, duration: Duration | None, sign: int = 1):
        if duration is None or duration.value == 0:
            return value
        return ShiftValue(reference=value, amount=_amount(duration, sign))

    def _inclusive(self, fact: ScheduleFact, side: str) -> bool:
        inclusive, _how = stated_inclusive(fact, side)
        if inclusive is None:
            raise NotExact(f"the protocol does not say whether the {side} boundary day itself is allowed")
        return inclusive

    def nodes(self, fact: ScheduleFact) -> list[Any]:
        """Leaf constraint nodes for one statement (each with its evidence)."""
        timing = fact.timing
        if timing is None:
            raise NotExact("the statement gives no timing")
        pattern = timing.pattern
        if pattern in UNSUPPORTED_PATTERNS:
            return [self.unsupported(fact, UNSUPPORTED_PATTERNS[pattern])]
        if fact.repeat is not None:
            raise NotExact("a repeating statement cannot be one restriction on a single occurrence")
        evidence = self.cite(fact)
        base = f"{fact.id}"
        if pattern == "ABSOLUTE" and timing.value is not None:
            return [AtConstraint(id=f"{base}/at", evidence_refs=evidence,
                                 value=LiteralDateValue(value=timing.value, precision="DAY"))]
        value = self.value(fact)
        duration = timing.duration or timing.offset

        def lower(amount, sign=1):
            return LowerBoundConstraint(id=f"{base}/lower", evidence_refs=evidence,
                                        value=self._shift(value, amount, sign),
                                        inclusive=self._inclusive(fact, "lower"))

        def upper(amount, sign=1):
            return UpperBoundConstraint(id=f"{base}/upper", evidence_refs=evidence,
                                        value=self._shift(value, amount, sign),
                                        inclusive=self._inclusive(fact, "upper"))

        if pattern == "OFFSET" and timing.offset is not None:
            if fact.window is not None and (fact.window.before or fact.window.after):
                raise NotExact("a target with its own window cannot yet be combined as one restriction")
            return [AtConstraint(id=f"{base}/at", evidence_refs=evidence, value=self._shift(value, timing.offset))]
        if pattern == "WITHIN_BEFORE" and duration is not None:
            return [lower(duration, -1), upper(None)]
        if pattern == "WITHIN_AFTER" and duration is not None:
            return [lower(None), upper(duration)]
        if pattern == "NO_LATER_THAN" and duration is not None:
            return [upper(duration)]
        if pattern == "NO_EARLIER_THAN" and duration is not None:
            return [lower(duration)]
        if pattern == "RANGE" and timing.start is not None and timing.end is not None:
            return [lower(timing.start), upper(timing.end)]
        raise NotExact(f"{pattern} timing cannot be written as a combined restriction")

    def unsupported(self, fact: ScheduleFact, operator: str) -> UnsupportedConstraint:
        timing = fact.timing
        return UnsupportedConstraint(
            id=f"{fact.id}/unsupported/{operator.lower()}", evidence_refs=self.cite(fact),
            family="CALENDAR" if operator != "BUSINESS_DAY_OFFSET" else "TIMING",
            source_operator=operator, source_text=_quotes(fact)[:500],
            details={"fact_id": fact.id,
                     "timing": timing.model_dump(mode="json", by_alias=True) if timing else None,
                     "repeat": fact.repeat.model_dump(mode="json", by_alias=True) if fact.repeat else None})

    @staticmethod
    def all_of(items: list[Any], node_id: str):
        return AllConstraint(id=node_id, items=items)

    @staticmethod
    def any_of(items: list[Any], node_id: str):
        return AnyConstraint(id=node_id, items=items)
