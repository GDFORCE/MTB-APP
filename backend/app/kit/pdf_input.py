"""Steps 1-2 of the kit: read the PDF, find schedule tables, choose pages.

Schedule tables are found by their structure, never by matching words: ruled
tables from their drawn cells (sent as grids), tables laid out with spacing only
from lined-up marks (read from the page image by the model, see kit/tables.py).
"""

from __future__ import annotations

import base64
import os
import re
import threading
from typing import Any, Callable, Sequence

#: PyMuPDF must not be used from two threads at once (uploads are read in a thread pool):
#: concurrent use made find_tables fail silently. Every PyMuPDF call here holds this lock.
PDF_LOCK = threading.RLock()

# Used only by ``select_pages`` (legacy page selection, when a character cap is set).
# Schedule tables are never found by text matching.
KEY = re.compile(
    r"schedule of (activities|assessments|events|procedures)|time and events|study calendar|visit|window|"
    r"\bday\s*-?\d+|\bweek\s*\d+|cycle|screening|follow[- ]?up|randomi[sz]|\barm\b|cohort|\bpart [a-d]\b|"
    r"period|washout|run-in|extension|surgery|infusion|progression|end of (treatment|study)|±|\+/-|"
    r"last dose|baseline|unscheduled|discontinu|pre-?dose|post-?dose", re.I)


class PdfReadError(ValueError):
    """The upload could not be read as a PDF."""


# Symbol-font characters arrive in the Unicode private-use area (U+F0xx, where xx
# is the font's own code), which a model cannot read: a Wingdings tick is
# U+F0FC, the Symbol font's ± is U+F0B1. These two common fonts have published
# encodings, so their characters are simply translated. This is only a shortcut:
# anything else unreadable, from any font, goes through the symbol legend below,
# which shows the model the symbol as printed instead of relying on a list.
_WINGDINGS = {
    0xFC: "✓", 0xFB: "✗", 0xFD: "☒", 0xFE: "☑", 0x6C: "●", 0x6E: "■", 0x6F: "□", 0x71: "❑",
    0x75: "◆", 0xA1: "○", 0xA7: "▪", 0xD8: "➢",
}
_SYMBOL = {
    **{0x41 + i: ch for i, ch in enumerate("ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ")},
    **{0x61 + i: ch for i, ch in enumerate("αβχδεφγηιϕκλμνοπθρστυϖωξψζ")},
    **{code: chr(code) for code in (*range(0x20, 0x40), 0x5B, 0x5D, 0x5F, 0x7B, 0x7C, 0x7D)
       if code not in (0x22, 0x24, 0x27, 0x2A)},
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x7E: "∼",
    0x2D: "−", 0xA3: "≤", 0xA5: "∞", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°",
    0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBB: "≈", 0xD7: "⋅",
}


def symbol_map(page: Any) -> dict[int, str]:
    """str.translate table for the page's Wingdings / Symbol private-use characters."""
    table: dict[int, str] = {}
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = str(span.get("font", "")).lower().replace(" ", "").replace("-", "")
                if "wingdings" in font:
                    codes = None if re.search(r"wingdings[23]", font) else _WINGDINGS
                else:
                    codes = _SYMBOL if "symbol" in font else None
                if not codes:
                    continue
                for ch in span.get("text", ""):
                    code = ord(ch)
                    if 0xF000 <= code <= 0xF0FF and (code & 0xFF) in codes:
                        table.setdefault(code, codes[code & 0xFF])
    return table


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


# ── Schedule tables found by their structure ─────────────────────────────
# PyMuPDF finds ruled tables from the lines drawn on the page and returns their
# cells. A table counts as a schedule when one short mark (X, ○, ✓, •, a
# Wingdings tick, "Y"...) repeats across rows and columns. Tables of contents,
# numeric results, abbreviation lists and synopsis grids do not do that.

MIN_MARKS = 5
#: Fewer drawn items than this cannot make a ruled grid; skipping those pages
#: saves most of the table-search time on long protocols.
MIN_DRAWINGS = 4
TABLE_IMAGE_LIMIT = 20
_NOT_A_MARK = re.compile(r"^[\d.,:;%/\-–—−±<>=()_|]+$")
_FOOTNOTE_SUFFIX = re.compile(r"^[0-9*†‡§,]+$")
_LETTER_SUFFIX = re.compile(r"^.[a-z]$")
GRID_LABEL = "schedule table as a grid"
ALIGNED_LABEL = "schedule table without ruled lines, column layout kept"
READ_LABEL = "schedule table read from the page image"


def _clean_cell(text: Any) -> str:
    return " ".join(str(text).split())


def mark_word(cell: Any) -> str | None:
    """The mark a cell holds, if it holds a short one: any symbol, letter or short word except
    numbers, separators and lowercase prose. A footnote number or sign glued to a mark counts
    as the mark ("X4", "X*" are "X"); a glued footnote letter ("Xa") is decided by
    ``merge_letter_footnotes``, since "No" is a word, not "N" with footnote "o".
    No list of allowed marks, so a new symbol works too."""
    text = _clean_cell(cell or "")
    word = text.split(" ", 1)[0]
    if (not word or len(text) > 20 or len(word) > 3 or _NOT_A_MARK.match(word)
            or (len(word) > 1 and word.isalpha() and word.islower())):
        return None
    return word[0] if _FOOTNOTE_SUFFIX.match(word[1:]) else word


def merge_letter_footnotes(words: list[str]) -> dict[str, str]:
    """word -> the mark it is: "Xa" is "X" with footnote a when the same table also has a bare
    "X"; otherwise it stays itself ("No" in a Yes/No table)."""
    present = set(words)
    return {w: (w[0] if _LETTER_SUFFIX.match(w) and w[0] in present else w) for w in present}


def table_marks(rows: Sequence[Sequence[str | None]], min_cols: int = 2) -> dict[str, int]:
    """Marks repeated over 2+ rows and ``min_cols``+ columns -> how often each appears."""
    cells = [(r, c, mark_word(cell)) for r, row in enumerate(rows) for c, cell in enumerate(row)]
    as_mark = merge_letter_footnotes([w for _, _, w in cells if w is not None])
    seen: dict[str, list[Any]] = {}
    for r, c, word in cells:
        if word is None:
            continue
        entry = seen.setdefault(as_mark[word], [0, set(), set()])
        entry[0] += 1
        entry[1].add(r)
        entry[2].add(c)
    return {w: n for w, (n, rs, cs) in seen.items() if len(rs) >= 2 and len(cs) >= min_cols}


def _legend_word(word: str) -> bool:
    """A mark worth showing as printed: one character, or one that is not plain letters
    (a word like "Day" or "PK" reads correctly as text)."""
    return len(word) == 1 or not word.isalnum()


def mark_score(rows: Sequence[Sequence[str | None]]) -> int:
    """How many times the most repeated mark appears (see ``table_marks``)."""
    return max(table_marks(rows).values(), default=0)


def _boundaries(values: list[float], tol: float = 1.5) -> list[float]:
    out: list[float] = []
    for value in sorted(values):
        if not out or value - out[-1] > tol:
            out.append(value)
    return out


def _nearest(bounds: list[float], value: float, tol: float = 3.0) -> int | None:
    i = min(range(len(bounds)), key=lambda k: abs(bounds[k] - value))
    return i if abs(bounds[i] - value) <= tol else None


def table_grid(table: Any) -> list[list[str]]:
    """Cell text row by row. A position covered by a merged cell is "←" (merged with the
    cell to its left) or "↑" (merged with the cell above); "" when that cannot be told."""
    rows = table.extract()
    xs = _boundaries([b[0] for b in table.cells] + [b[2] for b in table.cells])
    ys = [row.bbox[1] for row in table.rows] + [table.rows[-1].bbox[3]]
    owner: dict[tuple[int, int], tuple[int, int]] = {}
    if len(xs) == table.col_count + 1:
        for b in table.cells:
            c0, c1, r0, r1 = _nearest(xs, b[0]), _nearest(xs, b[2]), _nearest(ys, b[1]), _nearest(ys, b[3])
            if c0 is None or c1 is None or r0 is None or r1 is None:
                continue
            for r in range(r0, r1):
                for c in range(c0, c1):
                    owner[(r, c)] = (r0, c0)
    grid = []
    for r, row in enumerate(rows):
        line = []
        for c, cell in enumerate(row):
            if cell is not None:
                line.append(_clean_cell(cell))
                continue
            origin = owner.get((r, c))
            line.append("" if origin is None or origin == (r, c) else "←" if origin[0] == r else "↑")
        grid.append(line)
    return grid


def grid_text(grid: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in grid)


def _ruled_tables(page: Any) -> list[Any]:
    try:
        if len(page.get_cdrawings()) < MIN_DRAWINGS:
            return []
        return list(page.find_tables().tables)
    except Exception:  # noqa: BLE001 - a malformed page must not stop the upload; fallbacks still apply
        return []


def _table_info(table: Any) -> dict[str, Any]:
    return {"bbox": tuple(table.bbox), "grid": table_grid(table), "cols": table.col_count,
            "xs": _boundaries([b[0] for b in table.cells] + [b[2] for b in table.cells])}


def schedule_tables(page: Any) -> list[dict[str, Any]]:
    """The page's ruled schedule tables: [{bbox, grid, marks, cols, xs}]."""
    out = []
    for table in _ruled_tables(page):
        if table.row_count < 3 or table.col_count < 3:
            continue
        rows = table.extract()
        score = mark_score(rows)
        if score >= MIN_MARKS:
            # A mark may sit in one merged column (Screening only), so the legend also takes
            # marks repeated down a single column.
            used = table_marks(rows, min_cols=1)
            out.append({**_table_info(table), "marks": score,
                        "markWords": [w for w in sorted(used, key=lambda w: -used[w])
                                      if used[w] >= 3 and _legend_word(w)]})
    return out


# ── Tables that run over several pages ──────────────────────────────────
# A schedule often starts at the foot of one page (its header and a row or two, too few
# marks to count as a schedule on their own) and continues on the next pages without
# repeating the header. A table at the top of a page continues the table at the bottom
# of the page before when both have the same columns (same count, same positions). The
# start page is then included, and the header row is repeated on every continuation,
# so each grid says which column is which visit.

_EDGE = 0.25  # within the top / bottom quarter of the page


def _same_columns(a: dict[str, Any], b: dict[str, Any], tol: float = 4.0) -> bool:
    return (a["cols"] == b["cols"] and len(a["xs"]) == len(b["xs"])
            and all(abs(x - y) <= tol for x, y in zip(a["xs"], b["xs"])))


def link_continued_tables(doc: Any, found: dict[int, list[dict[str, Any]]]) -> dict[int, list[dict[str, Any]]]:
    """Add the start pages of schedule tables that run onto the next page, and repeat each
    table's header row on its continuation pages. ``found``: 0-based page -> schedule tables."""
    pages = dict(found)
    for i in sorted(found):
        if i == 0 or i - 1 in pages or not found[i]:
            continue
        first = min(found[i], key=lambda t: t["bbox"][1])
        height = doc[i].rect.height
        if first["bbox"][1] > height * _EDGE:
            continue
        before = [_table_info(t) for t in _ruled_tables(doc[i - 1])]
        if not before:
            continue
        last = max(before, key=lambda t: t["bbox"][3])
        if last["bbox"][3] >= doc[i - 1].rect.height * (1 - _EDGE) and _same_columns(last, first):
            pages[i - 1] = [{**last, "marks": 0, "markWords": [], "startsTable": True}]
    # Walk the pages in order, carrying each table's header row onto its continuations.
    for i in sorted(pages):
        first = min(pages[i], key=lambda t: t["bbox"][1])
        prev = pages.get(i - 1)
        if not prev or first["bbox"][1] > doc[i].rect.height * _EDGE:
            continue
        last = max(prev, key=lambda t: t["bbox"][3])
        if last["bbox"][3] < doc[i - 1].rect.height * (1 - _EDGE) or not _same_columns(last, first):
            continue
        header = last.get("header") or _header_rows(last["grid"])
        first["header"] = header
        if first["grid"] and header and first["grid"][:len(header)] != header and not _has_own_header(first["grid"]):
            label = header[0][0] or "Visit"
            first["grid"] = [[f"{label} (header repeated from page {i})", *header[0][1:]], *header[1:],
                             *first["grid"]]
    return pages


def _has_own_header(grid: list[list[str]]) -> bool:
    """The page repeats a header itself: a row before the first mark fills most visit columns
    with text. (A label wrapping over from the page before fills only its first cell.)"""
    marks = table_marks(grid, min_cols=1)
    main = max(marks, key=lambda w: marks[w]) if marks else None
    for row in grid[:6]:
        if main is not None and any(mark_word(cell) == main for cell in row[1:]):
            return False
        cells = row[1:]
        filled = sum(1 for cell in cells if cell and cell not in ("←", "↑"))
        if cells and filled >= len(cells) / 2:
            return True
    return False


def _header_rows(grid: list[list[str]], limit: int = 6) -> list[list[str]]:
    """The rows above the first row holding the table's main mark (visit names, visit codes,
    study days, windows). Visit codes such as "V1" sit in one row only, so they are not the mark."""
    marks = table_marks(grid, min_cols=1)
    if not marks:
        return grid[:1]
    main = max(marks, key=lambda w: marks[w])
    rows: list[list[str]] = []
    for row in grid[:limit]:
        if any(mark_word(cell) == main for cell in row[1:]):
            break
        rows.append(row)
    return rows or grid[:1]


def _inside(bbox: tuple, x: float, y: float) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def text_with_grids(page: Any, tables: list[dict[str, Any]]) -> str:
    """The page's text in reading order with each schedule table replaced by its grid."""
    blocks = []
    for b in page.get_text("blocks"):
        if len(b) > 6 and b[6] != 0:  # image block
            continue
        if any(_inside(t["bbox"], (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for t in tables):
            continue
        text = re.sub(r"[ \t]+", " ", b[4]).strip()
        if text:
            blocks.append((b[1], text))
    pending = sorted(tables, key=lambda t: t["bbox"][1])
    out: list[str] = []
    for y, text in blocks:
        while pending and pending[0]["bbox"][1] <= y:
            out.append(grid_text(pending.pop(0)["grid"]))
        out.append(text)
    out.extend(grid_text(t["grid"]) for t in pending)
    return "\n".join(out)


# ── Schedule tables without ruled lines ──────────────────────────────────
# Some tables are laid out with spacing only, so there are no lines to find.
# They are found from word positions with the same rule as ruled tables: one
# short mark, standing alone, repeated in lined-up columns over several rows.
# Their marks are counted per row and per column; the model then transcribes the
# table from the page image and the counts check its reading (see kit/tables.py).


def _clusters(values: list[tuple[float, int]], tol: float) -> list[list[int]]:
    """Group item indexes whose positions lie within ``tol`` of the previous one."""
    groups: list[list[int]] = []
    last = None
    for value, index in sorted(values):
        if last is None or value - last > tol:
            groups.append([])
        groups[-1].append(index)
        last = value
    return groups


def aligned_table(page: Any) -> dict[str, Any] | None:
    """A borderless schedule table on the page: its marks, rows and columns with mark counts."""
    try:
        words = page.get_text("words")
    except Exception:  # noqa: BLE001 - a malformed page must not stop the upload
        return None
    if not isinstance(words, list) or not words:
        return None
    rotated = bool(getattr(page, "rotation", 0))
    if rotated:
        # Word positions come in the unrotated layout; work in the page as it is seen.
        import fitz

        to_view = page.rotation_matrix
        words = [(*tuple(fitz.Rect(w[:4]) * to_view), *w[4:]) for w in words]
    height = sorted(w[3] - w[1] for w in words)[len(words) // 2] or 10.0

    def same_line(a: Any, b: Any) -> bool:
        return abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < height * 0.5

    # A mark stands alone: no other word within 1.2 line heights on its line.
    by_line: dict[int, list[Any]] = {}
    for w in words:
        by_line.setdefault(round((w[1] + w[3]) / 2 / (height * 0.5)), []).append(w)
    standing: list[tuple[str, Any]] = []
    for w in words:
        word = mark_word(w[4])
        if word is None or len(w[4]) > 4:
            continue
        key = round((w[1] + w[3]) / 2 / (height * 0.5))
        neighbours = [o for k in (key - 1, key, key + 1) for o in by_line.get(k, [])
                      if o is not w and same_line(o, w)]
        gap = min((max(o[0] - w[2], w[0] - o[2]) for o in neighbours), default=1e9)
        if gap >= height * 1.2:
            standing.append((word, w))
    as_mark = merge_letter_footnotes([word for word, _ in standing])
    marks = [(as_mark[word], w) for word, w in standing if _legend_word(as_mark[word])]
    counts: dict[str, int] = {}
    for word, _ in marks:
        counts[word] = counts.get(word, 0) + 1
    if not counts:
        return None
    top = max(counts, key=lambda k: counts[k])
    main = [w for word, w in marks if word == top]
    columns = _clusters([((w[0] + w[2]) / 2, i) for i, w in enumerate(main)], height * 0.8)
    if len(main) < MIN_MARKS or sum(1 for c in columns if len(c) >= 2) < 2:
        return None
    rows_of_main = _clusters([((w[1] + w[3]) / 2, i) for i, w in enumerate(main)], height * 0.5)
    if len(rows_of_main) < 2:
        return None
    # The table area holds the main mark; other standalone marks inside it count too ("O" beside "X").
    x0 = min(w[0] for w in main) - height
    x1 = max(w[2] for w in main) + height
    y0 = min(w[1] for w in main) - height * 0.5
    y1 = max(w[3] for w in main) + height * 0.5
    inside = [(word, w) for word, w in marks if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
    if len(main) < 0.6 * len(inside):
        return None  # many different "marks": garbled text (a bad scan layer), not a schedule
    row_groups = _clusters([((w[1] + w[3]) / 2, i) for i, (_, w) in enumerate(inside)], height * 0.5)
    col_groups = _clusters([((w[0] + w[2]) / 2, i) for i, (_, w) in enumerate(inside)], height * 0.8)
    centres = [sum((inside[i][1][1] + inside[i][1][3]) / 2 for i in g) / len(g) for g in row_groups]
    rows = []
    for n, group in enumerate(row_groups):
        y = centres[n]
        # A label may wrap over two lines around its marks ("Adverse / Events").
        gaps = [abs(y - other) for k, other in enumerate(centres) if k != n]
        reach = min([height * 1.2, *(g / 2 for g in gaps)])
        first_x = min(inside[i][1][0] for i in group)
        label = " ".join(o[4] for o in sorted(words, key=lambda o: (round(o[1]), o[0]))
                         if abs((o[1] + o[3]) / 2 - y) < reach and o[2] <= first_x - height * 0.5)
        rows.append({"y": round(y, 1), "label": label.strip(), "count": len(group)})
    columns_out = []
    for group in col_groups:
        x = sum((inside[i][1][0] + inside[i][1][2]) / 2 for i in group) / len(group)
        # The header is the closest block of lines above the column that overlaps it.
        above = sorted((o for o in words if o[0] <= x + height * 1.5 and o[2] >= x - height * 1.5
                        and y0 - height * 6 <= o[3] <= y0 + height * 0.5 and mark_word(o[4]) not in counts),
                       key=lambda o: -o[3])
        block: list[Any] = []
        for o in above:
            if block and min(b[1] for b in block) - o[3] > height * 0.8:
                break
            block.append(o)
        # Keep a heading's whole phrase ("Week 4", not "Week") when it sticks out past the column.
        grown = True
        while grown:
            grown = False
            for o in words:
                if o not in block and mark_word(o[4]) not in counts and any(
                        same_line(o, b) and min(abs(o[0] - b[2]), abs(b[0] - o[2])) < height * 0.6 for b in block):
                    block.append(o)
                    grown = True
        header = " ".join(o[4] for o in sorted(block, key=lambda o: (round(o[1]), o[0])))
        columns_out.append({"x": round(x, 1), "header": header.strip(), "count": len(group)})
    bbox = (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))
    text_bbox = bbox
    if rotated:
        import fitz

        text_bbox = tuple(round(v, 1) for v in fitz.Rect(bbox) * page.derotation_matrix)
    return {
        "marks": sorted({word for word, _ in inside}, key=lambda k: -counts[k]),
        "total": len(inside), "rows": rows, "columns": columns_out,
        # bbox: as the page is seen (crops); textBbox: in the text's own coordinates.
        "bbox": bbox, "textBbox": text_bbox,
    }


# ── Symbol legend ────────────────────────────────────────────────────────
# A symbol can reach the text as a code with no meaning (a private-use or
# replacement character), or as the wrong letter (a dingbat font whose tick is
# stored as "ü"). Rather than guessing what it means from a list, every such
# symbol is written [S1], [S2]... in the text, and every mark used in a schedule
# table is listed too. One legend image shows each one cut from the page exactly
# as printed, so the model reads the symbol itself, whatever font it came from.

LEGEND_LIMIT = 40
LEGEND_LABEL = "Symbol legend image (rows numbered as in the SYMBOL LEGEND):"


def is_unreadable(ch: str) -> bool:
    code = ord(ch)
    return 0xE000 <= code <= 0xF8FF or code >= 0xF0000 or code == 0xFFFD


def _font_name(span: dict[str, Any]) -> str:
    return str(span.get("font", "")).split("+", 1)[-1] or "unknown font"


class SymbolLegend:
    """Symbols the text cannot spell and the marks of schedule tables, each with one place
    it is printed, so the model can be shown how it looks."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.unreadable = 0

    def _entry(self, key: tuple[str, str, str], shown: str, page: int, bbox: Any,
               font: str = "") -> dict[str, Any]:
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = {
                "kind": key[0], "font": font or key[1], "shown": shown, "page": page, "bbox": tuple(bbox),
                "count": 0}
        entry["count"] += 1
        return entry

    def record(self, page: Any, page_index: int, known: dict[int, str],
               tables: list[dict[str, Any]], marks_only: bool = False) -> dict[int, str]:
        """Record the page's symbols. Returns a str.translate table writing each symbol the
        text cannot spell (and ``known`` does not translate) as its [S#] name.
        ``marks_only`` records just the tables' marks (the page was recorded before)."""
        names: dict[int, str] = {}
        marks = {w for t in tables for w in t.get("markWords", [])}
        for block in page.get_text("rawdict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    chars = span.get("chars") or []
                    if not chars:
                        continue
                    font = _font_name(span)
                    for ch in chars:
                        c = ch.get("c", "")
                        if marks_only or not c or not is_unreadable(c) or ord(c) in known:
                            continue
                        key = ("unreadable", font, c)
                        if key not in self.entries:
                            self.unreadable += 1
                            shown = f"[S{self.unreadable}]"
                        else:
                            shown = self.entries[key]["shown"]
                        self._entry(key, shown, page_index, ch["bbox"])
                        names.setdefault(ord(c), shown)
                    text = "".join(ch.get("c", "") for ch in chars)
                    word = mark_word(text)
                    x, y = (span["bbox"][0] + span["bbox"][2]) / 2, (span["bbox"][1] + span["bbox"][3]) / 2
                    if (word is not None and word in marks and not any(is_unreadable(c) and ord(c) not in known for c in word)
                            and any(_inside(t["bbox"], x, y) for t in tables)):
                        start = text.index(word[0])
                        boxes = [c["bbox"] for c in chars[start:start + len(word)]]
                        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                max(b[2] for b in boxes), max(b[3] for b in boxes))
                        # One row per mark: bold and regular prints of "X" are the same mark.
                        self._entry(("mark", "", word), f"“{word.translate(known)}”", page_index, bbox,
                                    font)
        return names

    def chosen(self) -> list[dict[str, Any]]:
        marks = sorted((e for e in self.entries.values() if e["kind"] == "mark"), key=lambda e: -e["count"])
        other = sorted((e for e in self.entries.values() if e["kind"] != "mark"), key=lambda e: -e["count"])
        return (marks + other)[:LEGEND_LIMIT]

    def note(self, chosen: list[dict[str, Any]]) -> str:
        lines = []
        for n, e in enumerate(chosen, 1):
            where = f"{e['count']}×, first on p. {e['page'] + 1}, font {e['font']}"
            if e["kind"] == "mark":
                lines.append(f"{n}. a schedule-table mark written {e['shown']} in the text ({where})")
            else:
                lines.append(f"{n}. written {e['shown']} in the text: the PDF gives no readable character for it ({where})")
        return (
            "SYMBOL LEGEND. The attached symbol legend image shows each of these, row by row, exactly as "
            "printed in the PDF:\n" + "\n".join(lines) + "\n"
            "Read each symbol from the image; the text may spell a symbol wrongly (a tick stored as a letter). "
            "What a symbol means comes from the protocol's own key or footnotes. If a schedule table uses "
            "different marks and the protocol does not say how they differ, record that in notes and ask a "
            "review question rather than choosing a meaning.")

    def render(self, doc: Any) -> dict[str, Any] | None:
        """{note, image} for the model, or None when there is nothing to show."""
        import fitz

        chosen = self.chosen()
        if not chosen:
            return None
        row = 30
        out = fitz.open()
        try:
            sheet = out.new_page(width=340, height=row * len(chosen) + 12)
            for n, e in enumerate(chosen, 1):
                top = 6 + (n - 1) * row
                sheet.insert_text((8, top + 19), f"{n}.", fontsize=12)
                source = doc[e["page"]]
                box = fitz.Rect(e["bbox"])
                pad = max(1.5, box.height * 0.3)
                clip = fitz.Rect(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad) & source.rect
                if clip.is_empty or clip.height <= 0:
                    continue
                height = row - 6
                width = min(280, height * clip.width / clip.height)
                sheet.show_pdf_page(fitz.Rect(44, top + 3, 44 + width, top + 3 + height), doc, e["page"], clip=clip)
            pix = sheet.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            image = {"page": "legend", "label": LEGEND_LABEL, "mediaType": "image/png",
                     "base64": base64.b64encode(pix.tobytes("png")).decode("ascii")}
        finally:
            out.close()
        return {"note": self.note(chosen), "image": image, "count": len(chosen)}


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
    """Everything the model step needs, plus the page images for the cross-check.
    One PDF at a time (``PDF_LOCK``); a second upload waits a few seconds."""
    with PDF_LOCK:
        return _prepare_protocol_input(pdf_bytes, budget=budget, full_text_max_chars=full_text_max_chars,
                                       on_progress=on_progress)


def _prepare_protocol_input(
    pdf_bytes: bytes, *, budget: int, full_text_max_chars: int | None,
    on_progress: Callable[[int, int], None] | None,
) -> dict[str, Any]:
    import fitz

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:  # noqa: BLE001
        raise PdfReadError("This file could not be read as a PDF.") from error
    if doc.page_count == 0:
        raise PdfReadError("This PDF has no pages.")
    pages: list[str] = []
    layout: list[str] = []
    ruled: dict[int, list[dict[str, Any]]] = {}  # 0-based page -> its ruled schedule tables
    page_symbols: list[dict[int, str]] = []
    legend = SymbolLegend()
    for i in range(doc.page_count):
        page = doc[i]
        symbols = symbol_map(page)
        text = plain_text(page)
        tables = schedule_tables(page)
        if tables or any(is_unreadable(ch) and ord(ch) not in symbols for ch in text):
            symbols = {**legend.record(page, i, symbols, tables), **symbols}
        page_symbols.append(symbols)
        pages.append(text.translate(symbols))
        layout.append(layout_text(page).translate(symbols))
        if tables:
            ruled[i] = tables
        if on_progress:
            on_progress(i + 1, doc.page_count)
    # A table that starts at the foot of the page before is included, and its header row is
    # repeated on each continuation page (see link_continued_tables).
    grids: dict[int, str] = {  # 0-based page -> its text with schedule tables as grids
        i: text_with_grids(doc[i], tables).translate(page_symbols[i])
        for i, tables in sorted(link_continued_tables(doc, ruled).items())}
    scanned = is_scanned(pages)
    # Pages without a ruled schedule table may hold one laid out with spacing only.
    aligned: dict[int, dict[str, Any]] = {}
    if not scanned:
        for i in range(doc.page_count):
            if i not in grids:
                found = aligned_table(doc[i])
                if found:
                    aligned[i] = {"page": i + 1, **found}
                    legend.record(doc[i], i, symbol_map(doc[i]),
                                  [{"bbox": found["textBbox"], "markWords": found["marks"]}], marks_only=True)
    table_idx = set(grids) | set(aligned)
    table_pages = [i + 1 for i in sorted(table_idx)]
    table_source = (" + ".join(name for name, hit in (("structure", grids), ("aligned", aligned)) if hit)
                    or None)
    # Send every page by default. GPT-6 Sol allows 922,000 input tokens, not
    # characters; the provider enforces that limit on the complete request,
    # including instructions and images. Do not discard pages at a guessed
    # character equivalent. An explicit KIT_FULL_TEXT_MAX_CHARS / argument
    # retains the legacy selection option (-1 = no character cap, 0 = select).
    if full_text_max_chars is None:
        full_text_max_chars = int(os.getenv("KIT_FULL_TEXT_MAX_CHARS") or -1)
    send_everything = full_text_max_chars < 0 or 0 < sum(len(p) + 16 for p in pages) <= full_text_max_chars
    picked: list[int] = []
    selected: list[int] = []
    if not scanned:
        selected = select_pages(pages, budget)
        selected += [i for i in sorted(table_idx) if i not in selected]
        selected.sort()
        picked = list(range(len(pages))) if send_everything else selected

    def page_block(i: int) -> str:
        if i in grids:
            return f"[[Page {i + 1} · {GRID_LABEL}]]\n{grids[i]}"
        if i in aligned:
            return f"[[Page {i + 1} · {ALIGNED_LABEL}]]\n{layout[i]}"
        return f"[[Page {i + 1}]]\n{pages[i]}"

    if scanned:
        text = ("(The PDF has no text layer. The attached images are its first pages. "
                "Read the schedule from them.)")
        table_text = text
        selected_text = text
    else:
        text = "\n\n".join(page_block(i) for i in picked)
        selected_text = "\n\n".join(page_block(i) for i in selected)
        # No schedule table found: the cross-check reads the protocol text the model read.
        table_text = (
            "\n\n".join(page_block(n - 1) for n in table_pages) if table_pages
            else text if send_everything else selected_text)
    result = {
        "pageCount": doc.page_count, "tablePages": table_pages, "tableSource": table_source,
        "alignedTables": [aligned[i] for i in sorted(aligned)],
        "picked": picked, "selected": selected, "scanned": scanned,
        "text": text, "selectedText": selected_text, "fullText": send_everything and not scanned,
        "pageTexts": [page_block(i) for i in range(len(pages))] if not scanned else [],
        "tableText": table_text,
        "tableImages": render_page_images(doc, table_pages[:image_limit()]),
        "scanImages": render_page_images(doc, [n for n in range(1, 9) if n <= doc.page_count]) if scanned else [],
        # Kept so the cross-check can also render the pages the model names as schedule tables.
        "pdfBytes": pdf_bytes,
        "symbolLegend": None if scanned else legend.render(doc),
    }
    doc.close()
    return result


def image_limit() -> int:
    """Schedule-table page images sent to the cross-check (env KIT_TABLE_IMAGE_LIMIT)."""
    try:
        return max(0, int(os.getenv("KIT_TABLE_IMAGE_LIMIT") or TABLE_IMAGE_LIMIT))
    except ValueError:
        return TABLE_IMAGE_LIMIT


def cross_check_material(prepared: dict[str, Any], model_pages: list[int]) -> dict[str, Any]:
    """Pages, table text and images for the cross-check: the detector's table pages plus
    the pages the model named as schedule tables. Images go to pages both agree on first."""
    detected = [n for n in prepared.get("tablePages") or [] if isinstance(n, int)]
    count = prepared.get("pageCount")
    named = sorted({n for n in model_pages if isinstance(n, int) and not isinstance(n, bool)
                    and n >= 1 and (not count or n <= count)})
    pages = sorted(set(detected) | set(named))
    page_texts = prepared.get("pageTexts") or []
    if pages and len(page_texts) >= pages[-1] and set(named) - set(detected):
        text = "\n\n".join(page_texts[n - 1] for n in pages)
    else:
        text = prepared.get("tableText") or ""
    limit = image_limit()
    order = ([n for n in detected if n in named] + [n for n in detected if n not in named]
             + [n for n in named if n not in detected])
    wanted = sorted(order[:limit])
    have = {img["page"]: img for img in prepared.get("tableImages") or []}
    missing = [n for n in wanted if n not in have]
    if missing and prepared.get("pdfBytes"):
        import fitz

        with PDF_LOCK:
            doc = fitz.open(stream=prepared["pdfBytes"], filetype="pdf")
            try:
                have.update({img["page"]: img for img in render_page_images(doc, missing)})
            finally:
                doc.close()
    return {"pages": pages, "text": text, "images": [have[n] for n in wanted if n in have]}
