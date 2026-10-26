"""Schedule tables without ruled lines: the model reads them from the page image.

A ruled table is read exactly from its cells (pdf_input.schedule_tables). A table
laid out with spacing only has no cells to read, so the model transcribes it from
the page image, row by row. Its reading is then checked against the marks counted
on the page (pdf_input.aligned_table), in total, per row and per column. When the
counts disagree the model is told exactly where and looks again at a zoomed crop,
up to ``MAX_RECHECKS`` times. Only rows that still disagree are flagged for review.
"""

from __future__ import annotations

import base64
import difflib
import os
import re
from typing import Any, Callable

from .llm import AnalysisError
from .pdf_input import PDF_LOCK, READ_LABEL, _legend_word, grid_text, mark_word

MAX_RECHECKS = 2
PAGE_LIMIT = 12
StageCallback = Callable[[str, str, Any], None]


def page_limit() -> int:
    """Borderless table pages read per protocol (env KIT_TRANSCRIBE_PAGE_LIMIT)."""
    try:
        return max(0, int(os.getenv("KIT_TRANSCRIBE_PAGE_LIMIT") or PAGE_LIMIT))
    except ValueError:
        return PAGE_LIMIT


# ── Images ─────────────────────────────────────────────────────────────────

def _render(pdf_bytes: bytes, page_no: int, clip: tuple | None = None, long_edge: int = 1600) -> dict[str, Any]:
    import fitz

    with PDF_LOCK:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[page_no - 1]
            area = fitz.Rect(clip) & page.rect if clip else page.rect
            scale = min(4.0, long_edge / max(area.width, area.height, 1))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=area, alpha=False)
            return {"page": page_no, "mediaType": "image/png",
                    "base64": base64.b64encode(pix.tobytes("png")).decode("ascii")}
        finally:
            doc.close()


def _zoom_area(table: dict[str, Any]) -> tuple:
    """The table with room above for its column headings and to the left for its row labels
    (rendered on its own it comes out much larger than on the whole page)."""
    _, y0, x1, y1 = table["bbox"]
    return (0, max(0, y0 - 90), x1 + 60, y1 + 30)


# ── The model's reading ────────────────────────────────────────────────────

def transcribe_prompt(page_no: int, symbol_note: str = "") -> str:
    note = f"{symbol_note}\n\n" if symbol_note else ""
    return (
        f"The image is page {page_no} of a clinical trial protocol. It holds a schedule table laid out without "
        "cell borders, so its columns are only shown by alignment. Copy the table exactly as printed.\n"
        "Reply with ONLY one JSON object:\n"
        '{"columns": [string] /* the visit/timepoint column headings, left to right; join a heading that wraps */,\n'
        ' "rows": [{"label": string /* the row heading as printed, joined if it wraps */,\n'
        '           "cells": [string] /* exactly one per column: what that cell shows ("X", "✓", "X a", text), "" if blank */}],\n'
        ' "footnotes": [string]}\n'
        "Do not add, merge, split or skip rows or columns. Write a mark as the symbol you see; keep a footnote "
        "letter or number next to it. If a cell is hard to read, write what you see followed by \"?\".\n\n"
        f"{note}"
    )


def recheck_prompt(page_no: int, problems: list[str], symbol_note: str = "") -> str:
    return (
        transcribe_prompt(page_no, symbol_note)
        + "You read this table before, but your reading does not match the marks counted on the page:\n- "
        + "\n- ".join(problems)
        + "\nThe second image is a zoomed view of the table. Look again, especially at these rows and columns, "
        "and reply with the whole table again. If after looking carefully you still read it the same way, "
        "keep your reading."
    )


def _is_mark(cell: Any) -> bool:
    word = mark_word(cell)
    return word is not None and _legend_word(word)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def clean_reading(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return None
    columns = [str(c) for c in raw.get("columns") or []]
    rows = []
    for row in raw["rows"]:
        if not isinstance(row, dict):
            continue
        cells = [str(c if c is not None else "") for c in row.get("cells") or []]
        cells = (cells + [""] * len(columns))[:len(columns)] if columns else cells
        rows.append({"label": str(row.get("label") or ""), "cells": cells})
    if not rows:
        return None
    return {"columns": columns, "rows": rows,
            "footnotes": [str(f) for f in raw.get("footnotes") or [] if f]}


# ── Checking the reading against the page ──────────────────────────────────

def _match_rows(page_rows: list[dict[str, Any]], read_rows: list[dict[str, Any]]) -> dict[int, int]:
    """page row index -> read row index, by label similarity, each used once."""
    pairs = []
    for i, p in enumerate(page_rows):
        for j, r in enumerate(read_rows):
            a, b = _norm(p["label"]), _norm(r["label"])
            if not a or not b:
                continue
            score = 1.0 if a == b or a.startswith(b) or b.startswith(a) else difflib.SequenceMatcher(None, a, b).ratio()
            if score >= 0.6:
                pairs.append((score, i, j))
    used_i, used_j, out = set(), set(), {}
    for score, i, j in sorted(pairs, reverse=True):
        if i not in used_i and j not in used_j:
            out[i] = j
            used_i.add(i)
            used_j.add(j)
    return out


def compare(table: dict[str, Any], reading: dict[str, Any]) -> dict[str, Any]:
    """Differences between the marks counted on the page and the model's reading."""
    read_counts = [sum(1 for c in r["cells"] if _is_mark(c)) for r in reading["rows"]]
    total = sum(read_counts)
    problems: list[str] = []
    rows_off: list[dict[str, Any]] = []
    if total != table["total"]:
        problems.append(f"the page has {table['total']} marks in this table; your reading has {total}")
    matched = _match_rows(table["rows"], reading["rows"])
    for i, page_row in enumerate(table["rows"]):
        name = page_row["label"] or f"row {i + 1}"
        j = matched.get(i)
        if j is None:
            problems.append(f"row \"{name}\" has {page_row['count']} marks on the page but is missing from your reading")
            rows_off.append({**page_row, "read": None})
        elif read_counts[j] != page_row["count"]:
            problems.append(f"row \"{name}\" has {page_row['count']} marks on the page; you read {read_counts[j]}")
            rows_off.append({**page_row, "read": read_counts[j]})
    # Columns: compare the marked columns left to right when both sides have the same number.
    page_cols = [c for c in table["columns"] if c["count"]]
    read_cols = [(name, sum(1 for r in reading["rows"] if k < len(r["cells"]) and _is_mark(r["cells"][k])))
                 for k, name in enumerate(reading["columns"])]
    read_cols = [c for c in read_cols if c[1]]
    if len(page_cols) == len(read_cols):
        for page_col, (name, n) in zip(page_cols, read_cols):
            if page_col["count"] != n:
                problems.append(f"column \"{page_col['header'] or name}\" has {page_col['count']} marks on the page; "
                                f"you read {n}")
    else:
        problems.append(f"the page has marks in {len(page_cols)} columns; your reading has marks in {len(read_cols)}")
    score = abs(total - table["total"]) + sum(abs((r["read"] or 0) - r["count"]) for r in rows_off) + len(problems)
    return {"ok": not problems, "problems": problems, "rows_off": rows_off, "score": score,
            "signature": (total, tuple(read_counts))}


def reading_block(page_no: int, reading: dict[str, Any], page_text: str) -> str:
    grid = [["", *reading["columns"]]] + [[r["label"], *r["cells"]] for r in reading["rows"]]
    notes = "\n".join(reading["footnotes"])
    return (f"[[Page {page_no} · {READ_LABEL}]]\n{grid_text(grid)}"
            + (f"\nFootnotes: {notes}" if notes else "")
            + f"\nPage text as extracted (columns approximate):\n{page_text}")


def read_table(table: dict[str, Any], pdf_bytes: bytes, client: Any, *, symbol_note: str = "",
               legend_images: list[dict[str, Any]] | None = None,
               status: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Transcribe one borderless table, rechecking against the page counts.

    Returns {reading, check, attempts, agreed}: ``agreed`` is true when the counts match, or the
    model read the table the same way twice against them (the count, not the reading, may be off)."""
    page_no = table["page"]
    page_image = {**_render(pdf_bytes, page_no), "label": f"Page {page_no} image:"}
    extra = list(legend_images or [])
    best: dict[str, Any] | None = None
    previous = None
    for attempt in range(MAX_RECHECKS + 1):
        if best is None:  # first try, or every earlier reply was unusable
            prompt, images = transcribe_prompt(page_no, symbol_note), [page_image, *extra]
        else:
            if status:
                status(f"p. {page_no}: rechecking ({attempt} of {MAX_RECHECKS})")
            zoom = {**_render(pdf_bytes, page_no, _zoom_area(table)),
                    "label": f"Zoomed view of the table on page {page_no}:"}
            prompt, images = recheck_prompt(page_no, best["check"]["problems"], symbol_note), [page_image, zoom, *extra]
        reading = clean_reading(client.json(prompt, images=images))
        if reading is None:
            continue
        check = compare(table, reading)
        current = {"reading": reading, "check": check, "attempts": attempt + 1}
        if best is None or check["score"] < best["check"]["score"]:
            best = current
        if check["ok"]:
            return {**current, "agreed": True}
        if previous is not None and check["signature"] == previous:
            # Read the same way twice against the count: keep it; the count may be what is off.
            return {**current, "agreed": True, "countDoubted": True}
        previous = check["signature"]
    if best is None:
        raise AnalysisError("invalid_json", f"The table on page {page_no} could not be read.")
    return {**best, "agreed": False}


# ── Flags for review ───────────────────────────────────────────────────────

def review_flag(page_no: int, result: dict[str, Any]) -> dict[str, Any] | None:
    """A review question for rows whose reading still disagrees with the page, or None."""
    check = result["check"]
    if result.get("agreed") and not result.get("countDoubted"):
        return None
    # No "p. 12" in the text: the review popup splits a question into sentences at ". ".
    if result.get("countDoubted"):
        detail = "; ".join(check["problems"][:6])
        text = f"Check page {page_no}: does the schedule match the table there?"
        why = (f"The table on page {page_no} has no cell borders, so it was read from the page image. "
               f"The reading was confirmed on a second look, but it does not match the marks counted "
               f"on the page: {detail}.")
    else:
        rows = check["rows_off"] or []
        names = ", ".join((r["label"] or "unnamed row") for r in rows[:4]) or "some rows"
        text = f"Check page {page_no}: do these rows match the table? {names}"
        text = text if len(text) <= 110 else f"Check page {page_no}: do {len(rows)} rows match the table?"
        detail = "; ".join(
            f"{r['label'] or 'unnamed row'}: page shows {r['count']} marks, read {r['read'] if r['read'] is not None else 'none'}"
            for r in rows[:8]) or "; ".join(check["problems"][:6])
        why = (f"The table on page {page_no} has no cell borders, so it was read from the page image. "
               f"After {result['attempts']} readings these rows still differ from the marks on the page: "
               f"{detail}. The visits these rows feed may list a procedure too many or too few.")
    return {
        "id": f"table_check_p{page_no}", "text": text[:110], "why": why, "recommended": None,
        "options": [
            {"value": "unsure", "label": "Not checked yet", "patches": []},
            {"value": "checked", "label": f"Checked: the schedule matches page {page_no}", "patches": []},
        ],
    }


# ── The step in the pipeline ───────────────────────────────────────────────

def read_borderless_tables(prepared: dict[str, Any], client: Any, *, on_stage: StageCallback,
                           symbol_note: str = "", legend_images: list[dict[str, Any]] | None = None
                           ) -> dict[str, Any]:
    """Read every borderless schedule table and put the readings into the protocol text.

    Changes ``prepared`` in place (page texts, text, table text) and returns
    {flags: [review question], notes: [str], warnings: [str]}."""
    tables = (prepared.get("alignedTables") or [])
    out: dict[str, list[Any]] = {"flags": [], "notes": [], "warnings": []}
    if not tables or not prepared.get("pdfBytes") or not prepared.get("pageTexts"):
        return out
    limit = page_limit()
    todo, skipped = tables[:limit], tables[limit:]
    if skipped:
        out["warnings"].append(
            f"{len(skipped)} more table pages without cell borders (p. {', '.join(str(t['page']) for t in skipped)}) "
            "were not read from their images; they are sent as text and checked against their images.")
    on_stage("tables", "run", f"0 of {len(todo)} tables")
    page_texts: list[str] = prepared["pageTexts"]
    for n, table in enumerate(todo, 1):
        page_no = table["page"]
        on_stage("tables", "run", f"reading p. {page_no} ({n} of {len(todo)})")
        try:
            result = read_table(table, prepared["pdfBytes"], client, symbol_note=symbol_note,
                                legend_images=legend_images,
                                status=lambda message: on_stage("tables", "run", message))
        except AnalysisError as error:
            if error.code in ("no_credits", "auth", "no_api_key"):
                raise
            out["warnings"].append(f"The table on p. {page_no} could not be read from its image ({error}); "
                                   "it is sent as text.")
            continue
        original = page_texts[page_no - 1].split("\n", 1)[-1]
        page_texts[page_no - 1] = reading_block(page_no, result["reading"], original)
        flag = review_flag(page_no, result)
        if flag:
            out["flags"].append(flag)
            out["notes"].append(flag["why"])
    _rebuild_texts(prepared)
    read = len(todo) - sum(1 for w in out["warnings"] if "could not be read" in w)
    on_stage("tables", "done", f"{read} table{'s' if read != 1 else ''} read"
             + (f" · {len(out['flags'])} to check" if out["flags"] else ""))
    return out


def _rebuild_texts(prepared: dict[str, Any]) -> None:
    """Rebuild the text, selected text and table text from the (updated) page texts."""
    page_texts = prepared["pageTexts"]
    picked = prepared.get("picked") or list(range(len(page_texts)))
    prepared["text"] = "\n\n".join(page_texts[i] for i in picked)
    if prepared.get("selected"):
        prepared["selectedText"] = "\n\n".join(page_texts[i] for i in prepared["selected"])
    if prepared.get("tablePages"):
        prepared["tableText"] = "\n\n".join(page_texts[n - 1] for n in prepared["tablePages"])
