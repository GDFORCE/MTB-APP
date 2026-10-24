"""Steps 1-2 of the kit: read the PDF, keep table columns, find schedule tables, choose pages."""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Callable

SOA_RE = re.compile(
    r"schedule of (activities|assessments|events|procedures)|time and events|study calendar|"
    r"sampling schedule|pharmacokinetic sampling", re.I)

KEY = re.compile(
    r"schedule of (activities|assessments|events|procedures)|time and events|study calendar|visit|window|"
    r"\bday\s*-?\d+|\bweek\s*\d+|cycle|screening|follow[- ]?up|randomi[sz]|\barm\b|cohort|\bpart [a-d]\b|"
    r"period|washout|run-in|extension|surgery|infusion|progression|end of (treatment|study)|±|\+/-|"
    r"last dose|baseline|unscheduled|discontinu|pre-?dose|post-?dose", re.I)

_X_MARK = re.compile(r"(^|\s)[X✓✔x]\d{0,2}(?=\s|$)")


class PdfReadError(ValueError):
    """The upload could not be read as a PDF."""


def _round(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def layout_text(page: Any) -> str:
    """One page as text with column positions kept (character column = round(x / 4))."""
    rows: dict[int, list[tuple[float, str]]] = {}
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                y = _round(span["origin"][1] / 3)
                rows.setdefault(y, []).append((span["origin"][0], text))
    out: list[str] = []
    for _, items in sorted(rows.items()):
        items.sort(key=lambda item: item[0])
        line = ""
        for x, text in items:
            col = _round(x / 4)
            if len(line) < col:
                line += " " * (col - len(line))
            elif line and not line.endswith(" "):
                line += " "
            line += text
        out.append(line.rstrip())
    return "\n".join(out)


def plain_text(page: Any) -> str:
    text = page.get_text("text")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def x_count(page_text: str) -> int:
    return len(_X_MARK.findall(page_text))


def find_schedule_table_pages(pages: list[str], limit: int = 10) -> list[int]:
    """1-based pages that carry 5+ tick marks and name a schedule or continue one."""
    found: set[int] = set()
    for i, text in enumerate(pages):
        if x_count(text) >= 5 and (SOA_RE.search(text) or (i + 1) in found):
            found.add(i + 1)
            if i + 1 < len(pages) and x_count(pages[i + 1]) >= 5:
                found.add(i + 2)
    return sorted(found)[:limit]


def is_scanned(pages: list[str]) -> bool:
    total = sum(len(p) for p in pages)
    return total < 120 * len(pages) and total < 4000


def select_pages(pages: list[str], budget: int) -> list[int]:
    """0-based pages to send when the protocol exceeds ``budget`` characters."""
    if sum(len(p) + 16 for p in pages) <= budget:
        return list(range(len(pages)))
    must = {i for i in range(4) if i < len(pages)}
    for i, text in enumerate(pages):
        if re.search("synopsis", text, re.I):
            must.update(i + k for k in range(3) if i + k < len(pages))
    scored = sorted(
        ((len(KEY.findall(text)) * 1000 / max(400, len(text)), i) for i, text in enumerate(pages)),
        key=lambda item: -item[0])
    pick: set[int] = set()
    used = 0
    for i in sorted(must):
        if used + len(pages[i]) < budget:
            pick.add(i)
            used += len(pages[i]) + 16
    for _, i in scored:
        if i in pick or used + len(pages[i]) > budget:
            continue
        pick.add(i)
        used += len(pages[i]) + 16
    return sorted(pick)


def render_page_images(doc: Any, page_numbers: list[int], quality: int = 82) -> list[dict[str, Any]]:
    """JPEG renders (long edge ~1300 px) -> [{page, mediaType, base64}]."""
    import fitz

    out = []
    for n in page_numbers:
        if n < 1 or n > doc.page_count:
            continue
        page = doc[n - 1]
        rect = page.rect
        scale = min(2.5, 1300 / max(rect.width, rect.height, 1))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        out.append({
            "page": n, "mediaType": "image/jpeg",
            "base64": base64.b64encode(pix.tobytes("jpeg", jpg_quality=quality)).decode("ascii"),
        })
    return out


def prepare_protocol_input(
    pdf_bytes: bytes, *, budget: int = 170_000, full_text_max_chars: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Everything the model step needs, plus the page images for the cross-check."""
    import fitz

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:  # noqa: BLE001
        raise PdfReadError("This file could not be read as a PDF.") from error
    if doc.page_count == 0:
        raise PdfReadError("This PDF has no pages.")
    pages: list[str] = []
    layout: list[str] = []
    for i in range(doc.page_count):
        page = doc[i]
        pages.append(plain_text(page))
        layout.append(layout_text(page))
        if on_progress:
            on_progress(i + 1, doc.page_count)
    table_pages = find_schedule_table_pages(pages)
    table_idx = {n - 1 for n in table_pages}
    scanned = is_scanned(pages)
    # Every page is sent whenever the protocol fits ``full_text_max_chars`` (env
    # KIT_FULL_TEXT_MAX_CHARS, default 650,000; 0 = always use the kit's page
    # selection): a skipped page can hold visit order, a footnote or an arm. The
    # kit's selection stays available as ``selectedText``, the fallback when the
    # model rejects the full text as too large.
    if full_text_max_chars is None:
        full_text_max_chars = int(os.getenv("KIT_FULL_TEXT_MAX_CHARS") or 650_000)
    send_everything = 0 < sum(len(p) + 16 for p in pages) <= full_text_max_chars
    picked: list[int] = []
    selected: list[int] = []
    if not scanned:
        selected = select_pages(pages, budget)
        selected += [i for i in sorted(table_idx) if i not in selected]
        selected.sort()
        picked = list(range(len(pages))) if send_everything else selected

    def page_block(i: int) -> str:
        if i in table_idx:
            return f"[[Page {i + 1} · schedule table, column layout preserved]]\n{layout[i]}"
        return f"[[Page {i + 1}]]\n{pages[i]}"

    if scanned:
        text = ("(The PDF has no text layer. The attached images are its first pages. "
                "Read the schedule from them.)")
        table_text = text
        selected_text = text
    else:
        text = "\n\n".join(page_block(i) for i in picked)
        selected_text = "\n\n".join(page_block(i) for i in selected)
        table_text = (
            "\n\n".join(f"[[Page {n}]]\n{layout[n - 1]}" for n in table_pages)
            if table_pages else "\n\n".join(page_block(i) for i in selected[:8]))
    result = {
        "pageCount": doc.page_count, "tablePages": table_pages, "picked": picked, "scanned": scanned,
        "text": text, "selectedText": selected_text, "fullText": send_everything and not scanned,
        "tableText": table_text,
        "tableImages": render_page_images(doc, table_pages[:5]),
        "scanImages": render_page_images(doc, [n for n in range(1, 9) if n <= doc.page_count]) if scanned else [],
    }
    doc.close()
    return result
