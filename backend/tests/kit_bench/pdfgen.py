"""A small PDF writer for the constructed protocol fixtures (PyMuPDF only).

It lays out headings, paragraphs, bullet lists, ruled tables (with merged cells) and tables set
out with spacing only, on portrait or landscape pages, with a running header and footer. Each
fixture picks its own fonts, numbering and table style so the files look like documents from
different sponsors. Every page carries the fixture notice in its footer.
"""

from __future__ import annotations

from typing import Any, Sequence

import fitz

NOTICE = "CONSTRUCTED TEST FIXTURE - NOT A REAL CLINICAL PROTOCOL"
PORTRAIT = (595, 842)
LANDSCAPE = (842, 595)


class Pdf:
    def __init__(self, *, header: str, footer: str = "", font: str = "helv", bold: str = "hebo",
                 italic: str = "heit", size: float = 10, margin: float = 56, leading: float = 1.32):
        self.doc = fitz.open()
        self.header, self.footer = header, footer
        self.font, self.bold, self.italic = font, bold, italic
        self.size, self.margin, self.leading = size, margin, leading
        self.page: Any = None
        self.y = 0.0
        self.w, self.h = PORTRAIT
        self.new_page()

    # ── pages ────────────────────────────────────────────────────────────
    def new_page(self, landscape: bool = False) -> None:
        self.w, self.h = LANDSCAPE if landscape else PORTRAIT
        self.page = self.doc.new_page(width=self.w, height=self.h)
        n = self.doc.page_count
        self.page.insert_text((self.margin, 30), self.header, fontname=self.font, fontsize=7.5, color=(0.3, 0.3, 0.3))
        self.page.insert_text((self.w - self.margin - 40, 30), f"Page {n}", fontname=self.font, fontsize=7.5, color=(0.3, 0.3, 0.3))
        self.page.insert_text((self.margin, self.h - 24), f"{NOTICE}  |  {self.footer}".strip(" |"), fontname=self.font,
                              fontsize=6.5, color=(0.35, 0.35, 0.35))
        self.y = 52

    @property
    def right(self) -> float:
        return self.w - self.margin

    def room(self, height: float) -> None:
        if self.y + height > self.h - 44:
            self.new_page(landscape=self.w > self.h)

    # ── text ─────────────────────────────────────────────────────────────
    def wrap(self, text: str, width: float, font: str, size: float) -> list[str]:
        lines: list[str] = []
        for para in str(text).split("\n"):
            line = ""
            for word in para.split(" "):
                trial = f"{line} {word}".strip()
                if fitz.get_text_length(trial, fontname=font, fontsize=size) <= width or not line:
                    line = trial
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
        return lines

    def text(self, text: str, *, font: str | None = None, size: float | None = None, indent: float = 0,
             gap: float = 6, width: float | None = None) -> None:
        font, size = font or self.font, size or self.size
        step = size * self.leading
        for line in self.wrap(text, (width or (self.right - self.margin)) - indent, font, size):
            self.room(step)
            self.page.insert_text((self.margin + indent, self.y + size), line, fontname=font, fontsize=size)
            self.y += step
        self.y += gap

    def h1(self, text: str) -> None:
        self.y += 6
        self.room(40)
        self.text(text, font=self.bold, size=self.size + 3, gap=6)

    def h2(self, text: str) -> None:
        self.y += 2
        self.room(34)
        self.text(text, font=self.bold, size=self.size + 1, gap=4)

    def p(self, text: str, **kw: Any) -> None:
        self.text(text, **kw)

    def bullets(self, items: Sequence[str], mark: str = "-", indent: float = 14) -> None:
        for item in items:
            step = self.size * self.leading
            self.room(step)
            self.page.insert_text((self.margin + indent - 10, self.y + self.size), mark, fontname=self.font, fontsize=self.size)
            self.text(item, indent=indent, gap=2)
        self.y += 4

    def title(self, lines: Sequence[str]) -> None:
        self.y = 150
        for i, line in enumerate(lines):
            self.text(line, font=self.bold if i < 2 else self.font, size=15 if i == 0 else 11, gap=10)

    # ── tables ───────────────────────────────────────────────────────────
    def table(self, rows: Sequence[Sequence[Any]], widths: Sequence[float], *, ruled: bool = True, size: float = 7.5,
              header_rows: int = 1, pad: float = 3, mark_font: str | None = None, marks: Sequence[str] = (),
              center_from: int = 1, repeat_header: bool = True) -> None:
        """rows of cells; a cell is text, None (empty), or ("text", colspan[, rowspan]). A cell
        covered by a span is given as "<" (left neighbour) or "^" (cell above). ``mark_font``
        draws the cells listed in ``marks`` in that font (a tick from ZapfDingbats)."""
        x0 = self.margin
        xs = [x0]
        for wd in widths:
            xs.append(xs[-1] + wd)
        step = size * 1.25

        def cell_text(cell: Any) -> str:
            return "" if cell in (None, "<", "^") else str(cell[0] if isinstance(cell, tuple) else cell)

        def spans(cell: Any) -> tuple[int, int]:
            return (cell[1], cell[2] if len(cell) > 2 else 1) if isinstance(cell, tuple) else (1, 1)

        def row_height(row: Sequence[Any]) -> float:
            tallest = 1
            for c, cell in enumerate(row):
                if cell in (None, "<", "^"):
                    continue
                cs, rs = spans(cell)
                if rs > 1:
                    continue
                width = xs[min(c + cs, len(widths))] - xs[c] - 2 * pad
                tallest = max(tallest, len(self.wrap(cell_text(cell), width, self.font, size)))
            return tallest * step + 2 * pad

        heights = [row_height(r) for r in rows]

        def draw_row(r: int, top: float) -> None:
            row = rows[r]
            for c, cell in enumerate(row):
                if cell in ("<", "^"):
                    continue
                cs, rs = spans(cell)
                left, right = xs[c], xs[min(c + cs, len(widths))]
                bottom = top + sum(heights[r:r + rs])
                if ruled:
                    self.page.draw_rect(fitz.Rect(left, top, right, bottom), color=(0, 0, 0), width=0.5)
                txt = cell_text(cell)
                if not txt:
                    continue
                font = self.bold if r < header_rows else self.font
                if mark_font and txt in marks:
                    font = mark_font
                lines = self.wrap(txt, right - left - 2 * pad, font if font != mark_font else self.font, size)
                yy = top + pad + size
                for line in lines:
                    width = fitz.get_text_length(line, fontname=font, fontsize=size)
                    centered = c >= center_from or r < header_rows and cs > 1
                    x = left + (right - left - width) / 2 if centered else left + pad
                    self.page.insert_text((x, yy), line, fontname=font, fontsize=size)
                    yy += step

        r = 0
        while r < len(rows):
            # Keep rows joined by a row span together.
            block = 1
            for cell in rows[r]:
                if isinstance(cell, tuple) and len(cell) > 2:
                    block = max(block, cell[2])
            need = sum(heights[r:r + block])
            if self.y + need > self.h - 44:
                self.new_page(landscape=self.w > self.h)
                if repeat_header and r >= header_rows:
                    for hr in range(header_rows):
                        draw_row(hr, self.y)
                        self.y += heights[hr]
            for k in range(block):
                draw_row(r + k, self.y)
                self.y += heights[r + k]
            r += block
        self.y += 8

    def save(self, path: str) -> None:
        self.doc.set_metadata({"title": self.header, "subject": NOTICE, "author": "MTB kit bench"})
        self.doc.save(path, garbage=3, deflate=True, no_new_id=True)
        self.doc.close()
