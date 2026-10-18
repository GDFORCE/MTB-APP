"""Versioned, conservative full-document text normalization and fingerprint (P2-02).

``protocol-text.v1`` removes only differences that cannot change what a protocol
says:

* Unicode NFC, then explicit single-character mappings: typographic ligatures
  (ﬁ ﬂ ...), non-breaking / thin / zero-width spaces, soft hyphen (removed),
  and the dash and minus variants (en/em dash, minus sign, non-breaking hyphen)
  folded to ``-``;
* line-ending and whitespace runs, INCLUDING page boundaries, collapsed to one
  space - so re-paginating or re-flowing the same text gives the same hash;
* leading / trailing whitespace.

It deliberately does NOT: change case, strip digits or punctuation, drop running
headers/footers or page numbers, rejoin hyphenated line breaks, or fold
compatibility characters (NFKC would turn "²" into "2"). Identifiers, amendment
dates, dosages and numbers therefore always survive and always affect the hash.

OCR/scanned pages carry no embedded text here. A document is
``verified_full_text`` only when EVERY page supplied text; anything else gets a
fingerprint for provenance only and can never prove a duplicate.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

from .models import TextCoverage, TextFingerprint

NORMALIZATION_VERSION = "protocol-text.v1"
#: Below this many normalized characters the text is too little to call a
#: document fingerprint (a title page alone is not a protocol).
MIN_VERIFIED_CHARS = 200

_MAP = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "﻿": "", "­": "",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
}
_TABLE = {ord(key): value for key, value in _MAP.items()}
_SPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text).translate(_TABLE)).strip()


def fingerprint_pages(pages: Iterable[tuple[int, str, bool]]) -> TextFingerprint:
    """``pages``: (page_number, text, has_usable_text) in document order."""
    rows = list(pages)
    without = [number for number, text, usable in rows if not usable or not text.strip()]
    joined = normalize_text(" ".join(text for _, text, usable in rows if usable))
    complete = bool(rows) and not without and len(joined) >= MIN_VERIFIED_CHARS
    return TextFingerprint(
        algorithm_version=NORMALIZATION_VERSION,
        sha256=hashlib.sha256(joined.encode("utf-8")).hexdigest() if joined else None,
        char_count=len(joined),
        coverage=TextCoverage(
            pages_total=len(rows), pages_with_text=len(rows) - len(without),
            pages_without_text=without),
        verified_full_text=complete)
