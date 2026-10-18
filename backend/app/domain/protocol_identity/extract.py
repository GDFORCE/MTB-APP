"""Deterministic, cited identity extraction from page text (P2-02).

No model is called. Every identifier carries its page and the line it came from,
and is marked authoritative only when it states THIS protocol's own identity:

* it sits in the front matter (``FRONT_MATTER_PAGES`` first pages) or on a line
  labelled as an identifier ("ClinicalTrials.gov Identifier:", "Protocol Number:"),
* and it is not inside a References / Bibliography / Appendix zone, and its line
  is not a citation ("see NCT...", "[12]", "NCT... reported").

An identifier outside those rules is still recorded (so a reviewer can see it)
but never selected as the document's identity. Absence of any authoritative
identifier is reported as absence - never filled in.

Identity-extraction budget: zero provider calls. Every page is scanned once with
regular expressions (O(total characters)). Pages without usable text contribute
nothing; they are listed in the fingerprint coverage, not guessed at.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from .models import (
    DocumentIdentityFacts, ExpectedClaim, ClaimSource, IdentifierEvidence, IdentifierKind,
    TextEvidence,
)

FRONT_MATTER_PAGES = 3
_SNIPPET = 160

_NCT = re.compile(r"\bNCT\s?-?\s?(\d{8})\b", re.IGNORECASE)
_CTRI = re.compile(r"\bCTRI\s*/\s*(\d{4})\s*/\s*(\d{2,3})\s*/\s*(\d{6})\b", re.IGNORECASE)
_EUDRACT = re.compile(r"\b(\d{4}-\d{6}-\d{2})\b")
_ISRCTN = re.compile(r"\bISRCTN\s?(\d{8})\b", re.IGNORECASE)
_PROTOCOL_NUMBER = re.compile(
    r"^\s*(?:sponsor\s+|study\s+)?protocol\s+(?:number|no\.?|id|identifier|code)\s*[:#\-]\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-_/.]{2,40})", re.IGNORECASE | re.MULTILINE)
_LABEL_WORDS = re.compile(
    r"(identifier|registry|registration|nct\s*number|trial\s*number|eudract|ctri|isrctn)",
    re.IGNORECASE)
_CITATION = re.compile(
    r"(\bsee\b|\bcf\.|\bref(?:erence)?s?\b|\bcited\b|\breported\b|\bpublished\b|\[\d+\]|"
    r"\bet\s+al\b|\bprevious(?:ly)?\b)", re.IGNORECASE)
_ZONE_START = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(references|bibliography|literature\s+cited|appendix(?:\s+[A-Z0-9]+)?)\b",
    re.IGNORECASE)

_VERSION = re.compile(
    r"^\s*(?:protocol\s+)?(?:version|amendment)\s*(?:no\.?|number|#)?\s*[:\-]?\s*"
    r"(\d+(?:\.\d+)*[A-Za-z]?|[A-Z])\b", re.IGNORECASE | re.MULTILINE)
_DATE_LABEL = re.compile(
    r"^\s*(?:version\s+date|date\s+of\s+(?:version|amendment|issue)|protocol\s+date|"
    r"amendment\s+date|issue\s+date|date)\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_TITLE = re.compile(r"^\s*(?:protocol\s+)?title\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_SPONSOR = re.compile(r"^\s*sponsor\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_INTERVENTION = re.compile(
    r"^\s*(?:investigational\s+(?:medicinal\s+)?(?:product|drug)|study\s+drug|intervention)"
    r"\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

_MONTHS = {name: index for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}


def normalize_identifier(kind: IdentifierKind, value: str) -> str:
    """Comparison form. Registry ids: upper case, no separators inside the id.
    Sponsor protocol numbers: upper case without whitespace/punctuation noise."""
    text = value.strip().upper()
    if kind is IdentifierKind.REGISTRY_CTRI:
        return re.sub(r"\s+", "", text)
    if kind is IdentifierKind.REGISTRY_NCT:
        digits = re.sub(r"\D", "", text)
        return f"NCT{digits}"
    if kind is IdentifierKind.REGISTRY_ISRCTN:
        digits = re.sub(r"\D", "", text)
        return f"ISRCTN{digits}"
    if kind is IdentifierKind.REGISTRY_EUDRACT:
        return text
    return re.sub(r"[\s\-_./]", "", text)


def claim_from_text(value: str, source: ClaimSource) -> ExpectedClaim | None:
    """An expected identity from something a person typed. A registry pattern is
    a registry id; anything else non-empty is a sponsor protocol number."""
    text = (value or "").strip()
    if not text:
        return None
    for kind, pattern in _REGISTRY_PATTERNS:
        found = pattern.search(text)
        if found:
            return ExpectedClaim(
                kind=kind, value=text, normalized=_normalized_from_match(kind, found), source=source)
    return ExpectedClaim(
        kind=IdentifierKind.PROTOCOL_NUMBER, value=text,
        normalized=normalize_identifier(IdentifierKind.PROTOCOL_NUMBER, text), source=source)


def claim_from_filename(filename: str) -> ExpectedClaim | None:
    """A registry identifier printed in the file name, as a weak claim. A file
    name with no identifier yields NO claim: a name is never an invented identity."""
    for kind, pattern in _REGISTRY_PATTERNS:
        found = pattern.search(filename or "")
        if found:
            return ExpectedClaim(
                kind=kind, value=found.group(0), normalized=_normalized_from_match(kind, found),
                source=ClaimSource.FILENAME)
    return None


_REGISTRY_PATTERNS: list[tuple[IdentifierKind, re.Pattern[str]]] = [
    (IdentifierKind.REGISTRY_NCT, _NCT),
    (IdentifierKind.REGISTRY_CTRI, _CTRI),
    (IdentifierKind.REGISTRY_ISRCTN, _ISRCTN),
    (IdentifierKind.REGISTRY_EUDRACT, _EUDRACT),
]


def _normalized_from_match(kind: IdentifierKind, match: re.Match[str]) -> str:
    if kind is IdentifierKind.REGISTRY_NCT:
        return f"NCT{match.group(1)}"
    if kind is IdentifierKind.REGISTRY_CTRI:
        return f"CTRI/{match.group(1)}/{match.group(2)}/{match.group(3)}"
    if kind is IdentifierKind.REGISTRY_ISRCTN:
        return f"ISRCTN{match.group(1)}"
    return match.group(1)


def _snippet(line: str) -> str:
    text = " ".join(line.split())
    return text if len(text) <= _SNIPPET else text[:_SNIPPET - 1] + "…"


def parse_date(text: str) -> date | None:
    """ISO, "12 March 2024" and "March 12, 2024" only. Numeric day/month orders
    are ambiguous and are NOT guessed."""
    value = text.strip().rstrip(".")
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    try:
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        dmy = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", value)
        if dmy:
            month = _month(dmy.group(2))
            if month:
                return date(int(dmy.group(3)), month, int(dmy.group(1)))
        mdy = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", value)
        if mdy:
            month = _month(mdy.group(1))
            if month:
                return date(int(mdy.group(3)), month, int(mdy.group(2)))
    except ValueError:
        return None
    return None


def _month(name: str) -> int | None:
    lowered = name.lower()
    for full, index in _MONTHS.items():
        if full == lowered or (len(lowered) >= 3 and full.startswith(lowered)):
            return index
    return None


def _printed_number_pattern(value: str) -> re.Pattern[str] | None:
    """The expected sponsor protocol number as it may be printed: tokens joined by
    optional separators, never inside a longer alphanumeric run."""
    tokens = [token for token in re.split(r"[\s\-_./]+", value.strip()) if token]
    if not tokens or len("".join(tokens)) < 4:
        return None                     # too short to be evidence of anything
    body = r"[\s\-_./]*".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def extract_identity(
    pages: Iterable[tuple[int, str]], *, expected_numbers: Iterable[str] = (),
) -> DocumentIdentityFacts:
    """``pages``: (page_number, text) in document order.

    ``expected_numbers``: sponsor protocol numbers the caller says this document
    is. Each is searched for in the front matter (outside citations and
    reference zones); a number printed there, even unlabelled ("PROTOCOL EXT-1"),
    is recorded as the document naming itself. Nothing is searched for when no
    number is expected - an unlabelled string is never promoted to an identity
    on its own.
    """
    rows = sorted(((number, text or "") for number, text in pages), key=lambda item: item[0])
    facts = DocumentIdentityFacts()
    printed = [(value, pattern) for value in expected_numbers
               if (pattern := _printed_number_pattern(value)) is not None]
    in_zone = False
    seen: set[tuple[str, int, str]] = set()

    for number, text in rows:
        for line in text.splitlines():
            if _ZONE_START.match(line) and number > 1:
                in_zone = True
            labelled = bool(_LABEL_WORDS.search(line))
            cited = bool(_CITATION.search(line))
            if not in_zone and not cited and number <= FRONT_MATTER_PAGES:
                for value, pattern in printed:
                    found = pattern.search(line)
                    normalized = normalize_identifier(IdentifierKind.PROTOCOL_NUMBER, value)
                    if found and (normalized, number, line) not in seen:
                        seen.add((normalized, number, line))
                        facts.identifiers.append(IdentifierEvidence(
                            kind=IdentifierKind.PROTOCOL_NUMBER, value=found.group(0),
                            normalized=normalized, page=number, snippet=_snippet(line),
                            authoritative=True,
                            context="the expected protocol number is printed in the front matter"))
            for kind, pattern in _REGISTRY_PATTERNS:
                for match in pattern.finditer(line):
                    if kind is IdentifierKind.REGISTRY_EUDRACT and not re.search(
                            r"eudract", line, re.IGNORECASE):
                        continue            # a bare 0000-000000-00 is not evidence
                    normalized = _normalized_from_match(kind, match)
                    key = (normalized, number, line)
                    if key in seen:
                        continue
                    seen.add(key)
                    front = number <= FRONT_MATTER_PAGES
                    if in_zone:
                        authoritative, why = False, "inside a References or Appendix section"
                    elif cited:
                        authoritative, why = False, "the line reads as a citation"
                    elif front or labelled:
                        authoritative = True
                        why = "front matter" if front and not labelled else "labelled identifier"
                    else:
                        authoritative, why = False, "unlabelled mention outside the front matter"
                    facts.identifiers.append(IdentifierEvidence(
                        kind=kind, value=match.group(0), normalized=normalized, page=number,
                        snippet=_snippet(line), authoritative=authoritative, context=why))
        for match in _PROTOCOL_NUMBER.finditer(text):
            value = match.group(1).rstrip(".,;")
            normalized = normalize_identifier(IdentifierKind.PROTOCOL_NUMBER, value)
            end = text.find("\n", match.end())
            line = text[text.rfind("\n", 0, match.start()) + 1:end if end != -1 else len(text)]
            # A labelled "Protocol Number:" line is the document naming itself, so it
            # counts near the front of the document and never inside a reference zone.
            authoritative = not in_zone and number <= FRONT_MATTER_PAGES + 2
            facts.identifiers.append(IdentifierEvidence(
                kind=IdentifierKind.PROTOCOL_NUMBER, value=value, normalized=normalized,
                page=number, snippet=_snippet(line), authoritative=authoritative,
                context=("labelled protocol number" if authoritative
                         else "labelled protocol number outside the front matter / in a reference zone")))
        if number <= FRONT_MATTER_PAGES:
            _front_matter_fields(facts, number, text)

    return facts


def _first(pattern: re.Pattern[str], text: str) -> tuple[str, str] | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip(), _snippet(match.group(0))


def _front_matter_fields(facts: DocumentIdentityFacts, page: int, text: str) -> None:
    for attribute, pattern in (
            ("title", _TITLE), ("sponsor", _SPONSOR), ("intervention", _INTERVENTION),
            ("version_label", _VERSION)):
        if getattr(facts, attribute) is None:
            found = _first(pattern, text)
            if found:
                setattr(facts, attribute, TextEvidence(value=found[0], page=page, snippet=found[1]))
    if facts.version_date is None:
        for match in _DATE_LABEL.finditer(text):
            parsed = parse_date(match.group(1))
            if parsed:
                facts.version_date = parsed
                facts.version_date_evidence = TextEvidence(
                    value=parsed.isoformat(), page=page, snippet=_snippet(match.group(0)))
                break
