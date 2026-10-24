# Extraction evidence verification - deferred backlog (22 October 2026)

Status: **design notes only. Nothing here is implemented, and implementation is deferred** until the owner has finished reading through the extraction code and says to start. Saving this note does not authorize deployment, clinical policy changes or data migration.

Scope: `backend/app/extraction/` (reader, facts, ledger, compiler). Written from a code-reading session; the current-state claims below were read from the code, not measured by running it.

## 1. Current state (what the code does today)

| Area | Behavior | Where |
|---|---|---|
| Quote check | Normalizes quote and page text (lowercase, letters/digits only). Found if the quote is a substring, or if a quote of 3+ words has at least 85% of its words present on the page. Word presence only: order and meaning are not checked. | `reader.py` `quote_found` (~700) |
| Citation states | `TEXT_MATCHED`, `UNVERIFIED` (page has no text layer), `REJECTED` (page missing, or quote not on the page). The model's own claim is overwritten with `None` first. | `reader.py` `verify_citation` (~777) |
| Fact-level state | Weakest of its citations. | `ledger.py` `evidence_state` (~208) |
| Scanned pages: reading | Pages with no text, or garbled text (`looks_scrambled`), are `image_or_empty`. The provider client attaches the PDF (trimmed to selected pages by `for_document`), so a vision-capable model can read them. | `documents.py`, `llm_client.py` |
| Scanned pages: checking | `verify_citation` compares against `page.text`, which is empty, so every fact cited from a scan is `UNVERIFIED`. No OCR was found in these files. | `reader.py` ~789 |
| Meaning check | None. A real quote with the wrong interpretation (for example "every 3 months" recorded as 6 MONTH) passes. | - |
| False None | A value the protocol states but the reader left `None` is not re-checked against the rest of the document. Batches are 8 pages each, sharing only visit/anchor/arm names. Pages scoring below the keyword threshold are never read. | `reader.py` `plan_pages`, `read_document` |

## 2. Proposed changes (in recommended order)

### A. Deterministic field-vs-quote check
- For fields that hold literal numbers and units (`Duration`, windows, counts, cycle length, `OccurrenceSelector`), require that the cited quote contains a number and unit that agree.
- On disagreement, flag the fact (blocking for material use) with the mismatched field named. Do not correct it.
- Cheap, explainable, no new model cost. Catches the most dangerous class of error (wrong number or unit).
- Open question: how to handle wording such as "q3w", "twice weekly", "±", ranges and Day-0 conventions without false alarms.

### B. Independent text layer for image-only pages
- Add an OCR (or transcription) pass for `image_or_empty` pages so quotes can be checked against it.
- Use a distinct label, for example `OCR_MATCHED`, never `TEXT_MATCHED`, so a reviewer knows the weaker kind of check was done.
- Prefer an engine independent of the extraction model. A second call to the same model family is easier but less independent.
- Open questions: engine choice (local vs cloud), handling of OCR noise in `quote_found`, PHI/data-handling implications of sending pages to a third party, and the provider's request limits (page count and file size must be confirmed against current provider docs, not the general web summary used in the session).

### C. Batched LLM meaning check
- Input per fact: the quote (plus surrounding page text if needed) and the structured fact. Question: does the quote support exactly these fields?
- Structured output per fact: `fact_id`, `verdict` (SUPPORTED / NOT_SUPPORTED / PARTLY), `mismatched_fields`, `reason`.
- Flag only. Never rewrite a fact. A `NOT_SUPPORTED` fact is blocked from material use and shown to a reviewer, like `REJECTED` today.
- Risks and mitigations:
  - Rubber-stamping on long lists: batch by page group (roughly 20-40 facts), and seed known-bad facts to confirm the verifier is attentive.
  - Cut-off replies: a fact with no verdict is `UNVERIFIED`, never supported.
  - Not independent of the reader: prefer a different model or provider.
  - Anchoring: preferred variant is a **blind** pass where the verifier extracts the fields from the quote alone, then plain code diffs them against the reader's fact.
- Reuse `artifacts.py` so a retry reuses completed verifier answers. Count against `MAX_MODEL_CALLS`.
- Placement: after the reader and citation check, before reconcile and compile (to be confirmed).

### D. False-None mitigation (separate from A-C)
- Idea: before accepting an unresolved or null value, search the full document for the missing item and show the reviewer any candidate sentences.
- Only worth building if measurement shows false Nones are common.

## 3. Measure before building
1. On several real protocols, hand-check 5-10 unresolved or null items against the PDF. Count how many are actually stated (false Nones).
2. Count facts whose numbers disagree with their own quote (target for A).
3. Count facts cited from `image_or_empty` pages (size of B).
4. Re-run with a verifier prototype and record found-real-error vs false-alarm rates (C).
Decide scope from these numbers. The `prototypes/extraction_lab/` directory and the existing extraction quality-gate tests may already measure part of this; check them first.

## 4. Guardrails for whoever implements this
- Follow the project rule: never default or guess an unstated value; mark it UNRESOLVED / blocked-pending-review.
- All new checks flag for a human. No check may approve, correct or fill in a fact.
- Do not weaken the existing `REJECTED` / `UNVERIFIED` blocking.
- Keep new states distinguishable in the statement ledger and on the review screens.
- Run the curated backend gate from `MTB_IMPLEMENTATION_HANDOFF.txt` section E (not plain `pytest`), minus the deleted extraction test files noted in `AGENTS.md`. Note that `AGENTS.md` is stale about the extraction engine: it exists and only returns 503 when no provider is configured.

## 5. Other items parked from the same session
- **Visit order:** Screening can appear in the middle or last when its timing is unresolved, when the cycle length is unstated, or when its anchor name matches none of the `ordering.py` stage patterns. Revisit with a concrete example. Files: `ordering.py`, `reconcile.py`.
- **Reading order of the extraction code:** contracts, facts, documents, runner, reader, reconcile, compiler (with constraints, ordering, statements, ledger), extraction_service, canonical_adapter, then the frontend upload screens. Walkthrough progress: contracts, facts, and the citation mechanism are covered; documents and runner are next.
