# Extraction: footnote reading, one-shot repair, evidence-page hint (2026-10-21)

Found by a real run of `Prot_003.pdf` (96 pages) through the live provider: 50 blocking findings, of which
the avoidable ones were malformed model output, un-interpreted footnotes and a mis-cited page. Changes:

| Problem seen | Cause | Change | Proof |
|---|---|---|---|
| `EXTRACTION_FACT_INVALID` (bare `30` where a duration `"30 DAY"` belongs) | the model's reply was wrong in shape and was never sent back | `reader._repair_invalid_facts`: ONE correction round per batch with the exact validation error; the corrected item must validate on its own, keeps its cited quote, nothing is converted locally; still-invalid items stay rejected and say `repair_attempted` | `tests/test_extraction_footnotes_repair.py` |
| `FOOTNOTE_NOT_INTERPRETED` for every footnote | a footnote with no rule had no outcome except "pending" | (1) the prompt requires every footnote, with `effect: RULE/NONE` + reason; (2) `reader.detect_footnote_lines` accounts for a footnote printed under a table that the model did not report (`found_by: TEXT_DETECTED`, pending); (3) new disposition `NO_SCHEDULING_EFFECT`: accepted only if the text is verified on its page, a reason was given AND a deterministic screen finds no timing/condition/population/requirement/cross-reference wording; otherwise it stays pending and says why the claim was refused | same test file |
| reviewer cannot record "no scheduling effect" | only a free-text resolve existed | `POST .../extraction-issues/{id}/resolve` takes an optional typed `resolution` (`NO_SCHEDULING_EFFECT`, accepted only for `FOOTNOTE_NOT_INTERPRETED`), stored in the review decision; the review view lists `footnote_findings`; `FootnotePanel` on the schedule review screen requires a sentence of reason | `test_uctsm_workflow_reliability.py`, `uctsm-visit-schedule.test.ts` |
| `EVIDENCE_QUOTE_NOT_FOUND` | the model cited page 35; the words are on page 37 | evidence stays REJECTED; the finding now says which page the words are on (`found_on_pages`) | same test file |

Not changed on purpose: a footnote the model calls harmless is never trusted on its word; an unstated
recurrence basis, unresolved Day 1 and conflicting synopsis/table statements still need a person.
A provider quota failure (`429`) still ends the run as `EXTRACTION_INTERRUPTED`; completed pages are kept.
