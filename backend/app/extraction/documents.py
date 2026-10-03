"""Turns a stored protocol document into pages an extraction engine can read."""

from __future__ import annotations

import re

from .contracts import DocumentPage
from .llm_client import ExtractionModelError

#: Reject oversized input explicitly; silently truncating the document can drop
#: visits or referenced footnotes.
MAX_DOCUMENT_CHARS = 600_000


def pages_from_pdf(pdf_bytes: bytes) -> list[DocumentPage]:
    """Read a PDF into per-page text.

    Page numbers are 1-based and preserved, because every piece of evidence a
    reviewer checks points at a page. Each page is also classified by how much
    embedded text it has, so scanned pages can be routed to an image reader.
    """
    from protocol_document_index import _extract_pdf_text_pages, _text_status

    pages: list[DocumentPage] = []
    for index, text in enumerate(_extract_pdf_text_pages(pdf_bytes)):
        if looks_scrambled(text):
            # A font without a usable character map extracts as nonsense
            # ("6HOLQH[RU" for "Selinexor"). That is not text: the page must be
            # read from its image, and the nonsense must not reach the model.
            pages.append(DocumentPage(page_number=index + 1, text="", text_status="image_or_empty"))
        else:
            pages.append(DocumentPage(page_number=index + 1, text=text, text_status=_text_status(text)))
    return pages


_WORD = re.compile(r"[A-Za-z]{3,}")


def looks_scrambled(text: str) -> bool:
    """Whether a page's embedded text is unreadable character soup.

    Real prose in any Latin-script language has a vowel in nearly every word;
    text extracted through a broken font map does not.
    """
    words = _WORD.findall(text)
    if len(words) < 20:
        return False
    without_vowel = sum(1 for word in words if not re.search(r"[aeiouyAEIOUY]", word))
    return without_vowel / len(words) > 0.3


def pages_from_text(text: str) -> list[DocumentPage]:
    """Split plain text on form feeds, falling back to a single page."""
    parts = text.split("\f") if "\f" in text else [text]
    return [
        DocumentPage(page_number=index + 1, text=part)
        for index, part in enumerate(parts)
    ]


_NO_TEXT_MARKER = (
    "\n[This page has little or no embedded text - it is a scanned or "
    "image-only page. Read it from the attached PDF's own page image "
    "instead of from this text block.]"
)


def document_text(pages: list[DocumentPage]) -> str:
    """The document as a model sees it, with page markers it can cite."""
    rendered: list[str] = []
    used = 0
    for page in pages:
        marker = _NO_TEXT_MARKER if page.text_status != "text" else ""
        block = f"\n--- page {page.page_number} ---\n{page.text}{marker}"
        if used + len(block) > MAX_DOCUMENT_CHARS:
            raise ExtractionModelError(
                f"Document exceeds the extraction text limit at page {page.page_number}. "
                "No pages were silently omitted."
            )
        rendered.append(block)
        used += len(block)
    return "".join(rendered)


async def load_pages(document_uri: str) -> tuple[list[DocumentPage], bytes | None]:
    """Fetch a stored protocol document and turn it into pages.

    Returns the raw PDF bytes alongside the pages (``None`` for a plain-text
    upload) so an engine can use the native PDF without fetching it again.
    """
    from storage import get_storage

    key = document_uri.split("://", 1)[-1] if "://" in document_uri else document_uri
    data, content_type = await get_storage().open(key)
    if "pdf" in (content_type or "").lower() or document_uri.lower().endswith(".pdf"):
        return pages_from_pdf(data), data
    return pages_from_text(data.decode("utf-8", errors="replace")), None
