"""Deterministic source checks; none of these infer patient dates."""
from __future__ import annotations

import re
import unicodedata

from app.extraction.graph import DocumentPage
from app.extraction.llm_client import LLMClient


def normalized_quote(text: str) -> str:
    # PDF text often contains ligatures and soft hyphens. Keep numbers, signs,
    # punctuation and word order intact: they can change a timing rule's meaning.
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def page_packet(client: LLMClient, pages: list[DocumentPage]) -> LLMClient:
    factory = getattr(client, "for_document", None)
    if factory is None:
        # Scripted test clients have no document attachment to narrow.
        return client
    text = "\n".join(f"--- ORIGINAL PHYSICAL PAGE {page.page_number} ---\n{page.text}"
        + ("\n[Read the attached page image; embedded text is incomplete.]" if page.text_status != "text" else "")
        for page in pages)
    return factory(document_text=text, page_numbers=[page.page_number for page in pages])


def referenced_pages(pages: list[DocumentPage], selected: set[int]) -> tuple[set[int], list[dict]]:
    """Follow explicit appendix/annex/table references to visible headings.

    Missing references are reported, never synthesized. This is a backstop to
    model discovery, not a universal cross-reference parser.
    """
    reference = re.compile(r"\b(?:see|refer\s+to|shown\s+in|summari[sz]ed\s+in)\s+(?:the\s+)?"
        r"(?P<kind>appendix|annex|table)\s+(?P<identifier>[A-Z0-9]+(?:[.-][A-Z0-9]+)*)\b", re.I)
    resolved, missing, checked = set(selected), [], set()
    # Follow links transitively; a table's footnotes can point to an appendix.
    while True:
        pending = [page for page in pages if page.page_number in resolved and page.page_number not in checked]
        if not pending:
            break
        for page in pending:
            checked.add(page.page_number)
            for match in reference.finditer(page.text):
                label = f"{match['kind']} {match['identifier']}"
                heading = re.compile(r"^\s*" + re.escape(match["kind"]) + r"\s+"
                    + re.escape(match["identifier"]) + r"(?=\s|[:.)-]|$)", re.I | re.M)
                destinations = {other.page_number for other in pages if heading.search(other.text)}
                if destinations:
                    resolved |= destinations
                else:
                    missing.append({"page": page.page_number, "reference": label})
    return resolved, list({(item["page"], item["reference"].casefold()): item for item in missing}.values())


def collective_count_visit(label: str) -> bool:
    """Reject a population count misread as a participant encounter.

    Deliberately narrow: cohort identifiers and real dates like 'Day 7' are not
    counts. Do not drop an entire mixed page or patient-specific stopping rules.
    """
    return bool(re.fullmatch(r"\s*\d+\s+(?:patients|participants|subjects)\s+"
        r"(?:evaluated|enrolled|randomi[sz]ed|analy[sz]ed)(?:\s*\([^)]*\))?\s*", label, re.I))
