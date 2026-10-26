"""Show what stage 1 (reading the PDF) hands to stage 2 (the AI prompts), without calling any AI.

    python scripts/kit_inspect_stage1.py <protocol.pdf> [output folder]

Writes, into the output folder (default: <pdf name>_stage1 next to the PDF):
  00_README.txt            what each file is
  01_summary.txt           pages, schedule tables found and how, symbols that needed a legend
  02_text_sent_to_ai.txt   the protocol text exactly as the first AI call receives it
  03_table_text.txt        the schedule-table pages the cross-check (second AI call) receives
  04_borderless_tables.txt tables without cell borders: mark counts per row and column
  05_symbol_legend.txt     the legend note (+ 05_symbol_legend.png, the image shown to the AI)
  06_first_prompt.txt      the complete first prompt: instructions + rules + the text above
  pages/table_pNN.jpg      the page images sent with the cross-check
  stage1.json              everything else stage 1 returns (images left out)
No network, no database; nothing is changed.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.kit.pdf_input import prepare_protocol_input  # noqa: E402
from app.kit.prompts import build_prompt  # noqa: E402

README = """What stage 1 (backend/app/kit/pdf_input.py) produced for this PDF, i.e. the input of stage 2
(backend/app/kit/prompts.py, which turns it into the two AI prompts).

01_summary.txt            Overview: pages, schedule tables found, how they were found.
02_text_sent_to_ai.txt    The protocol text exactly as the first AI call receives it. Look for:
                            [[Page n]]                                  an ordinary page
                            [[Page n · schedule table as a grid]]       a ruled table as | cells |
                                                                        (← merged with the cell to the left,
                                                                         ↑ merged with the cell above)
                            [[Page n · schedule table without ruled
                              lines, column layout kept]]               a borderless table; before the first
                                                                        AI call the model reads it from the
                                                                        page image and replaces this block
                            [S1], [S2]...                               a symbol the PDF has no readable
                                                                        character for (see the legend)
03_table_text.txt         Only the schedule-table pages: what the cross-check (second AI call) compares against.
04_borderless_tables.txt  Mark counts per row and column used to check the AI's reading of borderless tables.
05_symbol_legend.*        The note and image that show the AI each unusual symbol exactly as printed.
06_first_prompt.txt       The full first prompt (instructions, JSON shape, rules, then the text of 02).
pages/                    Page images of the schedule tables (sent with the cross-check).
stage1.json               All other fields stage 1 returns.
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else pdf.with_name(pdf.stem[:60] + "_stage1")
    (out / "pages").mkdir(parents=True, exist_ok=True)

    prepared = prepare_protocol_input(pdf.read_bytes())
    legend = prepared.get("symbolLegend") or {}
    aligned = prepared.get("alignedTables") or []

    (out / "00_README.txt").write_text(README, encoding="utf-8")
    sent = "all pages" if prepared["fullText"] else f"{len(prepared['picked'])} selected pages"
    summary = [
        f"File: {pdf.name}",
        f"Pages: {prepared['pageCount']}   scanned (no text layer): {prepared['scanned']}",
        f"Text sent to the AI: {len(prepared['text']):,} characters ({sent})",
        f"Schedule-table pages: {prepared['tablePages'] or 'none found'}   found by: {prepared['tableSource'] or '-'}",
        f"  ruled tables (sent as grids): {[n for n in prepared['tablePages'] if n not in {t['page'] for t in aligned}]}",
        f"  tables without cell borders (read from the image by the AI): {[t['page'] for t in aligned]}",
        f"Page images for the cross-check: {[i['page'] for i in prepared['tableImages']]}",
        f"Symbol legend rows: {legend.get('count', 0)}",
    ]
    (out / "01_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (out / "02_text_sent_to_ai.txt").write_text(prepared["text"], encoding="utf-8")
    (out / "03_table_text.txt").write_text(prepared["tableText"], encoding="utf-8")

    lines = []
    for t in aligned:
        lines.append(f"Page {t['page']}: {t['total']} marks {t['marks']}")
        lines += [f"  row    {r['count']:3}  {r['label'] or '(no label)'}" for r in t["rows"]]
        lines += [f"  column {c['count']:3}  {c['header'] or '(no heading)'}" for c in t["columns"]]
        lines.append("")
    (out / "04_borderless_tables.txt").write_text("\n".join(lines) or "None on this PDF.\n", encoding="utf-8")

    (out / "05_symbol_legend.txt").write_text(legend.get("note") or "No legend needed for this PDF.\n",
                                              encoding="utf-8")
    if legend.get("image"):
        (out / "05_symbol_legend.png").write_bytes(base64.b64decode(legend["image"]["base64"]))
    for img in prepared["tableImages"]:
        (out / "pages" / f"table_p{img['page']:03}.jpg").write_bytes(base64.b64decode(img["base64"]))

    (out / "06_first_prompt.txt").write_text(build_prompt(prepared["text"], legend.get("note") or ""),
                                             encoding="utf-8")
    rest = {k: v for k, v in prepared.items()
            if k not in ("pdfBytes", "tableImages", "scanImages", "text", "selectedText", "tableText", "pageTexts")}
    if rest.get("symbolLegend"):
        rest["symbolLegend"] = {k: v for k, v in rest["symbolLegend"].items() if k != "image"}
    (out / "stage1.json").write_text(json.dumps(rest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print("\n".join(summary))
    print(f"\nWritten to: {out}")


if __name__ == "__main__":
    main()
