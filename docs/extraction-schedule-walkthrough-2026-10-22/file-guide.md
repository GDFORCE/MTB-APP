# File and symbol directory

Snapshot: 22 October 2026. See [the written guide](README.md) first.

Every function/class/type in the selected files is indexed below. Application notes are separately authored; source docstrings are copied as documentation and can be stale. Full per-line syntax annotations are in [the source companion](walkthrough.html).

## 1. backend/app/extraction/runner.py

Connects model reading, reconciliation and compilation; also runs queued jobs.

352 lines. [Open annotated source](walkthrough.html#file=0).

### `configured_provider` — L59–L69

[Read source and line explanations](walkthrough.html#file=0&line=59).

Reads the explicitly selected provider and checks that provider's key is present. Merely having another provider's key does not select extraction.

Source documentation (check against implementation):

> The explicitly selected provider, when its key is present.
>
> Explicit on purpose: a key set for another feature (remote-audit vision
> checks use the same keys) must not silently switch protocol extraction on.

### `engine_available` — L72–L74

[Read source and line explanations](walkthrough.html#file=0&line=72).

Source documentation (check against implementation):

> Whether a run can execute at all, so callers can say so up front.

### `engine_signature` — L77–L90

[Read source and line explanations](walkthrough.html#file=0&line=77).

Builds the compatibility identity for reusable results from provider, configured model string and prompt/reconciler/compiler versions. None disables compatibility reuse.

Source documentation (check against implementation):

> What a stored extraction result must have been produced by to be reused:
> provider, model, prompt/reader, reconciler and compiler versions. None when no
> provider is configured, so no stored result is ever called "compatible"
> against an engine that cannot be identified.

### `build_client` — L93–L103

[Read source and line explanations](walkthrough.html#file=0&line=93).

Source documentation (check against implementation):

> The configured model client. ``pdf_bytes`` is attached only when some
> page has no usable text, so scanned pages can be read from the image.

### `_client_for` — L106–L110

[Read source and line explanations](walkthrough.html#file=0&line=106).

Current implementation passes PDF bytes to the chosen client for all PDF reads, including PDFs with embedded text; do not infer text-only behavior from older docstrings.

### `ProtocolExtraction` — L114–L119

[Read source and line explanations](walkthrough.html#file=0&line=114).

Source documentation (check against implementation):

> One protocol read: trial details plus one result per schedule option.

### `build_accountability` — L122–L159

[Read source and line explanations](walkthrough.html#file=0&line=122).

Joins reader source coverage with compiler statement dispositions. A successful model reply is not itself a completeness claim: coverage and uncompiled statements are checked separately.

Source documentation (check against implementation):

> The JSON agent/review surfaces read (shape ``ACCOUNTABILITY_SCHEMA``):
> coverage from the reader, dispositions from the compiler, relations and
> anchor reconciliation from the whole-document pass.

### `extract_protocol` — L162–L181

[Read source and line explanations](walkthrough.html#file=0&line=162).

The central sequence: read_document -> reconcile -> compile_document. It prepends source trace entries, attaches merged accountability and returns trial metadata, facts and schedule results.

Source documentation (check against implementation):

> Reader, whole-document reconciliation, then compiler. Blocking (model
> calls); run it off the event loop. ``artifacts`` makes the read resumable:
> completed calls for this document are reused, not repeated.

### `extract_trial_details` — L184–L188

[Read source and line explanations](walkthrough.html#file=0&line=184).

Reads Add Trial metadata only. This does not run the whole facts/reconciliation/schedule compiler.

Source documentation (check against implementation):

> Only the Add-Trial form fields: one bounded model call.

### `extract_schedules` — L191–L204

[Read source and line explanations](walkthrough.html#file=0&line=191).

Source documentation (check against implementation):

> One ``(schedule option, result)`` pair per independent Schedule of
> Assessments the document prints; the option is None for a single schedule.

### `storage_artifacts` — L207–L210

[Read source and line explanations](walkthrough.html#file=0&line=207).

Source documentation (check against implementation):

> Artifacts in the app's file storage (local disk or S3).

### `_clear_identity` — L213–L230

[Read source and line explanations](walkthrough.html#file=0&line=213).

Source documentation (check against implementation):

> P2-02 worker gate: the stored document must pass protocol identity
> verification before a run starts, whatever queued it. Raises
> ``IdentityBlockedError`` (recorded on the run as FAILED, no model call).

### `execute_run` — L233–L282

[Read source and line explanations](walkthrough.html#file=0&line=233).

SQL-session worker retained alongside the repository-port worker. It claims and commits before loading/calling the model, then commits drafts or records failure.

Source documentation (check against implementation):

> Run one queued extraction to completion.
>
> Returns the new schedule version ids, or None when extraction failed. The
> failure is already recorded on the run, so the caller does not have to.

### `execute_run_on_ports` — L285–L352

[Read source and line explanations](walkthrough.html#file=0&line=285).

Repository-port worker: claims a leased run, renews the lease on a heartbeat, loads and identity-checks the source, offloads extraction, then completes with the lease token. A worker that lost ownership cannot overwrite another worker's outcome.

Source documentation (check against implementation):

> ``execute_run`` on the repository ports (no SQLAlchemy session).
>
> Every state change is its own unit of work, and none is held open across the
> model calls: the run is marked RUNNING and committed, the slow blocking read
> happens with no transaction, and completion commits all drafts together.
> Failures are recorded on the run rather than raised.

### `execute_run_on_ports.heartbeat` — L312–L315

[Read source and line explanations](walkthrough.html#file=0&line=312).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 2. backend/app/extraction/documents.py

Turns stored PDF/text bytes into numbered pages and model-visible text.

97 lines. [Open annotated source](walkthrough.html#file=1).

### `pages_from_pdf` — L15–L33

[Read source and line explanations](walkthrough.html#file=1&line=15).

Extracts embedded text page by page. Suspected broken-font text is classified for image reading. The original one-based physical PDF page number is retained for evidence.

Source documentation (check against implementation):

> Read a PDF into per-page text.
>
> Page numbers are 1-based and preserved, because every piece of evidence a
> reviewer checks points at a page. Each page is also classified by how much
> embedded text it has, so scanned pages can be routed to an image reader.

### `looks_scrambled` — L39–L49

[Read source and line explanations](walkthrough.html#file=1&line=39).

Uses a Latin-script vowel-frequency heuristic, not a language-independent readability proof. Fewer than 20 candidate words do not trigger it.

Source documentation (check against implementation):

> Whether a page's embedded text is unreadable character soup.
>
> Real prose in any Latin-script language has a vowel in nearly every word;
> text extracted through a broken font map does not.

### `pages_from_text` — L52–L58

[Read source and line explanations](walkthrough.html#file=1&line=52).

Source documentation (check against implementation):

> Split plain text on form feeds, falling back to a single page.

### `document_text` — L68–L82

[Read source and line explanations](walkthrough.html#file=1&line=68).

Adds explicit physical-page markers and image-reading notices. Exceeding the text budget raises an error instead of silently truncating later pages.

Source documentation (check against implementation):

> The document as a model sees it, with page markers it can cite.

### `load_pages` — L85–L97

[Read source and line explanations](walkthrough.html#file=1&line=85).

Reads source bytes from storage and returns both parsed pages and original PDF bytes when applicable. Text uploads return None for the PDF part.

Source documentation (check against implementation):

> Fetch a stored protocol document and turn it into pages.
>
> Returns the raw PDF bytes alongside the pages (``None`` for a plain-text
> upload) so an engine can use the native PDF without fetching it again.

## 3. backend/app/extraction/contracts.py

Defines DocumentPage and ExtractionResult: the inputs and outputs of extraction.

35 lines. [Open annotated source](walkthrough.html#file=2).

### `DocumentPage` — L18–L24

[Read source and line explanations](walkthrough.html#file=2&line=18).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionResult` — L27–L35

[Read source and line explanations](walkthrough.html#file=2&line=27).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 4. backend/app/extraction/facts.py

Defines typed facts, citations and the document-level intermediate representation.

621 lines. [Open annotated source](walkthrough.html#file=3).

### `FactModel` — L26–L32

[Read source and line explanations](walkthrough.html#file=3&line=26).

Preserves extra model keys so the boundary can report unrecognized information rather than silently discard it. Preservation does not make those keys executable.

### `unknown_fields` — L41–L58

[Read source and line explanations](walkthrough.html#file=3&line=41).

Finds extra fields recursively. This is how unrecognized model output can remain visible to review.

Source documentation (check against implementation):

> Dotted paths of every key the schema does not define, at any depth.
>
> Used at the normalized boundary: the compiler reads only typed fields, so
> whatever is listed here was NOT interpreted and must be reviewed.

### `Duration` — L74–L98

[Read source and line explanations](walkthrough.html#file=3&line=74).

Source documentation (check against implementation):

> A signed amount of time exactly as the protocol states it.

### `Duration.from_text` — L82–L98

[Read source and line explanations](walkthrough.html#file=3&line=82).

Accepts stated duration text such as '-7 days' and normalizes known unit spellings; unrecognized amounts raise validation errors.

### `Citation` — L159–L170

[Read source and line explanations](walkthrough.html#file=3&line=159).

The reader, not the model, sets verification against source text. A real quote proves provenance, not necessarily correct clinical interpretation.

### `Criterion` — L173–L179

[Read source and line explanations](walkthrough.html#file=3&line=173).

Source documentation (check against implementation):

> A machine-checkable clinical threshold ("ANC >= 1500"), only when the
> protocol states one. Free text stays in the fact's quote.

### `ReferenceTo` — L182–L207

[Read source and line explanations](walkthrough.html#file=3&line=182).

Source documentation (check against implementation):

> What a timing counts from. ``kind`` None lets the compiler look the name
> up among the anchors, visits and conditions it knows.

### `ReferenceTo.from_text` — L198–L199

[Read source and line explanations](walkthrough.html#file=3&line=198).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReferenceTo.normalise_accepts` — L203–L207

[Read source and line explanations](walkthrough.html#file=3&line=203).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleSpec` — L210–L217

[Read source and line explanations](walkthrough.html#file=3&line=210).

Source documentation (check against implementation):

> Which treatment cycle a CYCLE_DAY fact sits in.

### `RepeatEnd` — L220–L230

[Read source and line explanations](walkthrough.html#file=3&line=220).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RepeatSpec` — L233–L239

[Read source and line explanations](walkthrough.html#file=3&line=233).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RepeatSegment` — L242–L258

[Read source and line explanations](walkthrough.html#file=3&line=242).

Source documentation (check against implementation):

> One cadence of a phased follow-up plan (spec E9).
>
> Segments follow one another: segment 1 starts at ``start`` (None = the
> plan reference itself) and every later segment starts where the previous
> one ended, so a later ``start`` is only a cross-check. A segment ends at
> ``end`` (an offset from the plan reference: "to year 2") or after
> ``count`` visits; the final segment may state neither, and then the fact's
> ``repeat.end`` says what ends the plan.

### `OccurrenceSelector` — L261–L288

[Read source and line explanations](walkthrough.html#file=3&line=261).

Source documentation (check against implementation):

> Which cycles an activity is done in (spec F5), only as stated.
>
> ``numbers``: the protocol lists them ("Cycle 1 only" -> [1], "Cycles 1-3"
> -> [1, 2, 3]). ``parity``: "on even cycles". ``every`` + ``starting_at``:
> "every second cycle starting with Cycle 2". An every-N rule whose first
> cycle is not stated is ambiguous (C1, C3, ... or C2, C4, ...) and stays an
> unresolved note.

### `OccurrenceSelector.single_number` — L278–L281

[Read source and line explanations](walkthrough.html#file=3&line=278).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceSelector.normalise_parity` — L285–L288

[Read source and line explanations](walkthrough.html#file=3&line=285).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WindowSpec` — L291–L296

[Read source and line explanations](walkthrough.html#file=3&line=291).

Source documentation (check against implementation):

> Allowed deviation around a target. Each half is independently nullable
> and never defaulted: "may be 3 days late" states ``after`` only.

### `ConditionSpec` — L299–L344

[Read source and line explanations](walkthrough.html#file=3&line=299).

Source documentation (check against implementation):

> What a clinical condition does to the schedule.

### `ConditionSpec.prose_criteria` — L332–L344

[Read source and line explanations](walkthrough.html#file=3&line=332).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FactTiming` — L347–L384

[Read source and line explanations](walkthrough.html#file=3&line=347).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FactTiming.normalise_weekdays` — L371–L376

[Read source and line explanations](walkthrough.html#file=3&line=371).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FactTiming.normalise_pattern` — L380–L384

[Read source and line explanations](walkthrough.html#file=3&line=380).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AppliesTo` — L387–L401

[Read source and line explanations](walkthrough.html#file=3&line=387).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AppliesTo.all_means_everyone` — L396–L401

[Read source and line explanations](walkthrough.html#file=3&line=396).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnchorSource` — L404–L410

[Read source and line explanations](walkthrough.html#file=3&line=404).

Source documentation (check against implementation):

> Where an anchor's date comes from.

### `_normalise_mode` — L420–L423

[Read source and line explanations](walkthrough.html#file=3&line=420).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleFact` — L426–L516

[Read source and line explanations](walkthrough.html#file=3&line=426).

One intermediate obligation or anchor/condition statement, with source evidence. Missing timing/mode/window fields can be represented here; later compilation and validation determine readiness.

Source documentation (check against implementation):

> One cited, atomic statement about the protocol's schedule.

### `ScheduleFact.normalise_statement_type` — L490–L494

[Read source and line explanations](walkthrough.html#file=3&line=490).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleFact.normalise_kind` — L498–L499

[Read source and line explanations](walkthrough.html#file=3&line=498).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleFact.normalise_dependency_mode` — L503–L504

[Read source and line explanations](walkthrough.html#file=3&line=503).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleFact.normalise_scope` — L508–L511

[Read source and line explanations](walkthrough.html#file=3&line=508).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleFact.single_citation` — L515–L516

[Read source and line explanations](walkthrough.html#file=3&line=515).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleOption` — L519–L524

[Read source and line explanations](walkthrough.html#file=3&line=519).

Source documentation (check against implementation):

> One independent Schedule of Assessments (e.g. a substudy).

### `DayNumberingFact` — L527–L532

[Read source and line explanations](walkthrough.html#file=3&line=527).

Source documentation (check against implementation):

> Which anchor is Day 1 and how days are counted - only as stated.

### `DimensionFact` — L535–L551

[Read source and line explanations](walkthrough.html#file=3&line=535).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DimensionFact.normalise_parent_type` — L548–L551

[Read source and line explanations](walkthrough.html#file=3&line=548).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `BlockFact` — L554–L565

[Read source and line explanations](walkthrough.html#file=3&line=554).

Source documentation (check against implementation):

> A repeating treatment block and how later cycles are timed (spec F1-F3).

### `BlockFact.normalise_dependency_mode` — L564–L565

[Read source and line explanations](walkthrough.html#file=3&line=564).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrialMetadata` — L568–L587

[Read source and line explanations](walkthrough.html#file=3&line=568).

Source documentation (check against implementation):

> Add-Trial form fields. Unstated values stay empty, never invented.

### `TrialMetadata.none_is_empty` — L586–L587

[Read source and line explanations](walkthrough.html#file=3&line=586).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReaderIssue` — L590–L599

[Read source and line explanations](walkthrough.html#file=3&line=590).

Source documentation (check against implementation):

> Something the reader itself could not do (a batch it could not parse,
> a quote not found on its cited page). Carried into the schedule so a
> reviewer sees it; never silently dropped.

### `DocumentFacts` — L602–L621

[Read source and line explanations](walkthrough.html#file=3&line=602).

One document's typed metadata, dimensions, treatment blocks, facts, reader issues, trace and accountability. It has no per-patient schedule dates.

Source documentation (check against implementation):

> Everything the reader found in one protocol.

## 5. backend/app/extraction/reader.py

Selects and batches source pages, calls the model, validates facts and accounts for coverage.

1335 lines. [Open annotated source](walkthrough.html#file=4).

### `_env_number` — L85–L94

[Read source and line explanations](walkthrough.html#file=4&line=85).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReaderLimits` — L98–L133

[Read source and line explanations](walkthrough.html#file=4&line=98).

Source documentation (check against implementation):

> Technical limits for one read. Exceeding one stops the run and marks
> what was not read on the coverage ledger; it never claims completeness.

### `ReaderLimits.from_env` — L118–L130

[Read source and line explanations](walkthrough.html#file=4&line=118).

The product environment path defaults to reading all pages, within configured budgets. Direct ReaderLimits() has read_all_pages=False; do not conflate these two construction paths.

### `ReaderLimits.as_dict` — L132–L133

[Read source and line explanations](walkthrough.html#file=4&line=132).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `score_page` — L174–L175

[Read source and line explanations](walkthrough.html#file=4&line=174).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `amendment_history_pages` — L193–L207

[Read source and line explanations](walkthrough.html#file=4&line=193).

Source documentation (check against implementation):

> Pages that describe changes between protocol versions.
>
> A page qualifies on two distinct markers; a page lying between two such
> pages (at most five apart) belongs to the same listing even if its own text
> did not extract.

### `plan_pages` — L210–L238

[Read source and line explanations](walkthrough.html#file=4&line=210).

Builds selected/not-selected/over-budget page lists. Depending on limits it reads all eligible pages or ranks keyword matches; image pages and table continuations have explicit handling.

Source documentation (check against implementation):

> Which pages to read, and why each other page is not read.
>
> A page that starts a schedule table also pulls in the page after it,
> because tables span pages. Image-only pages cannot be scored from text, so
> they are kept (up to the limit) for a client that can read the page image.
> Pages cut by ``limit`` are returned separately so they are marked
> BUDGET_EXHAUSTED rather than silently dropped.

### `select_pages` — L241–L243

[Read source and line explanations](walkthrough.html#file=4&line=241).

Source documentation (check against implementation):

> Page numbers worth reading, in document order (see ``plan_pages``).

### `UnusableReply` — L478–L479

[Read source and line explanations](walkthrough.html#file=4&line=478).

Source documentation (check against implementation):

> The model answered, twice, with something that could not be parsed.

### `BudgetExhausted` — L482–L483

[Read source and line explanations](walkthrough.html#file=4&line=482).

Source documentation (check against implementation):

> A configured call or time limit was reached before this call.

### `model_identity` — L486–L489

[Read source and line explanations](walkthrough.html#file=4&line=486).

Source documentation (check against implementation):

> Provider class plus model name: part of every artifact key (NF-02).

### `_Run` — L493–L523

[Read source and line explanations](walkthrough.html#file=4&line=493).

Source documentation (check against implementation):

> Everything one read shares: client, limits, artifacts, usage.

### `_Run.model` — L508–L509

[Read source and line explanations](walkthrough.html#file=4&line=508).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Run.check_budget` — L511–L517

[Read source and line explanations](walkthrough.html#file=4&line=511).

Enforces technical call/time budgets before further calls. These are extraction-resource limits, not clinical recurrence limits.

### `_Run.store` — L519–L523

[Read source and line explanations](walkthrough.html#file=4&line=519).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_pages_block` — L526–L527

[Read source and line explanations](walkthrough.html#file=4&line=526).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_scoped` — L530–L538

[Read source and line explanations](walkthrough.html#file=4&line=530).

Source documentation (check against implementation):

> Attach just these pages to the client when it supports document packets
> (so scanned pages can be read from the PDF image); otherwise the page text
> goes into the prompt itself.

### `_ask` — L541–L613

[Read source and line explanations](walkthrough.html#file=4&line=541).

Looks up a completed artifact for the exact question before spending a model call. Stores attempts, parses replies and allows one parse-repair retry. Transport errors are recorded and propagated.

Source documentation (check against implementation):

> One bounded question with at most one repair retry on a malformed reply.
>
> A completed answer to the identical question (same document, prompt
> version, model, pages and prompt text) is reused from the artifact store
> instead of calling the model. Every attempt - reply or failure - is stored.
> Transport failures (the model could not be reached) propagate after being
> recorded; only a reply that arrived but could not be parsed is retried.

### `_metadata_pages` — L618–L628

[Read source and line explanations](walkthrough.html#file=4&line=618).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_list_of` — L631–L650

[Read source and line explanations](walkthrough.html#file=4&line=631).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `read_metadata` — L653–L665

[Read source and line explanations](walkthrough.html#file=4&line=653).

Source documentation (check against implementation):

> Trial details, schedule options, Day 1 statement, arms, blocks and names.

### `read_trial_metadata` — L668–L671

[Read source and line explanations](walkthrough.html#file=4&line=668).

Source documentation (check against implementation):

> Only the Add-Trial form fields (``/protocols/extract-details``).

### `batches` — L676–L693

[Read source and line explanations](walkthrough.html#file=4&line=676).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_normalise` — L696–L697

[Read source and line explanations](walkthrough.html#file=4&line=696).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `quote_found` — L700–L710

[Read source and line explanations](walkthrough.html#file=4&line=700).

Source documentation (check against implementation):

> Whether a cited quote is really on the page, tolerant of PDF text noise
> (hyphenation, ligatures, line breaks) but not of invented wording.

### `detect_footnote_lines` — L718–L728

[Read source and line explanations](walkthrough.html#file=4&line=718).

Source documentation (check against implementation):

> Footnote-looking lines on a page that prints a table. Deterministic and conservative:
> it only exists so a footnote the model forgot to report is still accounted for.

### `_facts_prompt` — L731–L750

[Read source and line explanations](walkthrough.html#file=4&line=731).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `parse_facts_reply` — L756–L774

[Read source and line explanations](walkthrough.html#file=4&line=756).

Source documentation (check against implementation):

> ``{"facts": [...], "table_cells": [...], "footnotes": [...]}`` from a reply.
>
> ``facts`` is required (a bare list is accepted as the facts); the table
> lists are optional. Top-level keys outside the contract are returned so
> they are reported, not dropped.

### `verify_citation` — L777–L817

[Read source and line explanations](walkthrough.html#file=4&line=777).

Checks cited page membership and quote text, producing TEXT_MATCHED, UNVERIFIED or REJECTED. Image-only evidence cannot be proven by an embedded-text comparison.

Source documentation (check against implementation):

> TEXT_MATCHED / UNVERIFIED / REJECTED for one citation, plus the issue
> to raise. The model's own claim about verification is never used.

### `_cells_to_facts` — L820–L855

[Read source and line explanations](walkthrough.html#file=4&line=820).

Turns marked schedule-table cells into activity facts tied to the stated visits. This provides structured table information alongside narrative facts.

Source documentation (check against implementation):

> Structured SoA reading: every marked cell of every column, grouped per
> row into one activity fact performed at the marked visits. The row label
> is the quote (it is printed on the page and is verified like any quote).

### `_repair_invalid_facts` — L858–L891

[Read source and line explanations](walkthrough.html#file=4&line=858).

Requests one bounded correction of malformed facts. Invalid facts that cannot be corrected remain rejected rather than becoming assumed valid.

Source documentation (check against implementation):

> One bounded correction round for facts that failed validation. Returns the facts that
> now validate, keyed by their statement id; everything else stays rejected.

### `_repair_invalid_facts.parse` — L870–L871

[Read source and line explanations](walkthrough.html#file=4&line=870).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_read_batch` — L894–L1027

[Read source and line explanations](walkthrough.html#file=4&line=894).

Parses one batch's facts, table cells and footnotes; validates each item, records rejected/unknown information and verifies evidence. Raw failures remain accountable.

Source documentation (check against implementation):

> Facts from one batch, and whether the batch's reply was usable.

### `_read_batch.admit` — L926–L942

[Read source and line explanations](walkthrough.html#file=4&line=926).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_follow_references` — L1030–L1090

[Read source and line explanations](walkthrough.html#file=4&line=1030).

Follows source section/appendix references under depth/page limits. A referenced source that cannot be read is represented in coverage instead of silently treated as absent.

Source documentation (check against implementation):

> Detect section/appendix references on the pages being read and follow
> them, bounded by ``reference_depth`` and the page limit, with every
> visited page tracked. Returns the new selection, the pages that could not
> be added for budget reasons and why.

### `_new_accountability` — L1093–L1094

[Read source and line explanations](walkthrough.html#file=4&line=1093).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `read_document` — L1097–L1244

[Read source and line explanations](walkthrough.html#file=4&line=1097).

Initializes budgets and coverage; identifies readable pages; reports embedded instruction text; handles amendment-history exclusions; reads metadata and page batches; accounts for failures; returns DocumentFacts. Partial useful reads can survive with blocking issues.

Source documentation (check against implementation):

> Read a whole protocol into cited facts with a bounded number of calls:
> one metadata call plus one call per page batch (each retried at most once),
> reusing any completed call already in ``artifacts``.
>
> The returned ``DocumentFacts.accountability`` carries the source coverage
> ledger, raw and rejected items, footnotes and versions.

### `_coverage_issues` — L1247–L1297

[Read source and line explanations](walkthrough.html#file=4&line=1247).

Converts unread material, missing required references and interrupted reading into explicit review findings. Not every diagnostic has blocking=True; inspect the constructed issue.

Source documentation (check against implementation):

> One blocking issue per kind of coverage gap: approval cannot pass while
> required source material is unread (AT-13, AT-20).

### `_coverage_issues.pages_in` — L1251–L1253

[Read source and line explanations](walkthrough.html#file=4&line=1251).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_finish_manifest` — L1300–L1314

[Read source and line explanations](walkthrough.html#file=4&line=1300).

Writes a resumable manifest with source hashes, executed stages and usage counters. Durable storage failure is surfaced instead of changing completed answers into new facts.

Source documentation (check against implementation):

> Persist the document manifest and stage list (resumable checkpoint).

### `_one` — L1317–L1335

[Read source and line explanations](walkthrough.html#file=4&line=1317).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 6. backend/app/extraction/llm_client.py

Implements the common model-call contract for Anthropic, Gemini and OpenAI.

367 lines. [Open annotated source](walkthrough.html#file=5).

### `ExtractionModelError` — L40–L41

[Read source and line explanations](walkthrough.html#file=5&line=40).

Source documentation (check against implementation):

> The model could not be reached or did not answer usefully.

### `ExtractionNotConfigured` — L44–L45

[Read source and line explanations](walkthrough.html#file=5&line=44).

Source documentation (check against implementation):

> No API key: the caller should surface this rather than silently degrade.

### `LLMClient` — L48–L57

[Read source and line explanations](walkthrough.html#file=5&line=48).

Source documentation (check against implementation):

> Anything that can answer a prompt with text.
>
> Implementations must not retry silently forever, and must raise
> ExtractionModelError rather than returning a plausible-looking empty answer:
> an empty answer is indistinguishable from "the protocol does not say this",
> and those two must never be confused.

### `LLMClient.complete` — L57–L57

[Read source and line explanations](walkthrough.html#file=5&line=57).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DocumentContextClient` — L60–L82

[Read source and line explanations](walkthrough.html#file=5&line=60).

Source documentation (check against implementation):

> Create bounded document inputs without changing credentials or model.

### `DocumentContextClient.for_document` — L63–L82

[Read source and line explanations](walkthrough.html#file=5&line=63).

Creates a scoped client with only the selected original pages. The packet includes a mapping from packet positions to physical PDF page numbers.

### `AnthropicClient` — L85–L158

[Read source and line explanations](walkthrough.html#file=5&line=85).

Source documentation (check against implementation):

> Claude, via the Anthropic Messages API.
>
> The document text is sent as a cached prefix. A full extraction asks about
> nineteen semantic categories against the same protocol, so caching the
> document makes every call after the first far cheaper.

### `AnthropicClient.__init__` — L93–L108

[Read source and line explanations](walkthrough.html#file=5&line=93).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnthropicClient.configured` — L111–L112

[Read source and line explanations](walkthrough.html#file=5&line=111).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnthropicClient._client` — L114–L121

[Read source and line explanations](walkthrough.html#file=5&line=114).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnthropicClient.complete` — L123–L158

[Read source and line explanations](walkthrough.html#file=5&line=123).

Sends the reader's prompt through Anthropic Messages, including source context; extracts returned text and translates provider failure into ExtractionModelError.

### `GeminiClient` — L161–L232

[Read source and line explanations](walkthrough.html#file=5&line=161).

Source documentation (check against implementation):

> Google Gemini, via the genai SDK - the same ``LLMClient`` contract as
> ``AnthropicClient``, so ``ClaudeExtractionProvider`` (the assembler; the
> name predates this client and is unrelated to which provider answers)
> works unchanged regardless of which one is behind it. A fallback
> provider sitting behind the same interface, not a second pipeline.
>
> Gemini's own native PDF understanding covers the AD-2a image fallback the
> same way Claude's does (``types.Part.from_bytes``), so a caller's
> "attach the PDF only when a page needs it" decision applies unchanged no
> matter which client ends up handling the call.

### `GeminiClient.__init__` — L174–L185

[Read source and line explanations](walkthrough.html#file=5&line=174).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `GeminiClient.configured` — L188–L189

[Read source and line explanations](walkthrough.html#file=5&line=188).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `GeminiClient._client` — L191–L198

[Read source and line explanations](walkthrough.html#file=5&line=191).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `GeminiClient.complete` — L200–L232

[Read source and line explanations](walkthrough.html#file=5&line=200).

Sends the same reader question through Google's SDK and can attach native PDF content. This is a transport alternative for the same pipeline.

### `OpenAIClient` — L240–L316

[Read source and line explanations](walkthrough.html#file=5&line=240).

Source documentation (check against implementation):

> OpenAI, via the Chat Completions API - the same ``LLMClient`` contract
> as the Anthropic and Gemini clients.
>
> The reply is requested as a JSON object: every reader prompt asks for one,
> and constraining the output removes a whole class of unparseable replies.
> A scanned page is read from the attached PDF itself, sent as a file part.

### `OpenAIClient.__init__` — L249–L260

[Read source and line explanations](walkthrough.html#file=5&line=249).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OpenAIClient.configured` — L263–L264

[Read source and line explanations](walkthrough.html#file=5&line=263).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OpenAIClient._client` — L266–L273

[Read source and line explanations](walkthrough.html#file=5&line=266).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OpenAIClient.complete` — L275–L316

[Read source and line explanations](walkthrough.html#file=5&line=275).

Uses Chat Completions with a JSON-object response request; when present, the scoped PDF is attached as file content. Provider errors do not become an empty successful extraction.

### `parse_json_object` — L322–L347

[Read source and line explanations](walkthrough.html#file=5&line=322).

Tolerates presentation noise such as code fences/prefaces, but requires a JSON object; it does not prove that the object matches the facts schema.

Source documentation (check against implementation):

> Read one JSON object out of a model reply.
>
> Tolerates a code fence and a stray preface, because those are formatting
> noise rather than a difference of meaning. Anything else raises: a reply we
> cannot parse must become a visible extraction issue, never an empty result
> that reads as "the protocol does not mention this".

### `parse_json_list` — L350–L367

[Read source and line explanations](walkthrough.html#file=5&line=350).

Source documentation (check against implementation):

> Read ``{key: [...]}`` from a model reply, tolerating a bare list.

## 7. backend/app/extraction/reconcile.py

Normalizes facts across batches without calling a model.

323 lines. [Open annotated source](walkthrough.html#file=6).

### `anchor_key` — L46–L55

[Read source and line explanations](walkthrough.html#file=6&line=46).

Normalizes wording wrappers, punctuation and case for matching. It does not equate arbitrary clinical concepts with different names.

Source documentation (check against implementation):

> Wording-insensitive key: case, punctuation, articles and a "date of"
> wrapper do not change which date is meant. Different words do.

### `_semantic` — L58–L69

[Read source and line explanations](walkthrough.html#file=6&line=58).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_is_event_fact` — L72–L83

[Read source and line explanations](walkthrough.html#file=6&line=72).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_references` — L86–L97

[Read source and line explanations](walkthrough.html#file=6&line=86).

Source documentation (check against implementation):

> Every anchor-like reference a fact makes, with where it sits.

### `_timing_label` — L100–L117

[Read source and line explanations](walkthrough.html#file=6&line=100).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reconcile` — L120–L323

[Read source and line explanations](walkthrough.html#file=6&line=120).

Copies the document, creates stable source ordering, records duplicate relations, canonicalizes recognized anchor aliases, reports unmatched names and separates/groups occurrences using explicit context. No model call occurs.

Source documentation (check against implementation):

> Return a reconciled copy; the input is not modified.

### `reconcile.canonicalise` — L165–L181

[Read source and line explanations](walkthrough.html#file=6&line=165).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reconcile.order` — L316–L318

[Read source and line explanations](walkthrough.html#file=6&line=316).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 8. backend/app/extraction/compiler.py

Compiles cited facts into canonical schedule rules and review issues.

2562 lines. [Open annotated source](walkthrough.html#file=7).

### `_strip_ids` — L157–L163

[Read source and line explanations](walkthrough.html#file=7&line=157).

Source documentation (check against implementation):

> Meaning only: constraint ids and evidence links do not change a rule.

### `_page_ranges` — L180–L188

[Read source and line explanations](walkthrough.html#file=7&line=180).

Source documentation (check against implementation):

> [3,4,5,9] -> "3-5, 9".

### `code_for` — L191–L200

[Read source and line explanations](walkthrough.html#file=7&line=191).

Source documentation (check against implementation):

> A stable schedule code from a protocol name ("Cycle 1 Day 8" -> CYCLE_1_DAY_8).

### `_Unresolved` — L203–L204

[Read source and line explanations](walkthrough.html#file=7&line=203).

Source documentation (check against implementation):

> A fact the compiler cannot turn into a deterministic rule without guessing.

### `_Compiled` — L208–L235

[Read source and line explanations](walkthrough.html#file=7&line=208).

Source documentation (check against implementation):

> One fact's timing, before it is attached to an event.

### `_Compiled.signature` — L224–L235

[Read source and line explanations](walkthrough.html#file=7&line=224).

Source documentation (check against implementation):

> What two readings must agree on to be the same rule.

### `_CycleGroup` — L250–L262

[Read source and line explanations](walkthrough.html#file=7&line=250).

Source documentation (check against implementation):

> Every CYCLE_DAY statement of one treatment block, and the cycle plan they
> state together (length, dependency mode, what ends the cycles).

### `_dump` — L265–L271

[Read source and line explanations](walkthrough.html#file=7&line=265).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_amount` — L274–L275

[Read source and line explanations](walkthrough.html#file=7&line=274).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_positive` — L278–L281

[Read source and line explanations](walkthrough.html#file=7&line=278).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_same_unit` — L284–L297

[Read source and line explanations](walkthrough.html#file=7&line=284).

Source documentation (check against implementation):

> Express two amounts in one unit when that is exact (weeks -> days,
> years -> months).

### `_canonical` — L300–L305

[Read source and line explanations](walkthrough.html#file=7&line=300).

Source documentation (check against implementation):

> An amount in its exact base unit (days, or months), for comparison.

### `_criterion` — L308–L317

[Read source and line explanations](walkthrough.html#file=7&line=308).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_cycle_count_written_as_number` — L326–L345

[Read source and line explanations](walkthrough.html#file=7&line=326).

Source documentation (check against implementation):

> "Paclitaxel q3 wks x2 cycles" with ``cycle.number = 2`` means TWO cycles,
> not "Cycle 2 only". Corrected only when the cited words literally state the
> count ("x2 cycles", "for 2 cycles") and nothing else says what ends them.

### `_merge_phased` — L348–L391

[Read source and line explanations](walkthrough.html#file=7&line=348).

Source documentation (check against implementation):

> Spec E9 is ONE plan. A reader that wrote "every 3 months for 2 years",
> "every 6 months for years 3 and 4" and "annually" as three phased facts
> from the same reference is merged back into one plan - but only when the
> segments are contiguous (each starts exactly where the previous ends);
> anything else is left for the reviewer as it was stated.

### `_merge_phased.first_start` — L366–L368

[Read source and line explanations](walkthrough.html#file=7&line=366).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_months_or_days` — L399–L403

[Read source and line explanations](walkthrough.html#file=7&line=399).

Source documentation (check against implementation):

> Comparable size for contiguity only: months stay months, days stay days.

### `_Compiler` — L406–L2517

[Read source and line explanations](walkthrough.html#file=7&line=406).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.__init__` — L407–L459

[Read source and line explanations](walkthrough.html#file=7&line=407).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.issue` — L463–L477

[Read source and line explanations](walkthrough.html#file=7&line=463).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.cite` — L479–L501

[Read source and line explanations](walkthrough.html#file=7&line=479).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.claim` — L503–L511

[Read source and line explanations](walkthrough.html#file=7&line=503).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.citations` — L514–L515

[Read source and line explanations](walkthrough.html#file=7&line=514).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.ref` — L519–L530

[Read source and line explanations](walkthrough.html#file=7&line=519).

Source documentation (check against implementation):

> Record that these facts compiled into (part of) a rule.

### `_Compiler.outcome` — L532–L536

[Read source and line explanations](walkthrough.html#file=7&line=532).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.relate` — L538–L541

[Read source and line explanations](walkthrough.html#file=7&line=538).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._separate_non_schedule` — L543–L588

[Read source and line explanations](walkthrough.html#file=7&line=543).

Source documentation (check against implementation):

> Statements the reader classified NON_SCHEDULE never become visits -
> unless a schedule statement depends on them, in which case excluding
> them would conceal that dependency (design, gate sequence).

### `_Compiler._is_patient` — L592–L593

[Read source and line explanations](walkthrough.html#file=7&line=592).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._is_condition_fact` — L595–L597

[Read source and line explanations](walkthrough.html#file=7&line=595).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._is_event_fact` — L599–L610

[Read source and line explanations](walkthrough.html#file=7&line=599).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._intra_visit` — L613–L615

[Read source and line explanations](walkthrough.html#file=7&line=613).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._event_code` — L617–L619

[Read source and line explanations](walkthrough.html#file=7&line=617).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.collect_names` — L621–L659

[Read source and line explanations](walkthrough.html#file=7&line=621).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._day_1_code` — L661–L663

[Read source and line explanations](walkthrough.html#file=7&line=661).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.reference` — L665–L710

[Read source and line explanations](walkthrough.html#file=7&line=665).

Resolves an explicitly named dependency to an anchor/event/condition reference using collected symbols. This is reference resolution, not patient-date lookup.

Source documentation (check against implementation):

> Resolve a named reference to an anchor, visit or condition anchor.

### `_Compiler.condition_anchor_code` — L712–L720

[Read source and line explanations](walkthrough.html#file=7&line=712).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.compile_timing` — L724–L830

[Read source and line explanations](walkthrough.html#file=7&line=724).

Dispatches a stated timing pattern into typed timing expressions. Required missing amounts/references produce unresolved behavior instead of invented timing.

### `_Compiler.compile_timing.need` — L747–L750

[Read source and line explanations](walkthrough.html#file=7&line=747).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._weekday_series` — L832–L862

[Read source and line explanations](walkthrough.html#file=7&line=832).

Source documentation (check against implementation):

> WP3 WEEKDAY_GRID.v1: "Monday to Friday, 30 fractions". Weekdays,
> count basis, holiday and missed-session policies only as stated.

### `_Compiler._with_dependency` — L864–L882

[Read source and line explanations](walkthrough.html#file=7&line=864).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._termination` — L884–L924

[Read source and line explanations](walkthrough.html#file=7&line=884).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._attach_repeat` — L926–L954

[Read source and line explanations](walkthrough.html#file=7&line=926).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._cycle_day` — L956–L1005

[Read source and line explanations](walkthrough.html#file=7&line=956).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._cycle_block` — L1010–L1014

[Read source and line explanations](walkthrough.html#file=7&line=1010).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.collect_cycle_plans` — L1016–L1039

[Read source and line explanations](walkthrough.html#file=7&line=1016).

Source documentation (check against implementation):

> Spec F1-F5: one cycle plan per treatment block that has CYCLE_DAY rules.
>
> Length, what ends the cycles and how each cycle start is dated are
> properties of the block, so one statement of each suffices; two that
> disagree leave the plan unbuilt (and its visits unresolved). An unstated
> dependency mode stays UNCLEAR, which the validator blocks on.

### `_Compiler._build_cycle_plan` — L1041–L1104

[Read source and line explanations](walkthrough.html#file=7&line=1041).

Combines explicitly stated cycle-plan properties from facts and treatment-block metadata. Inconsistent or unstated properties become review issues.

### `_Compiler._phased` — L1106–L1166

[Read source and line explanations](walkthrough.html#file=7&line=1106).

Builds one segmented recurrence plan for phased follow-up. Segment boundaries and inclusivity come from source information, not an arbitrary default.

Source documentation (check against implementation):

> Spec E9: "every 3 months to year 2, then every 6 months to year 5,
> then yearly" is ONE visit with a segmented recurrence plan.

### `_Compiler.apply_window` — L1168–L1192

[Read source and line explanations](walkthrough.html#file=7&line=1168).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._dimension` — L1197–L1203

[Read source and line explanations](walkthrough.html#file=7&line=1197).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_dimensions` — L1205–L1217

[Read source and line explanations](walkthrough.html#file=7&line=1205).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._nest` — L1219–L1245

[Read source and line explanations](walkthrough.html#file=7&line=1219).

Source documentation (check against implementation):

> Spec H4: "Part A -> Cohort 1 or 2". The parent is recorded only when
> the protocol names one that resolves to exactly one defined group.

### `_Compiler.applicability` — L1247–L1278

[Read source and line explanations](walkthrough.html#file=7&line=1247).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._groups` — L1282–L1312

[Read source and line explanations](walkthrough.html#file=7&line=1282).

Source documentation (check against implementation):

> Facts describing the same visit, including stated alternatives.

### `_Compiler._groups.find` — L1286–L1290

[Read source and line explanations](walkthrough.html#file=7&line=1286).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._groups.union` — L1292–L1294

[Read source and line explanations](walkthrough.html#file=7&line=1292).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_events` — L1314–L1341

[Read source and line explanations](walkthrough.html#file=7&line=1314).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._recurring` — L1344–L1347

[Read source and line explanations](walkthrough.html#file=7&line=1344).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._pair_relation` — L1349–L1368

[Read source and line explanations](walkthrough.html#file=7&line=1349).

Source documentation (check against implementation):

> AND or CONFLICTS_WITH for two readings of one occurrence that
> compile to different rules (IR-04). Conservative: anything that is not
> clearly two restrictions on the same single occurrence conflicts.

### `_Compiler._span_days` — L1374–L1389

[Read source and line explanations](walkthrough.html#file=7&line=1374).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._span_days.days` — L1379–L1381

[Read source and line explanations](walkthrough.html#file=7&line=1379).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._table_precedence` — L1391–L1417

[Read source and line explanations](walkthrough.html#file=7&line=1391).

A narrow special case for one exact table reading and narrative summaries of that same timepoint. It is not a general rule that every table overrides narrative.

Source documentation (check against implementation):

> The schedule table's exact reading when the other statements are only the narrative
> summary of the same timepoint.
>
> Applies ONLY when: exactly one reading cites a table and every other reading cites none;
> all count from the same reference; and the narrative values agree with the table's within
> a small tolerance ("1 year" vs "365 days", "3-6 months" vs "+90-180 days"). A narrative
> that really disagrees ("6 months" vs a 12-month cell) is NOT overridden - it stays a conflict.

### `_Compiler._build_group` — L1419–L1585

[Read source and line explanations](walkthrough.html#file=7&line=1419).

Combines candidate readings of one occurrence, identifies conflicts/combined restrictions, and attaches provenance. It does not blindly keep the first fact.

### `_Compiler._build_group.signature` — L1446–L1447

[Read source and line explanations](walkthrough.html#file=7&line=1446).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._apply_holiday_rule` — L1587–L1612

[Read source and line explanations](walkthrough.html#file=7&line=1587).

Source documentation (check against implementation):

> HOLIDAY_ADJUSTMENT becomes the holiday policy of the weekday series it
> modifies; on anything else it stays an UNSUPPORTED node.

### `_Compiler._restriction_nodes` — L1614–L1642

[Read source and line explanations](walkthrough.html#file=7&line=1614).

Preserves additional restrictions as constraint nodes and tracks statements that could not be compiled. Retaining a node does not mean an operator is supported.

Source documentation (check against implementation):

> Constraint nodes for every restriction beyond the primary reading:
> AND extras, OR alternatives and calendar rules no operator executes.
> Returns (nodes, provenance, statements that could not be written).

### `_Compiler._alternative_nodes` — L1644–L1672

[Read source and line explanations](walkthrough.html#file=7&line=1644).

Source documentation (check against implementation):

> One ANY per alternative label. A label whose options cannot all be
> written exactly is not partially kept: every option stays pending.

### `_Compiler._build_alternatives_only` — L1674–L1701

[Read source and line explanations](walkthrough.html#file=7&line=1674).

Source documentation (check against implementation):

> A visit timed only by stated alternatives: CONSTRAINED timing = ANY.

### `_Compiler._event` — L1703–L1774

[Read source and line explanations](walkthrough.html#file=7&line=1703).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_activities` — L1778–L1858

[Read source and line explanations](walkthrough.html#file=7&line=1778).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._activity_timing` — L1860–L1872

[Read source and line explanations](walkthrough.html#file=7&line=1860).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._occurrence_numbers` — L1874–L1914

[Read source and line explanations](walkthrough.html#file=7&line=1874).

Source documentation (check against implementation):

> The cycle numbers an activity is done in, or why they cannot be
> stated as a checkable rule (then the note stays an unresolved qualifier).
>
> Only for a visit held once per cycle (``CYCLE_DAY`` of the CURRENT
> cycle), where the engine's ``occurrence_number`` IS the cycle number.
> A pattern ("every second cycle", "even cycles") becomes the explicit
> list of cycles, so it needs a stated number of cycles.

### `_Compiler.add_action` — L1918–L1926

[Read source and line explanations](walkthrough.html#file=7&line=1918).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.link_triggered_events` — L1928–L1937

[Read source and line explanations](walkthrough.html#file=7&line=1928).

Source documentation (check against implementation):

> A visit triggered by a condition is added only once it occurs (spec A10).

### `_Compiler._resolvable` — L1939–L1941

[Read source and line explanations](walkthrough.html#file=7&line=1939).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_conditions` — L1943–L2052

[Read source and line explanations](walkthrough.html#file=7&line=1943).

Compiles condition-dependent changes such as hold, resume or repeat into explicit schedule behavior where source facts are sufficient.

### `_Compiler._process_rule` — L2054–L2056

[Read source and line explanations](walkthrough.html#file=7&line=2054).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._participation` — L2058–L2080

[Read source and line explanations](walkthrough.html#file=7&line=2058).

Source documentation (check against implementation):

> Withdrawal from treatment, withdrawal of consent and end of
> follow-up are three different decisions and are never merged.

### `_Compiler.build_processes` — L2082–L2123

[Read source and line explanations](walkthrough.html#file=7&line=2082).

Source documentation (check against implementation):

> One process per named component, linked to the events it owns. Kind
> and hold/resume policy come only from what the protocol states.

### `_Compiler._blocks_of` — L2125–L2126

[Read source and line explanations](walkthrough.html#file=7&line=2125).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._block_action` — L2128–L2156

[Read source and line explanations](walkthrough.html#file=7&line=2128).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._repeat_until_resolved` — L2158–L2182

[Read source and line explanations](walkthrough.html#file=7&line=2158).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_condition_definitions` — L2184–L2237

[Read source and line explanations](walkthrough.html#file=7&line=2184).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_blocks` — L2239–L2296

[Read source and line explanations](walkthrough.html#file=7&line=2239).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.build_anchors` — L2300–L2338

[Read source and line explanations](walkthrough.html#file=7&line=2300).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler.finish_anchors` — L2340–L2383

[Read source and line explanations](walkthrough.html#file=7&line=2340).

Examines references to anchors not fully defined by a source fact; keeps missing definitions visible as review requirements.

Source documentation (check against implementation):

> Anchors named by rules but not defined by an anchor fact of their own.

### `_Compiler.non_patient` — L2387–L2401

[Read source and line explanations](walkthrough.html#file=7&line=2387).

Source documentation (check against implementation):

> Spec I8: trial/site/country rules go to a trial-level workflow.

### `_Compiler.day_numbering` — L2405–L2426

[Read source and line explanations](walkthrough.html#file=7&line=2405).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Compiler._entity_for` — L2428–L2442

[Read source and line explanations](walkthrough.html#file=7&line=2428).

Source documentation (check against implementation):

> The rule a reader issue is about, so it blocks THAT rule's readiness.

### `_Compiler.reader_issues` — L2444–L2461

[Read source and line explanations](walkthrough.html#file=7&line=2444).

Carries reader-origin issues into schedule validation, scoped to an affected entity when possible.

### `_Compiler.compile` — L2463–L2517

[Read source and line explanations](walkthrough.html#file=7&line=2463).

Runs name/dimension/cycle/event/activity/condition/anchor/process construction, creates an EXTRACTED UniversalSchedule, validates it and builds a statement ledger. Returns the draft plus issues and trace; does not approve it or calculate patient dates.

### `compile_schedule` — L2520–L2524

[Read source and line explanations](walkthrough.html#file=7&line=2520).

Source documentation (check against implementation):

> Compile one schedule from facts. Deterministic; raises nothing for bad
> facts - they become issues on the draft.

### `compile_document` — L2527–L2562

[Read source and line explanations](walkthrough.html#file=7&line=2527).

Compiles independent schedule options separately. Shared facts are available to each option; unknown option labels are retained for review with an explicit issue instead of dropped.

Source documentation (check against implementation):

> One ``(option, result)`` pair per independent Schedule of Assessments.
>
> A single-schedule protocol yields one pair whose option is None, which is
> the contract ``ExtractionService.complete_many`` and ``/protocols/extract``
> already use.

## 9. backend/app/domain/schedule/models.py

Defines schedule definitions, patient context, issues and evaluation outputs.

1686 lines. [Open annotated source](walkthrough.html#file=8).

### `utc_now` — L24–L25

[Read source and line explanations](walkthrough.html#file=8&line=24).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleStatus` — L28–L45

[Read source and line explanations](walkthrough.html#file=8&line=28).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `InterpretationStatus` — L48–L55

[Read source and line explanations](walkthrough.html#file=8&line=48).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Requiredness` — L58–L64

[Read source and line explanations](walkthrough.html#file=8&line=58).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EventType` — L67–L100

[Read source and line explanations](walkthrough.html#file=8&line=67).

Source documentation (check against implementation):

> The event types the projection layer knows how to present - reminder
> wording, attendance expectation, calendar treatment (doc 10 s2-s3).
>
> AD-6: closed but escapable. ``Event.event_type`` itself stays a plain
> ``str`` (not this enum) so an unrecognised value never fails validation -
> doc 10 s3 requires it surface as a non-blocking review warning instead,
> which is what ``validator.py`` already does by comparing against
> ``frozenset(EventType)``. A protocol may legitimately name something this
> list has never seen; refusing approval over vocabulary would stop real
> work for no clinical reason, while presenting an unknown type as an
> ordinary site visit could tell a patient to travel. OTHER is the escape
> member for exactly that case.

### `DependencyMode` — L131–L135

[Read source and line explanations](walkthrough.html#file=8&line=131).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleDependencyMode` — L138–L152

[Read source and line explanations](walkthrough.html#file=8&line=138).

Source documentation (check against implementation):

> How each treatment cycle's start is dated (doc F1-F3).
>
> NOMINAL: every cycle sits on the grid counted from Cycle 1 Day 1, so a
> delayed Cycle 2 does not move Cycle 3. ACTUAL_PREVIOUS_CYCLE_START: each
> cycle starts one cycle length after the day the previous cycle ACTUALLY
> started, so a later cycle cannot be dated until the one before it happened.
> MANUAL: a person enters each cycle start. UNCLEAR: the protocol does not
> say, which blocks approval - the engine never picks one.

### `cycle_start_anchor_code` — L165–L167

[Read source and line explanations](walkthrough.html#file=8&line=165).

Source documentation (check against implementation):

> The addressable anchor for one cycle's start, e.g. ``CYCLE_3_START``.

### `QualifierScope` — L170–L188

[Read source and line explanations](walkthrough.html#file=8&line=170).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `QualifierCategory` — L191–L215

[Read source and line explanations](walkthrough.html#file=8&line=191).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Severity` — L218–L222

[Read source and line explanations](walkthrough.html#file=8&line=218).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `IssueStatus` — L225–L229

[Read source and line explanations](walkthrough.html#file=8&line=225).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConditionState` — L232–L249

[Read source and line explanations](walkthrough.html#file=8&line=232).

Source documentation (check against implementation):

> Lifecycle of one protocol condition for one patient (requirement doc 2 s14).
>
> A condition is never a boolean checkbox: it carries a state, an occurrence date
> that can anchor resulting visits, and a resolution date that stops repeats.

### `BlockState` — L252–L255

[Read source and line explanations](walkthrough.html#file=8&line=252).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConfinementStatus` — L279–L296

[Read source and line explanations](walkthrough.html#file=8&line=279).

Source documentation (check against implementation):

> State of one continuous inpatient stay (requirement doc 6 section 36).
>
> The distinction that matters clinically is: not yet arrived, currently confined,
> actually discharged. A planned discharge date passing is not a discharge.

### `PatientActivityStatus` — L299–L306

[Read source and line explanations](walkthrough.html#file=8&line=299).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientEventStatus` — L309–L331

[Read source and line explanations](walkthrough.html#file=8&line=309).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `UnscheduledOccurrence` — L334–L346

[Read source and line explanations](walkthrough.html#file=8&line=334).

Source documentation (check against implementation):

> One unscheduled visit a site actually created (requirement doc 10 s14).
>
> A reason is required: an unscheduled visit with no stated cause is not
> reviewable later, and the protocol defines these by their cause.

### `DayNumbering` — L349–L351

[Read source and line explanations](walkthrough.html#file=8&line=349).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleMetadata` — L354–L365

[Read source and line explanations](walkthrough.html#file=8&line=354).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Epoch` — L368–L377

[Read source and line explanations](walkthrough.html#file=8&line=368).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `StudyDimension` — L380–L400

[Read source and line explanations](walkthrough.html#file=8&line=380).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `StudyDimension.parent_is_complete` — L394–L400

[Read source and line explanations](walkthrough.html#file=8&line=394).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `GenericDimension` — L403–L404

[Read source and line explanations](walkthrough.html#file=8&line=403).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Anchor` — L407–L423

[Read source and line explanations](walkthrough.html#file=8&line=407).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `schedule_origin_anchor` — L433–L460

[Read source and line explanations](walkthrough.html#file=8&line=433).

Source documentation (check against implementation):

> The anchor this schedule's Day 0 is measured from, or None.
>
> Never matches on an anchor's printed NAME. A protocol's origin may be called
> Baseline, Day 1, Randomisation or First Dose, and those are different
> clinical facts; the origin is whichever anchor the schedule was built
> against, which the builder records explicitly - either as this schedule's
> stated ``day_numbering.day_1_anchor`` (the primary, correct source: a
> protocol-stated fact, not an inference) or, for the legacy adapter path
> until it is retired, an anchor whose ``derivation_rule`` carries the
> origin role directly.
>
> Returns ``None`` when neither is stated. That is the correct answer, not a
> gap: a caller needing this anchor (patient enrolment) must fail loudly
> rather than guess, and the validator blocks approval of a CLINICAL_DAY
> schedule that reaches this state.

### `ApplicabilityRule` — L463–L484

[Read source and line explanations](walkthrough.html#file=8&line=463).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ApplicabilityRule.comparison_operator_takes_exactly_one_value` — L476–L484

[Read source and line explanations](walkthrough.html#file=8&line=476).

Source documentation (check against implementation):

> EQ/NE/LT/LTE/GT/GTE compare against one threshold, not a membership
> set - a second value would silently be ignored rather than compared.

### `Dependency` — L487–L503

[Read source and line explanations](walkthrough.html#file=8&line=487).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Dependency.occurrence_selection` — L500–L503

[Read source and line explanations](walkthrough.html#file=8&line=500).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Qualifier` — L506–L520

[Read source and line explanations](walkthrough.html#file=8&line=506).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConditionalAction` — L523–L535

[Read source and line explanations](walkthrough.html#file=8&line=523).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConfinementDefinition` — L538–L543

[Read source and line explanations](walkthrough.html#file=8&line=538).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RuleScope` — L546–L560

[Read source and line explanations](walkthrough.html#file=8&line=546).

Source documentation (check against implementation):

> Whose situation a protocol rule is about (doc I8).
>
> "Grade 3 toxicity: hold treatment" is about one patient. "Fatal toxicity:
> accrual is suspended and the DSMC reviews" is about the trial. Only the
> first kind may change a patient's visits; SITE/COUNTRY/TRIAL rules belong
> to a trial-level workflow and the patient engine refuses to act on them.

### `ConditionalDefinition` — L569–L585

[Read source and line explanations](walkthrough.html#file=8&line=569).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RepeatBlock` — L588–L607

[Read source and line explanations](walkthrough.html#file=8&line=588).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConfinementDayDefinition` — L610–L615

[Read source and line explanations](walkthrough.html#file=8&line=610).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConfinementEpisodeDefinition` — L618–L629

[Read source and line explanations](walkthrough.html#file=8&line=618).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrenceTermination` — L632–L659

[Read source and line explanations](walkthrough.html#file=8&line=632).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrenceTermination.matching_value` — L643–L659

[Read source and line explanations](walkthrough.html#file=8&line=643).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrenceBasis` — L662–L683

[Read source and line explanations](walkthrough.html#file=8&line=662).

Source documentation (check against implementation):

> What drives occurrence n of an interval repeat (P4-02).
>
> Distinct from ``TemporalBasis``: that one picks WHICH DATE of a referenced
> visit a rule counts from; this one says how the SERIES progresses.
>
> FIRST_RESOLVED_OCCURRENCE: occurrence 0 is the date the event's own timing
> resolves to; occurrence n is that date plus n intervals, always counted from
> occurrence 0 (so a 31 January series recovers 31 March after February).
> PREVIOUS_PLANNED_OCCURRENCE: occurrence n is the PLANNED date of occurrence
> n-1 plus one interval (repeated addition, so a month-end clamp carries on).
> PREVIOUS_ACTUAL_OCCURRENCE: occurrence n is the ACTUAL date (visit or
> treatment, per ``actual_source``) of occurrence n-1 plus one interval; it
> waits, undated, until that actual exists. FIXED_ANCHOR_SERIES: occurrence n
> sits on the grid ``start_reference (+ start_offset) + n intervals``; the
> event's own timing must fall on slot 0 of that grid.

### `RecurrenceRule` — L686–L730

[Read source and line explanations](walkthrough.html#file=8&line=686).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrenceRule.coherent_basis` — L713–L730

[Read source and line explanations](walkthrough.html#file=8&line=713).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Weekday` — L738–L745

[Read source and line explanations](walkthrough.html#file=8&line=738).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `HolidayCalendar` — L755–L773

[Read source and line explanations](walkthrough.html#file=8&line=755).

Source documentation (check against implementation):

> Non-treatment dates supplied as DATA, per schedule or per site.
>
> A calendar is never inferred from a locale or a library: which days a site
> treats on is an operational fact. ``business_weekdays`` is required because
> "next business day" means nothing until the site's working week is stated.

### `HolidayCalendar.unique_weekdays` — L770–L773

[Read source and line explanations](walkthrough.html#file=8&line=770).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `HolidayPolicy` — L776–L784

[Read source and line explanations](walkthrough.html#file=8&line=776).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MissedOccurrencePolicy` — L787–L790

[Read source and line explanations](walkthrough.html#file=8&line=787).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CountBasis` — L793–L799

[Read source and line explanations](walkthrough.html#file=8&line=793).

Source documentation (check against implementation):

> What a COUNT termination counts (RL-08): "30 slots" is not "30
> delivered fractions". The two only differ once a session is missed or
> held, which is exactly when an unstated basis blocks (and only then).

### `CalendarRecurrence` — L802–L831

[Read source and line explanations](walkthrough.html#file=8&line=802).

Source documentation (check against implementation):

> Recurrence on named weekdays ("Monday to Friday", "every other Tuesday").
>
> Occurrence identity is the SLOT ordinal (session 1, 2, ...), independent of
> the date a slot lands on: a missed or moved session keeps its number, and a
> replacement is a new ordinal linked to the session it replaces.

### `CalendarRecurrence.coherent` — L826–L831

[Read source and line explanations](walkthrough.html#file=8&line=826).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrenceSegment` — L834–L857

[Read source and line explanations](walkthrough.html#file=8&line=834).

Source documentation (check against implementation):

> One cadence of a phased follow-up plan (doc E9).
>
> A segment ends either at ``end_offset`` measured from the PLAN's start
> reference ("to year 2" is +24 months from end of therapy), or after
> ``count`` visits, or - final segment only - where the plan's termination
> says. ``end_inclusive`` decides which segment owns a visit falling exactly
> on the boundary; it is never defaulted, because guessing it either
> duplicates or drops the boundary visit (doc E4). The validator blocks an
> unset flag.

### `RecurrenceSegment.one_end` — L852–L857

[Read source and line explanations](walkthrough.html#file=8&line=852).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RecurrencePlan` — L860–L879

[Read source and line explanations](walkthrough.html#file=8&line=860).

Source documentation (check against implementation):

> A follow-up plan whose cadence changes over time, as ONE rule (doc E9).
>
> "Every 3 months for 2 years, then every 6 months to year 5, then yearly."
> Each segment starts where the previous one ended, every occurrence is
> counted from its segment's start (no drift), and the whole series is one
> visit with continuous occurrence numbering - not chained visits a reviewer
> has to keep consistent by hand.

### `CyclePlan` — L882–L902

[Read source and line explanations](walkthrough.html#file=8&line=882).

Source documentation (check against implementation):

> Treatment cycles as addressable starts (doc F1-F5).
>
> Cycle n's start is published as anchor ``<code>_<n>_START`` (CYCLE_2_START,
> ...): actual once it happened, planned before, so any rule can count from a
> specific cycle with the usual ACTUAL / PLANNED_OR_ACTUAL safety (doc C6).
> An actual cycle start is a person-recorded ``<code>_<n>_START`` anchor, or
> else the n-th recorded actual date of ``start_event_code`` (the Day-1 visit)
> in date order - cycles are given in sequence, so the k-th Day 1 given is
> Cycle k. Cycle 1 starts at ``start_reference``.

### `Activity` — L905–L922

[Read source and line explanations](walkthrough.html#file=8&line=905).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrackRebasePolicy` — L925–L943

[Read source and line explanations](walkthrough.html#file=8&line=925).

Source documentation (check against implementation):

> What may move a track's targets (P5-02). Distinct from ``TemporalBasis``
> (which DATE of a referenced visit a rule counts from) and ``RecurrenceBasis``
> (how one repeat series progresses): this says what the TRACK as a whole is
> allowed to be re-based on. A label such as "Imaging" never implies a policy.
>
> FIXED_FROM_ANCHOR: members count from the track anchor only; no actual,
> appointment or command moves them. PREVIOUS_PLANNED_OCCURRENCE /
> PREVIOUS_ACTUAL_OCCURRENCE: the track is a chain, each target counted from
> the previous planned / actual one (members' repeats must use the matching
> ``RecurrenceBasis``). MANUAL_REBASE: only an explicit REBASE command moves it,
> to a stated date for the track anchor. NO_REBASE: nothing may move it.

### `ScheduleTrack` — L946–L970

[Read source and line explanations](walkthrough.html#file=8&line=946).

Source documentation (check against implementation):

> A named group of events that share a rebase / pause / termination policy.
>
> Membership is explicit (``Event.track_id``); sharing a track never makes
> events move together - movement follows references and declared
> ``TrackLink``s. Policies the protocol does not state stay ``None`` and block
> approval (never defaulted).

### `TrackLink` — L973–L981

[Read source and line explanations](walkthrough.html#file=8&line=973).

Source documentation (check against implementation):

> An explicit cross-track rule: the target track's events may count from the
> source track's events. Without one, a reference across tracks is refused.

### `Event` — L984–L1059

[Read source and line explanations](walkthrough.html#file=8&line=984).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Event.track_membership` — L1037–L1044

[Read source and line explanations](walkthrough.html#file=8&line=1037).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Event.one_repeat_rule` — L1047–L1054

[Read source and line explanations](walkthrough.html#file=8&line=1047).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Event.repeats` — L1057–L1059

[Read source and line explanations](walkthrough.html#file=8&line=1057).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Evidence` — L1062–L1072

[Read source and line explanations](walkthrough.html#file=8&line=1062).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ClaimEvidence` — L1075–L1082

[Read source and line explanations](walkthrough.html#file=8&line=1075).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ValidationIssue` — L1085–L1094

[Read source and line explanations](walkthrough.html#file=8&line=1085).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProcessKind` — L1102–L1109

[Read source and line explanations](walkthrough.html#file=8&line=1102).

Source documentation (check against implementation):

> What a process is. Participation decisions act by kind, never by name:
> withdrawal from treatment stops INTERVENTION processes, end of follow-up
> completes FOLLOW_UP ones, withdrawal of consent stops everything.

### `ProcessState` — L1112–L1116

[Read source and line explanations](walkthrough.html#file=8&line=1112).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `HoldResumePolicy` — L1122–L1136

[Read source and line explanations](walkthrough.html#file=8&line=1122).

Source documentation (check against implementation):

> What a held process's remaining obligations do once it resumes
> (design s5). Never inferred from the word "resume".

### `ProcessDefinition` — L1146–L1159

[Read source and line explanations](walkthrough.html#file=8&line=1146).

Source documentation (check against implementation):

> One intervention or pathway with its own state (design s5).

### `ProcessTransitionReason` — L1162–L1168

[Read source and line explanations](walkthrough.html#file=8&line=1162).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProcessTarget` — L1171–L1185

[Read source and line explanations](walkthrough.html#file=8&line=1171).

Source documentation (check against implementation):

> Which processes a transition changes: one process, every process of a
> kind, or the patient's whole participation (consent withdrawal).

### `ProcessTarget.matching_value` — L1180–L1185

[Read source and line explanations](walkthrough.html#file=8&line=1180).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProcessTransition` — L1188–L1232

[Read source and line explanations](walkthrough.html#file=8&line=1188).

Source documentation (check against implementation):

> A dated, attributable change of process state (a patient fact).
>
> Only CONFIRMED transitions act. ``effective_at`` is when it takes effect
> clinically, ``recorded_at`` when it entered the system; a late entry with
> an earlier effective time re-evaluates the affected period.

### `ProcessTransition.coherent` — L1213–L1232

[Read source and line explanations](walkthrough.html#file=8&line=1213).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceFactKind` — L1240–L1251

[Read source and line explanations](walkthrough.html#file=8&line=1240).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceFact` — L1254–L1275

[Read source and line explanations](walkthrough.html#file=8&line=1254).

Source documentation (check against implementation):

> One recorded fact about one logical occurrence (event code + index).

### `OccurrenceFact.valued` — L1271–L1275

[Read source and line explanations](walkthrough.html#file=8&line=1271).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AssignmentFact` — L1278–L1292

[Read source and line explanations](walkthrough.html#file=8&line=1278).

Source documentation (check against implementation):

> A dated assignment (arm, sequence, cohort ...) with its recorded time.
>
> ``effective_from`` None means "from the start of participation" (the
> assignment in force before any dated change). Later facts never rewrite
> earlier ones; a correction supersedes by id and both stay in history.

### `UniversalSchedule` — L1295–L1320

[Read source and line explanations](walkthrough.html#file=8&line=1295).

Canonical definition containing metadata, anchors, events, dimensions, conditional rules, processes, evidence and validation issues. A definition is not an individual patient's generated calendar.

### `LogicalOccurrenceState` — L1323–L1342

[Read source and line explanations](walkthrough.html#file=8&line=1323).

Source documentation (check against implementation):

> What is already known about ONE logical visit occurrence (P1-02).
>
> Keyed by (event_code, occurrence_index) within the patient's schedule
> version. ``original_planned_date`` is the date the FIRST evaluation that
> dated this occurrence produced and never changes afterwards (D3);
> ``latest_planned_date`` is what the latest evaluation planned. The actual
> visit and the actual treatment are independent facts (D7): a patient can
> attend without being treated, and a treatment-based rule then waits.

### `TrackRebaseInput` — L1345–L1356

[Read source and line explanations](walkthrough.html#file=8&line=1345).

Source documentation (check against implementation):

> A MANUAL_REBASE command's effect: the track anchor counts from ``value``.

### `OccurrenceOverrideInput` — L1359–L1380

[Read source and line explanations](walkthrough.html#file=8&line=1359).

Source documentation (check against implementation):

> A patient-specific exception on ONE logical occurrence. It never replaces
> the protocol target or window; it is shown beside them.

### `OccurrenceOverrideInput.says_something` — L1374–L1380

[Read source and line explanations](walkthrough.html#file=8&line=1374).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceCancellationInput` — L1383–L1388

[Read source and line explanations](walkthrough.html#file=8&line=1383).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientContext` — L1391–L1450

[Read source and line explanations](walkthrough.html#file=8&line=1391).

The patient-specific inputs for evaluation: pinned version, known/planned dates, assignments and recorded facts/state. Availability of inputs determines what can resolve.

### `PatientContext.known_time_zone` — L1444–L1450

[Read source and line explanations](walkthrough.html#file=8&line=1444).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientConditionStatus` — L1453–L1477

[Read source and line explanations](walkthrough.html#file=8&line=1453).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientConditionStatus.recurrence_is_numbered` — L1461–L1468

[Read source and line explanations](walkthrough.html#file=8&line=1461).

Source documentation (check against implementation):

> A recurrence is at least the second occurrence, by definition.

### `PatientConditionStatus.dated_when_active` — L1471–L1477

[Read source and line explanations](walkthrough.html#file=8&line=1471).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ResolvedTiming` — L1480–L1486

[Read source and line explanations](walkthrough.html#file=8&line=1480).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FeasibilityStatus` — L1489–L1500

[Read source and line explanations](walkthrough.html#file=8&line=1489).

Source documentation (check against implementation):

> Outcome of evaluating a constraint set for one occurrence.
>
> FEASIBLE: every input is known and the permitted set is non-empty.
> INCOMPLETE: an input is missing; ``feasible_intervals`` hold the KNOWN
> bounds (a superset of the final set). Never operationally ready.
> INFEASIBLE: the known inputs already leave no permitted time.

### `FeasibleBound` — L1503–L1512

[Read source and line explanations](walkthrough.html#file=8&line=1503).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FeasibleInterval` — L1515–L1517

[Read source and line explanations](walkthrough.html#file=8&line=1515).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MissingInput` — L1520–L1530

[Read source and line explanations](walkthrough.html#file=8&line=1520).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConstraintTraceEntry` — L1533–L1547

[Read source and line explanations](walkthrough.html#file=8&line=1533).

Source documentation (check against implementation):

> NF-03: how one leaf constraint was evaluated for this occurrence.

### `ConstraintRelation` — L1550–L1555

[Read source and line explanations](walkthrough.html#file=8&line=1550).

Source documentation (check against implementation):

> A semantic relation found while normalising a constraint set.

### `EvaluatedActivity` — L1558–L1577

[Read source and line explanations](walkthrough.html#file=8&line=1558).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EvaluatedEvent` — L1580–L1608

[Read source and line explanations](walkthrough.html#file=8&line=1580).

One resulting occurrence with status, resolved timing when available, activities and an explanation. A blocked/waiting occurrence can exist without a date.

### `EvaluatedConfinementDay` — L1611–L1615

[Read source and line explanations](walkthrough.html#file=8&line=1611).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EvaluatedConfinement` — L1618–L1631

[Read source and line explanations](walkthrough.html#file=8&line=1618).

Source documentation (check against implementation):

> One patient confinement episode: a single parent stay, not one visit per day.

### `RollingHorizon` — L1634–L1645

[Read source and line explanations](walkthrough.html#file=8&line=1634).

Source documentation (check against implementation):

> How much of an open-ended repeat to materialize (requirement doc 3 s7-s8).
>
> ``upcoming_limit`` bounds only what is WRITTEN DOWN. It is a display and
> storage policy, never a statement about how long the protocol runs: doc 3 s2
> and s36 are explicit that a technical cap must not be presented as the
> protocol maximum. ``None`` keeps the previous behaviour of materializing
> everything up to the date horizon.

### `RepeatSummary` — L1648–L1664

[Read source and line explanations](walkthrough.html#file=8&line=1648).

Source documentation (check against implementation):

> What a repeating rule does beyond the occurrences that were materialized.
>
> Carried alongside the events so a UI can show "every 21 days, continues"
> instead of implying the schedule ends at the last row (doc 3 s4, s36).

### `EvaluationResult` — L1667–L1676

[Read source and line explanations](walkthrough.html#file=8&line=1667).

The result envelope with patient/version/evaluator identity, input snapshot, generated occurrences, confinements and evaluation metadata.

## 10. backend/app/domain/schedule/timing.py

Defines strict typed timing, references, boundaries and constraint expressions.

509 lines. [Open annotated source](walkthrough.html#file=9).

### `StrictModel` — L10–L15

[Read source and line explanations](walkthrough.html#file=9&line=10).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TimeUnit` — L18–L24

[Read source and line explanations](walkthrough.html#file=9&line=18).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Precision` — L27–L30

[Read source and line explanations](walkthrough.html#file=9&line=27).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TemporalAmount` — L33–L35

[Read source and line explanations](walkthrough.html#file=9&line=33).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `NonNegativeTemporalAmount` — L38–L40

[Read source and line explanations](walkthrough.html#file=9&line=38).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PositiveTemporalAmount` — L43–L45

[Read source and line explanations](walkthrough.html#file=9&line=43).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnchorAcceptance` — L48–L57

[Read source and line explanations](walkthrough.html#file=9&line=48).

Source documentation (check against implementation):

> Which recorded values of an anchor a rule may count from (doc C6).
>
> A planned surgery date may place the pre-op visits, but a post-op visit dated
> from it would be presented - and reminded about - as if surgery had happened.
> So every anchor reference states whether it accepts a planned value at all.

### `AnchorReference` — L60–L69

[Read source and line explanations](walkthrough.html#file=9&line=60).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TemporalBasis` — L72–L92

[Read source and line explanations](walkthrough.html#file=9&line=72).

Source documentation (check against implementation):

> Which recorded date of the referenced visit a rule counts from (P1-01).
>
> Never inferred from an event's name, code or status: a reference says which
> date it uses, or it is not a complete rule.
>
> ORIGINAL_PLANNED: the date the first evaluation of that occurrence produced;
> it never moves afterwards. CURRENT_PLANNED: the date the latest evaluation
> plans for it. ACTUAL_VISIT / ACTUAL_TREATMENT: the date the patient attended
> / was actually treated - independent facts (a visit can happen with the
> treatment withheld). ACTUAL_OR_PLANNED: the actual date once it exists, else
> the planned basis named in ``fallback_basis``, with the fallback stated in
> the explanation. MANUAL_ANCHOR: a date a person entered by hand.

### `BasisSource` — L99–L112

[Read source and line explanations](walkthrough.html#file=9&line=99).

Source documentation (check against implementation):

> Who stated a reference's temporal basis.
>
> EXPLICIT: a person or the extractor read it from the protocol / a reviewer
> chose it. LEGACY_EQUIVALENT: the reference predates the field and carries
> the basis the engine already used for it (so nothing moves); it was derived
> at read time, not stated by anyone. UNCONFIRMED: extraction could not tell;
> the stored basis is a placeholder that cannot be approved while the owning
> event's dependency mode is also unstated.

### `EventReference` — L115–L136

[Read source and line explanations](walkthrough.html#file=9&line=115).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EventReference.coherent_basis` — L128–L136

[Read source and line explanations](walkthrough.html#file=9&line=128).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ActivityReference` — L139–L148

[Read source and line explanations](walkthrough.html#file=9&line=139).

Source documentation (check against implementation):

> An intra-day anchor: another activity inside the same visit occurrence.
>
> Protocols say "2 hours after dosing" or "2 hours after completion of infusion".
> The referenced activity's ACTUAL recorded time is the anchor; a planned time is
> never substituted, so dependent activities stay unresolved until it is recorded.

### `Window` — L157–L165

[Read source and line explanations](walkthrough.html#file=9&line=157).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Window.same_unit` — L162–L165

[Read source and line explanations](walkthrough.html#file=9&line=162).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AbsoluteTiming` — L168–L170

[Read source and line explanations](walkthrough.html#file=9&line=168).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OffsetTiming` — L173–L176

[Read source and line explanations](walkthrough.html#file=9&line=173).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProtocolDayTiming` — L179–L189

[Read source and line explanations](walkthrough.html#file=9&line=179).

Source documentation (check against implementation):

> A numbered clinical day; unlike OFFSET, Day 1 resolves to the anchor.

### `ProtocolDayTiming.no_day_zero` — L186–L189

[Read source and line explanations](walkthrough.html#file=9&line=186).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RangeTiming` — L192–L204

[Read source and line explanations](walkthrough.html#file=9&line=192).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RangeTiming.ordered_compatible_bounds` — L199–L204

[Read source and line explanations](walkthrough.html#file=9&line=199).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `NominalWindowTiming` — L207–L210

[Read source and line explanations](walkthrough.html#file=9&line=207).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WithinTiming` — L213–L217

[Read source and line explanations](walkthrough.html#file=9&line=213).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `NoLaterThanTiming` — L220–L223

[Read source and line explanations](walkthrough.html#file=9&line=220).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ApproximateTiming` — L226–L230

[Read source and line explanations](walkthrough.html#file=9&line=226).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TriggerOffset` — L233–L235

[Read source and line explanations](walkthrough.html#file=9&line=233).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TriggerWithin` — L238–L241

[Read source and line explanations](walkthrough.html#file=9&line=238).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TriggerNoLaterThan` — L244–L246

[Read source and line explanations](walkthrough.html#file=9&line=244).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TriggeredTiming` — L249–L255

[Read source and line explanations](walkthrough.html#file=9&line=249).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleSelector` — L258–L266

[Read source and line explanations](walkthrough.html#file=9&line=258).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleSelector.number_matches_type` — L263–L266

[Read source and line explanations](walkthrough.html#file=9&line=263).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleDayTiming` — L269–L273

[Read source and line explanations](walkthrough.html#file=9&line=269).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProtocolExtension` — L276–L278

[Read source and line explanations](walkthrough.html#file=9&line=276).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProtocolDefinedTiming` — L281–L285

[Read source and line explanations](walkthrough.html#file=9&line=281).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `UnresolvedTiming` — L288–L292

[Read source and line explanations](walkthrough.html#file=9&line=288).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceSelector` — L309–L327

[Read source and line explanations](walkthrough.html#file=9&line=309).

Source documentation (check against implementation):

> Which occurrence of a repeated event a reference means.
>
> FIRST/LAST/NUMBER select within the referenced event's own series. CURRENT
> is the occurrence with the same index as the one being evaluated; PREVIOUS
> is the one before it (index - 1), which is the only selector a rule may use
> to refer to its OWN event ("at least 21 days after the previous dose").
> LAST with ACTUAL is known only once every scheduled occurrence has an
> actual - the latest dose given so far is not the last dose.

### `OccurrenceSelector.number_matches_type` — L324–L327

[Read source and line explanations](walkthrough.html#file=9&line=324).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnchorValue` — L330–L335

[Read source and line explanations](walkthrough.html#file=9&line=330).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EventValue` — L338–L348

[Read source and line explanations](walkthrough.html#file=9&line=338).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ActivityValue` — L351–L355

[Read source and line explanations](walkthrough.html#file=9&line=351).

Source documentation (check against implementation):

> The ACTUAL recorded time of another activity in the same visit occurrence.

### `ProcessBoundaryValue` — L358–L369

[Read source and line explanations](walkthrough.html#file=9&line=358).

Source documentation (check against implementation):

> WP3: when an intervention/pathway process actually started or ended.
>
> START is its first confirmed ACTIVE transition; END its confirmed STOPPED
> or COMPLETED transition. Both are ACTUAL facts only - a process that has
> not ended has no end date yet ("end of treatment" stays unknown while
> treatment continues), and no planned value is ever substituted.

### `LiteralDateValue` — L372–L386

[Read source and line explanations](walkthrough.html#file=9&line=372).

Source documentation (check against implementation):

> A fixed calendar date or instant. ``precision`` is required: "by March
> 2027" is not the same bound as "by 1 March 2027", and the evaluator will
> not pick a day inside a month for you (it reports missing precision).

### `LiteralDateValue.precision_matches_value` — L382–L386

[Read source and line explanations](walkthrough.html#file=9&line=382).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ShiftValue` — L389–L396

[Read source and line explanations](walkthrough.html#file=9&line=389).

Source documentation (check against implementation):

> ``reference`` moved by a signed, calendar-aware amount. Months and years
> stay calendar units; a month-end clamp needs ``calendar_policy_ref``.

### `_ConstraintNode` — L406–L411

[Read source and line explanations](walkthrough.html#file=9&line=406).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `LowerBoundConstraint` — L414–L417

[Read source and line explanations](walkthrough.html#file=9&line=414).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `UpperBoundConstraint` — L420–L423

[Read source and line explanations](walkthrough.html#file=9&line=420).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AtConstraint` — L426–L428

[Read source and line explanations](walkthrough.html#file=9&line=426).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AllConstraint` — L431–L435

[Read source and line explanations](walkthrough.html#file=9&line=431).

Source documentation (check against implementation):

> Conjunction: the feasible sets of every item are intersected.

### `AnyConstraint` — L438–L443

[Read source and line explanations](walkthrough.html#file=9&line=438).

Source documentation (check against implementation):

> Permitted temporal alternatives: a union that is never widened to its
> convex hull. It never selects a treatment arm or branch.

### `UnsupportedConstraint` — L446–L459

[Read source and line explanations](walkthrough.html#file=9&line=446).

Source documentation (check against implementation):

> A source requirement the language cannot yet execute (a capability gap).
>
> Carried so the requirement stays visible and blocks approval of the rule
> it belongs to - never squeezed into a similar executable operator.
> ``family`` names what kind of operator was missing (CONSTRAINT, RECURRENCE,
> CONDITION, TIMING, ...), ``source_operator`` what the source asked for.

### `ConstraintTiming` — L475–L483

[Read source and line explanations](walkthrough.html#file=9&line=475).

Source documentation (check against implementation):

> Timing stated only as a constraint set (no single nominal target).
>
> Its own window is unbounded; the feasible set is exactly ``constraint``
> (intersected with the owning event's ``constraints`` when both exist).

## 11. backend/app/domain/schedule/validator.py

Checks rule integrity and reports blocking and nonblocking issues.

1813 lines. [Open annotated source](walkthrough.html#file=10).

### `_comparable` — L55–L57

[Read source and line explanations](walkthrough.html#file=10&line=55).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_is_cycle_anchor` — L60–L65

[Read source and line explanations](walkthrough.html#file=10&line=60).

Source documentation (check against implementation):

> CYCLE_3_START is a real anchor when CYCLE is a stated cycle plan.

### `legacy_reference` — L68–L89

[Read source and line explanations](walkthrough.html#file=10&line=68).

Source documentation (check against implementation):

> A constraint reference value as the equivalent legacy reference model,
> so graph, reference and enrolment code keyed on ``code``/``event_code``
> sees constraint dependencies too. Occurrence detail beyond FIRST/LAST is
> not representable there and is not needed by those callers.

### `constraint_references` — L92–L98

[Read source and line explanations](walkthrough.html#file=10&line=92).

Source documentation (check against implementation):

> Legacy-shaped references for every anchor/event/activity in a constraint tree.

### `event_temporal_references` — L101–L106

[Read source and line explanations](walkthrough.html#file=10&line=101).

Source documentation (check against implementation):

> Every reference an event's timing AND its constraint set depend on.

### `_graph_references` — L109–L121

[Read source and line explanations](walkthrough.html#file=10&line=109).

Source documentation (check against implementation):

> References that order events. A constraint's reference to its OWN event
> is occurrence-advancing (PREVIOUS - validated in ``_constraints``) and is
> not an ordering edge; legacy timing references are kept exactly as before.

### `own_event_references` — L124–L141

[Read source and line explanations](walkthrough.html#file=10&line=124).

Source documentation (check against implementation):

> The stored (legacy-timing) event references an event carries: its timing,
> its repeat rule's start / end, and its activities' timing. Constraint values
> state their own acceptance and are validated with the constraints.

### `timing_references` — L144–L156

[Read source and line explanations](walkthrough.html#file=10&line=144).

Source documentation (check against implementation):

> Return all temporal references nested in a timing expression.

### `_has_no_single_target` — L159–L164

[Read source and line explanations](walkthrough.html#file=10&line=159).

Source documentation (check against implementation):

> Timings whose result is a span of allowed dates rather than one target.

### `_planned_after_references` — L167–L182

[Read source and line explanations](walkthrough.html#file=10&line=167).

Source documentation (check against implementation):

> Planned-accepting anchor references that place a visit after the anchor.

### `_non_acknowledgeable_codes` — L194–L197

[Read source and line explanations](walkthrough.html#file=10&line=194).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator` — L200–L1770

[Read source and line explanations](walkthrough.html#file=10&line=200).

Checks structural and semantic integrity without consulting a model. Follow validate() to see the actual rule checks and blocking() to see which findings block readiness.

### `ScheduleValidator.validate` — L203–L249

[Read source and line explanations](walkthrough.html#file=10&line=203).

Source documentation (check against implementation):

> ``evaluating_approved``: the engine re-checks an already APPROVED
> version before dating a patient. Findings about a legacy repeat that
> predates ``RecurrenceRule.basis`` are then advisory - an approved
> schedule must keep dating exactly as it was approved (NF-08); they
> still block approving a NEW or corrected version.

### `ScheduleValidator._interval_recurrences` — L251–L354

[Read source and line explanations](walkthrough.html#file=10&line=251).

Source documentation (check against implementation):

> P4-02/P4-04: an interval repeat states how its series progresses, and
> its first occurrence agrees with the event's own timing.
>
> Structural only: a patient who has not yet got an actual or a condition
> value does not make the PROTOCOL invalid (that is an evaluation wait).
> Legacy rules (``basis`` is None) that are unambiguous stay a warning so
> approved schedules keep working; ambiguous ones block approval.

### `ScheduleValidator._dependency_selectors` — L356–L373

[Read source and line explanations](walkthrough.html#file=10&line=356).

Source documentation (check against implementation):

> New and edited drafts must say WHICH occurrence of a source a dependency
> needs. ``CURRENT`` is only an alias of LAST (the latest generated
> occurrence), not a target-relative reading, so it is refused for approval;
> approved legacy schedules keep evaluating (this runs on drafts only).

### `ScheduleValidator._tracks` — L375–L533

[Read source and line explanations](walkthrough.html#file=10&line=375).

Source documentation (check against implementation):

> P5-01/P5-02: track membership, policies and how they compose.
>
> A schedule with no tracks and no classified events is a legacy schedule:
> nothing is forced and every date is as before. Once a schedule carries
> tracks it is TRACK-AWARE and each event must be a member of a track or be
> explicitly GLOBAL - there is no fabricated default track.

### `ScheduleValidator._constraints` — L535–L538

[Read source and line explanations](walkthrough.html#file=10&line=535).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._windows` — L540–L575

[Read source and line explanations](walkthrough.html#file=10&line=540).

Source documentation (check against implementation):

> Doc B2/B3: an event window needs one target date to sit around.
>
> RANGE, WITHIN and NO_LATER_THAN already ARE the allowed dates; a second
> window on top has no stated centre, and choosing one (the range start?
> its midpoint?) would be a guess. A repeating event is exempt: each
> occurrence's own target date is the centre. A NOMINAL_WITH_WINDOW timing
> stating a different window from the event is two readings of one rule.

### `ScheduleValidator._temporal_basis` — L589–L649

[Read source and line explanations](walkthrough.html#file=10&line=589).

Source documentation (check against implementation):

> P1-01 / P1-02: every event reference says which date it counts from,
> and what it says must be coherent with the rest of the rule.

### `ScheduleValidator._temporal_basis.block` — L602–L606

[Read source and line explanations](walkthrough.html#file=10&line=602).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._planned_anchor_use` — L651–L674

[Read source and line explanations](walkthrough.html#file=10&line=651).

Source documentation (check against implementation):

> Doc C6: surface a planned anchor dating a visit AFTER that anchor.
>
> PLANNED_OR_ACTUAL exists for visits that precede the event (pre-op). A
> post-op visit that accepts a planned surgery date is exactly what the
> rule is there to prevent, so a reviewer is asked to look - not blocked,
> because a protocol may genuinely want a projected follow-up.

### `ScheduleValidator._rule_scopes` — L676–L704

[Read source and line explanations](walkthrough.html#file=10&line=676).

Source documentation (check against implementation):

> Doc I8: a trial-level rule never changes a patient's visits.
>
> The patient engine refuses SITE/COUNTRY/TRIAL rules, so any patient
> action such a rule carries will never happen. Said here, at review,
> rather than left for someone to discover when accrual is suspended and
> no patient schedule moved. Not blocking: the rule is legitimate protocol
> content that belongs in a trial-level workflow, and refusing approval
> would only push a reviewer to delete it.

### `ScheduleValidator._issue` — L707–L725

[Read source and line explanations](walkthrough.html#file=10&line=707).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._unique_codes` — L727–L754

[Read source and line explanations](walkthrough.html#file=10&line=727).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._anchor_integrity` — L756–L825

[Read source and line explanations](walkthrough.html#file=10&line=756).

Source documentation (check against implementation):

> Doc s4/s29: an anchor the import could not fully resolve stays a
> durable, re-derivable block - not a one-time note the adapter happened
> to attach at import.
>
> A ``ValidationIssue`` appended only once, at import time, is discarded
> the next time anyone calls validate/submit/approve, because those all
> replace ``validation_issues`` with a fresh run of this validator. If a
> reviewer patches around the SYMPTOM (say, gives one event's timing an
> explicit anchor) without ever resolving which anchor is actually the
> schedule's origin, a validator with no memory of the ambiguity would
> wave the schedule through. Checking ``Anchor.status`` here instead
> means the block is re-derived from the schedule's own persisted state
> every single time, so it cannot be bypassed by fixing something else.

### `ScheduleValidator._information_loss` — L827–L875

[Read source and line explanations](walkthrough.html#file=10&line=827).

Source documentation (check against implementation):

> Doc s29: re-derive every import-time loss marker on every pass.
>
> The adapter cannot leave a trace of something that never entered the
> schedule (a missing activity template has no row to point at), so it
> records the fact durably instead: on the OWNING event's own
> ``metadata`` when there is one, or on ``schedule_metadata.extensions``
> when there is not (a recurrence naming an event that does not exist
> anywhere in the plan). This mirrors ``_anchor_integrity`` - the point
> of storing it durably rather than only returning it once from the
> adapter is that validate/submit/approve all replace
> ``validation_issues`` with a fresh run of this validator, which would
> otherwise silently discard a one-time note the moment anyone
> revalidates.

### `ScheduleValidator._references` — L877–L1070

[Read source and line explanations](walkthrough.html#file=10&line=877).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._references.check_reference` — L895–L907

[Read source and line explanations](walkthrough.html#file=10&line=895).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._dependency_graph` — L1072–L1108

[Read source and line explanations](walkthrough.html#file=10&line=1072).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._clinical_completeness` — L1110–L1328

[Read source and line explanations](walkthrough.html#file=10&line=1110).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._schedule_structures` — L1330–L1519

[Read source and line explanations](walkthrough.html#file=10&line=1330).

Source documentation (check against implementation):

> Validate conditional definitions, repeat blocks, and confinement episodes.
>
> These are the structures a reviewer cannot easily eyeball, so an impossible
> one is blocked at approval rather than discovered on a patient.

### `ScheduleValidator._schedule_structures.require_structure_evidence` — L1349–L1366

[Read source and line explanations](walkthrough.html#file=10&line=1349).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleValidator._cycle_plans` — L1521–L1602

[Read source and line explanations](walkthrough.html#file=10&line=1521).

Source documentation (check against implementation):

> Doc F1-F3: a cycle plan must say how each cycle start is dated.
>
> UNCLEAR blocks approval: nominal grid and follow-the-actual-cycle give
> different dates from Cycle 3 on, and the engine never picks one.

### `ScheduleValidator._recurrence_plans` — L1604–L1649

[Read source and line explanations](walkthrough.html#file=10&line=1604).

Source documentation (check against implementation):

> Doc E4/E9: every segment boundary is stated, never assumed.

### `ScheduleValidator._calendar_recurrences` — L1651–L1711

[Read source and line explanations](walkthrough.html#file=10&line=1651).

Source documentation (check against implementation):

> WP3 (RL-08/RL-09): material calendar policies are stated or visible.
>
> An unstated holiday, missed-session or count-basis policy blocks only
> the sessions that depend on it at runtime, so it is a WARNING here; a
> stated policy that cannot be executed (no calendar to shift against) or
> two policies that contradict each other block approval.

### `ScheduleValidator._processes` — L1713–L1757

[Read source and line explanations](walkthrough.html#file=10&line=1713).

Source documentation (check against implementation):

> WP3 (RL-10/RL-11): process definitions are well formed and evidenced.

### `ScheduleValidator.blocking` — L1760–L1770

[Read source and line explanations](walkthrough.html#file=10&line=1760).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `topological_event_codes` — L1773–L1813

[Read source and line explanations](walkthrough.html#file=10&line=1773).

Source documentation (check against implementation):

> Stable topological ordering used by the evaluator after validation.

## 12. backend/app/application/extraction.py

Claims runs and persists draft schedule versions through repository ports.

654 lines. [Open annotated source](walkthrough.html#file=11).

### `ProvenanceError` — L42–L43

[Read source and line explanations](walkthrough.html#file=11&line=42).

Source documentation (check against implementation):

> A run without provider/model/prompt/schema provenance may not execute.

### `ImportedSchedule` — L47–L56

[Read source and line explanations](walkthrough.html#file=11&line=47).

Source documentation (check against implementation):

> One canonical draft produced from one detected schedule option.

### `_Completion` — L60–L62

[Read source and line explanations](walkthrough.html#file=11&line=60).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionClaimUnavailableError` — L65–L66

[Read source and line explanations](walkthrough.html#file=11&line=65).

Source documentation (check against implementation):

> Another worker owns this attempt, or the run has already finished.

### `ExtractionClaimLostError` — L69–L70

[Read source and line explanations](walkthrough.html#file=11&line=69).

Source documentation (check against implementation):

> An expired/replaced worker cannot finish, fail or renew the current attempt.

### `plan_fingerprint` — L73–L76

[Read source and line explanations](walkthrough.html#file=11&line=73).

Source documentation (check against implementation):

> Content address for an extracted plan (identical to the SQL bridge).

### `ExtractionService` — L80–L421

[Read source and line explanations](walkthrough.html#file=11&line=80).

Source documentation (check against implementation):

> Transactional boundary around a separately hosted extraction worker.

### `ExtractionService.__init__` — L83–L85

[Read source and line explanations](walkthrough.html#file=11&line=83).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService._require_claim` — L87–L93

[Read source and line explanations](walkthrough.html#file=11&line=87).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService._run` — L95–L103

[Read source and line explanations](walkthrough.html#file=11&line=95).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.queue` — L106–L116

[Read source and line explanations](walkthrough.html#file=11&line=106).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.queue_in` — L119–L148

[Read source and line explanations](walkthrough.html#file=11&line=119).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService._persist_one` — L152–L177

[Read source and line explanations](walkthrough.html#file=11&line=152).

Deep-copies a compiled schedule, assigns fresh issue IDs, marks it VALIDATION_REQUIRED, resolves/creates its definition and persists a new version. This is the handoff from in-memory result to stored draft.

Source documentation (check against implementation):

> Persist one already-extracted schedule as a new draft version.

### `ExtractionService.complete_in` — L179–L238

[Read source and line explanations](walkthrough.html#file=11&line=179).

Checks run ownership and state; persists usable schedule options; attaches traces/accountability; rejects empty accountable schedules; commits COMPLETED/PARTIAL/FAILED state through the caller's transaction.

Source documentation (check against implementation):

> Persist every schedule one run produced, then mark the run once.
>
> A run that produced no usable schedule is marked FAILED with every
> option's issues attached; that outcome is returned (not raised) so the
> FAILED state can be committed before the caller reports it.

### `ExtractionService.complete_many` — L240–L247

[Read source and line explanations](walkthrough.html#file=11&line=240).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.complete` — L249–L250

[Read source and line explanations](walkthrough.html#file=11&line=249).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.accountability_for_version` — L252–L264

[Read source and line explanations](walkthrough.html#file=11&line=252).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.accountability_for_version.work` — L253–L259

[Read source and line explanations](walkthrough.html#file=11&line=253).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.record_statement_decisions` — L267–L367

[Read source and line explanations](walkthrough.html#file=11&line=267).

Source documentation (check against implementation):

> Release the statements a question held, by recorded reviewer decision.

### `ExtractionService.record_statement_decisions.work` — L273–L365

[Read source and line explanations](walkthrough.html#file=11&line=273).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.require_provenance` — L371–L376

[Read source and line explanations](walkthrough.html#file=11&line=371).

Source documentation (check against implementation):

> A run may only execute when we can say WHICH provider, model, prompt and schema produced its drafts.

### `ExtractionService.start` — L378–L395

[Read source and line explanations](walkthrough.html#file=11&line=378).

Claims a queued/recoverable extraction attempt using a lease token. Workers must prove ownership before completing/failing it.

Source documentation (check against implementation):

> Atomically claim a queued run or reclaim an explicitly expired attempt.

### `ExtractionService.start.work` — L382–L393

[Read source and line explanations](walkthrough.html#file=11&line=382).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.renew` — L397–L406

[Read source and line explanations](walkthrough.html#file=11&line=397).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.renew.work` — L400–L405

[Read source and line explanations](walkthrough.html#file=11&line=400).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.fail` — L408–L421

[Read source and line explanations](walkthrough.html#file=11&line=408).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.fail.work` — L409–L419

[Read source and line explanations](walkthrough.html#file=11&line=409).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_trial` — L425–L440

[Read source and line explanations](walkthrough.html#file=11&line=425).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_protocol` — L443–L450

[Read source and line explanations](walkthrough.html#file=11&line=443).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_protocol_version` — L453–L477

[Read source and line explanations](walkthrough.html#file=11&line=453).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_imported` — L480–L486

[Read source and line explanations](walkthrough.html#file=11&line=480).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `import_native_schedules` — L489–L549

[Read source and line explanations](walkthrough.html#file=11&line=489).

Imports already-computed native extraction payloads through repository ports in one unit of work. It does not rerun the LLM to build a stored draft.

Source documentation (check against implementation):

> Import every natively extracted schedule option as a canonical draft.
>
> One unit of work: a failing option leaves nothing behind. A definition with
> no ``native_schedule`` is skipped - an empty schedule would look approvable
> while promising a patient nothing.

### `import_native_schedules.work` — L503–L547

[Read source and line explanations](walkthrough.html#file=11&line=503).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `import_schedule_definitions` — L552–L607

[Read source and line explanations](walkthrough.html#file=11&line=552).

Source documentation (check against implementation):

> Import every operational schedule definition as a canonical draft version.

### `import_schedule_definitions.work` — L562–L605

[Read source and line explanations](walkthrough.html#file=11&line=562).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleVersionRow` — L611–L624

[Read source and line explanations](walkthrough.html#file=11&line=611).

Source documentation (check against implementation):

> One row of the trial-level protocol/schedule version table (doc 9).

### `list_schedule_versions` — L627–L654

[Read source and line explanations](walkthrough.html#file=11&line=627).

Source documentation (check against implementation):

> Every canonical version this trial has, with its patient count; a
> superseded version is listed, never hidden.

### `list_schedule_versions.work` — L632–L652

[Read source and line explanations](walkthrough.html#file=11&line=632).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 13. backend/app/application/schedule_review.py

Applies reviewed changes and enforces approval gates in guarded writes.

542 lines. [Open annotated source](walkthrough.html#file=12).

### `approval_blockers` — L39–L52

[Read source and line explanations](walkthrough.html#file=12&line=39).

Uses the review projection to gather pending row, source coverage and statement-disposition blockers, in addition to the validator's rule findings.

Source documentation (check against implementation):

> THE approval gate: pending rows, source coverage and uncompiled
> statements block as well as validator findings. Empty means approvable.

### `_accountability` — L55–L63

[Read source and line explanations](walkthrough.html#file=12&line=55).

Source documentation (check against implementation):

> The coverage / statement ledger stored with the run that produced a draft.

### `ScheduleReviewService` — L68–L539

[Read source and line explanations](walkthrough.html#file=12&line=68).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.__init__` — L72–L74

[Read source and line explanations](walkthrough.html#file=12&line=72).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService._run` — L76–L84

[Read source and line explanations](walkthrough.html#file=12&line=76).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService._audit` — L88–L93

[Read source and line explanations](walkthrough.html#file=12&line=88).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService._require_not_frozen` — L96–L98

[Read source and line explanations](walkthrough.html#file=12&line=96).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.validate` — L101–L118

[Read source and line explanations](walkthrough.html#file=12&line=101).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.validate.work` — L102–L116

[Read source and line explanations](walkthrough.html#file=12&line=102).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.correct_event` — L120–L159

[Read source and line explanations](walkthrough.html#file=12&line=120).

Applies an explicit reviewer correction to an editable draft and records the decision/audit; approved versions are frozen.

### `ScheduleReviewService.correct_event.work` — L123–L157

[Read source and line explanations](walkthrough.html#file=12&line=123).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.bulk_confirm_all_fields` — L161–L167

[Read source and line explanations](walkthrough.html#file=12&line=161).

Source documentation (check against implementation):

> Confirm every required field with ONE reviewer action - and say so (doc s14).

### `ScheduleReviewService._bulk_confirm` — L169–L200

[Read source and line explanations](walkthrough.html#file=12&line=169).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.resolve_qualifier` — L202–L278

[Read source and line explanations](walkthrough.html#file=12&line=202).

Turns an extracted footnote into a recorded reviewer decision and, where supported, explicit schedule meaning.

Source documentation (check against implementation):

> Turn one extracted footnote into reviewed schedule meaning (doc s6).

### `ScheduleReviewService.resolve_qualifier.work` — L215–L276

[Read source and line explanations](walkthrough.html#file=12&line=215).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.record_decision` — L280–L304

[Read source and line explanations](walkthrough.html#file=12&line=280).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.record_decision.work` — L288–L302

[Read source and line explanations](walkthrough.html#file=12&line=288).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.submit_for_review` — L306–L307

[Read source and line explanations](walkthrough.html#file=12&line=306).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService._submit` — L309–L317

[Read source and line explanations](walkthrough.html#file=12&line=309).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.resolve_extraction_issue` — L319–L348

[Read source and line explanations](walkthrough.html#file=12&line=319).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.resolve_extraction_issue.work` — L326–L346

[Read source and line explanations](walkthrough.html#file=12&line=326).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.approve_reviewed_table` — L350–L377

[Read source and line explanations](walkthrough.html#file=12&line=350).

Source documentation (check against implementation):

> One explicit table confirmation, with the existing gates and bulk audit.

### `ScheduleReviewService.approve_reviewed_table.work` — L360–L375

[Read source and line explanations](walkthrough.html#file=12&line=360).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.approve` — L379–L384

[Read source and line explanations](walkthrough.html#file=12&line=379).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService._approve` — L386–L479

[Read source and line explanations](walkthrough.html#file=12&line=386).

Requires IN_REVIEW, enforces maker/checker policy, checks manual decisions and required inputs, reruns validation, checks approval blockers and required field confirmations, then stamps approval hashes in a revision-guarded write and stores evaluator compilation metadata.

### `ScheduleReviewService._enforce_maker_checker` — L481–L494

[Read source and line explanations](walkthrough.html#file=12&line=481).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.reject` — L496–L516

[Read source and line explanations](walkthrough.html#file=12&line=496).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.reject.work` — L497–L514

[Read source and line explanations](walkthrough.html#file=12&line=497).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleReviewService.list_pending_review` — L518–L539

[Read source and line explanations](walkthrough.html#file=12&line=518).

Source documentation (check against implementation):

> Every IN_REVIEW schedule version across this organisation's trials.

### `ScheduleReviewService.list_pending_review.work` — L520–L537

[Read source and line explanations](walkthrough.html#file=12&line=520).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 14. backend/app/domain/schedule/evaluator.py

Evaluates approved schedule rules against a patient's recorded context.

3486 lines. [Open annotated source](walkthrough.html#file=13).

### `_confinement_integrity` — L120–L138

[Read source and line explanations](walkthrough.html#file=13&line=120).

Source documentation (check against implementation):

> Doc 6 s26 rule 1: discharge must not precede admission.

### `_flag_overlapping_episodes` — L141–L192

[Read source and line explanations](walkthrough.html#file=13&line=141).

Source documentation (check against implementation):

> Doc 6 s26 rules 2 and 3: a patient cannot be in two stays at once.
>
> Overlap is judged on the dates the patient is actually committed to, so an
> episode that is NOT_APPLICABLE or still waiting on its anchor is ignored.

### `_flag_overlapping_episodes.span` — L158–L162

[Read source and line explanations](walkthrough.html#file=13&line=158).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_evaluate_on_demand` — L195–L248

[Read source and line explanations](walkthrough.html#file=13&line=195).

Source documentation (check against implementation):

> Doc 10 s13-s15: an unscheduled definition, plus whatever was created.
>
> The definition itself is always returned so the schedule shows it exists and
> a site can create one. It is AVAILABLE_ON_DEMAND, so nothing downstream can
> read it as due, overdue, or missed. Each occurrence a site actually created
> is returned as a dated, completed visit carrying the definition's activities.

### `_with_block_reason` — L251–L264

[Read source and line explanations](walkthrough.html#file=13&line=251).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_containing_pause` — L267–L282

[Read source and line explanations](walkthrough.html#file=13&line=267).

Source documentation (check against implementation):

> The pause period this date falls in, treating an open pause as unbounded.
>
> A pause covers its own start day (doc I1: held on 26-Oct, the C2 dose due
> 26-Oct is PAUSED, not left due and then overdue) up to, not including, the
> day treatment resumes.

### `_pause_days_before` — L285–L297

[Read source and line explanations](walkthrough.html#file=13&line=285).

Source documentation (check against implementation):

> Total days the block was paused before this occurrence.

### `_shift` — L300–L301

[Read source and line explanations](walkthrough.html#file=13&line=300).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_apply_resume_mode` — L304–L355

[Read source and line explanations](walkthrough.html#file=13&line=304).

Source documentation (check against implementation):

> Doc 3 s20: what the cadence does after a pause ends.

### `_cadence_text` — L358–L366

[Read source and line explanations](walkthrough.html#file=13&line=358).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_milestone_text` — L369–L373

[Read source and line explanations](walkthrough.html#file=13&line=369).

Source documentation (check against implementation):

> '+24 MONTH' reads as 'year 2'; anything else keeps its own unit.

### `_interval_text` — L376–L381

[Read source and line explanations](walkthrough.html#file=13&line=376).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `calendar_cadence_text` — L384–L389

[Read source and line explanations](walkthrough.html#file=13&line=384).

Source documentation (check against implementation):

> 'every Mon, Tue, Wed, Thu, Fri' / 'every 2nd week on Tue'.

### `plan_segment_texts` — L392–L402

[Read source and line explanations](walkthrough.html#file=13&line=392).

Source documentation (check against implementation):

> One phrase per cadence of a phased plan, in order (doc E9).

### `plan_cadence_text` — L405–L407

[Read source and line explanations](walkthrough.html#file=13&line=405).

Source documentation (check against implementation):

> 'every 3 months to year 2, then every 6 months to year 5, then yearly'.

### `_termination_text` — L410–L428

[Read source and line explanations](walkthrough.html#file=13&line=410).

Source documentation (check against implementation):

> Plain wording for how a repeat ends, never implying a cap we invented.

### `_apply_rolling_horizon` — L431–L559

[Read source and line explanations](walkthrough.html#file=13&line=431).

Limits what is materialized for the current projection. It does not convert the technical projection horizon into the clinical end of recurrence.

Source documentation (check against implementation):

> Doc 3 s7-s8: keep a bounded rolling set of upcoming occurrences.
>
> The cap governs what is MATERIALIZED, never how long the protocol runs. Every
> repeating rule that has more to come is reported in a RepeatSummary, so the
> caller can say "continues" rather than showing the last kept row as the end
> (doc 3 s2, s4, s36).

### `_as_date` — L562–L563

[Read source and line explanations](walkthrough.html#file=13&line=562).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_sort_value` — L566–L569

[Read source and line explanations](walkthrough.html#file=13&line=566).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_elapsed_day` — L572–L574

[Read source and line explanations](walkthrough.html#file=13&line=572).

Source documentation (check against implementation):

> Convert a clinical day number to days elapsed from Day 1 (there is no Day 0).

### `_first_nominal` — L577–L581

[Read source and line explanations](walkthrough.html#file=13&line=577).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_first_actual` — L584–L586

[Read source and line explanations](walkthrough.html#file=13&line=584).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_before_or_equal` — L589–L595

[Read source and line explanations](walkthrough.html#file=13&line=589).

Source documentation (check against implementation):

> Compare values that may mix dates and datetimes.

### `_localise` — L598–L602

[Read source and line explanations](walkthrough.html#file=13&line=598).

Source documentation (check against implementation):

> Read a naive recorded date-time as local site time; dates stay dates.

### `localise_context` — L605–L622

[Read source and line explanations](walkthrough.html#file=13&line=605).

Source documentation (check against implementation):

> Doc K2: give every naive recorded date-time the site's time zone.
>
> Without a zone a naive 00:30 has no defined elapsed-time relationship to
> anything, so hour-based rules could not be computed correctly across a
> clock change. Values already carrying a zone are left as recorded.

### `nth_occurrence` — L635–L660

[Read source and line explanations](walkthrough.html#file=13&line=635).

Calculates occurrence n relative to the original start so a month-end clamp in one occurrence does not permanently shift later months.

Source documentation (check against implementation):

> Occurrence ``n`` of a repeat, counted from its ORIGINAL start (doc E6/E8).
>
> Adding the interval to the previous occurrence lets a month-end clamp
> stick: monthly from 31-Jan gives 28-Feb, then 28-Mar forever. Counting
> every occurrence from the start keeps the protocol's day: 28-Feb, 31-Mar,
> 30-Apr; yearly from 29-Feb-2028 returns to 29-Feb in 2032. A stated
> first-occurrence offset is folded into the same single calculation when
> its unit is commensurable with the interval, so it cannot introduce a
> clamp of its own either. Days and weeks are unaffected mathematically.

### `_sum_offsets` — L663–L675

[Read source and line explanations](walkthrough.html#file=13&line=663).

Source documentation (check against implementation):

> Add two offsets in one calendar, or None when they cannot be combined
> exactly (weeks and months), so the caller falls back to a dated origin.

### `effective_window` — L678–L689

[Read source and line explanations](walkthrough.html#file=13&line=678).

Source documentation (check against implementation):

> The window stated for this visit, wherever the schedule stated it.
>
> ``Event.window`` is the independent rule (doc B2/B3). A NOMINAL_WITH_WINDOW
> timing still supplies one for schedules written before that existed. No
> window is ever invented: an event stating neither has none (doc B4).

### `_window_bounds` — L692–L700

[Read source and line explanations](walkthrough.html#file=13&line=692).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Bound` — L708–L751

[Read source and line explanations](walkthrough.html#file=13&line=708).

Source documentation (check against implementation):

> Where a repeat stops for this patient, resolved once per rule.
>
> Shared by plain recurrences, phased plans and treatment cycles so that
> "for 6 cycles", "until progression" and "for 2 years after X" mean the
> same thing whichever kind of repeat states them. ``status`` is set when
> the end itself cannot be decided yet; the repeat then gets no dates.

### `_Bound.interrupted_by_cap` — L731–L742

[Read source and line explanations](walkthrough.html#file=13&line=731).

Source documentation (check against implementation):

> Why the engine, not the protocol, ended the projection (or None).

### `_Bound.allows` — L744–L751

[Read source and line explanations](walkthrough.html#file=13&line=744).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_undated` — L754–L756

[Read source and line explanations](walkthrough.html#file=13&line=754).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_earlier` — L759–L760

[Read source and line explanations](walkthrough.html#file=13&line=759).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_resolve_termination` — L763–L819

[Read source and line explanations](walkthrough.html#file=13&line=763).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_basis_key` — L833–L837

[Read source and line explanations](walkthrough.html#file=13&line=833).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_history_by_code` — L840–L844

[Read source and line explanations](walkthrough.html#file=13&line=840).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_select_position` — L847–L854

[Read source and line explanations](walkthrough.html#file=13&line=847).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_manual_value` — L857–L865

[Read source and line explanations](walkthrough.html#file=13&line=857).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_basis_series` — L868–L910

[Read source and line explanations](walkthrough.html#file=13&line=868).

Source documentation (check against implementation):

> The dates a reference's basis resolves to, by occurrence index, and
> where each came from. A missing date is absent - never filled in.

### `_prepare_basis_values` — L913–L953

[Read source and line explanations](walkthrough.html#file=13&line=913).

Source documentation (check against implementation):

> Resolve every event reference of ``event`` to the series its basis names
> and report, per reference, which date was used and where it came from.

### `_actual_recorded` — L956–L976

[Read source and line explanations](walkthrough.html#file=13&line=956).

Source documentation (check against implementation):

> Is the actual that ACTUAL_PREVIOUS_EVENT waits for on ``code`` recorded?
> A rule counting from the treatment date waits for THAT date, not the visit.
>
> ``indices`` is the occurrence an exact dependency selector named: only that
> occurrence's own actual counts. None is the legacy event-level reading.

### `_exact_indices` — L979–L991

[Read source and line explanations](walkthrough.html#file=13&line=979).

Source documentation (check against implementation):

> code -> occurrence indexes named by an exact selector (ANY is absent).

### `_resolve_reference` — L994–L1021

[Read source and line explanations](walkthrough.html#file=13&line=994).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `evaluate_timing` — L1024–L1128

[Read source and line explanations](walkthrough.html#file=13&line=1024).

Resolves one typed timing expression using the available context. Missing anchor/dependency data is represented as waiting/unresolved behavior, not filled from today's date.

### `_coerce_threshold` — L1148–L1173

[Read source and line explanations](walkthrough.html#file=13&line=1148).

Source documentation (check against implementation):

> Parse a rule's string threshold to compare against an already-typed
> dimension value (AD-7: numeric/date-aware compare, not string compare).

### `_kleene_and` — L1176–L1179

[Read source and line explanations](walkthrough.html#file=13&line=1176).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_kleene_or` — L1182–L1185

[Read source and line explanations](walkthrough.html#file=13&line=1182).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_evaluate_applicability` — L1188–L1199

[Read source and line explanations](walkthrough.html#file=13&line=1188).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `assignment_dimensions` — L1208–L1209

[Read source and line explanations](walkthrough.html#file=13&line=1208).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_awaited_assignments` — L1212–L1233

[Read source and line explanations](walkthrough.html#file=13&line=1212).

Source documentation (check against implementation):

> The assignments an UNKNOWN applicability is waiting for - or nothing.
>
> Non-empty only when EVERY rule that could not be decided is an assignment
> dimension. If any undecided rule is a clinical attribute (age, WOCBP, a
> typed condition), the visit is still waiting on a clinical fact, so it
> stays WAITING_FOR_CONDITION rather than claiming randomisation alone will
> settle it.

### `_applicability_rule_results` — L1236–L1273

[Read source and line explanations](walkthrough.html#file=13&line=1236).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_missing_activity_anchor` — L1276–L1285

[Read source and line explanations](walkthrough.html#file=13&line=1276).

Source documentation (check against implementation):

> Name the intra-day activity a timing expression waits on, if any.

### `_event_anchor_references` — L1288–L1310

[Read source and line explanations](walkthrough.html#file=13&line=1288).

Source documentation (check against implementation):

> Every anchor an event's own date depends on, including its repeat rule.

### `_record_anchor_basis` — L1313–L1333

[Read source and line explanations](walkthrough.html#file=13&line=1313).

Source documentation (check against implementation):

> Say which anchors dated this visit from a plan, and which wait on reality.
>
> Returns the anchors that have only a planned value while this event's rule
> accepts an actual one - the reason the visit is still undated (doc D2).

### `_waiting_for_actual_reason` — L1336–L1340

[Read source and line explanations](walkthrough.html#file=13&line=1336).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `schedule_cycle_plans` — L1343–L1347

[Read source and line explanations](walkthrough.html#file=13&line=1343).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_cycle_plan_for` — L1350–L1353

[Read source and line explanations](walkthrough.html#file=13&line=1350).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_every_cycle_plan` — L1356–L1360

[Read source and line explanations](walkthrough.html#file=13&line=1356).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CycleStart` — L1364–L1377

[Read source and line explanations](walkthrough.html#file=13&line=1364).

Source documentation (check against implementation):

> One treatment cycle's start for this patient.
>
> ``start`` is None when the cycle cannot be dated yet; ``status`` then says
> why (BLOCKED waiting for the previous cycle's actual start, WAITING_FOR_ANCHOR
> for a manual entry, UNRESOLVED for an unstated mode).

### `_actual_cycle_start` — L1380–L1391

[Read source and line explanations](walkthrough.html#file=13&line=1380).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cycle_starts` — L1394–L1472

[Read source and line explanations](walkthrough.html#file=13&line=1394).

Calculates each cycle's start using its explicit dependency mode and known actuals up to termination/horizon. Planned starts are not automatically actual dates.

Source documentation (check against implementation):

> Every cycle start this patient has, up to the plan's end or the horizon.
>
> Returns the starts and the plan's resolved end. The end is None when Cycle
> 1's own anchor is not known yet, and carries a status when the plan's
> termination cannot be settled yet (no starts are listed then). The list stops at the
> first cycle that cannot be dated: in ACTUAL_PREVIOUS_CYCLE_START mode that
> cycle is returned BLOCKED on the previous cycle's actual start and no later
> cycle is listed, because none of them has a date to show.

### `publish_cycle_anchors` — L1475–L1503

[Read source and line explanations](walkthrough.html#file=13&line=1475).

Source documentation (check against implementation):

> Expose every cycle start as ``<code>_<n>_START`` (doc F2).
>
> Actual starts go to ``anchors``; calculated ones only to ``planned_anchors``,
> so a rule counting from CYCLE_3_START with the default ACTUAL acceptance
> waits for Cycle 3 to really start. Values a person recorded are never
> overwritten.

### `evaluate_activities` — L1506–L1621

[Read source and line explanations](walkthrough.html#file=13&line=1506).

Source documentation (check against implementation):

> Resolve one visit occurrence's activities.
>
> Activity times resolve progressively: an activity anchored on another activity
> (dose, infusion end, previous sample) stays WAITING_FOR_ANCHOR with a named
> reason until that activity's ACTUAL time is recorded. No time is ever invented,
> and a recorded actual time is never overwritten by a recalculated planned time.

### `_combine` — L1624–L1627

[Read source and line explanations](walkthrough.html#file=13&line=1624).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_evaluate_conditions` — L1630–L1636

[Read source and line explanations](walkthrough.html#file=13&line=1630).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `constraint_roots` — L1647–L1653

[Read source and line explanations](walkthrough.html#file=13&line=1647).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource` — L1656–L1855

[Read source and line explanations](walkthrough.html#file=13&line=1656).

Source documentation (check against implementation):

> Resolves constraint references against one patient occurrence.
>
> ACTUAL references read only recorded facts; PLANNED_OR_ACTUAL may fall back
> to a planned anchor or a CALCULATED target of an earlier-evaluated event.
> A planned value never satisfies an ACTUAL-only reference (PT-01).

### `ContextValueSource.__init__` — L1664–L1679

[Read source and line explanations](walkthrough.html#file=13&line=1664).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource.process_boundary` — L1681–L1699

[Read source and line explanations](walkthrough.html#file=13&line=1681).

Source documentation (check against implementation):

> START/END of a process from its CONFIRMED transitions only.

### `ContextValueSource.process_boundary.missing` — L1683–L1686

[Read source and line explanations](walkthrough.html#file=13&line=1683).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource._planned_ends` — L1701–L1710

[Read source and line explanations](walkthrough.html#file=13&line=1701).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource._counted` — L1712–L1720

[Read source and line explanations](walkthrough.html#file=13&line=1712).

Source documentation (check against implementation):

> Occurrence indexes that still count toward a series (a cancelled or
> inapplicable occurrence can never become the last one).

### `ContextValueSource.anchor` — L1722–L1733

[Read source and line explanations](walkthrough.html#file=13&line=1722).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource._actual_by_index` — L1735–L1746

[Read source and line explanations](walkthrough.html#file=13&line=1735).

Source documentation (check against implementation):

> Recorded actual dates keyed by the occurrence they belong to.
>
> Positional (the n-th recorded date is occurrence n) only for a context
> built without occurrence history; with history an occurrence that was
> missed or skipped can no longer shift the later ones (D5).

### `ContextValueSource._planned_series` — L1748–L1759

[Read source and line explanations](walkthrough.html#file=13&line=1748).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource.event` — L1761–L1812

[Read source and line explanations](walkthrough.html#file=13&line=1761).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource.event.missing` — L1767–L1770

[Read source and line explanations](walkthrough.html#file=13&line=1767).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource.event.found` — L1772–L1775

[Read source and line explanations](walkthrough.html#file=13&line=1772).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ContextValueSource._end_of` — L1814–L1844

[Read source and line explanations](walkthrough.html#file=13&line=1814).

Source documentation (check against implementation):

> END boundary: planned from the nominal end, actual only from a
> recorded end (a performed start never proves the end).

### `ContextValueSource.activity` — L1846–L1855

[Read source and line explanations](walkthrough.html#file=13&line=1846).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_base_window` — L1858–L1867

[Read source and line explanations](walkthrough.html#file=13&line=1858).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_timing_missing_inputs` — L1870–L1893

[Read source and line explanations](walkthrough.html#file=13&line=1870).

Source documentation (check against implementation):

> Name the references a legacy timing is still waiting on.

### `_feasibility_fields` — L1896–L1904

[Read source and line explanations](walkthrough.html#file=13&line=1896).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_describe_missing` — L1907–L1909

[Read source and line explanations](walkthrough.html#file=13&line=1907).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_timing_from_feasible` — L1912–L1949

[Read source and line explanations](walkthrough.html#file=13&line=1912).

Source documentation (check against implementation):

> Carry a FEASIBLE set onto the flat timing fields without widening it.
>
> The flat earliest/latest pair can hold ONE interval. When the nominal
> target lies inside the set, the interval containing it is used; otherwise
> the first interval is, and the nominal target is dropped (a constraint that
> excludes the protocol target leaves no target - none is invented). A
> CONSTRAINT-precision timing (range/window-only) never had a target, so its
> nominal fields are cleared: a range start is not a planned date. Every
> interval stays in ``feasible_intervals`` and in ``timing.constraints``.

### `apply_event_constraints` — L1952–L2014

[Read source and line explanations](walkthrough.html#file=13&line=1952).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `apply_activity_constraints` — L2017–L2062

[Read source and line explanations](walkthrough.html#file=13&line=2017).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_Run` — L2066–L2078

[Read source and line explanations](walkthrough.html#file=13&line=2066).

Source documentation (check against implementation):

> Per-evaluation WP3 inputs, resolved once (never stored on the evaluator).

### `_open_series` — L2081–L2117

[Read source and line explanations](walkthrough.html#file=13&line=2081).

Source documentation (check against implementation):

> Series whose LAST occurrence cannot be known yet (design: "LAST in an
> unfinished series can be unknown").
>
> Open-ended by rule - HORIZON termination (or a CONDITION one that has not
> fired), or a conditional repeat until resolution that is unresolved - and
> not ended by a terminal state of its process. A materialization horizon is
> never the end of a series.

### `_with_assignment_values` — L2120–L2134

[Read source and line explanations](walkthrough.html#file=13&line=2120).

Source documentation (check against implementation):

> A copy of the context carrying one assignment period's values.

### `_period_json` — L2137–L2142

[Read source and line explanations](walkthrough.html#file=13&line=2137).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_item_date` — L2145–L2150

[Read source and line explanations](walkthrough.html#file=13&line=2145).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_reindex` — L2153–L2164

[Read source and line explanations](walkthrough.html#file=13&line=2153).

Source documentation (check against implementation):

> Give one condition episode's occurrences their own identities (AT-10).

### `ScheduleEvaluator` — L2167–L3486

[Read source and line explanations](walkthrough.html#file=13&line=2167).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._evaluate_with_history` — L2170–L2242

[Read source and line explanations](walkthrough.html#file=13&line=2170).

Evaluates occurrences using assignments in force on their dates and distinct condition episodes, rather than applying the latest assignment to all history.

Source documentation (check against implementation):

> Evaluate one event over its assignment periods and condition episodes.
>
> RL-11/AT-11: with dated assignment history, each occurrence is judged
> against the assignment in force ON ITS DATE; a late crossover changes
> only the period after its effective date.
> Without history (every existing context) this is ``_evaluate_event``.

### `ScheduleEvaluator._evaluate_with_history.once` — L2189–L2206

[Read source and line explanations](walkthrough.html#file=13&line=2189).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._episode_identity` — L2245–L2255

[Read source and line explanations](walkthrough.html#file=13&line=2245).

Source documentation (check against implementation):

> AT-10: an ADD_EVENT visit of a LATER condition episode gets that
> episode's own identities, so it can never reuse - and overwrite - the
> first episode's persisted occurrence. Episode 0 keeps its numbering
> (NF-08). Repeats are numbered per episode in ``_episode_repeats``.

### `ScheduleEvaluator.evaluate` — L2257–L2400

[Read source and line explanations](walkthrough.html#file=13&line=2257).

Rejects unapproved or mismatched versions; obtains dependency order; plans reuse; localizes context; builds conditional/process/assignment state; evaluates events in dependency order; derives anchors; applies rolling materialization; returns explained occurrences and confinements.

### `ScheduleEvaluator._derive_event_anchors` — L2403–L2444

[Read source and line explanations](walkthrough.html#file=13&line=2403).

Publishes event-derived reference dates while keeping actual and merely planned dates separate.

Source documentation (check against implementation):

> Set anchors that come from an event (``Anchor.source_event_code``).
>
> Doc C6: only what actually happened is actual. A date the site recorded
> for the source event is actual; a date this evaluation merely CALCULATED
> for it is a plan, so it lands in ``planned_anchors`` and dates only rules
> that accept a planned value. A value recorded directly on the anchor
> always wins over one derived here, because a person entered it.
>
> A calculated date is used as a plan only when it is an exact target:
> a RANGE's start is the opening of a window, not the day surgery is
> planned for, so a range-timed event yields no planned anchor at all.
>
> "Last" is only actual once no scheduled occurrence remains outstanding:
> the second of six doses is the latest dose given, not the last dose.

### `ScheduleEvaluator._evaluate_confinements` — L2446–L2466

[Read source and line explanations](walkthrough.html#file=13&line=2446).

Source documentation (check against implementation):

> Resolve continuous inpatient stays as one parent episode each.
>
> A protocol that lists Day -1 through Day 3 while the patient stays admitted
> is ONE episode with four study days, not four hospital visits. Admission,
> dosing, and discharge stay distinct events so the dosing anchor is never
> replaced by the admission time.

### `ScheduleEvaluator._evaluate_confinement` — L2468–L2553

[Read source and line explanations](walkthrough.html#file=13&line=2468).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._conditional_gate` — L2555–L2590

[Read source and line explanations](walkthrough.html#file=13&line=2555).

Decides whether a conditional occurrence is active before calculating its date. An untriggered visit cannot acquire an artificial due date.

Source documentation (check against implementation):

> Apply conditional consequences before any date is calculated.
>
> An untriggered conditional visit must never receive a date, because a dated
> visit becomes due, then overdue, and generates reminders for something the
> protocol may never require of this patient.

### `ScheduleEvaluator._expand_conditional_repeats` — L2592–L2614

[Read source and line explanations](walkthrough.html#file=13&line=2592).

Source documentation (check against implementation):

> Generate 'repeat every N days until resolved' occurrences (doc 2 s17).
>
> Generation stops at the recorded resolution date, so once the PI/CRC marks
> the condition resolved no further repeat visit is produced. While the
> condition remains active, generation is bounded by the evaluation horizon.

### `ScheduleEvaluator._episode_repeats` — L2617–L2665

[Read source and line explanations](walkthrough.html#file=13&line=2617).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._apply_block_state` — L2668–L2740

[Read source and line explanations](walkthrough.html#file=13&line=2668).

Source documentation (check against implementation):

> Apply stop, pause and resume to a repeating block (doc 3 s19-s20).
>
> Occurrences already in the past are untouched: stopping a treatment branch
> cancels what has not happened, it does not erase completed cycles.
>
> Resume is the part the protocol has to answer. Once a paused block comes
> back, either the cadence stayed on its original grid (NOMINAL) or it
> restarts from the day the patient actually resumed (ACTUAL_RESUME). When
> the protocol does not say, no date is invented: the occurrences after the
> resume go to UNRESOLVED for a reviewer.

### `ScheduleEvaluator._evaluate_event` — L2742–L2926

[Read source and line explanations](walkthrough.html#file=13&line=2742).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._expand_recurrence` — L2928–L3023

[Read source and line explanations](walkthrough.html#file=13&line=2928).

Expands a repeat rule into indexed occurrences using stated cadence, termination, dependency and horizon behavior.

### `ScheduleEvaluator._expand_recurrence.occurrence` — L2971–L2972

[Read source and line explanations](walkthrough.html#file=13&line=2971).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._expand_basis_recurrence` — L3025–L3202

[Read source and line explanations](walkthrough.html#file=13&line=3025).

Source documentation (check against implementation):

> An interval series with an EXPLICIT basis (P4-02/P4-03).
>
> Occurrence 0 is the event's own resolved timing (so a first visit "+14
> days" is never restarted at the raw anchor and its offset is never
> applied twice); later occurrences follow ``rule.basis``. Nothing here
> invents a date for an actual that has not been recorded.

### `ScheduleEvaluator._expand_basis_recurrence.refuse` — L3051–L3055

[Read source and line explanations](walkthrough.html#file=13&line=3051).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._expand_basis_recurrence.value_at` — L3087–L3088

[Read source and line explanations](walkthrough.html#file=13&line=3087).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._expand_basis_recurrence.value_at` — L3107–L3108

[Read source and line explanations](walkthrough.html#file=13&line=3107).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleEvaluator._expand_recurrence_plan` — L3204–L3302

[Read source and line explanations](walkthrough.html#file=13&line=3204).

Expands phased follow-up as one indexed series across stated segment boundaries.

Source documentation (check against implementation):

> Expand a phased follow-up plan into one continuous series (doc E9).
>
> Each segment is counted from its own start - the plan anchor plus the
> previous segment's end offset - with ``nth_occurrence``, so a monthly
> cadence never drifts onto a clamped month-end and a boundary is
> computed exactly once. The previous segment's ``end_inclusive`` decides
> who owns a visit falling on the boundary: inclusive, it belongs to the
> ending segment and the next one starts one interval later; exclusive,
> the next segment's first visit is the boundary itself. So a boundary
> visit is never duplicated and never dropped.

### `ScheduleEvaluator._expand_calendar_recurrence` — L3304–L3385

[Read source and line explanations](walkthrough.html#file=13&line=3304).

Expands explicit weekday-series policies using the calendar recurrence operators and process holds.

Source documentation (check against implementation):

> Expand a weekday series (WEEKDAY_GRID.v1) - see calendar_recurrence.
>
> Each slot keeps its ordinal as ``occurrence_index`` whatever date it
> lands on. Holds of the event's process are laid back onto the grid
> here, so ``apply_process_states`` only applies stops for it.

### `ScheduleEvaluator._expand_cycle_days` — L3387–L3486

[Read source and line explanations](walkthrough.html#file=13&line=3387).

Dates Cycle-N-Day-D from that cycle's own start, preserving cycle occurrence identity and activity restrictions.

Source documentation (check against implementation):

> Date a Cycle-N-Day-D visit from its cycle's own start (doc F1-F5).
>
> Every-cycle visits ("Days 1, 8 and 15 of each cycle") get one
> occurrence per cycle, numbered cycle - 1, so an activity condition on
> ``occurrence_number`` means the cycle number (doc F5). A cycle that
> cannot be dated yet appears once, undated, with the reason - and in
> ACTUAL_PREVIOUS_CYCLE_START mode nothing after it is listed, because a
> later cycle has no date until the one before it actually starts.

## 15. backend/app/application/patient_schedule.py

Builds patient context, evaluates and persists projections and occurrence facts.

1347 lines. [Open annotated source](walkthrough.html#file=14).

### `occurrence_fact_from_record` — L89–L124

[Read source and line explanations](walkthrough.html#file=14&line=89).

Source documentation (check against implementation):

> The typed fact one occurrence record states, or None when it records
> nothing the engine acts on.

### `_as_date` — L127–L128

[Read source and line explanations](walkthrough.html#file=14&line=127).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_as_instant` — L131–L134

[Read source and line explanations](walkthrough.html#file=14&line=131).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `anchor_value` — L137–L148

[Read source and line explanations](walkthrough.html#file=14&line=137).

Source documentation (check against implementation):

> The anchor's value in the zone it was entered in. A store keeps instants in UTC, so the offset the
> clinic used (for example +05:30) is recorded with the anchor and restored here; otherwise a 23:30 IST dose
> would read as 18:00 UTC and its minute-based visit would land on the wrong calendar day.

### `PatientScheduleService` — L151–L1347

[Read source and line explanations](walkthrough.html#file=14&line=151).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.__init__` — L152–L153

[Read source and line explanations](walkthrough.html#file=14&line=152).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService._run` — L156–L170

[Read source and line explanations](walkthrough.html#file=14&line=156).

Source documentation (check against implementation):

> One atomic operation, retried when a concurrent writer won the race.
>
> A stale pointer or a lost sequence/idempotency slot means somebody else
> evaluated this patient first; re-running recalculates against what they
> wrote, so two commits can never both be "the latest" evaluation.

### `PatientScheduleService.result_date` — L174–L179

[Read source and line explanations](walkthrough.html#file=14&line=174).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.current_events` — L181–L194

[Read source and line explanations](walkthrough.html#file=14&line=181).

Source documentation (check against implementation):

> The latest evaluation's projections with recorded outcomes applied.
>
> The stored projection is immutable; "completed" / "missed" is what the
> newest un-superseded fact for that logical visit says.

### `PatientScheduleService.version_lineage` — L197–L228

[Read source and line explanations](walkthrough.html#file=14&line=197).

Uses explicit reviewed mappings to decide which historical facts can carry across schedule versions; equal event codes alone are insufficient.

Source documentation (check against implementation):

> The schedule versions whose occurrence facts apply to ``schedule_version_id``.
>
> (event code, occurrence index) is a logical identity only WITHIN a version,
> so a fact belongs to a version's history when and only when that is
> explicit: it was recorded against the version itself, OR the patient was
> approved to move from the version that holds it (the assignment history
> written by an impact confirm), OR a pending proposal names it as the source
> (``carry_from``: the preview/confirm evaluation of the target). A version
> that merely reuses an event code contributes nothing (P1-02).

### `PatientScheduleService.occurrences_for` — L230–L240

[Read source and line explanations](walkthrough.html#file=14&line=230).

Source documentation (check against implementation):

> The patient's occurrence facts that apply to one version, oldest first
> (one indexed query per version in its lineage, never one per event).

### `PatientScheduleService._outcomes` — L242–L256

[Read source and line explanations](walkthrough.html#file=14&line=242).

Source documentation (check against implementation):

> logical visit -> COMPLETED | MISSED, from the newest effective fact,
> over the version's explicit lineage only (see ``version_lineage``).

### `PatientScheduleService._overlay` — L259–L261

[Read source and line explanations](walkthrough.html#file=14&line=259).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.protected_event_keys` — L263–L289

[Read source and line explanations](walkthrough.html#file=14&line=263).

Source documentation (check against implementation):

> Visits whose dates and status a re-evaluation must carry forward.
>
> Only facts of the version the ``events`` belong to count; a version-less
> call takes it from the events themselves.

### `PatientScheduleService.patient_conditions` — L291–L303

[Read source and line explanations](walkthrough.html#file=14&line=291).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.build_context` — L306–L428

[Read source and line explanations](walkthrough.html#file=14&line=306).

Loads and normalizes the patient's anchors, occurrence history, condition state, assignments and other facts into a PatientContext for one schedule version.

### `PatientScheduleService._code_of` — L431–L434

[Read source and line explanations](walkthrough.html#file=14&line=431).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.evaluate_result` — L436–L466

[Read source and line explanations](walkthrough.html#file=14&line=436).

Loads the canonical schedule, verifies manual handling, gets compiled metadata, builds context and calls evaluation through the memoization layer. The imported evaluator is not necessarily called directly on every request.

### `PatientScheduleService.evaluate` — L469–L483

[Read source and line explanations](walkthrough.html#file=14&line=469).

Source documentation (check against implementation):

> Evaluate one patient against a schedule version (AD-4d: the caller may
> name the version explicitly; otherwise the pinned one is used).

### `PatientScheduleService.reconcile_projection` — L485–L586

[Read source and line explanations](walkthrough.html#file=14&line=485).

Pure reconciliation shared with preview: preserves protected outcomes/history while applying new calculated future occurrences.

Source documentation (check against implementation):

> THE reconciliation: a raw evaluation plus the patient's prior projection (and the
> keys whose history must be kept) become the rows that are PERSISTED. Pure and free of
> side effects, so a preview (amendment simulation) and the commit consume exactly the
> same semantics - protected history, effective-point boundaries, additions, removals,
> statuses and windows - instead of two calculations that can drift apart.

### `PatientScheduleService._evaluate` — L588–L652

[Read source and line explanations](walkthrough.html#file=14&line=588).

Performs evaluation inside a unit of work, reconciles immutable projections with protected history, persists the result/pointer and appends the evaluation audit record.

### `PatientScheduleService._activity_entries` — L655–L679

[Read source and line explanations](walkthrough.html#file=14&line=655).

Source documentation (check against implementation):

> Day-wise activity schedule of one visit occurrence.

### `PatientScheduleService._confinements` — L681–L710

[Read source and line explanations](walkthrough.html#file=14&line=681).

Source documentation (check against implementation):

> AD-8: persist this evaluation's confinement episodes. A DISCHARGED
> episode's dates are protected across re-evaluation, found by its code.

### `PatientScheduleService.create_unscheduled_visit` — L713–L730

[Read source and line explanations](walkthrough.html#file=14&line=713).

Source documentation (check against implementation):

> One occurrence of a protocol-defined unscheduled visit (doc 10 s13-s15).

### `PatientScheduleService.create_unscheduled_visit.work` — L723–L728

[Read source and line explanations](walkthrough.html#file=14&line=723).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.build_unscheduled_visit` — L733–L759

[Read source and line explanations](walkthrough.html#file=14&line=733).

Source documentation (check against implementation):

> Validate and build (not store) an unscheduled visit - shared with the
> command preview so it cannot accept what the commit would refuse.

### `PatientScheduleService.record_activity` — L761–L794

[Read source and line explanations](walkthrough.html#file=14&line=761).

Source documentation (check against implementation):

> An intra-day actual time or completion decision; corrections supersede.

### `PatientScheduleService.record_activity.work` — L772–L792

[Read source and line explanations](walkthrough.html#file=14&line=772).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.record_occurrence_fact` — L796–L812

[Read source and line explanations](walkthrough.html#file=14&line=796).

Records a performed/missed/booked fact against a logical occurrence. A booking is not an actual clinical event and a passed date is not proof of completion.

Source documentation (check against implementation):

> Record a performed / missed / booked fact for one logical occurrence.
>
> PT-02 append-only; PT-04 a booking never sets an actual date; PT-07
> nothing is derived from a date passing.

### `PatientScheduleService._record_fact` — L814–L841

[Read source and line explanations](walkthrough.html#file=14&line=814).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService._build_fact` — L843–L901

[Read source and line explanations](walkthrough.html#file=14&line=843).

Source documentation (check against implementation):

> Validate and BUILD one occurrence fact without storing it. The same
> function backs the real write and a command preview, so the preview can
> not accept what the commit would refuse.

### `PatientScheduleService.record_patient_fact` — L903–L925

[Read source and line explanations](walkthrough.html#file=14&line=903).

Source documentation (check against implementation):

> Append one reserved-code WP3 fact (process transition, assignment
> fact, site calendar) to the patient's append-only state history.

### `PatientScheduleService.record_patient_fact.work` — L915–L923

[Read source and line explanations](walkthrough.html#file=14&line=915).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService._history` — L928–L977

[Read source and line explanations](walkthrough.html#file=14&line=928).

Source documentation (check against implementation):

> Per logical visit: original / latest planned date, actual visit and
> actual treatment date, and status - read in three bounded queries
> (pointer, its projections, the patient's occurrence facts).

### `PatientScheduleService.current_calendar` — L979–L987

[Read source and line explanations](walkthrough.html#file=14&line=979).

Source documentation (check against implementation):

> The latest calendar: every visit with its plan AND what happened.
>
> Completion and actual dates come from the facts recorded against each
> logical visit, so they are present however many evaluations have run.

### `PatientScheduleService.calendar_in` — L989–L1055

[Read source and line explanations](walkthrough.html#file=14&line=989).

Source documentation (check against implementation):

> The shared calendar builder inside a caller-owned snapshot/transaction.

### `PatientScheduleService.calendar_in._d` — L1024–L1025

[Read source and line explanations](walkthrough.html#file=14&line=1024).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService._current_bookings` — L1059–L1073

[Read source and line explanations](walkthrough.html#file=14&line=1059).

Source documentation (check against implementation):

> logical key -> the booking in force (newest un-superseded, not withdrawn).

### `PatientScheduleService.record_actual_occurrence` — L1076–L1204

[Read source and line explanations](walkthrough.html#file=14&line=1076).

Validates actual dates, appends the occurrence fact, audits and reevaluates dependent dates atomically where required.

Source documentation (check against implementation):

> Record an actual occurrence and bring the schedule up to date, atomically.
>
> validate -> save the occurrence and audit it -> decide whether anything
> depends on it -> (only then) re-evaluate and save a NEW immutable
> evaluation -> return the before/after, with reasons -> write the outbox
> record. All of it commits together or not at all.
>
> * the same idempotency key with the same payload replays the first
>   result and writes nothing;
> * the same key with a different payload is refused (409);
> * the schedule version the request was resolved against is recorded on
>   the receipt; a replay after the patient moved to another version, or a
>   request naming an ``expected_schedule_version_id`` the patient is no
>   longer on, is refused (409) rather than silently replayed or applied;
> * an occurrence nothing depends on is saved and audited but triggers no
>   evaluation (empty change list, reason "no dependent rules").
>
> Correcting a wrongly entered date is the supersede path here; the full
> reversal command belongs to epic P5.

### `PatientScheduleService.record_actual_occurrence.work` — L1124–L1202

[Read source and line explanations](walkthrough.html#file=14&line=1124).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.check_actual_dates` — L1207–L1225

[Read source and line explanations](walkthrough.html#file=14&line=1207).

Source documentation (check against implementation):

> The rules every recorded actual obeys, whichever entry point carries it.

### `PatientScheduleService._ledger_fact` — L1228–L1261

[Read source and line explanations](walkthrough.html#file=14&line=1228).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService._changes` — L1264–L1308

[Read source and line explanations](walkthrough.html#file=14&line=1264).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.evaluate_in` — L1311–L1323

[Read source and line explanations](walkthrough.html#file=14&line=1311).

Source documentation (check against implementation):

> ``evaluate`` for a caller that already holds the unit of work.

### `PatientScheduleService.record_patient_fact_in` — L1326–L1340

[Read source and line explanations](walkthrough.html#file=14&line=1326).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleService.protected_keys_for` — L1343–L1347

[Read source and line explanations](walkthrough.html#file=14&line=1343).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 16. backend/app/application/schedule_projection.py

Converts confirmed resolved occurrences into calendar entries and reminder candidates.

348 lines. [Open annotated source](walkthrough.html#file=15).

### `FeasibleWindow` — L78–L83

[Read source and line explanations](walkthrough.html#file=15&line=78).

Source documentation (check against implementation):

> One permitted interval of a constrained occurrence, as inclusive dates.
> ``None`` on a side means unbounded on that side.

### `feasibility_from_explanation` — L86–L102

[Read source and line explanations](walkthrough.html#file=15&line=86).

Source documentation (check against implementation):

> Read the feasibility summary the evaluator stores in an occurrence's
> explanation (persisted as ``PatientEvent.generation_reason``) back into
> projection inputs. Returns (None, []) for legacy occurrences.

### `ProjectedEvent` — L105–L137

[Read source and line explanations](walkthrough.html#file=15&line=105).

Source documentation (check against implementation):

> The minimum a projection needs to know about one patient occurrence.

### `ProjectedConfinement` — L140–L146

[Read source and line explanations](walkthrough.html#file=15&line=140).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReminderCandidate` — L149–L159

[Read source and line explanations](walkthrough.html#file=15&line=149).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CalendarEntry` — L162–L169

[Read source and line explanations](walkthrough.html#file=15&line=162).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `SuppressedItem` — L172–L174

[Read source and line explanations](walkthrough.html#file=15&line=172).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProjectionResult` — L177–L180

[Read source and line explanations](walkthrough.html#file=15&line=177).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Reconciliation` — L183–L189

[Read source and line explanations](walkthrough.html#file=15&line=183).

Source documentation (check against implementation):

> What must actually be sent, updated, or withdrawn.

### `_policy_state` — L192–L197

[Read source and line explanations](walkthrough.html#file=15&line=192).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_windowed_policy_state` — L200–L212

[Read source and line explanations](walkthrough.html#file=15&line=200).

Source documentation (check against implementation):

> Due/overdue across disjoint permitted intervals: inside any interval is
> DUE_WINDOW, a gap between intervals is UPCOMING (the next interval is
> still permitted), and only after the last interval closes is it OVERDUE.

### `_wording` — L215–L224

[Read source and line explanations](walkthrough.html#file=15&line=215).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_requires_attendance` — L227–L231

[Read source and line explanations](walkthrough.html#file=15&line=227).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `project` — L234–L324

[Read source and line explanations](walkthrough.html#file=15&line=234).

Creates patient-facing entries only from eligible resolved dated occurrences. Applies attendance wording and confinement grouping; suppressed/undated items do not become reminders.

Source documentation (check against implementation):

> Project the confirmed patient plan into reminders and calendar entries.

### `reconcile` — L327–L348

[Read source and line explanations](walkthrough.html#file=15&line=327).

Diffs existing reminder keys against desired dated candidates. A moved date requires the old key to be cancelled and a new dated key to be scheduled.

Source documentation (check against implementation):

> Diff what is already scheduled against what the plan now requires.
>
> The reminder key contains the date, so a moved visit produces a cancel plus an
> add rather than leaving the patient reminded of both dates (doc 1 section 24).

## 17. backend/server.py

Contains upload/identity/consume/build endpoints and the rest of the platform's main API.

11570 lines. [Open annotated source](walkthrough.html#file=16).

### `now` — L57–L57

[Read source and line explanations](walkthrough.html#file=16&line=57).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `iso` — L58–L58

[Read source and line explanations](walkthrough.html#file=16&line=58).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `normalize_email` — L61–L63

[Read source and line explanations](walkthrough.html#file=16&line=61).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_split_calling_code` — L91–L97

[Read source and line explanations](walkthrough.html#file=16&line=91).

Source documentation (check against implementation):

> Longest-first match of `digits` against the known calling codes.

### `normalize_phone` — L100–L134

[Read source and line explanations](walkthrough.html#file=16&line=100).

Source documentation (check against implementation):

> Canonicalize a registration phone to E.164, defaulting to India.
>
> Numbers arrive from the client already in `+<code><national>` form. A bare
> number with no `+` keeps the historical Indian interpretation so existing
> clients and stored values stay valid.

### `_normalize_indian_national` — L137–L142

[Read source and line explanations](walkthrough.html#file=16&line=137).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `normalize_registration_profile` — L145–L169

[Read source and line explanations](walkthrough.html#file=16&line=145).

Source documentation (check against implementation):

> Validate role-specific dates and replace client-supplied age with truth.

### `RegisterIn` — L174–L182

[Read source and line explanations](walkthrough.html#file=16&line=174).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterStartIn` — L184–L199

[Read source and line explanations](walkthrough.html#file=16&line=184).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterAvailabilityIn` — L201–L203

[Read source and line explanations](walkthrough.html#file=16&line=201).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterVerifyIn` — L205–L208

[Read source and line explanations](walkthrough.html#file=16&line=205).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterCompleteIn` — L210–L212

[Read source and line explanations](walkthrough.html#file=16&line=210).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterSecurityQuestionsIn` — L214–L216

[Read source and line explanations](walkthrough.html#file=16&line=214).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RegisterResendIn` — L218–L220

[Read source and line explanations](walkthrough.html#file=16&line=218).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `LoginIn` — L222–L226

[Read source and line explanations](walkthrough.html#file=16&line=222).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `RefreshIn` — L229–L230

[Read source and line explanations](walkthrough.html#file=16&line=229).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `LogoutIn` — L233–L234

[Read source and line explanations](walkthrough.html#file=16&line=233).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ForgotIn` — L236–L238

[Read source and line explanations](walkthrough.html#file=16&line=236).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ForgotVerifyIn` — L241–L243

[Read source and line explanations](walkthrough.html#file=16&line=241).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `LoginSupportStartIn` — L246–L249

[Read source and line explanations](walkthrough.html#file=16&line=246).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `LoginSupportVerifyIn` — L252–L254

[Read source and line explanations](walkthrough.html#file=16&line=252).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ResetIn` — L257–L262

[Read source and line explanations](walkthrough.html#file=16&line=257).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PasswordResetLinkIn` — L264–L266

[Read source and line explanations](walkthrough.html#file=16&line=264).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProfileUpdateIn` — L268–L275

[Read source and line explanations](walkthrough.html#file=16&line=268).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ChangePasswordIn` — L277–L279

[Read source and line explanations](walkthrough.html#file=16&line=277).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ChangeContactStartIn` — L281–L283

[Read source and line explanations](walkthrough.html#file=16&line=281).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ChangeContactVerifyIn` — L285–L286

[Read source and line explanations](walkthrough.html#file=16&line=285).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TicketIn` — L288–L291

[Read source and line explanations](walkthrough.html#file=16&line=288).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EmergencyContactIn` — L293–L297

[Read source and line explanations](walkthrough.html#file=16&line=293).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrialIn` — L299–L316

[Read source and line explanations](walkthrough.html#file=16&line=299).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrialPatchIn` — L318–L333

[Read source and line explanations](walkthrough.html#file=16&line=318).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `SponsorTrialSiteIn` — L335–L352

[Read source and line explanations](walkthrough.html#file=16&line=335).

Source documentation (check against implementation):

> A sponsor-owned site assignment for one trial.
>
> The site record is persistent and may be reused across trials. Professional
> contact details are intentionally limited to the PI/site team; patient PII
> is never accepted or returned by this contract.

### `VisitIn` — L354–L398

[Read source and line explanations](walkthrough.html#file=16&line=354).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitUpdate` — L400–L439

[Read source and line explanations](walkthrough.html#file=16&line=400).

Source documentation (check against implementation):

> Partial edit of an existing visit TEMPLATE (Task 4.1 edit mode). Only the
> fields present are applied; trial_id is immutable here. `visit_number` is
> editable so the editor can keep template order unique after a row is
> deleted/reordered (Finding 1); a change re-points the seq of eligible
> future instances.

### `PatientIn` — L441–L482

[Read source and line explanations](walkthrough.html#file=16&line=441).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `UctsmTrialLinkIn` — L485–L488

[Read source and line explanations](walkthrough.html#file=16&line=485).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientInvitationIn` — L491–L494

[Read source and line explanations](walkthrough.html#file=16&line=491).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patient_initials` — L496–L507

[Read source and line explanations](walkthrough.html#file=16&line=496).

Source documentation (check against implementation):

> Normalize staff-entered initials, falling back to the first two name parts.

### `SchedulePreviewIn` — L509–L519

[Read source and line explanations](walkthrough.html#file=16&line=509).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MessageIn` — L521–L523

[Read source and line explanations](walkthrough.html#file=16&line=521).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ChatAttachmentIn` — L525–L530

[Read source and line explanations](walkthrough.html#file=16&line=525).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ChatMessageIn` — L532–L535

[Read source and line explanations](walkthrough.html#file=16&line=532).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConversationIn` — L537–L542

[Read source and line explanations](walkthrough.html#file=16&line=537).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConversationSettingsIn` — L544–L547

[Read source and line explanations](walkthrough.html#file=16&line=544).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConversationMembersIn` — L549–L550

[Read source and line explanations](walkthrough.html#file=16&line=549).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConversationReportIn` — L552–L553

[Read source and line explanations](walkthrough.html#file=16&line=552).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TeamMemberPatchIn` — L555–L559

[Read source and line explanations](walkthrough.html#file=16&line=555).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `make_token` — L562–L566

[Read source and line explanations](walkthrough.html#file=16&line=562).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_refresh_token_hash` — L569–L570

[Read source and line explanations](walkthrough.html#file=16&line=569).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `issue_refresh_token` — L573–L593

[Read source and line explanations](walkthrough.html#file=16&line=573).

Source documentation (check against implementation):

> Issue a one-time opaque refresh token and persist only its SHA-256 hash.

### `revoke_refresh_family` — L596–L611

[Read source and line explanations](walkthrough.html#file=16&line=596).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `current_user` — L613–L636

[Read source and line explanations](walkthrough.html#file=16&line=613).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `optional_current_user` — L639–L643

[Read source and line explanations](walkthrough.html#file=16&line=639).

Source documentation (check against implementation):

> Return the signed-in user when supplied, while allowing public lookups.

### `require_roles` — L645–L650

[Read source and line explanations](walkthrough.html#file=16&line=645).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `require_roles.dep` — L646–L649

[Read source and line explanations](walkthrough.html#file=16&line=646).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `serialize` — L652–L657

[Read source and line explanations](walkthrough.html#file=16&line=652).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_read_upload_capped` — L659–L673

[Read source and line explanations](walkthrough.html#file=16&line=659).

Source documentation (check against implementation):

> Read an UploadFile in 1 MB chunks, aborting with 413 the moment the total
> exceeds max_bytes — so an oversized (or maliciously unbounded) request body
> never fully materializes in memory. Returns the bytes when within the cap.

### `write_audit` — L676–L702

[Read source and line explanations](walkthrough.html#file=16&line=676).

Source documentation (check against implementation):

> Write a standard audit row for any mutation.
>
> `user` is the acting user document (or None for anonymous/public actions,
> e.g. a public invitation accept). `action` is dotted `category.verb`
> (e.g. 'visit.patch'); the category is derived from it unless overridden
> via ctx. Extra keyword context (target_id, changes, …) is stored verbatim.
> Returns the audit row id.

### `org_type_for_role` — L707–L710

[Read source and line explanations](walkthrough.html#file=16&line=707).

Source documentation (check against implementation):

> sponsor/cro/smo/site users belong to that org type; pi/crc (and anyone
> else) work at a site.

### `organization_name_key` — L712–L713

[Read source and line explanations](walkthrough.html#file=16&line=712).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `find_organization_by_name` — L716–L728

[Read source and line explanations](walkthrough.html#file=16&line=716).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `find_existing_organization` — L731–L747

[Read source and line explanations](walkthrough.html#file=16&line=731).

Source documentation (check against implementation):

> Match a real-world place identifier first, then the normalized name.
>
> Place IDs protect against duplicate registrations when Google's canonical
> hospital name differs from the name already stored on MTB. Older records
> without a Place ID continue to use the existing exact normalized-name rule.

### `ensure_organization` — L750–L792

[Read source and line explanations](walkthrough.html#file=16&line=750).

Source documentation (check against implementation):

> Upsert an organization record the first time its name is seen
> and return ``(organization, created)``.

### `register` — L796–L835

[Read source and line explanations](walkthrough.html#file=16&line=796).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `login` — L838–L862

[Read source and line explanations](walkthrough.html#file=16&line=838).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `refresh_token` — L865–L916

[Read source and line explanations](walkthrough.html#file=16&line=865).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `logout` — L920–L926

[Read source and line explanations](walkthrough.html#file=16&line=920).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `me` — L929–L930

[Read source and line explanations](walkthrough.html#file=16&line=929).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `master_data_options` — L934–L954

[Read source and line explanations](walkthrough.html#file=16&line=934).

Source documentation (check against implementation):

> Published values plus pending/rejected values private to the caller.

### `update_me` — L957–L989

[Read source and line explanations](walkthrough.html#file=16&line=957).

Source documentation (check against implementation):

> Update the signed-in user's profile. Name/phone/email are top-level;
> dob/gender/language ride in the `profile` sub-document.

### `change_password` — L992–L999

[Read source and line explanations](walkthrough.html#file=16&line=992).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_tickets` — L1003–L1004

[Read source and line explanations](walkthrough.html#file=16&line=1003).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_ticket` — L1007–L1017

[Read source and line explanations](walkthrough.html#file=16&line=1007).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_deliver_login_support_code` — L1020–L1026

[Read source and line explanations](walkthrough.html#file=16&line=1020).

Source documentation (check against implementation):

> Do not let provider behavior disclose whether a pre-login email exists.

### `start_login_support` — L1030–L1082

[Read source and line explanations](walkthrough.html#file=16&line=1030).

Source documentation (check against implementation):

> Start an email-verified support request without requiring a session.
>
> The response deliberately does not reveal whether the email is registered.
> Unknown addresses receive a synthetic request id and no database record.

### `verify_login_support` — L1086–L1153

[Read source and line explanations](walkthrough.html#file=16&line=1086).

Source documentation (check against implementation):

> Verify the email code and create one admin-visible login support ticket.

### `forgot` — L1157–L1207

[Read source and line explanations](walkthrough.html#file=16&line=1157).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_validate_password_recovery_otp` — L1210–L1225

[Read source and line explanations](walkthrough.html#file=16&line=1210).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `verify_forgot_password_otp` — L1229–L1234

[Read source and line explanations](walkthrough.html#file=16&line=1229).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reset` — L1238–L1262

[Read source and line explanations](walkthrough.html#file=16&line=1238).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `complete_password_reset_link` — L1266–L1324

[Read source and line explanations](walkthrough.html#file=16&line=1266).

Source documentation (check against implementation):

> Consume an admin-issued reset/setup link exactly once.
>
> Only a SHA-256 digest is stored. Claiming the token is atomic, so concurrent
> or replayed submissions cannot both reset the account.

### `_channel_configured` — L1345–L1348

[Read source and line explanations](walkthrough.html#file=16&line=1345).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `required_channels` — L1350–L1352

[Read source and line explanations](walkthrough.html#file=16&line=1350).

Source documentation (check against implementation):

> Patients verify by phone only; everyone else verifies email AND phone.

### `_otp_matches` — L1354–L1365

[Read source and line explanations](walkthrough.html#file=16&line=1354).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_enforce_rate_limit` — L1367–L1382

[Read source and line explanations](walkthrough.html#file=16&line=1367).

Source documentation (check against implementation):

> Fixed-window per-identifier cap to curb abuse and runaway SMS spend.

### `_deliver_otp` — L1384–L1414

[Read source and line explanations](walkthrough.html#file=16&line=1384).

Source documentation (check against implementation):

> Send a code, mapping provider failures to clean HTTP errors. Blocking
> provider I/O runs in a threadpool so it never stalls the event loop.

### `_finalize_registration` — L1416–L1479

[Read source and line explanations](walkthrough.html#file=16&line=1416).

Source documentation (check against implementation):

> Create the real user from a fully-verified pending registration + issue tokens.

### `_complete_registration` — L1482–L1671

[Read source and line explanations](walkthrough.html#file=16&line=1482).

Source documentation (check against implementation):

> Create the account and consume its invitation as one lifecycle.

### `register_check_availability` — L1675–L1690

[Read source and line explanations](walkthrough.html#file=16&line=1675).

Source documentation (check against implementation):

> Field-level duplicate check for the registration details screen.
>
> The authoritative checks in ``register_start`` remain in place to protect
> against races between this preview and submission.

### `register_start` — L1694–L1908

[Read source and line explanations](walkthrough.html#file=16&line=1694).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `register_verify` — L1911–L1962

[Read source and line explanations](walkthrough.html#file=16&line=1911).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `register_complete` — L1965–L1980

[Read source and line explanations](walkthrough.html#file=16&line=1965).

Source documentation (check against implementation):

> Final step of the design flow: set the password on an already-verified pending
> registration and create the account.

### `register_security_questions` — L1984–L2023

[Read source and line explanations](walkthrough.html#file=16&line=1984).

Source documentation (check against implementation):

> Save recovery questions after phone/email verification, for every
> registration path (invited or self-registered).

### `register_resend` — L2026–L2061

[Read source and line explanations](walkthrough.html#file=16&line=2026).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_contact_value_taken` — L2070–L2071

[Read source and line explanations](walkthrough.html#file=16&line=2070).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `change_contact_start` — L2074–L2112

[Read source and line explanations](walkthrough.html#file=16&line=2074).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `change_contact_verify` — L2115–L2143

[Read source and line explanations](walkthrough.html#file=16&line=2115).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_protocol_details` — L2146–L2161

[Read source and line explanations](walkthrough.html#file=16&line=2146).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `lookup_protocol` — L2166–L2189

[Read source and line explanations](walkthrough.html#file=16&line=2166).

Source documentation (check against implementation):

> Resolve a protocol without exposing another sponsor's private studies.

### `lookup_protocol_query` — L2194–L2203

[Read source and line explanations](walkthrough.html#file=16&line=2194).

Source documentation (check against implementation):

> Query-parameter form used by the Add Trial UI.
>
> Keep the original path-parameter endpoint for backwards compatibility;
> both forms deliberately execute the same scoped lookup.

### `raise_extraction_unavailable` — L2206–L2213

[Read source and line explanations](walkthrough.html#file=16&line=2206).

Source documentation (check against implementation):

> No extraction provider is configured (UCTSM_EXTRACTION_PROVIDER plus
> its API key). Upload validation still runs first, so callers keep the
> same contract; the sponsor falls back to entering the schedule manually.

### `_extraction_pages` — L2216–L2223

[Read source and line explanations](walkthrough.html#file=16&line=2216).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_extraction_details` — L2226–L2232

[Read source and line explanations](walkthrough.html#file=16&line=2226).

Source documentation (check against implementation):

> The Add Trial form's details, in the legacy ExtractedTrialDetails shape.

### `_extraction_table_preview` — L2235–L2250

[Read source and line explanations](walkthrough.html#file=16&line=2235).

Source documentation (check against implementation):

> Schedule preview plus the accountability ledgers (source coverage and
> statement dispositions, shape ``uctsm-extraction-accountability.v1``) so
> the review screen can show what was read, skipped and left uncompiled.

### `_extraction_verification` — L2253–L2264

[Read source and line explanations](walkthrough.html#file=16&line=2253).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_identity_org_uuid_for` — L2267–L2270

[Read source and line explanations](walkthrough.html#file=16&line=2267).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_gate_protocol_upload` — L2273–L2319

[Read source and line explanations](walkthrough.html#file=16&line=2273).

Source documentation (check against implementation):

> The protocol identity gate (P2). Runs AFTER file/MIME/size validation and
> BEFORE any model call:
>
>     binary hash -> page text -> normalized-text fingerprint -> cited identity
>     -> deterministic classification -> (quarantine 409 | return the identity)
>
> A MISMATCH or UNCERTAIN upload is stored privately for review, never
> attached to a trial or protocol, and never reaches a model. Provider
> availability is deliberately not consulted here.
>
> Returns ``(identity, pages)``; raises 409 ``PROTOCOL_IDENTITY_BLOCKED``.

### `_extraction_payload` — L2322–L2337

[Read source and line explanations](walkthrough.html#file=16&line=2322).

Source documentation (check against implementation):

> The durable, reusable form of one full extraction (every schedule option).

### `_materialize_drafts` — L2340–L2376

[Read source and line explanations](walkthrough.html#file=16&line=2340).

Source documentation (check against implementation):

> Cache each schedule option of a (new or reused) result as a 2-hour,
> user-scoped draft carrying the identity it was verified under. No model call.

### `_extract_for_identity` — L2379–L2456

[Read source and line explanations](walkthrough.html#file=16&line=2379).

After identity clearance, claims/reuses an extraction keyed by organization/content/engine signature; runs extraction in a thread pool; stores the source and result; materializes draft extraction entries. Conflicting in-progress work returns 409.

Source documentation (check against implementation):

> Full extraction for a document that PASSED the identity gate, or reuse of
> a stored compatible result. Returns ``(details, entries, reused)``.
>
> Exactly one model run exists per (organization, document content, engine
> signature): the claim is storage-enforced. Another request for the same
> content gets the stored result, or 409 ``EXTRACTION_IN_PROGRESS``.

### `_run_protocol_extraction` — L2459–L2481

[Read source and line explanations](walkthrough.html#file=16&line=2459).

Validates/parses uploaded content and resolves identity before invoking the full extraction path.

Source documentation (check against implementation):

> Gate one uploaded protocol, then read it with the fact reader +
> deterministic compiler (app/extraction/) - or reuse a stored result - and
> cache every schedule option as a 2-hour protocol_extractions draft.
>
> Returns the trial details, one entry per schedule option carrying the
> stored draft document and its API preview, and the identity view.

### `extract_protocol_details` — L2486–L2521

[Read source and line explanations](walkthrough.html#file=16&line=2486).

Metadata-only upload endpoint. A provider configuration failure is 503; it is not evidence that every configured full extraction request always returns 503.

### `extract_protocol_alias` — L2526–L2610

[Read source and line explanations](walkthrough.html#file=16&line=2526).

Full protocol upload endpoint used by Add Trial. Delegates to shared identity-gated extraction helpers.

Source documentation (check against implementation):

> Add Trial protocol upload: trial details plus every schedule the
> protocol prints, each compiled from cited facts and cached for 2 hours so
> the trial can consume it after creation without another model call.
>
> ``schedule_option_id`` is accepted for backward compatibility; every
> independent Schedule of Assessments is extracted and returned together.

### `list_trials` — L2614–L2673

[Read source and line explanations](walkthrough.html#file=16&line=2614).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_trial` — L2676–L2710

[Read source and line explanations](walkthrough.html#file=16&line=2676).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_trial` — L2713–L2719

[Read source and line explanations](walkthrough.html#file=16&line=2713).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_trial` — L2722–L2753

[Read source and line explanations](walkthrough.html#file=16&line=2722).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sponsor_dashboard` — L2758–L3156

[Read source and line explanations](walkthrough.html#file=16&line=2758).

Source documentation (check against implementation):

> One de-identified, organization-scoped payload for the Sponsor/CRO app.
>
> Patient names/contact fields never leave this endpoint. Site performance is
> derived from aggregate enrolment and visit-instance status only.

### `_recruitment_bucket` — L3159–L3168

[Read source and line explanations](walkthrough.html#file=16&line=3159).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_sponsor_trial_detail_payload` — L3171–L3436

[Read source and line explanations](walkthrough.html#file=16&line=3171).

Source documentation (check against implementation):

> Build the sponsor/CRO detail contract from scoped aggregate data.
>
> Subjects are represented only by study identifiers/initials. Names,
> contact details, DOB and other direct patient identifiers are never added.

### `_sponsor_trial_detail_payload.visit_sort_timestamp` — L3244–L3254

[Read source and line explanations](walkthrough.html#file=16&line=3244).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_require_sponsor_trial_detail` — L3439–L3446

[Read source and line explanations](walkthrough.html#file=16&line=3439).

Source documentation (check against implementation):

> Resolve one trial and enforce its role-appropriate relationship boundary.

### `trial_recruitment` — L3451–L3473

[Read source and line explanations](walkthrough.html#file=16&line=3451).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `trial_subjects` — L3478–L3492

[Read source and line explanations](walkthrough.html#file=16&line=3478).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `trial_subject_visits` — L3497–L3520

[Read source and line explanations](walkthrough.html#file=16&line=3497).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `trial_team` — L3525–L3527

[Read source and line explanations](walkthrough.html#file=16&line=3525).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `trial_documents` — L3532–L3534

[Read source and line explanations](walkthrough.html#file=16&line=3532).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `trial_versions` — L3539–L3545

[Read source and line explanations](walkthrough.html#file=16&line=3539).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sponsor_trial_detail` — L3550–L3552

[Read source and line explanations](walkthrough.html#file=16&line=3550).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sponsor_pi_lookup` — L3557–L3578

[Read source and line explanations](walkthrough.html#file=16&line=3557).

Source documentation (check against implementation):

> Resolve an exact PI email for an organization-admin site assignment.

### `sponsor_share_site_directory` — L3583–L3697

[Read source and line explanations](walkthrough.html#file=16&line=3583).

Source documentation (check against implementation):

> All active Site organizations available to the schedule-share picker.
>
> Sponsor dashboard sites are deliberately scoped to the caller's existing
> network. Schedule sharing additionally needs a discovery surface so a
> sponsor can find a new registered Site and establish the relationship by
> sharing a trial. Only professional PI identity is returned; patient data is
> never part of this directory contract.

### `sponsor_trial_subjects` — L3702–L3722

[Read source and line explanations](walkthrough.html#file=16&line=3702).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sponsor_add_trial_site` — L3727–L3851

[Read source and line explanations](walkthrough.html#file=16&line=3727).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sponsor_import_trial_sites` — L3856–L3963

[Read source and line explanations](walkthrough.html#file=16&line=3856).

Source documentation (check against implementation):

> Import a CSV site roster with explicit per-row success/error results.
>
> Successful rows use the exact same persistent site-assignment and PI
> invitation workflow as single entry. A malformed row cannot be mistaken
> for an import: it is returned with its 1-based spreadsheet row number and
> validation message.

### `smo_dashboard` — L3968–L4077

[Read source and line explanations](walkthrough.html#file=16&line=3968).

Source documentation (check against implementation):

> De-identified OPERATIONAL dashboard for SMO/Site organization members.
>
> Deliberately separate from the org-admin console: this is available to
> every SMO/Site account and exposes network totals, masked subjects and
> visit workload, while member management, ownership transfer and other
> governance remain in the org-admin console (org_admin only).

### `_persist_schedule_definition` — L4079–L4120

[Read source and line explanations](walkthrough.html#file=16&line=4079).

Source documentation (check against implementation):

> Persist the immutable AI draft without replacing an approved schedule.

### `_persist_native_schedule_definition` — L4123–L4164

[Read source and line explanations](walkthrough.html#file=16&line=4123).

Source documentation (check against implementation):

> Native-pipeline sibling of `_persist_schedule_definition`: the cached
> draft already carries a full `native_schedule` (a UniversalSchedule, not
> a legacy ExtractedSchedule to translate), so this just copies it onto the
> immutable Mongo record `build_canonical_schedule` will later read -
> no second AI call, same "immutable AI draft" discipline as the legacy path.

### `_schedule_extraction_payload` — L4167–L4224

[Read source and line explanations](walkthrough.html#file=16&line=4167).

Source documentation (check against implementation):

> Convert an extracted schedule to the existing editor response contract.

### `extract_schedule` — L4229–L4305

[Read source and line explanations](walkthrough.html#file=16&line=4229).

Source documentation (check against implementation):

> Protocol upload for an existing trial. Trial-ownership scoped. Every
> schedule the protocol prints is compiled from cited facts and stored as a
> draft schedule definition for review (``POST /trials/{id}/uctsm-schedule``
> then builds the canonical draft); nothing is approved here.

### `consume_protocol_extraction` — L4310–L4386

[Read source and line explanations](walkthrough.html#file=16&line=4310).

Attaches an extracted option to a specific authorized trial without reading the model again.

Source documentation (check against implementation):

> Return the schedule produced during Add Trial without another AI call.

### `_require_verified_draft` — L4389–L4416

[Read source and line explanations](walkthrough.html#file=16&line=4389).

Source documentation (check against implementation):

> Consumption-time identity recheck (P2-02): the draft must carry an identity
> that is still permitted for this organization and trial.

### `IdentityResolveIn` — L4419–L4424

[Read source and line explanations](walkthrough.html#file=16&line=4419).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_identity_scope` — L4427–L4449

[Read source and line explanations](walkthrough.html#file=16&line=4427).

Source documentation (check against implementation):

> Load an identity inside the caller's organization and trial scope. Anything
> outside it is a plain 404, so existence is never disclosed.

### `_review_view` — L4452–L4463

[Read source and line explanations](walkthrough.html#file=16&line=4452).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_protocol_identities` — L4468–L4496

[Read source and line explanations](walkthrough.html#file=16&line=4468).

Source documentation (check against implementation):

> The review queue. Reviewers see their organization's items (within trial
> scope); everyone else sees only their own uploads, read-only.

### `get_protocol_identity` — L4501–L4506

[Read source and line explanations](walkthrough.html#file=16&line=4501).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_resume_identity` — L4509–L4539

[Read source and line explanations](walkthrough.html#file=16&line=4509).

Source documentation (check against implementation):

> Execute the durable resume intent of an APPROVED identity, exactly once.
>
> The decision and its intent were already committed; this is the side effect
> (reading the stored bytes, one extraction) run outside any database
> transaction. A failure leaves the intent retryable.

### `resolve_protocol_identity` — L4544–L4576

[Read source and line explanations](walkthrough.html#file=16&line=4544).

Source documentation (check against implementation):

> A reviewer's decision on a quarantined document. Needs the review
> permission, a reason and the revision the reviewer saw. Approval re-runs the
> deterministic classification and queues extraction once; rejection or an
> unresolved review never resumes it.

### `resume_protocol_identity` — L4581–L4590

[Read source and line explanations](walkthrough.html#file=16&line=4581).

Source documentation (check against implementation):

> Retry the extraction of an APPROVED identity whose resume failed (for
> example the provider was down). Idempotent: a completed or running resume is
> reported, never repeated.

### `get_identity_extraction` — L4595–L4622

[Read source and line explanations](walkthrough.html#file=16&line=4595).

Source documentation (check against implementation):

> The prepared schedule(s) of a resolved upload, for its uploader: fresh
> 2-hour drafts built from the durable result - no model call.

### `get_schedule_definition` — L4627–L4640

[Read source and line explanations](walkthrough.html#file=16&line=4627).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `preview_trial_schedule` — L4653–L4692

[Read source and line explanations](walkthrough.html#file=16&line=4653).

Source documentation (check against implementation):

> Calculate the selected trial's real protocol schedule before enrollment.
>
> This is intentionally read-only: staff must review the same template-based
> dates that will be copied to the patient only after invitation acceptance.

### `_require_schedule_owner` — L4695–L4704

[Read source and line explanations](walkthrough.html#file=16&line=4695).

Source documentation (check against implementation):

> Trial-ownership gate shared by the schedule CRUD endpoints. sponsor/cro
> own via their org (_trial_in_caller_org); pi owns via _pi_owns_trial. Raises
> 403 for a foreign trial (fail-closed).

### `list_trial_substudies` — L4709–L4720

[Read source and line explanations](walkthrough.html#file=16&line=4709).

Source documentation (check against implementation):

> The distinct substudy_label values saved on this trial's visits, for
> the enrollment substudy picker. Empty for every ordinary,
> single-schedule trial — the picker only ever appears when there's
> something to actually choose between.

### `list_trial_arms` — L4725–L4736

[Read source and line explanations](walkthrough.html#file=16&line=4725).

Source documentation (check against implementation):

> The distinct arm_label values saved on this trial's visits, for the
> enrollment arm picker. Empty for every ordinary, single-arm/shared
> trial — the picker only ever appears when there's something to
> actually choose between.

### `_patient_care_context` — L4739–L4766

[Read source and line explanations](walkthrough.html#file=16&line=4739).

Source documentation (check against implementation):

> Site + PI contact for a patient, joined from their assigned PI user.
>
> Enriches GET /visits/mine so the mobile app renders the real site name and
> PI (name / phone / email for tel:+mailto: links) instead of hardcoding
> "AIIMS Delhi / Dr. Sharma". All keys are always present (empty string when
> the patient has no PI assigned) so the client can rely on the shape.

### `_trial_checklist_map` — L4768–L4773

[Read source and line explanations](walkthrough.html#file=16&line=4768).

Source documentation (check against implementation):

> Map of visit-template id -> its `checklist` prep steps (empty list when
> the template carries none), used to enrich per-patient visit instances.

### `_structured_visit_procedures` — L4776–L4804

[Read source and line explanations](walkthrough.html#file=16&line=4776).

Source documentation (check against implementation):

> Return patient-safe structured procedure rows without inventing copy.

### `_patient_visit_detail` — L4807–L4862

[Read source and line explanations](walkthrough.html#file=16&line=4807).

Source documentation (check against implementation):

> Enrich an owned visit instance/template for the patient detail screen.

### `_apply_canonical_dates` — L4864–L4921

[Read source and line explanations](walkthrough.html#file=16&line=4864).

Source documentation (check against implementation):

> Overlay the canonical engine's visit dates when this trial has cut over.
>
> Returns `rows` untouched unless the trial is explicitly in ENGINE read mode,
> which requires a recorded parity run that matched for every patient on the
> version they are pinned to. Any failure here also returns the rows untouched:
> a schedule read must not break because the canonical side is unavailable.
>
> Only dates move. The operational documents keep their identity, comments,
> tasks and workflow state, so reverting is instant and nothing is lost.

### `_apply_canonical_dates._read` — L4889–L4911

[Read source and line explanations](walkthrough.html#file=16&line=4889).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `my_visits` — L4925–L4988

[Read source and line explanations](walkthrough.html#file=16&line=4925).

Source documentation (check against implementation):

> Return upcoming/completed visits for the logged-in patient.
>
> Served from the patient's own `visit_instances` (created at enrollment /
> startup migration). Falls back to on-the-fly template computation for
> legacy patient records that were never materialized, keeping the exact
> field names the mobile app already consumes. Each visit is additively
> enriched with site/pi_name/pi_phone/pi_email (joined from the patient's PI)
> and the visit template's `checklist`.

### `my_visit_detail` — L4992–L5021

[Read source and line explanations](walkthrough.html#file=16&line=4992).

Source documentation (check against implementation):

> Return exactly one visit owned by the logged-in patient.
>
> A real but foreign visit is a 403; an unknown visit is a 404. This prevents
> the detail UI from downloading the whole schedule and selecting a fallback.

### `_patient_visit_anchor` — L5029–L5053

[Read source and line explanations](walkthrough.html#file=16&line=5029).

Source documentation (check against implementation):

> The date a patient's visit schedule anchors on: their baseline date when
> present, else the explicit enrolment date for legacy/seed patients.
>
> Never substitutes the current date. Missing or invalid clinical anchors must
> remain visibly unresolved so downstream materialization can require review.

### `_schedule_window` — L5056–L5065

[Read source and line explanations](walkthrough.html#file=16&line=5056).

Source documentation (check against implementation):

> Return a protocol visit window, preserving asymmetric windows when set.

### `_normalized_template_timing` — L5068–L5109

[Read source and line explanations](walkthrough.html#file=16&line=5068).

Source documentation (check against implementation):

> Apply deterministic simple-Day conversion without mutating the caller.
>
> Historical rows have no complete numbering convention and pass through
> unchanged. Newly extracted rows with an exact ``Day N`` label are corrected
> from that evidence before persistence or date calculation.

### `_has_calculable_template_time` — L5112–L5119

[Read source and line explanations](walkthrough.html#file=16&line=5112).

Source documentation (check against implementation):

> Whether a template can produce a date without guessing baseline.

### `_calculate_template_datetime` — L5122–L5147

[Read source and line explanations](walkthrough.html#file=16&line=5122).

Source documentation (check against implementation):

> Calculate from the canonical offset and explicit hour interpretation.

### `_calculate_template_end_datetime` — L5150–L5167

[Read source and line explanations](walkthrough.html#file=16&line=5150).

Source documentation (check against implementation):

> Calculate an optional multi-day/hour-range end from the same anchor.

### `_template_matches_arm` — L5170–L5173

[Read source and line explanations](walkthrough.html#file=16&line=5170).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_template_matches_substudy` — L5176–L5184

[Read source and line explanations](walkthrough.html#file=16&line=5176).

Source documentation (check against implementation):

> Same "blank matches everyone" shape as _template_matches_arm, scoped
> to which independent Schedule of Assessments (substudy) a template
> belongs to. A template with no substudy_label (every ordinary,
> single-schedule trial) matches every patient regardless of their own
> substudy_label.

### `_build_schedule_preview` — L5187–L5233

[Read source and line explanations](walkthrough.html#file=16&line=5187).

Source documentation (check against implementation):

> Deterministically calculate a reviewable schedule without writing data.

### `_resolve_relative_template_offsets` — L5236–L5294

[Read source and line explanations](walkthrough.html#file=16&line=5236).

Source documentation (check against implementation):

> Recompute persisted relative visits to a fixed point.
>
> A target edit or rename must never leave a dependent visit carrying a stale
> absolute offset. Resolution is arm/period scoped; ambiguous, missing, and
> circular targets become explicitly undated/manual-review rows.

### `_recompute_relative_templates` — L5297–L5322

[Read source and line explanations](walkthrough.html#file=16&line=5297).

Source documentation (check against implementation):

> Persist and re-materialize relative visits affected by a template edit.

### `_visit_timing_snapshot` — L5333–L5336

[Read source and line explanations](walkthrough.html#file=16&line=5333).

Source documentation (check against implementation):

> Copy additive protocol timing evidence onto a patient visit instance.

### `_visit_task_snapshot` — L5339–L5367

[Read source and line explanations](walkthrough.html#file=16&line=5339).

Source documentation (check against implementation):

> Create deterministic task rows for a per-patient visit instance.
>
> IDs are derived from template + task kind + original position, so repeated
> materialization/migration produces the same identity while duplicate labels
> remain distinct.

### `_effective_visit_status` — L5370–L5397

[Read source and line explanations](walkthrough.html#file=16&line=5370).

Source documentation (check against implementation):

> Return the approved display status without overwriting explicit history.

### `_instance_scheduled_datetime` — L5400–L5410

[Read source and line explanations](walkthrough.html#file=16&line=5400).

Source documentation (check against implementation):

> The instance's scheduled date as an aware datetime, or None.

### `_instance_seq_value` — L5413–L5422

[Read source and line explanations](walkthrough.html#file=16&line=5413).

Source documentation (check against implementation):

> The instance's ordering number, tolerating the fractional seq an
> inserted visit carries so it can sit between two protocol visits.

### `_visit_instance_order_key` — L5425–L5440

[Read source and line explanations](walkthrough.html#file=16&line=5425).

Source documentation (check against implementation):

> Order a patient's visit instances the way a calendar reads them.
>
> Dated visits come first in date order, so a visit added or re-dated
> mid-trial lands *between* its neighbours rather than being pushed to the
> top or the bottom of the timeline. Visits with no calculable date
> (manual review) sit at the end in protocol order, where they cannot be
> mistaken for the next thing due. `seq` is the tie-break, so an inserted
> visit's fractional seq still resolves a same-day tie against the protocol
> visit it was added beside.

### `_order_visit_instances` — L5443–L5445

[Read source and line explanations](walkthrough.html#file=16&line=5443).

Source documentation (check against implementation):

> Sort visit instances chronologically (see _visit_instance_order_key).

### `_insertion_seq` — L5448–L5471

[Read source and line explanations](walkthrough.html#file=16&line=5448).

Source documentation (check against implementation):

> The ordering number that drops a newly added visit BETWEEN the visits
> it falls between by date.
>
> Returns the midpoint of its two dated neighbours' seq values, so the added
> visit keeps its place for every consumer that still reads `seq` — and so
> the protocol visits either side keep the numbers the site already knows
> them by, instead of everything after the insertion point shifting up one.

### `_ensure_visit_instance_workflow` — L5474–L5495

[Read source and line explanations](walkthrough.html#file=16&line=5474).

Source documentation (check against implementation):

> Lazily migrate legacy instances to per-visit task/comment snapshots.

### `materialize_visit_instances` — L5498–L5585

[Read source and line explanations](walkthrough.html#file=16&line=5498).

Source documentation (check against implementation):

> Create one visit_instance per trial visit template for `patient`.
>
> Idempotent per patient (no-op if any instances already exist). Honors the
> legacy `completed_visit_ids` list so migrated patients keep their history;
> otherwise status derives from the scheduled date vs. now (matching the old
> GET /visits/mine computation). Returns the number of instances created.

### `_materialize_new_template_for_enrolled` — L5588–L5664

[Read source and line explanations](walkthrough.html#file=16&line=5588).

Source documentation (check against implementation):

> Create the single instance of a JUST-ADDED template for every patient
> already enrolled in its trial (Finding 2).
>
> `materialize_visit_instances` is a per-patient no-op once a patient has any
> instances, so a template added mid-trial would otherwise never reach enrolled
> patients. Here we create only THIS template's instance, future-dated off each
> patient's own baseline/enrolment anchor, with the same status/shape as normal
> materialization. Patients not yet materialized are skipped (they'll pick it up
> at enrollment); an existing instance for this template is never duplicated.
> Returns the number of instances created.

### `_instance_is_repointable` — L5667–L5689

[Read source and line explanations](walkthrough.html#file=16&line=5667).

Source documentation (check against implementation):

> Whether a visit_instance may be safely re-materialized when its template
> changes. FAIL-CLOSED: only FUTURE, still-pending instances that no one has
> touched are eligible. Completed / missed / rescheduled / past instances and
> any instance carrying patient activity (a note, or an explicit
> updated_by from a PATCH) are treated as history and left untouched.

### `_rematerialize_template_change` — L5692–L5753

[Read source and line explanations](walkthrough.html#file=16&line=5692).

Source documentation (check against implementation):

> Propagate a TEMPLATE edit to the trial's future-pending visit_instances.
>
> Recomputes name/activities/window/scheduled_date for every eligible instance
> (see `_instance_is_repointable`) off each patient's own visit anchor, so a
> schedule edit flows through to patients who haven't yet had the visit —
> without ever clobbering completed/missed/past/touched history. Returns the
> number of instances updated.

### `_rematerialize_template_delete` — L5756–L5767

[Read source and line explanations](walkthrough.html#file=16&line=5756).

Source documentation (check against implementation):

> Remove the future-pending visit_instances of a DELETED template. Completed
> / missed / past / patient-touched instances are kept as history. Returns the
> number of instances removed.

### `_migrate_visit_instances` — L5770–L5782

[Read source and line explanations](walkthrough.html#file=16&line=5770).

Source documentation (check against implementation):

> Startup backfill: materialize instances for patients enrolled before the
> visit_instances collection existed. Idempotent + cheap (skips patients that
> already have instances); failures only log so the API still boots.

### `_derive_patient_status` — L5791–L5833

[Read source and line explanations](walkthrough.html#file=16&line=5791).

Source documentation (check against implementation):

> Reduce a patient's visit instances to a single list-level status plus
> the soonest actionable visit (`next_visit`), both computed on read.
>
> - no instances                       → 'no_visits'
> - a pending visit already past-due    → 'overdue'
> - a pending visit still ahead         → 'active'
> - every instance completed            → 'completed'
> - otherwise (only missed remain)      → 'active'
> `next_visit` is the soonest actionable instance (past-due first, else the
> next upcoming), or None when nothing is actionable. A stored instance
> only ever carries a static 'planned'/'completed' status — its real
> overdue/due/planned state is derived here via _effective_visit_status,
> the same derivation GET /patients/{id}/visits and GET /tasks apply.

### `_org_of` — L5847–L5852

[Read source and line explanations](walkthrough.html#file=16&line=5847).

Source documentation (check against implementation):

> The organization string of a user id (empty when unknown/unset).

### `_patient_site_org` — L5854–L5861

[Read source and line explanations](walkthrough.html#file=16&line=5854).

Source documentation (check against implementation):

> The site a patient belongs to: the org of its assigned PI, else its CRC,
> else whoever enrolled it (created_by).

### `_trial_in_caller_org` — L5863–L5886

[Read source and line explanations](walkthrough.html#file=16&line=5863).

Source documentation (check against implementation):

> True when a trial belongs to the sponsor/cro caller's organization — they
> created it, or its sponsor_name matches their org.

### `_can_access_trial` — L5889–L5944

[Read source and line explanations](walkthrough.html#file=16&line=5889).

Source documentation (check against implementation):

> Relationship-scoped trial access shared by list/detail/mutations.
>
> Sponsor/CRO: owned by or explicitly granted to their organization.
> PI: creator, same trial organization, or assigned to an enrolled subject.
> CRC: creator or assigned to an enrolled subject.
> Patient: enrolled in the trial through their own account.

### `_has_accepted_trial_invitation` — L5947–L5967

[Read source and line explanations](walkthrough.html#file=16&line=5947).

Source documentation (check against implementation):

> Match an accepted invite only on a real contact identifier.
>
> Empty phone values are common for email registrations and must never make
> unrelated users equivalent.

### `_can_access_patient` — L5969–L5986

[Read source and line explanations](walkthrough.html#file=16&line=5969).

Source documentation (check against implementation):

> Ownership predicate shared by every single-patient staff endpoint.
>
> pi/crc: the patient must be assigned to them (pi_id / crc_id), enrolled by
> them (created_by), or sit at their own site (same organization).
> sponsor/cro: the patient must be enrolled in a trial belonging to their org.
> Any other role: no access.

### `_require_patient` — L5988–L5996

[Read source and line explanations](walkthrough.html#file=16&line=5988).

Source documentation (check against implementation):

> Load a patient and enforce the caller's ownership scope. 404 when it does
> not exist at all; 403 when it exists but lies outside the caller's scope.

### `_pi_owns_trial` — L5998–L6015

[Read source and line explanations](walkthrough.html#file=16&line=5998).

Source documentation (check against implementation):

> Whether a PI may review a trial's visit schedule. FAIL-CLOSED: the PI must
> belong to the trial via one of three legitimate ties —
>   1. they created it (`created_by`), or
>   2. their org matches the trial's org (`sponsor_name`) — this is the
>      pre-enrollment approval path (valid even for an unclaimed trial), or
>   3. they are a listed PI on it (own a patient enrolled in it).
> A PI whose org differs from the trial's org, who is not the creator, and who
> has no enrolled patient gets no access — even on an 'unclaimed' trial (no
> prior 'unclaimed -> any PI' allow-path).

### `_canonical_uuid` — L6021–L6025

[Read source and line explanations](walkthrough.html#file=16&line=6021).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_operational_factory` — L6028–L6032

[Read source and line explanations](walkthrough.html#file=16&line=6028).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_compute_patient_projection_sync` — L6038–L6054

[Read source and line explanations](walkthrough.html#file=16&line=6038).

Source documentation (check against implementation):

> Runs on the threadpool: open a SQL session, evaluate, project.
>
> Doc s15: the schedule engine could already calculate reminders
> (app.services.schedule_projection.project); nothing ever consumed them.
> This is that consumer's SQL half - the Mongo half (who to notify, what
> was already sent) stays in the async caller, which is the only place
> that can touch both databases.

### `_sync_reminders_for_one_patient` — L6057–L6099

[Read source and line explanations](walkthrough.html#file=16&line=6057).

Source documentation (check against implementation):

> One patient's worker pass: compute, diff against what was sent, write.

### `sync_all_patient_reminders` — L6108–L6154

[Read source and line explanations](walkthrough.html#file=16&line=6108).

Source documentation (check against implementation):

> One pass over every canonically-linked patient. Never raises past a
> single patient's own failure - one bad record must not stop everyone
> else's reminders from being recalculated.
>
> AD-12: a per-patient failure used to be a log line only - invisible to
> the CRC/PI responsible for that patient's delivery. It is now also
> persisted onto the patient's own document (`uctsm_reminder_sync`) so
> `GET /api/patients/reminder-sync-status` can surface it, and clears
> itself the next time that patient's sync succeeds.

### `_extend_stale_horizons_sync` — L6166–L6218

[Read source and line explanations](walkthrough.html#file=16&line=6166).

Source documentation (check against implementation):

> Runs on the threadpool: one SQL session, one pass over every patient
> pinned to an approved schedule. A patient with no dated events yet
> (everything still WAITING_FOR_ANCHOR/WAITING_FOR_CONDITION) is skipped -
> extending the horizon cannot resolve a blocked input.

### `extend_stale_horizons` — L6221–L6244

[Read source and line explanations](walkthrough.html#file=16&line=6221).

Source documentation (check against implementation):

> AD-12's same discipline applied to this job: a per-patient failure is
> patient-visible delivery state (mirrored onto the linked patient's own
> Mongo document as `uctsm_horizon_refresh`), not only a log line. A
> canonical-only patient with no `external_patient_id` has no Mongo
> document to record it on and is logged only - there is nowhere else for
> that visibility to live today.

### `reminder_worker_loop` — L6247–L6260

[Read source and line explanations](walkthrough.html#file=16&line=6247).

Source documentation (check against implementation):

> Long-running startup task: keep in-app reminders current (doc s15),
> and keep every patient's materialized horizon from running dry (AD-10's
> Step 1a follow-up) - one loop, two periodic jobs, same cadence.

### `_link_operational_trial_sync` — L6263–L6278

[Read source and line explanations](walkthrough.html#file=16&line=6263).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_preview_canonical_enrolment_sync` — L6281–L6298

[Read source and line explanations](walkthrough.html#file=16&line=6281).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_validate_enrolment_schedule` — L6301–L6310

[Read source and line explanations](walkthrough.html#file=16&line=6301).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_enroll_operational_patient_sync` — L6313–L6342

[Read source and line explanations](walkthrough.html#file=16&line=6313).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_parse_baseline_date` — L6345–L6355

[Read source and line explanations](walkthrough.html#file=16&line=6345).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_project_operational_enrollment` — L6358–L6445

[Read source and line explanations](walkthrough.html#file=16&line=6358).

Source documentation (check against implementation):

> Assign and evaluate one linked patient, then refresh the Mongo read model.

### `_list_canonical_schedules_sync` — L6448–L6471

[Read source and line explanations](walkthrough.html#file=16&line=6448).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_canonical_schedules` — L6476–L6501

[Read source and line explanations](walkthrough.html#file=16&line=6476).

Source documentation (check against implementation):

> Protocol/schedule versions for this trial, newest first (doc 9, Case 10).
>
> Superseded versions are included on purpose: patients enrolled under one stay
> on it, so a site has to be able to see which schedule each patient is on.

### `link_trial_to_uctsm` — L6506–L6540

[Read source and line explanations](walkthrough.html#file=16&line=6506).

Source documentation (check against implementation):

> Explicitly bind an operational trial to one approved canonical schedule.

### `_import_canonical_schedules_sync` — L6543–L6595

[Read source and line explanations](walkthrough.html#file=16&line=6543).

Selects native import when native payloads are present; otherwise selects the legacy canonical-plan bridge. It imports stored definitions, not a second LLM extraction.

### `build_canonical_schedule` — L6600–L6673

[Read source and line explanations](walkthrough.html#file=16&line=6600).

Loads the trial and stored definitions, checks access, imports canonical drafts, updates linkage and returns version IDs. It does not approve or automatically cut over every trial's reads.

Source documentation (check against implementation):

> Turn this trial's extracted schedule into canonical draft versions.
>
> This is the join that makes UCTSM the scheduling system for a newly created
> trial: Add Trial extracts once, and the same plan becomes the canonical
> schedule the Sponsor/PI reviews, approves and enrols patients onto. It never
> approves anything - the draft lands in VALIDATION_REQUIRED for human review.

### `get_trial_uctsm_link` — L6678–L6710

[Read source and line explanations](walkthrough.html#file=16&line=6678).

Source documentation (check against implementation):

> Expose linkage/readiness and patient migration counts without changing data.

### `list_patients` — L6714–L6743

[Read source and line explanations](walkthrough.html#file=16&line=6714).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reminder_sync_status` — L6747–L6773

[Read source and line explanations](walkthrough.html#file=16&line=6747).

Source documentation (check against implementation):

> AD-12: patients whose reminder/calendar sync failed, visible to the
> CRC/PI/sponsor responsible for them - the worker already isolates one
> patient's failure from the rest, but used to only log it. Same
> trial/PI/CRC access scoping as `GET /patients`.

### `add_patient` — L6776–L6871

[Read source and line explanations](walkthrough.html#file=16&line=6776).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `add_patient.default_trial_staff` — L6799–L6815

[Read source and line explanations](walkthrough.html#file=16&line=6799).

Source documentation (check against implementation):

> Prefer the staff already handling this trial, then an active
> organisation colleague. This gives new patients a sensible trial-team
> default without replacing the patient-level assignment.

### `check_patient_invitation_availability` — L6876–L6919

[Read source and line explanations](walkthrough.html#file=16&line=6876).

Source documentation (check against implementation):

> Validate patient invite identifiers before the staff member submits.

### `invite_patient_for_enrollment` — L6923–L7056

[Read source and line explanations](walkthrough.html#file=16&line=6923).

Source documentation (check against implementation):

> Invite a patient to register, then enrol them only after acceptance.

### `invite_patient_for_enrollment.default_trial_staff` — L6951–L6964

[Read source and line explanations](walkthrough.html#file=16&line=6951).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_patient` — L7060–L7067

[Read source and line explanations](walkthrough.html#file=16&line=7060).

Source documentation (check against implementation):

> Patient detail: the patient record + its trial + its visit instances.

### `_audit_trail_canonical_sync` — L7070–L7129

[Read source and line explanations](walkthrough.html#file=16&line=7070).

Source documentation (check against implementation):

> The canonical (SQL) half of one patient's audit trail.
>
> Runs on the threadpool - a fresh SQL session, resolves the linked
> canonical patient via `external_patient_id` (the same lookup
> `_apply_canonical_dates` already uses), then reads every AuditEvent whose
> entity is that patient or something that belongs to them. Built by
> enumerating every `entity_type=`/`entity_id=` an AuditEvent construction
> site in app/ actually uses for a patient-owned row (grep, not guesswork):
> PATIENT (patient.id itself - activity records, state changes, schedule
> version assignment), PATIENT_EVENT (current_events()'s own ids - occurrence
> recording), PATIENT_ANCHOR (candidate ids, including ones derived from a
> recorded occurrence), PATIENT_CONDITION (condition ids), and
> SCHEDULE_IMPACT_PROPOSAL (proposal ids - impact_service.py's preview/
> confirm/cancel audit trail keys on the PROPOSAL's own id, not the
> patient's, so it would otherwise be invisible here). This is the concrete,
> boundable set of canonical rows one patient can have today - not a
> generic cross-table join engine, and not exhaustive against every entity
> type that could ever exist, only every one that exists now.

### `_as_aware` — L7132–L7138

[Read source and line explanations](walkthrough.html#file=16&line=7132).

Source documentation (check against implementation):

> SQLite (the test/dev backend) can return a naive datetime even for a
> timezone(True) column; Mongo's own `now()` never is. Comparing the two
> logs by timestamp must not crash on that difference.

### `patient_audit_trail` — L7142–L7176

[Read source and line explanations](walkthrough.html#file=16&line=7142).

Source documentation (check against implementation):

> Both audit logs, for one patient, merged and time-ordered.
>
> Doc's Consistency Conventions: two audit logs exist by design for as long
> as AD-1 runs - canonical schedule meaning/evaluation writes
> `uctsm_audit_events`, operational workflow state writes Mongo
> `audit_logs` - and "neither log may be read alone and presented as
> complete." This is that combined view.

### `reconcile_patient_uctsm` — L7181–L7214

[Read source and line explanations](walkthrough.html#file=16&line=7181).

Source documentation (check against implementation):

> Retry a failed/idempotent canonical projection; never migrates legacy rows silently.

### `get_patient_visits` — L7217–L7223

[Read source and line explanations](walkthrough.html#file=16&line=7217).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientVisitIn` — L7226–L7240

[Read source and line explanations](walkthrough.html#file=16&line=7226).

Source documentation (check against implementation):

> An extra, one-off visit added to a single patient's own schedule.
>
> This is the unscheduled/ad-hoc case — an unplanned safety check, a repeat
> lab, a re-consent — not a protocol amendment. It is added against a
> calendar date the site already knows, so it slots into the timeline
> between the protocol visits it falls between rather than at either end.

### `add_patient_visit` — L7244–L7311

[Read source and line explanations](walkthrough.html#file=16&line=7244).

Source documentation (check against implementation):

> Add one extra dated visit to THIS patient's schedule.
>
> Never touches the trial's visit templates, so no other patient is
> affected. The visit is placed by its date: `_insertion_seq` gives it an
> ordering number between its two dated neighbours, so it reads as an
> insertion into the middle of the schedule and every protocol visit keeps
> the number the site already knows it by.

### `delete_patient_visit` — L7315–L7334

[Read source and line explanations](walkthrough.html#file=16&line=7315).

Source documentation (check against implementation):

> Remove an extra visit that was added by mistake.
>
> Only ever an added (`unscheduled`) visit — a protocol visit belongs to the
> approved schedule and is cancelled through its status, never deleted.

### `list_organizations` — L7339–L7354

[Read source and line explanations](walkthrough.html#file=16&line=7339).

Source documentation (check against implementation):

> Public directory of known organizations (used by the register screen).
>
> Directory searches never include staff contact data. The deprecated
> ``include_platform_contact`` parameter is accepted for client compatibility
> but intentionally ignored; callers must use the exact organization contact
> endpoint after selecting a specific organization.

### `_enforce_places_rate_limit` — L7357–L7383

[Read source and line explanations](walkthrough.html#file=16&line=7357).

Source documentation (check against implementation):

> Limit public Places proxy traffic per client without retaining raw IPs.

### `hospital_place_autocomplete` — L7387–L7402

[Read source and line explanations](walkthrough.html#file=16&line=7387).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `hospital_place_details` — L7406–L7420

[Read source and line explanations](walkthrough.html#file=16&line=7406).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `organization_place_autocomplete` — L7424–L7439

[Read source and line explanations](walkthrough.html#file=16&line=7424).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `organization_place_details` — L7443–L7457

[Read source and line explanations](walkthrough.html#file=16&line=7443).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_public_platform_admin_contact` — L7460–L7476

[Read source and line explanations](walkthrough.html#file=16&line=7460).

Source documentation (check against implementation):

> Return the configured MTB Platform Administrator contact only.
>
> Registration duplicate warnings must never disclose PI, CRC, investigator,
> or organization-member details, even when that member is an org admin.

### `organization_registration_check` — L7480–L7506

[Read source and line explanations](walkthrough.html#file=16&line=7480).

Source documentation (check against implementation):

> Authoritative duplicate check used at the end of org registration.
>
> A Google Place ID is authoritative when supplied; exact normalized name is
> the fallback for manual entry and older organizations. Contact information
> is always the configured MTB Platform Administrator, never organization
> members such as PIs, CRCs, investigators, or organization admins.

### `organization_platform_contact` — L7510–L7526

[Read source and line explanations](walkthrough.html#file=16&line=7510).

Source documentation (check against implementation):

> Exact-match registration confirmation contract for an existing org.

### `my_notifications` — L7530–L7538

[Read source and line explanations](walkthrough.html#file=16&line=7530).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `unread_notification_count` — L7541–L7544

[Read source and line explanations](walkthrough.html#file=16&line=7541).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `mark_all_notifications_read` — L7547–L7552

[Read source and line explanations](walkthrough.html#file=16&line=7547).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `mark_read` — L7555–L7557

[Read source and line explanations](walkthrough.html#file=16&line=7555).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_require_conversation_member` — L7560–L7567

[Read source and line explanations](walkthrough.html#file=16&line=7560).

Source documentation (check against implementation):

> Load a conversation and fail closed unless the caller participates.

### `_patient_chat_assignment` — L7570–L7586

[Read source and line explanations](walkthrough.html#file=16&line=7570).

Source documentation (check against implementation):

> Patients may chat only with the PI/CRC assigned to their enrollment.

### `_chat_trial_ids` — L7589–L7634

[Read source and line explanations](walkthrough.html#file=16&line=7589).

Source documentation (check against implementation):

> Trials that establish a clinical working relationship for a staff user.

### `_can_chat_with` — L7637–L7652

[Read source and line explanations](walkthrough.html#file=16&line=7637).

Source documentation (check against implementation):

> Allow chat only inside an organization or an assigned trial relationship.

### `_cleared_at` — L7655–L7656

[Read source and line explanations](walkthrough.html#file=16&line=7655).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_visible_messages_filter` — L7659–L7667

[Read source and line explanations](walkthrough.html#file=16&line=7659).

Source documentation (check against implementation):

> Message query filter honoring this caller's per-user 'clear messages'
> boundary — messages sent before their last clear stay hidden for them
> only; everyone else's history is untouched.

### `_create_chat_message` — L7670–L7693

[Read source and line explanations](walkthrough.html#file=16&line=7670).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_conversation_media_count` — L7696–L7698

[Read source and line explanations](walkthrough.html#file=16&line=7696).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_hydrate_conversation_trial` — L7701–L7710

[Read source and line explanations](walkthrough.html#file=16&line=7701).

Source documentation (check against implementation):

> Attach {protocol_id, title} for a conversation's linked trial, if any.

### `list_conversations` — L7714–L7756

[Read source and line explanations](walkthrough.html#file=16&line=7714).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConversationFlagsIn` — L7759–L7762

[Read source and line explanations](walkthrough.html#file=16&line=7759).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `set_conversation_flags` — L7766–L7781

[Read source and line explanations](walkthrough.html#file=16&line=7766).

Source documentation (check against implementation):

> Per-user pin/mute/archive stored on the conversation (member-gated).

### `create_conversation` — L7784–L7810

[Read source and line explanations](walkthrough.html#file=16&line=7784).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_messaging_recipients` — L7814–L7824

[Read source and line explanations](walkthrough.html#file=16&line=7814).

Source documentation (check against implementation):

> Only return people the caller may legally start a conversation with.

### `messaging_unread_count` — L7828–L7841

[Read source and line explanations](walkthrough.html#file=16&line=7828).

Source documentation (check against implementation):

> Total unread messages across every non-archived conversation the caller
> is a member of — powers the Messages tab badge.

### `get_conversation_detail` — L7845–L7866

[Read source and line explanations](walkthrough.html#file=16&line=7845).

Source documentation (check against implementation):

> Full dossier for the channel-info screen: roster with role/org/online/
> admin flag, description, linked trial, shared-media count, and this
> caller's notification/archive state.

### `add_conversation_members` — L7870–L7893

[Read source and line explanations](walkthrough.html#file=16&line=7870).

Source documentation (check against implementation):

> Add members to a group conversation (any current member may invite).

### `remove_conversation_member` — L7897–L7913

[Read source and line explanations](walkthrough.html#file=16&line=7897).

Source documentation (check against implementation):

> Remove a member (self-removal = Leave group; removing someone else
> requires being the channel admin/creator).

### `get_conversation_invite_link` — L7917–L7926

[Read source and line explanations](walkthrough.html#file=16&line=7917).

Source documentation (check against implementation):

> Generate (once) and return this group's invite code.

### `join_conversation_by_invite` — L7930–L7941

[Read source and line explanations](walkthrough.html#file=16&line=7930).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_conversation_files` — L7945–L7962

[Read source and line explanations](walkthrough.html#file=16&line=7945).

Source documentation (check against implementation):

> Shared files & media filmstrip — every attachment ever posted in this
> conversation, newest first.

### `clear_conversation_messages` — L7966–L7972

[Read source and line explanations](walkthrough.html#file=16&line=7966).

Source documentation (check against implementation):

> 'Clear messages' — hides this caller's history up to now; everyone
> else's copy of the conversation is untouched.

### `report_conversation` — L7976–L7990

[Read source and line explanations](walkthrough.html#file=16&line=7976).

Source documentation (check against implementation):

> Report group — files a support ticket platform admins can triage.

### `update_conversation_settings` — L7994–L8010

[Read source and line explanations](walkthrough.html#file=16&line=7994).

Source documentation (check against implementation):

> Channel-wide settings: auto-delete timer, and (group only) rename/
> description edits. Admin/creator only.

### `get_messages` — L8014–L8024

[Read source and line explanations](walkthrough.html#file=16&line=8014).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `post_message` — L8028–L8037

[Read source and line explanations](walkthrough.html#file=16&line=8028).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `mark_conversation_read` — L8041–L8055

[Read source and line explanations](walkthrough.html#file=16&line=8041).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_users` — L8059–L8061

[Read source and line explanations](walkthrough.html#file=16&line=8059).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_can_manage_team_member` — L8065–L8072

[Read source and line explanations](walkthrough.html#file=16&line=8065).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_team` — L8075–L8185

[Read source and line explanations](walkthrough.html#file=16&line=8075).

Source documentation (check against implementation):

> Org- and trial-scoped clinical team for the caller — NOT the whole user
> directory. A member qualifies when they either share the caller's
> organization or collaborate on a trial the caller is connected to (its
> creator/sponsor, or the PI/CRC of any patient enrolled in it). Patients and
> unrelated accounts are never included.

### `_manageable_team_member` — L8188–L8200

[Read source and line explanations](walkthrough.html#file=16&line=8188).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_ensure_pi_remains` — L8202–L8212

[Read source and line explanations](walkthrough.html#file=16&line=8202).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_team_member` — L8215–L8265

[Read source and line explanations](walkthrough.html#file=16&line=8215).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `remove_team_member` — L8268–L8299

[Read source and line explanations](walkthrough.html#file=16&line=8268).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WSManager` — L8301–L8319

[Read source and line explanations](walkthrough.html#file=16&line=8301).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WSManager.__init__` — L8302–L8303

[Read source and line explanations](walkthrough.html#file=16&line=8302).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WSManager.connect` — L8305–L8308

[Read source and line explanations](walkthrough.html#file=16&line=8305).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WSManager.disconnect` — L8310–L8311

[Read source and line explanations](walkthrough.html#file=16&line=8310).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `WSManager.send` — L8313–L8319

[Read source and line explanations](walkthrough.html#file=16&line=8313).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ws_endpoint` — L8324–L8384

[Read source and line explanations](walkthrough.html#file=16&line=8324).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_seed_upsert` — L8393–L8403

[Read source and line explanations](walkthrough.html#file=16&line=8393).

Source documentation (check against implementation):

> Upsert one seed doc keyed on `key`. `insert` fields apply only on first
> creation ($setOnInsert — existing rows are never overwritten); `update`
> fields are refreshed on every run ($set). Returns the stored document.

### `seed_demo` — L8406–L8737

[Read source and line explanations](walkthrough.html#file=16&line=8406).

Source documentation (check against implementation):

> Idempotent demo seed (rich data for every role):
>
> - one account per role incl. `admin` (password Password1!), org_admin
>   flags on the sponsor/site accounts
> - 4 organizations, 3 trials with visit templates
> - 8 patients across the trials, visit instances curated into a mix of
>   completed / upcoming / missed plus one overdue and visits due today
>   (so GET /api/tasks has items for pi@mtb.app / crc@mtb.app)
> - medications + 14 days of dose logs → ~93% adherence for patient@mtb.app
> - notifications of each kind, support tickets in each status, invitations
>   in all four lifecycle statuses, sample audit rows
> - admin-module fixtures: master-data submissions, terms versions, system
>   alerts, broadcast messages

### `seed_demo._sched` — L8540–L8544

[Read source and line explanations](walkthrough.html#file=16&line=8540).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seed_demo._curate` — L8546–L8549

[Read source and line explanations](walkthrough.html#file=16&line=8546).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seed_demo._dose` — L8586–L8592

[Read source and line explanations](walkthrough.html#file=16&line=8586).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seed_demo._seed_invite` — L8647–L8653

[Read source and line explanations](walkthrough.html#file=16&line=8647).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitPatch` — L8740–L8743

[Read source and line explanations](walkthrough.html#file=16&line=8740).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_visit` — L8746–L8763

[Read source and line explanations](walkthrough.html#file=16&line=8746).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitInstancePatch` — L8766–L8777

[Read source and line explanations](walkthrough.html#file=16&line=8766).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitInstanceTaskPatch` — L8779–L8780

[Read source and line explanations](walkthrough.html#file=16&line=8779).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitInstanceCommentIn` — L8782–L8783

[Read source and line explanations](walkthrough.html#file=16&line=8782).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_visit_instance` — L8786–L8849

[Read source and line explanations](walkthrough.html#file=16&line=8786).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_visit_instance_task` — L8853–L8913

[Read source and line explanations](walkthrough.html#file=16&line=8853).

Source documentation (check against implementation):

> Complete/reopen one stable task on one patient's visit instance.

### `add_visit_instance_comment` — L8917–L8956

[Read source and line explanations](walkthrough.html#file=16&line=8917).

Source documentation (check against implementation):

> Append an attributed, immutable clinical/admin visit comment.

### `ScheduleFlagIn` — L8959–L8960

[Read source and line explanations](walkthrough.html#file=16&line=8959).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_notify_trial_sponsors` — L8962–L8979

[Read source and line explanations](walkthrough.html#file=16&line=8962).

Source documentation (check against implementation):

> Notify the trial's sponsor users: its creator (if sponsor/cro) plus every
> sponsor-role user in the trial's sponsor organization.

### `_review_schedule` — L8981–L9005

[Read source and line explanations](walkthrough.html#file=16&line=8981).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `approve_schedule` — L9008–L9009

[Read source and line explanations](walkthrough.html#file=16&line=9008).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `flag_schedule` — L9012–L9013

[Read source and line explanations](walkthrough.html#file=16&line=9012).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_refresh_trial_schedule_status` — L9015–L9034

[Read source and line explanations](walkthrough.html#file=16&line=9015).

Source documentation (check against implementation):

> Keep the legacy trial-level status useful without losing per-site state.
>
> A rejected site makes the aggregate flagged. The schedule becomes approved
> only after every assigned site review is approved; otherwise it is pending.

### `my_tasks` — L9055–L9211

[Read source and line explanations](walkthrough.html#file=16&line=9055).

Source documentation (check against implementation):

> Action queue for site staff: overdue visit instances, visits due today,
> trials awaiting schedule review, and an unread-messages rollup. Computed
> from existing collections on every read — nothing is stored.

### `team_calendar` — L9217–L9297

[Read source and line explanations](walkthrough.html#file=16&line=9217).

Source documentation (check against implementation):

> Read-only site schedule: visit instances for the caller's OWN patients
> (pi_id / crc_id scoping — same rule as GET /patients) within a bounded
> date range, joined with privacy-safe patient identifiers (initials +
> subject label, never full names) and the trial's protocol / condition.
>
> ?from=&to= are inclusive YYYY-MM-DD bounds. Both omitted → the current
> UTC month; one omitted → a window extending from the other. The span is
> capped at 100 days so a single call can never sweep the whole collection.
> Read-only, so no audit row is written.

### `_clinical_dashboard_payload` — L9300–L9405

[Read source and line explanations](walkthrough.html#file=16&line=9300).

Source documentation (check against implementation):

> Normalized, relationship-scoped dashboard for PI and CRC users.

### `pi_dashboard` — L9409–L9410

[Read source and line explanations](walkthrough.html#file=16&line=9409).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `crc_dashboard` — L9414–L9415

[Read source and line explanations](walkthrough.html#file=16&line=9414).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReminderIn` — L9418–L9419

[Read source and line explanations](walkthrough.html#file=16&line=9418).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_reminders` — L9422–L9423

[Read source and line explanations](walkthrough.html#file=16&line=9422).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_reminder` — L9426–L9428

[Read source and line explanations](walkthrough.html#file=16&line=9426).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `update_reminder` — L9431–L9433

[Read source and line explanations](walkthrough.html#file=16&line=9431).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `delete_reminder` — L9436–L9437

[Read source and line explanations](walkthrough.html#file=16&line=9436).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleSlot` — L9444–L9446

[Read source and line explanations](walkthrough.html#file=16&line=9444).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MedicationIn` — L9448–L9456

[Read source and line explanations](walkthrough.html#file=16&line=9448).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DoseLogIn` — L9458–L9461

[Read source and line explanations](walkthrough.html#file=16&line=9458).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_parse_ymd` — L9463–L9471

[Read source and line explanations](walkthrough.html#file=16&line=9463).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_own_patient_record` — L9473–L9478

[Read source and line explanations](walkthrough.html#file=16&line=9473).

Source documentation (check against implementation):

> The `patients` row linked to the signed-in patient user.

### `_staff_scoped_patient` — L9480–L9491

[Read source and line explanations](walkthrough.html#file=16&line=9480).

Source documentation (check against implementation):

> pi/crc access check: the patient must be one of THEIR patients
> (same scoping GET /patients applies — pi_id / crc_id match).

### `_resolve_patient_scope` — L9493–L9497

[Read source and line explanations](walkthrough.html#file=16&line=9493).

Source documentation (check against implementation):

> Patient → own record (ignores ?patient_id=); pi/crc → their patient.

### `list_medications` — L9500–L9504

[Read source and line explanations](walkthrough.html#file=16&line=9500).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_medication` — L9507–L9535

[Read source and line explanations](walkthrough.html#file=16&line=9507).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_med_for_access` — L9537–L9549

[Read source and line explanations](walkthrough.html#file=16&line=9537).

Source documentation (check against implementation):

> Load a medication and enforce access: the patient it belongs to, or
> pi/crc staff who manage that patient.

### `log_dose` — L9552–L9573

[Read source and line explanations](walkthrough.html#file=16&line=9552).

Source documentation (check against implementation):

> Idempotent upsert keyed on (medication_id, date, time): re-logging the
> same slot replaces its status (same row id), never duplicates.

### `list_doses` — L9576–L9591

[Read source and line explanations](walkthrough.html#file=16&line=9576).

Source documentation (check against implementation):

> Dose history for one medication, optionally windowed by ?from=&to=
> (inclusive YYYY-MM-DD bounds — lexicographic compare is safe for ISO dates).

### `compute_adherence` — L9593–L9680

[Read source and line explanations](walkthrough.html#file=16&line=9593).

Source documentation (check against implementation):

> Adherence summary for one patient. THE formula (frontend contract):
>
> - Expected doses ("total"): for every ACTIVE medication, each calendar day
>   D with start_date <= D <= min(today, end_date) contributes
>   len(schedule) expected doses (meds with an empty schedule contribute 0;
>   a future start_date contributes 0 until it arrives). Days are UTC dates,
>   inclusive on both ends — a med started today with 2 slots expects 2 today.
> - "taken": dose_logs with status == 'taken' whose (date, time) fall on an
>   expected slot of that med (date within the active window AND time equal
>   to one of the med's schedule times). Upsert semantics guarantee at most
>   one log per (medication_id, date, time), so taken <= total always.
> - "rate": round(taken / total * 100) as an int; 0 when total == 0
>   (e.g. 13/14 -> 93).
> - "streak_days": consecutive fully-adherent days (every expected dose that
>   day logged 'taken') counting backwards from today; if today is not yet
>   complete the streak ends at yesterday instead (an in-progress day never
>   breaks the streak). Days with zero expected doses stop the streak.
>   Capped at 365.
> - "last7": exactly 7 entries, oldest first, ending today:
>   [{date: "YYYY-MM-DD", taken: int, total: int}].

### `compute_adherence.expected_on` — L9635–L9636

[Read source and line explanations](walkthrough.html#file=16&line=9635).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `compute_adherence.day_stats` — L9658–L9659

[Read source and line explanations](walkthrough.html#file=16&line=9658).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_adherence` — L9683–L9686

[Read source and line explanations](walkthrough.html#file=16&line=9683).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `InvitationIn` — L9691–L9699

[Read source and line explanations](walkthrough.html#file=16&line=9691).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `normalize_invite_code` — L9701–L9714

[Read source and line explanations](walkthrough.html#file=16&line=9701).

Source documentation (check against implementation):

> Canonical friendly code while retaining legacy UUID-token support.

### `new_invite_code` — L9716–L9718

[Read source and line explanations](walkthrough.html#file=16&line=9716).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_find_invitation_by_code` — L9720–L9726

[Read source and line explanations](walkthrough.html#file=16&line=9720).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_invite_link` — L9728–L9730

[Read source and line explanations](walkthrough.html#file=16&line=9728).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_can_manage_invitation` — L9732–L9735

[Read source and line explanations](walkthrough.html#file=16&line=9732).

Source documentation (check against implementation):

> The inviter — or anyone in the same organization — may manage it.

### `create_invitation` — L9738–L9782

[Read source and line explanations](walkthrough.html#file=16&line=9738).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_invitations` — L9785–L9789

[Read source and line explanations](walkthrough.html#file=16&line=9785).

Source documentation (check against implementation):

> Invitations sent by the caller or anyone in their organization.

### `_invitation_status` — L9791–L9797

[Read source and line explanations](walkthrough.html#file=16&line=9791).

Source documentation (check against implementation):

> Effective status: a pending invitation past its expiry reads as expired.

### `resolve_invitation` — L9800–L9825

[Read source and line explanations](walkthrough.html#file=16&line=9800).

Source documentation (check against implementation):

> Public: resolve an invite token for the accept screen.

### `InvitationAcceptIn` — L9827–L9831

[Read source and line explanations](walkthrough.html#file=16&line=9827).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `accept_invitation` — L9834–L9852

[Read source and line explanations](walkthrough.html#file=16&line=9834).

Source documentation (check against implementation):

> Validate an invite and return registration prefills without consuming it.

### `resend_invitation` — L9855–L9891

[Read source and line explanations](walkthrough.html#file=16&line=9855).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cancel_invitation` — L9894–L9906

[Read source and line explanations](walkthrough.html#file=16&line=9894).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ShareSiteIn` — L9909–L9916

[Read source and line explanations](walkthrough.html#file=16&line=9909).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ShareIn` — L9918–L9926

[Read source and line explanations](walkthrough.html#file=16&line=9918).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_validate_share_site` — L9928–L10014

[Read source and line explanations](walkthrough.html#file=16&line=9928).

Source documentation (check against implementation):

> Validate a selected site against live trial/network relationships.
>
> Client-provided names are display metadata only. A reviewer is accepted
> when they are a PI already attached to this trial, or work at a site in the
> sponsor organization's managed network. A platform-directory Site may also
> be selected; in that case the PI must be an active member of that exact
> Site organization. The relationship is persisted only after every selected
> site has passed validation.

### `_link_shared_site_to_network` — L10017–L10142

[Read source and line explanations](walkthrough.html#file=16&line=10017).

Source documentation (check against implementation):

> Attach a successfully selected Site to the sponsor's trial network.
>
> Existing network rows only gain the shared trial id. Directory discoveries
> create a governed network row keyed to the source Site organization so
> later shares and dashboard loads reuse the same relationship.

### `_schedule_visit_diff` — L10157–L10198

[Read source and line explanations](walkthrough.html#file=16&line=10157).

Source documentation (check against implementation):

> Return a field-level diff between two immutable visit snapshots.

### `_shared_document_metadata` — L10201–L10211

[Read source and line explanations](walkthrough.html#file=16&line=10201).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_share` — L10215–L10365

[Read source and line explanations](walkthrough.html#file=16&line=10215).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `share_pdf` — L10368–L10439

[Read source and line explanations](walkthrough.html#file=16&line=10368).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_prefs` — L10443–L10445

[Read source and line explanations](walkthrough.html#file=16&line=10443).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `patch_prefs` — L10448–L10460

[Read source and line explanations](walkthrough.html#file=16&line=10448).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `register_push` — L10464–L10468

[Read source and line explanations](walkthrough.html#file=16&line=10464).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_deidentify_audit_row` — L10470–L10501

[Read source and line explanations](walkthrough.html#file=16&line=10470).

Source documentation (check against implementation):

> Return a sponsor-safe copy of an audit row.
>
> A sponsor/CRO may audit its own trials but must NEVER see patient PII. We
> (1) drop any direct PII columns, (2) scrub the patient's identifiers out of
> the free-text `detail` and `user_name` (a patient-actor row carries the
> patient's own name), replacing them with a trial-level subject label, and
> (3) relabel a patient actor's name outright. Fail-safe: even with no patient
> row resolved, direct PII columns and a patient-actor name are still stripped.

### `_scope_audit_logs` — L10504–L10572

[Read source and line explanations](walkthrough.html#file=16&line=10504).

Source documentation (check against implementation):

> Fail-closed row-level scoping of audit entries for the calling role.
>
> patient  → own actions, or rows whose subject is their own patient record.
> pi/crc   → own action rows + rows referencing a patient/trial they own
>            (reuses _can_access_patient / _pi_owns_trial — never cross-site).
> sponsor/cro → rows for a trial in their org, DE-IDENTIFIED.
> admin    → unrestricted.
> any other role → nothing.

### `_scope_audit_logs._patient` — L10529–L10532

[Read source and line explanations](walkthrough.html#file=16&line=10529).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_scope_audit_logs._trial_ok` — L10534–L10544

[Read source and line explanations](walkthrough.html#file=16&line=10534).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `list_audit` — L10576–L10604

[Read source and line explanations](walkthrough.html#file=16&line=10576).

Source documentation (check against implementation):

> Role-scoped audit trail. Open to every authenticated role; the returned
> rows are scoped fail-closed per role (see _scope_audit_logs) so no caller
> can read another tenant's / site's activity, and a sponsor's view is
> de-identified. ?category= is an exact match; ?from=&to= are inclusive
> YYYY-MM-DD timestamp bounds (same parse + range guard as the calendar).

### `app_config` — L10647–L10653

[Read source and line explanations](walkthrough.html#file=16&line=10647).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `support_contact` — L10657–L10669

[Read source and line explanations](walkthrough.html#file=16&line=10657).

Source documentation (check against implementation):

> Public contact data used by pre-auth Help & Support navigation.

### `RoleReportIn` — L10678–L10683

[Read source and line explanations](walkthrough.html#file=16&line=10678).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_role_report_scope` — L10685–L10694

[Read source and line explanations](walkthrough.html#file=16&line=10685).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_report_date_in_range` — L10696–L10709

[Read source and line explanations](walkthrough.html#file=16&line=10696).

Source documentation (check against implementation):

> Compare stored ISO/date/datetime values against an optional inclusive range.

### `_role_report_rows` — L10711–L10789

[Read source and line explanations](walkthrough.html#file=16&line=10711).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `role_generate_report` — L10792–L10820

[Read source and line explanations](walkthrough.html#file=16&line=10792).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `role_report_options` — L10823–L10829

[Read source and line explanations](walkthrough.html#file=16&line=10823).

Source documentation (check against implementation):

> Trials the current user may include in a report filter.

### `role_recent_reports` — L10832–L10837

[Read source and line explanations](walkthrough.html#file=16&line=10832).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `role_download_report` — L10840–L10856

[Read source and line explanations](walkthrough.html#file=16&line=10840).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_faq` — L10860–L10865

[Read source and line explanations](walkthrough.html#file=16&line=10860).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `get_legal` — L10868–L10876

[Read source and line explanations](walkthrough.html#file=16&line=10868).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `accept_legal` — L10879–L10882

[Read source and line explanations](walkthrough.html#file=16&line=10879).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_caller_in_trial` — L10907–L10931

[Read source and line explanations](walkthrough.html#file=16&line=10907).

Source documentation (check against implementation):

> Whether the caller legitimately belongs to a trial (for trial-scoped file
> access). sponsor/cro: their org owns it. pi: _pi_owns_trial. crc: they are a
> listed CRC on an enrolled patient, created it, or share the trial's org.
> Fail-closed for everyone else.

### `_file_access_allowed` — L10934–L10950

[Read source and line explanations](walkthrough.html#file=16&line=10934).

Source documentation (check against implementation):

> Scope gate for GET /api/files/{id}. Owner and admin always pass; otherwise
> the caller must satisfy the file's scope. Fail-closed (unknown scope → deny).

### `upload_file` — L10954–L11015

[Read source and line explanations](walkthrough.html#file=16&line=10954).

Source documentation (check against implementation):

> Upload a file (any authenticated role). 10 MB cap; pdf/png/jpg/docx only
> (validated by extension AND content-type AND magic bytes). The blob is stored
> under a uuid key via the configured storage backend and indexed in `files`
> with a scope (default {type:'user', id: caller}). Returns
> {id, name, size, content_type, url} — url is the presigned link (S3) or the
> authenticated API GET path (local).

### `list_files` — L11019–L11040

[Read source and line explanations](walkthrough.html#file=16&line=11019).

Source documentation (check against implementation):

> List metadata for files in one authorized scope.
>
> The response never exposes storage keys. Trial lists use the same access
> check as downloads and return persistent document metadata for trial
> summary/version-history screens.

### `download_file` — L11044–L11064

[Read source and line explanations](walkthrough.html#file=16&line=11044).

Source documentation (check against implementation):

> Scope-checked download. Missing → 404; foreign scope → 403. Streams the
> bytes (local) or redirects to the presigned URL (S3).

### `delete_file` — L11068–L11082

[Read source and line explanations](walkthrough.html#file=16&line=11068).

Source documentation (check against implementation):

> Delete a file blob + its db doc. Owner or admin only (else 403).

### `root` — L11086–L11086

[Read source and line explanations](walkthrough.html#file=16&line=11086).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `current_uctsm_user` — L11094–L11099

[Read source and line explanations](walkthrough.html#file=16&line=11094).

Source documentation (check against implementation):

> Attach the authoritative organization UUID used for SQL tenant scoping.

### `_MongoGrantStore` — L11110–L11149

[Read source and line explanations](walkthrough.html#file=16&line=11110).

Source documentation (check against implementation):

> Capability grants persisted in ``uctsm_capability_grants`` (operational
> Mongo). Read fresh on every request, so a revocation is effective on the
> next call. Only an authorised administrator writes here (see the grants API
> below); nothing a client sends, and nothing in a JWT, is a grant.

### `_MongoGrantStore._row` — L11117–L11123

[Read source and line explanations](walkthrough.html#file=16&line=11117).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_MongoGrantStore.active_for_user` — L11125–L11129

[Read source and line explanations](walkthrough.html#file=16&line=11125).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_MongoGrantStore.list_for_organization` — L11131–L11134

[Read source and line explanations](walkthrough.html#file=16&line=11131).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_MongoGrantStore.add` — L11136–L11142

[Read source and line explanations](walkthrough.html#file=16&line=11136).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_MongoGrantStore.revoke` — L11144–L11149

[Read source and line explanations](walkthrough.html#file=16&line=11144).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_OperationalAccess` — L11152–L11167

[Read source and line explanations](walkthrough.html#file=16&line=11152).

Source documentation (check against implementation):

> The existing operational relationship rules, keyed by the operational ids
> that canonical trials/patients carry as ``external_*_id``.

### `_OperationalAccess.can_access_trial` — L11156–L11158

[Read source and line explanations](walkthrough.html#file=16&line=11156).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_OperationalAccess.can_access_patient` — L11160–L11162

[Read source and line explanations](walkthrough.html#file=16&line=11160).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_OperationalAccess.is_own_patient_record` — L11164–L11167

[Read source and line explanations](walkthrough.html#file=16&line=11164).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_record_authorization_denial` — L11170–L11182

[Read source and line explanations](walkthrough.html#file=16&line=11170).

Source documentation (check against implementation):

> Denials go to the operational audit log, outside any SQL/Mongo business
> transaction, so a rolled-back mutation cannot erase them. No PHI: canonical
> resource id, capability, reason code and policy version only.

### `_provision_pilot_operational` — L11197–L11228

[Read source and line explanations](walkthrough.html#file=16&line=11197).

Source documentation (check against implementation):

> Record the synthetic pilot's OPERATIONAL relationship documents.
>
> The production relationship provider (``_OperationalAccess``) decides staff
> access from the operational ``trials``/``patients`` collections, keyed by the
> ``external_*_id`` that canonical records carry. A native pilot patient has no
> operational record until this runs, so the seeding user would otherwise be
> unable to reach it. These are ordinary, clearly synthetic operational documents
> with the seeding user as creator (and as assigned PI/CRC by role) - the normal
> relationship rules then apply unchanged. ``$setOnInsert`` keeps a re-seed from
> re-granting an assignment that was later revoked.

### `_amendment_decider_still_authorised` — L11236–L11254

[Read source and line explanations](walkthrough.html#file=16&line=11236).

Source documentation (check against implementation):

> The deferred-adoption worker's re-check (P6-05): does the person who DECIDED still hold
> ``amendment.adopt`` for this patient, today? A missing, suspended or deactivated account, a
> revoked grant or a lost operational assignment answers no, and the decision is settled as
> BLOCKED instead of being applied.

### `_amendment_worker_interval` — L11257–L11265

[Read source and line explanations](walkthrough.html#file=16&line=11257).

Source documentation (check against implementation):

> Seconds between deferred-adoption passes; 0 turns the worker off. Off unless the
> MongoDB patient plane is configured - it never falls back to anything else.

### `_authz_principal` — L11268–L11271

[Read source and line explanations](walkthrough.html#file=16&line=11268).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_require_uctsm_capability` — L11274–L11290

[Read source and line explanations](walkthrough.html#file=16&line=11274).

Source documentation (check against implementation):

> Capability check for the UCTSM entry points that live in ``server.py``
> (identity review, grants). Denials are audited like the router's.

### `CapabilityGrantIn` — L11296–L11301

[Read source and line explanations](walkthrough.html#file=16&line=11296).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `my_uctsm_capabilities` — L11305–L11318

[Read source and line explanations](walkthrough.html#file=16&line=11305).

Source documentation (check against implementation):

> What the signed-in user may do, for the UI to show or hide actions. The
> server still enforces every route; this is informational.

### `list_capability_grants` — L11322–L11328

[Read source and line explanations](walkthrough.html#file=16&line=11322).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `add_capability_grant` — L11332–L11362

[Read source and line explanations](walkthrough.html#file=16&line=11332).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `revoke_capability_grant` — L11366–L11376

[Read source and line explanations](walkthrough.html#file=16&line=11366).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_identity_reviewer` — L11382–L11391

[Read source and line explanations](walkthrough.html#file=16&line=11382).

Source documentation (check against implementation):

> P2 identity review is the ``protocol_identity.review`` capability: an
> organization admin (documented compatibility default) or an explicit grant.
> The old ``user['permissions']`` field has no protected writer and is no
> longer an authorization source.

### `disable_legacy_schedule_authoring` — L11394–L11415

[Read source and line explanations](walkthrough.html#file=16&line=11394).

Source documentation (check against implementation):

> Fail closed after cutover instead of silently invoking visit-gap logic.

### `_readiness` — L11433–L11442

[Read source and line explanations](walkthrough.html#file=16&line=11433).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_ensure_indexes` — L11448–L11485

[Read source and line explanations](walkthrough.html#file=16&line=11448).

Source documentation (check against implementation):

> Create indexes in the background so a slow/unreachable DB never blocks startup.

### `_ensure_admin_seed` — L11487–L11502

[Read source and line explanations](walkthrough.html#file=16&line=11487).

Source documentation (check against implementation):

> Guarantee the platform-admin account exists (admins cannot self-register,
> so a fresh database would otherwise have no way into the admin portal).

### `_migrate_organization_ownership` — L11504–L11543

[Read source and line explanations](walkthrough.html#file=16&line=11504).

Source documentation (check against implementation):

> Give pre-existing organizations an owner without changing dashboards.

### `startup` — L11546–L11567

[Read source and line explanations](walkthrough.html#file=16&line=11546).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `shutdown` — L11570–L11570

[Read source and line explanations](walkthrough.html#file=16&line=11570).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 18. frontend/app/(app)/sponsor/add-trial.tsx

Connects protocol upload, extraction previews, trial saving and canonical draft building.

762 lines. [Open annotated source](walkthrough.html#file=17).

### `Source` — L43–L43

[Read source and line explanations](walkthrough.html#file=17&line=43).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrialStatus` — L44–L44

[Read source and line explanations](walkthrough.html#file=17&line=44).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Details` — L45–L55

[Read source and line explanations](walkthrough.html#file=17&line=45).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `canonicalPhase` — L111–L115

[Read source and line explanations](walkthrough.html#file=17&line=111).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `normalized` — L128–L142

[Read source and line explanations](walkthrough.html#file=17&line=128).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial` — L144–L630

[Read source and line explanations](walkthrough.html#file=17&line=144).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > openVisitSchedule` — L170–L173

[Read source and line explanations](walkthrough.html#file=17&line=170).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > buildFailureText` — L174–L179

[Read source and line explanations](walkthrough.html#file=17&line=174).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > retryBuild` — L180–L192

[Read source and line explanations](walkthrough.html#file=17&line=180).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > update` — L194–L195

[Read source and line explanations](walkthrough.html#file=17&line=194).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > update > callback to setDetails` — L195–L195

[Read source and line explanations](walkthrough.html#file=17&line=195).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > resetResolution` — L197–L205

[Read source and line explanations](walkthrough.html#file=17&line=197).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > lookup` — L207–L228

[Read source and line explanations](walkthrough.html#file=17&line=207).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > runExtract` — L230–L287

[Read source and line explanations](walkthrough.html#file=17&line=230).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > runExtract > callback to extractions.flatMap` — L262–L262

[Read source and line explanations](walkthrough.html#file=17&line=262).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > runExtract > callback to extractions.map` — L263–L263

[Read source and line explanations](walkthrough.html#file=17&line=263).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > extract` — L289–L297

[Read source and line explanations](walkthrough.html#file=17&line=289).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit` — L299–L384

[Read source and line explanations](walkthrough.html#file=17&line=299).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit > callback to previews.some` — L300–L300

[Read source and line explanations](walkthrough.html#file=17&line=300).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit > callback to details.indications.map` — L304–L304

[Read source and line explanations](walkthrough.html#file=17&line=304).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit > goToTrialWithNotice` — L343–L352

[Read source and line explanations](walkthrough.html#file=17&line=343).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit > goToTrialWithNotice > callback at line 348` — L348–L350

[Read source and line explanations](walkthrough.html#file=17&line=348).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > submit > callback to extractionIds.map` — L365–L366

[Read source and line explanations](walkthrough.html#file=17&line=365).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 402` — L402–L404

[Read source and line explanations](walkthrough.html#file=17&line=402).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 408` — L408–L408

[Read source and line explanations](walkthrough.html#file=17&line=408).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 408 > callback to setShowTrialDetails` — L408–L408

[Read source and line explanations](walkthrough.html#file=17&line=408).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 438` — L438–L442

[Read source and line explanations](walkthrough.html#file=17&line=438).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 480` — L480–L480

[Read source and line explanations](walkthrough.html#file=17&line=480).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 481` — L481–L481

[Read source and line explanations](walkthrough.html#file=17&line=481).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback to PHASES.map` — L485–L487

[Read source and line explanations](walkthrough.html#file=17&line=485).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback to PHASES.map > callback at line 486` — L486–L486

[Read source and line explanations](walkthrough.html#file=17&line=486).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 494` — L494–L494

[Read source and line explanations](walkthrough.html#file=17&line=494).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 494 > callback to value.split(",").map` — L494–L494

[Read source and line explanations](walkthrough.html#file=17&line=494).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 499` — L499–L499

[Read source and line explanations](walkthrough.html#file=17&line=499).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 500` — L500–L500

[Read source and line explanations](walkthrough.html#file=17&line=500).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 503` — L503–L503

[Read source and line explanations](walkthrough.html#file=17&line=503).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 506` — L506–L506

[Read source and line explanations](walkthrough.html#file=17&line=506).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback to STATUSES.map` — L512–L514

[Read source and line explanations](walkthrough.html#file=17&line=512).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback to STATUSES.map > callback at line 513` — L513–L513

[Read source and line explanations](walkthrough.html#file=17&line=513).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 523` — L523–L525

[Read source and line explanations](walkthrough.html#file=17&line=523).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 543` — L543–L543

[Read source and line explanations](walkthrough.html#file=17&line=543).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback to CREATE_TRIAL_RESPONSIBILITIES.map` — L561–L568

[Read source and line explanations](walkthrough.html#file=17&line=561).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 579` — L579–L579

[Read source and line explanations](walkthrough.html#file=17&line=579).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 582` — L582–L582

[Read source and line explanations](walkthrough.html#file=17&line=582).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 594` — L594–L594

[Read source and line explanations](walkthrough.html#file=17&line=594).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddTrial > callback at line 609` — L609–L609

[Read source and line explanations](walkthrough.html#file=17&line=609).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Label` — L632–L634

[Read source and line explanations](walkthrough.html#file=17&line=632).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `FieldProps` — L636–L646

[Read source and line explanations](walkthrough.html#file=17&line=636).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Field` — L648–L676

[Read source and line explanations](walkthrough.html#file=17&line=648).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `Choice` — L678–L698

[Read source and line explanations](walkthrough.html#file=17&line=678).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 19. frontend/src/features/uctsm/api.ts

Defines typed request helpers for schedule review and patient operations.

966 lines. [Open annotated source](walkthrough.html#file=18).

### `DemoWorkspace` — L10–L14

[Read source and line explanations](walkthrough.html#file=18&line=10).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seedDemoWorkspace` — L16–L19

[Read source and line explanations](walkthrough.html#file=18&line=16).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getUniversalSchedule` — L21–L24

[Read source and line explanations](walkthrough.html#file=18&line=21).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getScheduleProjection` — L26–L29

[Read source and line explanations](walkthrough.html#file=18&line=26).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getApprovedSchedules` — L31–L34

[Read source and line explanations](walkthrough.html#file=18&line=31).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `validateSchedule` — L36–L41

[Read source and line explanations](walkthrough.html#file=18&line=36).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `recordFieldDecision` — L43–L58

[Read source and line explanations](walkthrough.html#file=18&line=43).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `bulkConfirmSchedule` — L66–L72

[Read source and line explanations](walkthrough.html#file=18&line=66).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `correctScheduleEvent` — L74–L85

[Read source and line explanations](walkthrough.html#file=18&line=74).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `QualifierRow` — L88–L106

[Read source and line explanations](walkthrough.html#file=18&line=88).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getScheduleQualifiers` — L108–L113

[Read source and line explanations](walkthrough.html#file=18&line=108).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `resolveScheduleQualifier` — L123–L143

[Read source and line explanations](walkthrough.html#file=18&line=123).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `submitScheduleReview` — L145–L148

[Read source and line explanations](walkthrough.html#file=18&line=145).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PendingScheduleReview` — L150–L159

[Read source and line explanations](walkthrough.html#file=18&line=150).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPendingScheduleReviews` — L165–L169

[Read source and line explanations](walkthrough.html#file=18&line=165).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `decideSchedule` — L171–L180

[Read source and line explanations](walkthrough.html#file=18&line=171).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `evaluatePatientSchedule` — L182–L189

[Read source and line explanations](walkthrough.html#file=18&line=182).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `recordPatientAnchor` — L199–L212

[Read source and line explanations](walkthrough.html#file=18&line=199).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewAnchorImpact` — L214–L225

[Read source and line explanations](walkthrough.html#file=18&line=214).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewPatientStateImpact` — L227–L248

[Read source and line explanations](walkthrough.html#file=18&line=227).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientSchedule` — L250–L253

[Read source and line explanations](walkthrough.html#file=18&line=250).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientConditions` — L255–L259

[Read source and line explanations](walkthrough.html#file=18&line=255).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScheduleImpact` — L261–L276

[Read source and line explanations](walkthrough.html#file=18&line=261).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewConditionImpact` — L282–L297

[Read source and line explanations](walkthrough.html#file=18&line=282).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `confirmScheduleImpact` — L299–L303

[Read source and line explanations](walkthrough.html#file=18&line=299).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cancelScheduleImpact` — L305–L309

[Read source and line explanations](walkthrough.html#file=18&line=305).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `recordPatientActivity` — L311–L324

[Read source and line explanations](walkthrough.html#file=18&line=311).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientProjection` — L326–L352

[Read source and line explanations](walkthrough.html#file=18&line=326).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientScheduleProjection` — L354–L358

[Read source and line explanations](walkthrough.html#file=18&line=354).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnchorStatusResponse` — L362–L367

[Read source and line explanations](walkthrough.html#file=18&line=362).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientAnchorStatuses` — L369–L373

[Read source and line explanations](walkthrough.html#file=18&line=369).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `VisitDeviation` — L375–L387

[Read source and line explanations](walkthrough.html#file=18&line=375).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DeviationReportResponse` — L389–L397

[Read source and line explanations](walkthrough.html#file=18&line=389).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientDeviations` — L399–L404

[Read source and line explanations](walkthrough.html#file=18&line=399).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DashboardAction` — L406–L415

[Read source and line explanations](walkthrough.html#file=18&line=406).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getDashboardActions` — L417–L422

[Read source and line explanations](walkthrough.html#file=18&line=417).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `createUnscheduledVisit` — L428–L435

[Read source and line explanations](walkthrough.html#file=18&line=428).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getEnrolmentOptions` — L437–L445

[Read source and line explanations](walkthrough.html#file=18&line=437).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewAssignmentImpact` — L448–L460

[Read source and line explanations](walkthrough.html#file=18&line=448).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewProcessTransition` — L463–L468

[Read source and line explanations](walkthrough.html#file=18&line=463).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `recordOccurrenceFact` — L471–L475

[Read source and line explanations](walkthrough.html#file=18&line=471).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `compareScheduleVersions` — L477–L481

[Read source and line explanations](walkthrough.html#file=18&line=477).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `queueScheduleExtraction` — L483–L494

[Read source and line explanations](walkthrough.html#file=18&line=483).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getExtractionRun` — L496–L502

[Read source and line explanations](walkthrough.html#file=18&line=496).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ParityDifference` — L506–L513

[Read source and line explanations](walkthrough.html#file=18&line=506).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ParityCheckResponse` — L515–L523

[Read source and line explanations](walkthrough.html#file=18&line=515).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `runParityCheck` — L530–L539

[Read source and line explanations](walkthrough.html#file=18&line=530).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReadModePatientRun` — L541–L549

[Read source and line explanations](walkthrough.html#file=18&line=541).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReadModeStatus` — L551–L565

[Read source and line explanations](walkthrough.html#file=18&line=551).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getReadMode` — L567–L570

[Read source and line explanations](walkthrough.html#file=18&line=567).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `setReadMode` — L577–L583

[Read source and line explanations](walkthrough.html#file=18&line=577).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `acceptParityRun` — L591–L597

[Read source and line explanations](walkthrough.html#file=18&line=591).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CanonicalScheduleRow` — L604–L616

[Read source and line explanations](walkthrough.html#file=18&line=604).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CanonicalScheduleBuild` — L618–L630

[Read source and line explanations](walkthrough.html#file=18&line=618).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `buildCanonicalSchedule` — L633–L636

[Read source and line explanations](walkthrough.html#file=18&line=633).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `approveReviewedTable` — L638–L641

[Read source and line explanations](walkthrough.html#file=18&line=638).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `resolveExtractionIssue` — L643–L646

[Read source and line explanations](walkthrough.html#file=18&line=643).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CanonicalScheduleIndex` — L648–L654

[Read source and line explanations](walkthrough.html#file=18&line=648).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getCanonicalSchedules` — L657–L660

[Read source and line explanations](walkthrough.html#file=18&line=657).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getReviewView` — L665–L669

[Read source and line explanations](walkthrough.html#file=18&line=665).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PlaneReview` — L671–L672

[Read source and line explanations](walkthrough.html#file=18&line=671).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `planeReviewPath` — L673–L674

[Read source and line explanations](walkthrough.html#file=18&line=673).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPlaneReview` — L675–L677

[Read source and line explanations](walkthrough.html#file=18&line=675).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `approvePlaneTable` — L678–L681

[Read source and line explanations](walkthrough.html#file=18&line=678).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewWriteResult` — L683–L688

[Read source and line explanations](walkthrough.html#file=18&line=683).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `editScheduleVisit` — L690–L694

[Read source and line explanations](walkthrough.html#file=18&line=690).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `addScheduleVisit` — L696–L699

[Read source and line explanations](walkthrough.html#file=18&line=696).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `resolveReviewQuestion` — L701–L705

[Read source and line explanations](walkthrough.html#file=18&line=701).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `approveScheduleVersion` — L708–L713

[Read source and line explanations](walkthrough.html#file=18&line=708).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `exportScheduleVersion` — L716–L722

[Read source and line explanations](walkthrough.html#file=18&line=716).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `exportScheduleVersion > callback at line 718` — L718–L718

[Read source and line explanations](walkthrough.html#file=18&line=718).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `attachHolidayCalendar` — L725–L733

[Read source and line explanations](walkthrough.html#file=18&line=725).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getMyCapabilities` — L736–L742

[Read source and line explanations](walkthrough.html#file=18&line=736).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `plane` — L745–L746

[Read source and line explanations](walkthrough.html#file=18&line=745).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPlaneCalendar` — L749–L754

[Read source and line explanations](walkthrough.html#file=18&line=749).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `previewPatientCommand` — L756–L759

[Read source and line explanations](walkthrough.html#file=18&line=756).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `confirmPatientCommand` — L761–L768

[Read source and line explanations](walkthrough.html#file=18&line=761).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cancelPatientCommand` — L770–L776

[Read source and line explanations](walkthrough.html#file=18&line=770).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `listPatientCommands` — L778–L781

[Read source and line explanations](walkthrough.html#file=18&line=778).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reviewScheduleTracks` — L784–L788

[Read source and line explanations](walkthrough.html#file=18&line=784).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `listPlanePatients` — L791–L797

[Read source and line explanations](walkthrough.html#file=18&line=791).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seedPilot` — L800–L806

[Read source and line explanations](walkthrough.html#file=18&line=800).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `amendmentsOfTrial` — L809–L809

[Read source and line explanations](walkthrough.html#file=18&line=809).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getRuleHandling` — L810–L813

[Read source and line explanations](walkthrough.html#file=18&line=810).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `declareRuleHandling` — L814–L816

[Read source and line explanations](walkthrough.html#file=18&line=814).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `approveRuleHandling` — L817–L819

[Read source and line explanations](walkthrough.html#file=18&line=817).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `amendmentsOfPatient` — L820–L821

[Read source and line explanations](walkthrough.html#file=18&line=820).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getTrialAmendments` — L824–L834

[Read source and line explanations](walkthrough.html#file=18&line=824).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `prepareAmendmentPair` — L837–L842

[Read source and line explanations](walkthrough.html#file=18&line=837).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getAmendmentPair` — L844–L848

[Read source and line explanations](walkthrough.html#file=18&line=844).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reviewAmendmentMapping` — L851–L857

[Read source and line explanations](walkthrough.html#file=18&line=851).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getPatientAmendments` — L859–L862

[Read source and line explanations](walkthrough.html#file=18&line=859).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `simulateAmendment` — L864–L868

[Read source and line explanations](walkthrough.html#file=18&line=864).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `confirmAmendment` — L870–L878

[Read source and line explanations](walkthrough.html#file=18&line=870).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `executeAmendmentDecision` — L880–L884

[Read source and line explanations](walkthrough.html#file=18&line=880).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cancelAmendmentDecision` — L886–L892

[Read source and line explanations](walkthrough.html#file=18&line=886).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `seedPilotAmendment` — L895–L901

[Read source and line explanations](walkthrough.html#file=18&line=895).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `versionOfTrial` — L905–L906

[Read source and line explanations](walkthrough.html#file=18&line=905).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getConflicts` — L907–L909

[Read source and line explanations](walkthrough.html#file=18&line=907).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `resolveConflict` — L910–L912

[Read source and line explanations](walkthrough.html#file=18&line=910).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getRequiredInputs` — L916–L918

[Read source and line explanations](walkthrough.html#file=18&line=916).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `addRequiredInput` — L919–L921

[Read source and line explanations](walkthrough.html#file=18&line=919).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `removeRequiredInput` — L922–L924

[Read source and line explanations](walkthrough.html#file=18&line=922).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `acknowledgeRequiredInputs` — L925–L927

[Read source and line explanations](walkthrough.html#file=18&line=925).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getEnrolmentInputs` — L928–L931

[Read source and line explanations](walkthrough.html#file=18&line=928).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `listDeadLetters` — L936–L939

[Read source and line explanations](walkthrough.html#file=18&line=936).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `replayDeadLetter` — L940–L942

[Read source and line explanations](walkthrough.html#file=18&line=940).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `createCohort` — L943–L945

[Read source and line explanations](walkthrough.html#file=18&line=943).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getCohortReadiness` — L946–L948

[Read source and line explanations](walkthrough.html#file=18&line=946).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `promoteCohort` — L949–L951

[Read source and line explanations](walkthrough.html#file=18&line=949).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `rollbackCohort` — L952–L954

[Read source and line explanations](walkthrough.html#file=18&line=952).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getNotificationPreferences` — L955–L959

[Read source and line explanations](walkthrough.html#file=18&line=955).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `putNotificationPreferences` — L960–L963

[Read source and line explanations](walkthrough.html#file=18&line=960).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `getScheduleReport` — L964–L966

[Read source and line explanations](walkthrough.html#file=18&line=964).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 20. backend/tests/test_extraction_reader.py

Uses scripted clients to check reader behavior without a live model.

375 lines. [Open annotated source](walkthrough.html#file=19).

### `pages` — L42–L43

[Read source and line explanations](walkthrough.html#file=19&line=42).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `fact` — L59–L60

[Read source and line explanations](walkthrough.html#file=19&line=59).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScriptedClient` — L70–L89

[Read source and line explanations](walkthrough.html#file=19&line=70).

Source documentation (check against implementation):

> Answers the metadata question from ``metadata`` and each facts question
> from ``facts`` (a list consumed in order, or a callable of the prompt).

### `ScriptedClient.__init__` — L74–L77

[Read source and line explanations](walkthrough.html#file=19&line=74).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ScriptedClient.complete` — L79–L89

[Read source and line explanations](walkthrough.html#file=19&line=79).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_page_selection_reads_tables_and_the_narrative_sections_timing_lives_in` — L94–L99

[Read source and line explanations](walkthrough.html#file=19&line=94).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_image_only_pages_are_kept_for_a_reader_that_can_see_the_page_image` — L102–L105

[Read source and line explanations](walkthrough.html#file=19&line=102).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_page_selection_is_bounded` — L108–L111

[Read source and line explanations](walkthrough.html#file=19&line=108).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_quote_matching_tolerates_pdf_noise_but_not_invented_words` — L114–L119

[Read source and line explanations](walkthrough.html#file=19&line=114).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_the_prompt_forbids_dates_guesses_and_invented_windows` — L124–L127

[Read source and line explanations](walkthrough.html#file=19&line=124).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_read_document_makes_one_metadata_call_and_one_call_per_batch` — L130–L144

[Read source and line explanations](walkthrough.html#file=19&line=130).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_large_documents_are_read_in_bounded_batches` — L147–L153

[Read source and line explanations](walkthrough.html#file=19&line=147).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_malformed_reply_is_retried_once_then_used` — L156–L164

[Read source and line explanations](walkthrough.html#file=19&line=156).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_pages_that_stay_unreadable_become_a_blocking_issue_not_an_empty_answer` — L167–L175

[Read source and line explanations](walkthrough.html#file=19&line=167).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_when_no_batch_can_be_read_extraction_fails_loudly` — L178–L182

[Read source and line explanations](walkthrough.html#file=19&line=178).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_transport_failure_is_not_retried_or_hidden` — L185–L190

[Read source and line explanations](walkthrough.html#file=19&line=185).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_an_invalid_fact_is_reported_and_the_rest_are_kept` — L193–L199

[Read source and line explanations](walkthrough.html#file=19&line=193).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_quote_not_on_its_cited_page_is_flagged` — L202–L209

[Read source and line explanations](walkthrough.html#file=19&line=202).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_fact_ids_are_made_unique_and_conflict_links_follow_them` — L212–L220

[Read source and line explanations](walkthrough.html#file=19&line=212).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_an_unreadable_overview_degrades_to_unstated_values_with_an_issue` — L223–L228

[Read source and line explanations](walkthrough.html#file=19&line=223).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_client_that_takes_document_packets_gets_only_its_pages_attached` — L231–L243

[Read source and line explanations](walkthrough.html#file=19&line=231).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_client_that_takes_document_packets_gets_only_its_pages_attached.PacketClient` — L234–L237

[Read source and line explanations](walkthrough.html#file=19&line=234).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_client_that_takes_document_packets_gets_only_its_pages_attached.PacketClient.for_document` — L235–L237

[Read source and line explanations](walkthrough.html#file=19&line=235).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_trial_metadata_alone_is_one_call` — L246–L251

[Read source and line explanations](walkthrough.html#file=19&line=246).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_engine_is_available_only_with_an_explicit_provider_and_its_key` — L256–L271

[Read source and line explanations](walkthrough.html#file=19&line=256).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_extract_schedules_runs_reader_then_compiler` — L274–L283

[Read source and line explanations](walkthrough.html#file=19&line=274).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_the_facts_prompt_asks_for_planned_anchors_cycle_modes_occurrences_and_one_phased_plan` — L288–L295

[Read source and line explanations](walkthrough.html#file=19&line=288).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_new_fact_fields_round_trip_from_a_reply_through_the_compiler` — L298–L327

[Read source and line explanations](walkthrough.html#file=19&line=298).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_the_product_default_reads_every_page_not_only_keyword_matches` — L336–L344

[Read source and line explanations](walkthrough.html#file=19&line=336).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_scrambled_font_text_is_treated_as_an_image_page` — L347–L358

[Read source and line explanations](walkthrough.html#file=19&line=347).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_amendment_history_pages_are_never_a_schedule_source` — L361–L375

[Read source and line explanations](walkthrough.html#file=19&line=361).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 21. backend/tests/test_extraction_compiler.py

Demonstrates facts-to-schedule mappings and unresolved cases with assertions.

1168 lines. [Open annotated source](walkthrough.html#file=20).

### `cite` — L42–L43

[Read source and line explanations](walkthrough.html#file=20&line=42).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `fact` — L46–L47

[Read source and line explanations](walkthrough.html#file=20&line=46).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `compile_facts` — L58–L65

[Read source and line explanations](walkthrough.html#file=20&line=58).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `event` — L68–L69

[Read source and line explanations](walkthrough.html#file=20&line=68).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `codes` — L72–L73

[Read source and line explanations](walkthrough.html#file=20&line=72).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `reviewed` — L76–L92

[Read source and line explanations](walkthrough.html#file=20&line=76).

Source documentation (check against implementation):

> The schedule after a reviewer EXPLICITLY states each recurrence basis the compiler could
> only propose (``basis_status == UNCONFIRMED``), re-validated with the current rules.
>
> The protocol text rarely says what each repeat counts from, so compilation never states it
> (P4-02): an unconfirmed basis blocks approval until a person does. This models that explicit
> edit; it is not a default the compiler or the validator may apply.

### `reviewed_codes` — L95–L96

[Read source and line explanations](walkthrough.html#file=20&line=95).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `issues_for` — L102–L103

[Read source and line explanations](walkthrough.html#file=20&line=102).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_pre_treatment_labs_within_28_days_before_registration_are_within_before_that_anchor` — L108–L119

[Read source and line explanations](walkthrough.html#file=20&line=108).

Source documentation (check against implementation):

> A8 / Change 8 compile rule: never re-pointed at first dose.

### `test_pet_scan_12_to_16_weeks_after_end_of_therapy_is_a_range_from_the_milestone_anchor` — L122–L135

[Read source and line explanations](walkthrough.html#file=20&line=122).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_offset_with_a_stated_window_keeps_the_window_as_its_own_rule` — L138–L147

[Read source and line explanations](walkthrough.html#file=20&line=138).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_stated_late_only_window_is_kept_one_sided` — L150–L157

[Read source and line explanations](walkthrough.html#file=20&line=150).

Source documentation (check against implementation):

> B3: 'may be up to 3 days late but not early' states both sides (-0/+3).

### `test_a_window_on_a_range_is_not_applied_because_it_has_no_centre` — L160–L167

[Read source and line explanations](walkthrough.html#file=20&line=160).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_missing_window_stays_empty_and_is_never_defaulted` — L170–L178

[Read source and line explanations](walkthrough.html#file=20&line=170).

Source documentation (check against implementation):

> B4: no window stated -> no window, no issue pretending one exists.

### `test_a_one_sided_window_is_kept_as_stated_but_not_turned_into_a_guessed_window` — L181–L189

[Read source and line explanations](walkthrough.html#file=20&line=181).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_protocol_day_under_clinical_day_counting_uses_the_stated_day_1_anchor` — L192–L197

[Read source and line explanations](walkthrough.html#file=20&line=192).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_day_0_under_clinical_day_counting_is_refused_not_shifted` — L200–L204

[Read source and line explanations](walkthrough.html#file=20&line=200).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_elapsed_day_counting_turns_day_n_into_an_offset` — L207–L215

[Read source and line explanations](walkthrough.html#file=20&line=207).

Source documentation (check against implementation):

> A3: 'Day 0 is the day of first dose; Day 7 follow-up'.

### `test_no_day_1_statement_leaves_day_numbering_unset_and_the_validator_blocks` — L218–L225

[Read source and line explanations](walkthrough.html#file=20&line=218).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_stated_anchor_but_unstated_counting_convention_still_blocks` — L228–L233

[Read source and line explanations](walkthrough.html#file=20&line=228).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_absolute_no_later_than_and_approximate_map_to_their_rules` — L236–L247

[Read source and line explanations](walkthrough.html#file=20&line=236).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_an_anchor_named_but_never_defined_becomes_a_recorded_anchor_with_a_warning` — L250–L256

[Read source and line explanations](walkthrough.html#file=20&line=250).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_an_unmappable_statement_is_unresolved_with_its_source` — L259–L267

[Read source and line explanations](walkthrough.html#file=20&line=259).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_visit_counted_from_another_visit_depends_on_it_and_needs_a_stated_mode` — L270–L284

[Read source and line explanations](walkthrough.html#file=20&line=270).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_on_demand_visit_is_available_not_dated` — L287–L293

[Read source and line explanations](walkthrough.html#file=20&line=287).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_conflicting_qol_statements_are_recorded_as_conflicting_and_block_approval` — L298–L313

[Read source and line explanations](walkthrough.html#file=20&line=298).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_two_unflagged_readings_of_one_visit_that_disagree_are_also_conflicting` — L316–L321

[Read source and line explanations](walkthrough.html#file=20&line=316).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_two_readings_that_agree_merge_into_one_visit_with_both_citations` — L324–L332

[Read source and line explanations](walkthrough.html#file=20&line=324).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_counted_repeat_and_open_ended_repeat` — L337–L348

[Read source and line explanations](walkthrough.html#file=20&line=337).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_repeat_whose_first_occurrence_is_not_stated_is_unresolved` — L351–L356

[Read source and line explanations](walkthrough.html#file=20&line=351).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_repeat_for_a_duration_needs_a_stated_inclusive_boundary` — L359–L373

[Read source and line explanations](walkthrough.html#file=20&line=359).

Source documentation (check against implementation):

> E4: 'every 3 months for 2 years' - whether the 24-month visit counts is never assumed.

### `test_repeat_until_a_condition_is_stopped_by_that_condition` — L376–L389

[Read source and line explanations](walkthrough.html#file=20&line=376).

Source documentation (check against implementation):

> E3: 'every 12 weeks until progression'.

### `e9_fact` — L401–L404

[Read source and line explanations](walkthrough.html#file=20&line=401).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_phased_follow_up_compiles_to_one_visit_with_a_segmented_plan` — L407–L424

[Read source and line explanations](walkthrough.html#file=20&line=407).

Source documentation (check against implementation):

> E9 / Change 5: one follow-up plan, not three chained visits.

### `test_phased_boundary_ownership_stated_by_the_next_segment_is_used` — L427–L437

[Read source and line explanations](walkthrough.html#file=20&line=427).

Source documentation (check against implementation):

> '...then every 6 months starting at year 2' says the 24-month visit opens segment 2.

### `test_phased_segment_without_a_stated_boundary_is_left_unset_and_blocks` — L440–L450

[Read source and line explanations](walkthrough.html#file=20&line=440).

Source documentation (check against implementation):

> E4: the boundary owner is never assumed; the validator blocks the plan.

### `test_phased_plan_with_no_stated_end_is_not_defaulted_to_open_ended` — L453–L458

[Read source and line explanations](walkthrough.html#file=20&line=453).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_phased_segments_that_disagree_about_the_boundary_are_unresolved` — L461–L467

[Read source and line explanations](walkthrough.html#file=20&line=461).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cycle_day` — L476–L483

[Read source and line explanations](walkthrough.html#file=20&line=476).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cycle_block` — L486–L487

[Read source and line explanations](walkthrough.html#file=20&line=486).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `cycle_dates` — L490–L498

[Read source and line explanations](walkthrough.html#file=20&line=490).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_every_cycle_day_compiles_to_a_cycle_plan_and_cycle_day_timing` — L501–L518

[Read source and line explanations](walkthrough.html#file=20&line=501).

Source documentation (check against implementation):

> F4: 'Days 1 and 8 of each 21-day cycle' - one plan, dated per cycle.

### `test_f1_nominal_cycles_keep_the_grid_when_cycle_2_is_late` — L521–L527

[Read source and line explanations](walkthrough.html#file=20&line=521).

Source documentation (check against implementation):

> F1: C1D1 05-Oct-2026, 21-day cycles: C2 26-Oct, C3 16-Nov; C2 given 02-Nov moves nothing.

### `test_f2_actual_previous_cycle_start_moves_the_next_cycle` — L530–L548

[Read source and line explanations](walkthrough.html#file=20&line=530).

Source documentation (check against implementation):

> F2: 'Each cycle starts 21 days after Day 1 of the previous cycle.' C2 given
> 02-Nov -> C3 23-Nov-2026; C4 waits for C3's actual start.

### `test_the_older_actual_previous_event_wording_means_the_same_for_cycles` — L551–L555

[Read source and line explanations](walkthrough.html#file=20&line=551).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_cycle_dependency_mode_stated_on_the_cycle_fact_is_used` — L558–L563

[Read source and line explanations](walkthrough.html#file=20&line=558).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_unstated_cycle_mode_is_unclear_and_blocks` — L566–L572

[Read source and line explanations](walkthrough.html#file=20&line=566).

Source documentation (check against implementation):

> F3: whether cycles follow the plan or the actual previous cycle is never assumed.

### `test_cycle_length_or_count_unstated_stays_unresolved` — L575–L586

[Read source and line explanations](walkthrough.html#file=20&line=575).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_cycles_until_progression_are_stopped_by_the_condition` — L589–L598

[Read source and line explanations](walkthrough.html#file=20&line=589).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_specific_cycle_day_is_dated_from_the_cycle_plan` — L601–L612

[Read source and line explanations](walkthrough.html#file=20&line=601).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_specific_cycle_day_without_a_plan_is_an_offset_only_on_a_stated_nominal_grid` — L615–L624

[Read source and line explanations](walkthrough.html#file=20&line=615).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `mri` — L629–L631

[Read source and line explanations](walkthrough.html#file=20&line=629).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `activity_statuses` — L634–L643

[Read source and line explanations](walkthrough.html#file=20&line=634).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_mri_every_second_cycle_starting_cycle_2_becomes_an_occurrence_condition` — L646–L657

[Read source and line explanations](walkthrough.html#file=20&line=646).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_pk_cycle_1_only_and_even_cycles` — L660–L666

[Read source and line explanations](walkthrough.html#file=20&line=660).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_an_ambiguous_every_second_cycle_stays_an_unresolved_qualifier` — L669–L677

[Read source and line explanations](walkthrough.html#file=20&line=669).

Source documentation (check against implementation):

> No first cycle stated: C1, C3, C5 or C2, C4, C6 - never guessed.

### `test_an_occurrence_pattern_on_open_ended_cycles_or_a_non_cycle_visit_stays_unresolved` — L680–L690

[Read source and line explanations](walkthrough.html#file=20&line=680).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_note_without_a_structured_selector_stays_an_unresolved_qualifier` — L693–L697

[Read source and line explanations](walkthrough.html#file=20&line=693).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_toxicity_hold_and_resume_become_pause_and_resume_block_actions` — L707–L722

[Read source and line explanations](walkthrough.html#file=20&line=707).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_hold_with_no_stated_resume_rule_leaves_resume_unclear` — L725–L731

[Read source and line explanations](walkthrough.html#file=20&line=725).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_discontinuation_stops_the_treatment_block_and_adds_follow_up` — L734–L754

[Read source and line explanations](walkthrough.html#file=20&line=734).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_repeat_until_resolved_needs_its_interval_and_a_checkable_resolution` — L757–L774

[Read source and line explanations](walkthrough.html#file=20&line=757).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_trial_level_fatal_toxicity_rule_never_becomes_a_patient_action` — L777–L802

[Read source and line explanations](walkthrough.html#file=20&line=777).

Source documentation (check against implementation):

> I8: 'Fatal toxicity: accrual is suspended and the DSMC reviews.'

### `test_a_trial_rule_naming_nothing_patient_level_is_recorded_without_blocking` — L805–L815

[Read source and line explanations](walkthrough.html#file=20&line=805).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_condition_whose_effect_is_not_stated_is_reported` — L818–L822

[Read source and line explanations](walkthrough.html#file=20&line=818).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_arm_and_attribute_rules_and_activity_links` — L827–L841

[Read source and line explanations](walkthrough.html#file=20&line=827).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_every_compiled_entity_carries_claim_level_evidence` — L844–L852

[Read source and line explanations](walkthrough.html#file=20&line=844).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_reader_issues_survive_revalidation` — L855–L864

[Read source and line explanations](walkthrough.html#file=20&line=855).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_compilation_is_deterministic` — L867–L876

[Read source and line explanations](walkthrough.html#file=20&line=867).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_compilation_is_deterministic.shape` — L871–L874

[Read source and line explanations](walkthrough.html#file=20&line=871).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_independent_schedule_options_compile_separately` — L879–L893

[Read source and line explanations](walkthrough.html#file=20&line=879).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_single_schedule_protocol_has_no_option` — L896–L901

[Read source and line explanations](walkthrough.html#file=20&line=896).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `journey_facts` — L910–L927

[Read source and line explanations](walkthrough.html#file=20&line=910).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `session` — L931–L935

[Read source and line explanations](walkthrough.html#file=20&line=931).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_compiled_facts_approve_and_date_a_real_patient` — L938–L990

[Read source and line explanations](walkthrough.html#file=20&line=938).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_phased_follow_up_dates_match_the_spec_example` — L993–L1014

[Read source and line explanations](walkthrough.html#file=20&line=993).

Source documentation (check against implementation):

> E9 worked example: end of therapy 07-Dec-2026, one plan, each boundary once.

### `test_a_window_on_a_repeating_visit_is_applied_to_every_occurrence` — L1017–L1036

[Read source and line explanations](walkthrough.html#file=20&line=1017).

Source documentation (check against implementation):

> B2: 'every 3 months +/- 14 days' - each occurrence gets its own window.

### `surgery_result` — L1044–L1052

[Read source and line explanations](walkthrough.html#file=20&line=1044).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_only_a_rule_stated_against_the_planned_date_accepts_a_planned_anchor` — L1055–L1061

[Read source and line explanations](walkthrough.html#file=20&line=1055).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_d2_planned_surgery_dates_pre_op_but_post_op_waits_for_the_actual_date` — L1064–L1084

[Read source and line explanations](walkthrough.html#file=20&line=1064).

Source documentation (check against implementation):

> D2: surgery planned 14-Dec-2026 -> pre-op window 30-Nov..14-Dec RESOLVED;
> post-op WAITING_FOR_ANCHOR. D3: surgery on 17-Dec -> post-op 16-Jan-2027.

### `test_d2_planned_surgery_dates_pre_op_but_post_op_waits_for_the_actual_date.evaluate` — L1070–L1075

[Read source and line explanations](walkthrough.html#file=20&line=1070).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_cohorts_nested_in_parts_carry_their_parent` — L1089–L1110

[Read source and line explanations](walkthrough.html#file=20&line=1089).

Source documentation (check against implementation):

> 'Part A -> Cohort 1 or 2; Part B -> Cohort 3.'

### `test_a_parent_that_is_not_defined_is_reported_not_guessed` — L1113–L1119

[Read source and line explanations](walkthrough.html#file=20&line=1113).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_a_series_stopped_by_a_condition_does_not_invent_a_block_mode` — L1124–L1136

[Read source and line explanations](walkthrough.html#file=20&line=1124).

Source documentation (check against implementation):

> The engine never reads a plain block's dependency mode, but the validator
> requires one; the compiler does not invent it and explains why the block exists.

### `test_site_country_and_trial_rules_keep_their_scope_on_the_conditional_definition` — L1142–L1151

[Read source and line explanations](walkthrough.html#file=20&line=1142).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `test_events_are_listed_in_protocol_order_not_the_order_the_pages_were_read` — L1156–L1168

[Read source and line explanations](walkthrough.html#file=20&line=1156).

Source documentation (check against implementation):

> A follow-up section read before the visit table must not list first.

## 22. backend/app/extraction/artifacts.py

Keys and stores model-call artifacts so completed questions can be reused.

156 lines. [Open annotated source](walkthrough.html#file=21).

### `ArtifactStore` — L30–L32

[Read source and line explanations](walkthrough.html#file=21&line=30).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ArtifactStore.get` — L31–L31

[Read source and line explanations](walkthrough.html#file=21&line=31).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ArtifactStore.put` — L32–L32

[Read source and line explanations](walkthrough.html#file=21&line=32).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `NullArtifactStore` — L35–L44

[Read source and line explanations](walkthrough.html#file=21&line=35).

Source documentation (check against implementation):

> Persists nothing; every call is a fresh model call.

### `NullArtifactStore.get` — L40–L41

[Read source and line explanations](walkthrough.html#file=21&line=40).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `NullArtifactStore.put` — L43–L44

[Read source and line explanations](walkthrough.html#file=21&line=43).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MemoryArtifactStore` — L47–L63

[Read source and line explanations](walkthrough.html#file=21&line=47).

Source documentation (check against implementation):

> In-process store for tests and single-process retries.

### `MemoryArtifactStore.__init__` — L52–L54

[Read source and line explanations](walkthrough.html#file=21&line=52).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MemoryArtifactStore.get` — L56–L58

[Read source and line explanations](walkthrough.html#file=21&line=56).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `MemoryArtifactStore.put` — L60–L63

[Read source and line explanations](walkthrough.html#file=21&line=60).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DiskArtifactStore` — L66–L95

[Read source and line explanations](walkthrough.html#file=21&line=66).

Source documentation (check against implementation):

> Artifacts as JSON files under a directory: a quota failure or a crash keeps every completed
> model answer, and a rerun (or an offline analysis) reuses them instead of paying again.
> Keys are the same content-addressed keys as every other store.

### `DiskArtifactStore.__init__` — L73–L76

[Read source and line explanations](walkthrough.html#file=21&line=73).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DiskArtifactStore._path` — L78–L79

[Read source and line explanations](walkthrough.html#file=21&line=78).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DiskArtifactStore.get` — L81–L85

[Read source and line explanations](walkthrough.html#file=21&line=81).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `DiskArtifactStore.put` — L87–L95

[Read source and line explanations](walkthrough.html#file=21&line=87).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_run_async` — L98–L109

[Read source and line explanations](walkthrough.html#file=21&line=98).

Source documentation (check against implementation):

> Run an async storage call from the reader's worker thread.
>
> The reader runs in a threadpool (``run_in_threadpool``) under the API, so
> ``anyio.from_thread`` reaches the event loop; outside any loop (worker,
> scripts) a private loop is used.

### `StorageArtifactStore` — L112–L138

[Read source and line explanations](walkthrough.html#file=21&line=112).

Source documentation (check against implementation):

> Artifacts as JSON blobs in the app's file storage (``storage.py``).

### `StorageArtifactStore.__init__` — L117–L118

[Read source and line explanations](walkthrough.html#file=21&line=117).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `StorageArtifactStore.get` — L120–L129

[Read source and line explanations](walkthrough.html#file=21&line=120).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `StorageArtifactStore.put` — L131–L138

[Read source and line explanations](walkthrough.html#file=21&line=131).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `sha256_text` — L141–L142

[Read source and line explanations](walkthrough.html#file=21&line=141).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `call_key` — L145–L152

[Read source and line explanations](walkthrough.html#file=21&line=145).

Source documentation (check against implementation):

> Storage key of one model call. Same inputs -> same key -> reusable.

### `manifest_key` — L155–L156

[Read source and line explanations](walkthrough.html#file=21&line=155).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 23. backend/app/extraction/ledger.py

Defines source coverage states, statement dispositions and completeness summaries.

218 lines. [Open annotated source](walkthrough.html#file=22).

### `_kind` — L57–L61

[Read source and line explanations](walkthrough.html#file=22&line=57).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `detect_references` — L64–L73

[Read source and line explanations](walkthrough.html#file=22&line=64).

Source documentation (check against implementation):

> Every appendix / section reference in a page's text, deduplicated.

### `_heading_pattern` — L76–L83

[Read source and line explanations](walkthrough.html#file=22&line=76).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `resolve_reference` — L86–L100

[Read source and line explanations](walkthrough.html#file=22&line=86).

Source documentation (check against implementation):

> Pages whose text carries a heading for the referenced unit.
>
> A table-of-contents line naming the unit is not the unit itself.

### `CoverageLedger` — L103–L169

[Read source and line explanations](walkthrough.html#file=22&line=103).

Source documentation (check against implementation):

> One coverage state per page, plus the reference inventory.

### `CoverageLedger.__init__` — L106–L120

[Read source and line explanations](walkthrough.html#file=22&line=106).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CoverageLedger.set` — L122–L127

[Read source and line explanations](walkthrough.html#file=22&line=122).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CoverageLedger.missing_reference` — L129–L141

[Read source and line explanations](walkthrough.html#file=22&line=129).

Source documentation (check against implementation):

> A referenced unit that is not in the document is its own source unit.

### `CoverageLedger.gaps` — L143–L144

[Read source and line explanations](walkthrough.html#file=22&line=143).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CoverageLedger.to_dict` — L146–L169

[Read source and line explanations](walkthrough.html#file=22&line=146).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `disposition_counts` — L202–L206

[Read source and line explanations](walkthrough.html#file=22&line=202).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `evidence_state` — L209–L218

[Read source and line explanations](walkthrough.html#file=22&line=209).

Source documentation (check against implementation):

> Fact-level evidence state: the weakest of its citations.

## 24. backend/app/extraction/statements.py

Accounts for each source statement and blocks affected rules when statements remain uncompiled.

331 lines. [Open annotated source](walkthrough.html#file=23).

### `_code` — L33–L35

[Read source and line explanations](walkthrough.html#file=23&line=33).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_issue_fact_ids` — L38–L45

[Read source and line explanations](walkthrough.html#file=23&line=38).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_batch` — L48–L50

[Read source and line explanations](walkthrough.html#file=23&line=48).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_citations` — L53–L56

[Read source and line explanations](walkthrough.html#file=23&line=53).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `build_statement_ledger` — L59–L168

[Read source and line explanations](walkthrough.html#file=23&line=59).

Source documentation (check against implementation):

> (statements, relations, extra blocking issues) for one compiled schedule.

### `_new_issue` — L171–L174

[Read source and line explanations](walkthrough.html#file=23&line=171).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `schedule_relevant_wording` — L189–L191

[Read source and line explanations](walkthrough.html#file=23&line=189).

Source documentation (check against implementation):

> The schedule-relevant words found in a footnote (empty = nothing that could be a rule).

### `_no_effect` — L194–L209

[Read source and line explanations](walkthrough.html#file=23&line=194).

Source documentation (check against implementation):

> Whether a footnote may be recorded as having no scheduling effect, and why / why not.
>
> The model only PROPOSES it. It is accepted when the footnote text is verified on its page, the
> model gave a reason, AND a deterministic screen finds no schedule-relevant wording. Anything
> else stays pending interpretation - an unstated rule is never assumed absent.

### `_footnotes` — L212–L295

[Read source and line explanations](walkthrough.html#file=23&line=212).

Source documentation (check against implementation):

> A schedule-table footnote is a statement too: it is interpreted by the
> facts that quote it, or it is pending - and a pending footnote on a marked
> cell blocks the visits that cell's activity is performed at.

### `_uncompiled_blocks_same_activity` — L298–L331

[Read source and line explanations](walkthrough.html#file=23&line=298).

Source documentation (check against implementation):

> IR-05 / AT-04: an uncompiled statement about an activity blocks every
> compiled rule about that same activity, so none can read as ready.

## 25. backend/app/services/native_import.py

The SQL-session native importer selected by server.py's current Add Trial build helper.

196 lines. [Open annotated source](walkthrough.html#file=24).

### `ImportedSchedule` — L37–L46

[Read source and line explanations](walkthrough.html#file=24&line=37).

Source documentation (check against implementation):

> One canonical draft produced from one detected schedule option.

### `_protocol_version` — L49–L94

[Read source and line explanations](walkthrough.html#file=24&line=49).

Source documentation (check against implementation):

> Differs from ``canonical_bridge``'s sibling only in what it is given:
> the real uploaded PDF's storage key and content hash, not a fingerprint
> of the legacy pipeline's already-translated plan - so a natively
> extracted protocol version points at a genuinely resolvable, auditable
> document (``canonical_bridge``'s synthetic
> ``private://protocol-extractions/{id}`` URI is never actually opened).
>
> Every schedule option detected in one document shares this ONE protocol
> version, unlike ``canonical_bridge``'s per-definition versions - options
> are all read from the same real PDF, so one document identity is the
> correct shape, and ``ScheduleDefinition``'s own uniqueness (protocol
> version + name + type) already assumes several schedules can share one
> protocol version.

### `_existing_version_for_run` — L97–L100

[Read source and line explanations](walkthrough.html#file=24&line=97).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `import_native_schedules` — L103–L196

[Read source and line explanations](walkthrough.html#file=24&line=103).

Source documentation (check against implementation):

> Import every natively extracted schedule option as a canonical draft
> version - counterpart to ``canonical_bridge.import_schedule_definitions``
> for definitions carrying an already-computed ``native_schedule``/
> ``native_issues`` payload (produced once, synchronously, by
> ``runner.extract_schedules`` at upload time - see
> ``server.py``'s ``/protocols/extract``) instead of a legacy
> ``CanonicalSchedulePlan`` to translate. No model call happens here.
>
> A definition with no ``native_schedule`` is skipped, same discipline
> ``canonical_bridge`` applies to a definition with no ``canonical_plan``:
> an empty schedule would look approvable while promising a patient nothing.

## 26. backend/app/services/extraction_service.py

Persists extraction runs and draft schedules through SQLAlchemy sessions.

343 lines. [Open annotated source](walkthrough.html#file=25).

### `utc_now` — L16–L17

[Read source and line explanations](walkthrough.html#file=25&line=16).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService` — L20–L343

[Read source and line explanations](walkthrough.html#file=25&line=20).

Source documentation (check against implementation):

> Transactional boundary around a separately hosted extraction worker.

### `ExtractionService.__init__` — L23–L24

[Read source and line explanations](walkthrough.html#file=25&line=23).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.queue` — L26–L68

[Read source and line explanations](walkthrough.html#file=25&line=26).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService._persist_one` — L70–L107

[Read source and line explanations](walkthrough.html#file=25&line=70).

Source documentation (check against implementation):

> Persist one already-extracted schedule as a new draft version.
>
> Shared by every caller of `complete_many` regardless of how many
> schedules one run produced - the run itself is marked QUEUED->
> COMPLETED exactly once, by the caller, after every schedule in the
> batch has been persisted.

### `ExtractionService.complete_many` — L109–L178

[Read source and line explanations](walkthrough.html#file=25&line=109).

Source documentation (check against implementation):

> Persist every schedule one run produced - one per independent
> Schedule of Assessments the document printed (see
> runner.extract_schedules) - as its own draft version,
> then mark the run COMPLETED once.
>
> A run that produced no usable schedule at all (every option failed)
> is marked FAILED with every option's issues attached, same as a
> single-schedule run that failed outright.

### `ExtractionService.complete` — L180–L181

[Read source and line explanations](walkthrough.html#file=25&line=180).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ExtractionService.accountability_for_version` — L183–L196

[Read source and line explanations](walkthrough.html#file=25&line=183).

Source documentation (check against implementation):

> The coverage / statement ledgers stored with a draft version, for
> the review screen. None when the version was not produced by a run
> that recorded them.

### `ExtractionService.record_statement_decisions` — L198–L310

[Read source and line explanations](walkthrough.html#file=25&line=198).

Source documentation (check against implementation):

> Record a reviewer decision that releases statements a question held.
>
> A statement left PENDING_INTERPRETATION only because of the issue(s) a
> reviewer has now answered becomes COMPILED *by reviewer decision*. The
> model's own disposition and reason are kept in ``disposition_history``,
> the decision is appended to the ledger's ``reviewer_decisions`` (the
> ledger ``revision`` counts them), the summary is recomputed, and an
> audit event records before/after. A statement with any other blocking
> code, or with no compiled rule to point at, is NOT released.
> Returns the ids actually released.

### `ExtractionService.start` — L312–L336

[Read source and line explanations](walkthrough.html#file=25&line=312).

Source documentation (check against implementation):

> QUEUED -> RUNNING. Split out from `execute` so a caller that needs
> to build the extraction pipeline itself (runner.execute_run, to fan
> out over several schedule options) can do so after the run is marked
> RUNNING but before any model call happens, instead of this class
> owning the whole pipeline construction.

### `ExtractionService.fail` — L338–L343

[Read source and line explanations](walkthrough.html#file=25&line=338).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 27. backend/app/services/schedule_service.py

The SQL-backed review and patient workflow used alongside the Mongo repository-port implementation.

52 lines. [Open annotated source](walkthrough.html#file=26).

### `ScheduleReviewService` — L42–L44

[Read source and line explanations](walkthrough.html#file=26&line=42).

Source documentation (check against implementation):

> Compatibility facade (P12-02): the behaviour lives in ``review_base`` / ``review_editing`` /
> ``review_approval`` / ``review_queue``; every public method, signature and import path is unchanged.

### `PatientScheduleService` — L50–L52

[Read source and line explanations](walkthrough.html#file=26&line=50).

Source documentation (check against implementation):

> Compatibility facade (P12-02): the behaviour lives in ``patient_schedule_reads`` / ``_context`` /
> ``_persistence`` / ``_recording`` / ``_evaluation``; every public method, signature and import path is unchanged.

## 28. backend/app/api/uctsm.py

HTTP routes for SQL-session UCTSM authoring, review, evaluation and cutover.

2215 lines. [Open annotated source](walkthrough.html#file=27).

### `ResolveIssueIn` — L49–L54

[Read source and line explanations](walkthrough.html#file=27&line=49).

Source documentation (check against implementation):

> A source-review resolution. ``resolution`` is optional and typed: NO_SCHEDULING_EFFECT says
> a footnote was read and states nothing about the schedule (only for an uninterpreted footnote).

### `BulkConfirmIn` — L57–L61

[Read source and line explanations](walkthrough.html#file=27&line=57).

Source documentation (check against implementation):

> Doc s14: bulk confirmation must record WHY, same as any other reviewed
> decision - it is a real reviewer action, just not a per-field one.

### `ApproveTableIn` — L64–L66

[Read source and line explanations](walkthrough.html#file=27&line=64).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_ReviewWriteIn` — L69–L74

[Read source and line explanations](walkthrough.html#file=27&line=69).

Source documentation (check against implementation):

> Every review write names the draft revision it was made against (RV-05).

### `EventChangesIn` — L77–L93

[Read source and line explanations](walkthrough.html#file=27&line=77).

Source documentation (check against implementation):

> A typed semantic patch (UI-07). Only the fields sent are changed, so a
> label edit can never rewrite timing. ``window: null`` states "not stated".

### `HolidayCalendarIn` — L96–L97

[Read source and line explanations](walkthrough.html#file=27&line=96).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrackIn` — L100–L109

[Read source and line explanations](walkthrough.html#file=27&line=100).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrackLinkIn` — L112–L117

[Read source and line explanations](walkthrough.html#file=27&line=112).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrackMembershipIn` — L120–L123

[Read source and line explanations](walkthrough.html#file=27&line=120).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TracksIn` — L126–L129

[Read source and line explanations](walkthrough.html#file=27&line=126).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EventEditIn` — L132–L135

[Read source and line explanations](walkthrough.html#file=27&line=132).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AddVisitIn` — L138–L149

[Read source and line explanations](walkthrough.html#file=27&line=138).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `QuestionResolutionIn` — L152–L156

[Read source and line explanations](walkthrough.html#file=27&line=152).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ApproveVersionIn` — L159–L166

[Read source and line explanations](walkthrough.html#file=27&line=159).

Source documentation (check against implementation):

> Whole-version approval only (RV-04): there is deliberately no row or
> subset selector, and unknown fields are refused rather than ignored.

### `_maker_checker_refused` — L169–L187

[Read source and line explanations](walkthrough.html#file=27&line=169).

Source documentation (check against implementation):

> Refuse an approval that violates segregation of duties.
>
> The business transaction is rolled back first (status and pointers stay
> unchanged); the refusal is then recorded in its OWN committed transaction so
> a rollback can never erase the evidence that it happened. It is recorded as
> a denial, never as an approval.

### `_stale` — L190–L194

[Read source and line explanations](walkthrough.html#file=27&line=190).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewDecisionIn` — L197–L205

[Read source and line explanations](walkthrough.html#file=27&line=197).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `QualifierResolutionIn` — L208–L226

[Read source and line explanations](walkthrough.html#file=27&line=208).

Source documentation (check against implementation):

> A reviewer stating what one footnote means (doc s6).
>
> ``reason`` is mandatory on every outcome, including NOT_APPLICABLE and
> ESCALATE: the audit trail has to answer "why was this decided" for a rule
> that changes what happens to a patient.

### `ReviewIn` — L229–L235

[Read source and line explanations](walkthrough.html#file=27&line=229).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ParityCheckIn` — L238–L247

[Read source and line explanations](walkthrough.html#file=27&line=238).

Source documentation (check against implementation):

> The operational schedule to compare against, supplied by the caller.
>
> The canonical services speak to Postgres; the operational store is Mongo. The
> caller crosses that boundary and hands the visits over, so nothing here has
> to reach across it.

### `ReadModeIn` — L250–L252

[Read source and line explanations](walkthrough.html#file=27&line=250).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AcceptParityRunIn` — L255–L259

[Read source and line explanations](walkthrough.html#file=27&line=255).

Source documentation (check against implementation):

> AD-1a: signing off one DIFFERENCES_FOUND run. Reason is mandatory - the
> same "why" requirement every other reviewed decision in this file carries.

### `UnscheduledVisitIn` — L262–L267

[Read source and line explanations](walkthrough.html#file=27&line=262).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EvaluateIn` — L270–L286

[Read source and line explanations](walkthrough.html#file=27&line=270).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `EvaluateIn.rolling` — L283–L286

[Read source and line explanations](walkthrough.html#file=27&line=283).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `TrialIn` — L289–L296

[Read source and line explanations](walkthrough.html#file=27&line=289).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProtocolIn` — L299–L300

[Read source and line explanations](walkthrough.html#file=27&line=299).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProtocolVersionIn` — L303–L310

[Read source and line explanations](walkthrough.html#file=27&line=303).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientIn` — L313–L321

[Read source and line explanations](walkthrough.html#file=27&line=313).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientAnchorIn` — L324–L330

[Read source and line explanations](walkthrough.html#file=27&line=324).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientStateIn` — L333–L337

[Read source and line explanations](walkthrough.html#file=27&line=333).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `StateImpactPreviewIn` — L340–L344

[Read source and line explanations](walkthrough.html#file=27&line=340).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientOccurrenceIn` — L347–L352

[Read source and line explanations](walkthrough.html#file=27&line=347).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ActivityRecordIn` — L355–L361

[Read source and line explanations](walkthrough.html#file=27&line=355).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ConditionChangeIn` — L364–L374

[Read source and line explanations](walkthrough.html#file=27&line=364).

Source documentation (check against implementation):

> A proposed condition state change, previewed before it is applied.

### `ConditionPreviewIn` — L377–L381

[Read source and line explanations](walkthrough.html#file=27&line=377).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProcessTargetIn` — L384–L388

[Read source and line explanations](walkthrough.html#file=27&line=384).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ProcessTransitionIn` — L391–L409

[Read source and line explanations](walkthrough.html#file=27&line=391).

Source documentation (check against implementation):

> WP3: a proposed hold / resume / stop / completion of one process (or the
> patient's whole participation). Previewed, then confirmed; never applied
> directly.

### `ProcessTransitionIn.to_domain` — L408–L409

[Read source and line explanations](walkthrough.html#file=27&line=408).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ImpactPreviewIn` — L412–L419

[Read source and line explanations](walkthrough.html#file=27&line=412).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `OccurrenceFactIn` — L422–L434

[Read source and line explanations](walkthrough.html#file=27&line=422).

Source documentation (check against implementation):

> PT-02/PT-04: a performed, missed or booked fact for one occurrence.
> A correction names the record it supersedes; history is kept.

### `ImpactConfirmIn` — L437–L438

[Read source and line explanations](walkthrough.html#file=27&line=437).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AnchorImpactPreviewIn` — L441–L444

[Read source and line explanations](walkthrough.html#file=27&line=441).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `AssignmentPreviewIn` — L447–L461

[Read source and line explanations](walkthrough.html#file=27&line=447).

Source documentation (check against implementation):

> A proposed change to which protocol groups a patient belongs to.
>
> Keys are dimension names the schedule defines - ARM, COHORT, POPULATION, or
> any open dimension such as PART, SEQUENCE or SUBSTUDY (doc 8 s35). A null
> value removes the assignment.

### `ImpactCancelIn` — L464–L465

[Read source and line explanations](walkthrough.html#file=27&line=464).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_as_date` — L468–L471

[Read source and line explanations](walkthrough.html#file=27&line=468).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_feasibility_fields` — L474–L484

[Read source and line explanations](walkthrough.html#file=27&line=474).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_wp3_event_fields` — L487–L507

[Read source and line explanations](walkthrough.html#file=27&line=487).

Source documentation (check against implementation):

> Process state, booking, performed time, episode and calendar session,
> read from the persisted evaluator explanation (WP3).

### `_assert_schedule_tenant` — L510–L520

[Read source and line explanations](walkthrough.html#file=27&line=510).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `_run_extraction_task` — L523–L536

[Read source and line explanations](walkthrough.html#file=27&line=523).

Source documentation (check against implementation):

> Execute one queued extraction on its own session.
>
> The request's session is closed once the 202 is returned, so a background
> task that borrowed it would fail on its first query.

### `_require_high_impact` — L539–L559

[Read source and line explanations](walkthrough.html#file=27&line=539).

Source documentation (check against implementation):

> A persisted process transition (hold / resume / stop) is a high-impact
> operation: the generic impact preview/confirm right is not enough, exactly as for
> the typed command plane. Re-evaluated from the caller's CURRENT principal and
> the already-resolved patient relationship, never from the request body.

### `create_uctsm_router` — L562–L2215

[Read source and line explanations](walkthrough.html#file=27&line=562).

Source documentation (check against implementation):

> Every route authenticates through ``current_user_dependency`` and is then
> authorised by ``authorizer`` (capability + persisted scope, P3) before its
> handler runs. There is deliberately no default: an unguarded router cannot be
> built, and a route missing from ``app.authorization.manifest`` is refused.

### `create_uctsm_router.seed_demo` — L574–L586

[Read source and line explanations](walkthrough.html#file=27&line=574).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.create_trial` — L589–L606

[Read source and line explanations](walkthrough.html#file=27&line=589).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.create_protocol` — L609–L625

[Read source and line explanations](walkthrough.html#file=27&line=609).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.create_protocol_version` — L628–L657

[Read source and line explanations](walkthrough.html#file=27&line=628).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.approved_schedules` — L660–L685

[Read source and line explanations](walkthrough.html#file=27&line=660).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.create_patient` — L688–L771

[Read source and line explanations](walkthrough.html#file=27&line=688).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.queue_extraction` — L774–L822

[Read source and line explanations](walkthrough.html#file=27&line=774).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_extraction_run` — L825–L844

[Read source and line explanations](walkthrough.html#file=27&line=825).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.list_pending_schedule_reviews` — L847–L859

[Read source and line explanations](walkthrough.html#file=27&line=847).

Source documentation (check against implementation):

> Every IN_REVIEW schedule version across this org's trials, newest
> decision-needed first - the canonical replacement for the legacy
> `schedule_reviews` inbox. Registered ahead of the
> `/schedule-versions/{schedule_version_id}` route below so this literal
> path is matched first, not swallowed as an invalid UUID.

### `create_uctsm_router.get_schedule` — L862–L865

[Read source and line explanations](walkthrough.html#file=27&line=862).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.validate_schedule` — L868–L878

[Read source and line explanations](walkthrough.html#file=27&line=868).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.record_decision` — L881–L892

[Read source and line explanations](walkthrough.html#file=27&line=881).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.resolve_extraction_issue` — L895–L907

[Read source and line explanations](walkthrough.html#file=27&line=895).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.bulk_confirm` — L910–L926

[Read source and line explanations](walkthrough.html#file=27&line=910).

Source documentation (check against implementation):

> Confirm every required field with one action, and record it as one
> action (doc s14) - never presented as N individual field reviews.

### `create_uctsm_router.approve_table` — L929–L947

[Read source and line explanations](walkthrough.html#file=27&line=929).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.correct_event` — L950–L977

[Read source and line explanations](walkthrough.html#file=27&line=950).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.resolve_qualifier` — L981–L1017

[Read source and line explanations](walkthrough.html#file=27&line=981).

Source documentation (check against implementation):

> Record what a footnote MEANS, so it can stop blocking approval (doc s6).
>
> Extraction never interprets a table marker; it carries the marker, its
> text and its evidence and leaves it UNRESOLVED, which blocks approval by
> design. Without this endpoint nothing could ever clear that block, so a
> protocol with a single footnote produced a draft that could not be
> approved at all.

### `create_uctsm_router.list_qualifiers` — L1020–L1068

[Read source and line explanations](walkthrough.html#file=27&line=1020).

Source documentation (check against implementation):

> Every footnote on this version, with what a reviewer needs to judge it.
>
> Doc s6 and s17: the marker as printed, the source text, what it is
> attached to, and the evidence behind it - so the reviewer can answer
> "why does MTB think this applies here?" without leaving the screen.

### `create_uctsm_router.submit_review` — L1071–L1080

[Read source and line explanations](walkthrough.html#file=27&line=1071).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.review_schedule` — L1083–L1107

[Read source and line explanations](walkthrough.html#file=27&line=1083).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_review_view` — L1112–L1118

[Read source and line explanations](walkthrough.html#file=27&line=1112).

Source documentation (check against implementation):

> The ScheduleReviewView: rows, shared questions, document issues and
> backend approval eligibility, derived from the canonical rules and a
> fresh validation run. Read-only; contains no patient dates (UI-10).

### `create_uctsm_router._review_write` — L1120–L1152

[Read source and line explanations](walkthrough.html#file=27&line=1120).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.edit_event` — L1155–L1164

[Read source and line explanations](walkthrough.html#file=27&line=1155).

Source documentation (check against implementation):

> Typed semantic edit of one visit rule, then revalidation (UI-07).

### `create_uctsm_router.add_visit` — L1167–L1175

[Read source and line explanations](walkthrough.html#file=27&line=1167).

Source documentation (check against implementation):

> Add Visit: a MANUAL-origin rule with rationale and review state (UI-07).

### `create_uctsm_router.attach_holiday_calendar` — L1178–L1184

[Read source and line explanations](walkthrough.html#file=27&line=1178).

Source documentation (check against implementation):

> Attach a reviewed schedule-level holiday calendar to the draft.

### `create_uctsm_router.review_tracks` — L1187–L1196

[Read source and line explanations](walkthrough.html#file=27&line=1187).

Source documentation (check against implementation):

> Reviewed track definitions, cross-track rules and event membership.

### `create_uctsm_router.resolve_review_question` — L1199–L1208

[Read source and line explanations](walkthrough.html#file=27&line=1199).

Source documentation (check against implementation):

> Answer one shared question once for every affected row (UI-06).

### `create_uctsm_router.approve_version` — L1211–L1223

[Read source and line explanations](walkthrough.html#file=27&line=1211).

Source documentation (check against implementation):

> Whole-version approval at a named revision (UI-08, RV-04, RV-05).
>
> The gates are rerun server-side from the stored draft; a stale revision
> or any blocker is refused and nothing is partially activated.

### `create_uctsm_router.export_schedule` — L1226–L1242

[Read source and line explanations](walkthrough.html#file=27&line=1226).

Source documentation (check against implementation):

> Reviewed schedule with provenance and draft/approved label (UI-09).

### `create_uctsm_router.get_accountability` — L1245–L1253

[Read source and line explanations](walkthrough.html#file=27&line=1245).

Source documentation (check against implementation):

> The extraction accountability ledger (coverage, statement dispositions,
> anchor reconciliation) recorded with this draft, or 404 when none was.

### `create_uctsm_router.get_projection` — L1256–L1259

[Read source and line explanations](walkthrough.html#file=27&line=1256).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_enrolment_options` — L1262–L1289

[Read source and line explanations](walkthrough.html#file=27&line=1262).

Source documentation (check against implementation):

> Which groups a patient can still be assigned to (doc 8 s15-s18).
>
> Selections narrow what follows: once a Part is chosen only that Part's
> cohorts are offered. A dimension whose values all nest inside an unchosen
> parent comes back blocked, rather than listing values that cannot apply.

### `create_uctsm_router.diff_schedules` — L1292–L1302

[Read source and line explanations](walkthrough.html#file=27&line=1292).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.evaluate_patient` — L1305–L1349

[Read source and line explanations](walkthrough.html#file=27&line=1305).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_patient_schedule` — L1352–L1458

[Read source and line explanations](walkthrough.html#file=27&line=1352).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_patient_schedule_projection` — L1461–L1541

[Read source and line explanations](walkthrough.html#file=27&line=1461).

Source documentation (check against implementation):

> Reminders and calendar entries implied by the confirmed patient plan.
>
> Undated, conditional, paused, cancelled, and unscheduled items are reported
> under `suppressed` with a reason rather than silently dropped, so an
> operator can see why a patient is not being reminded about something.

### `create_uctsm_router.get_dashboard_actions` — L1544–L1562

[Read source and line explanations](walkthrough.html#file=27&line=1544).

Source documentation (check against implementation):

> What currently needs a PI or CRC decision - actions, not a schedule.
>
> Deliberately bounded: an open-ended protocol must not put every future cycle
> on the board, and a conditional the patient never triggered is absent
> entirely (doc 2 s13, doc 3 s28).

### `create_uctsm_router.get_patient_deviations` — L1565–L1593

[Read source and line explanations](walkthrough.html#file=27&line=1565).

Source documentation (check against implementation):

> Protocol deviations derived from the current plan and recorded actuals.
>
> Nothing is stored: the protocol-expected date moves when an anchor is
> corrected, so a stored deviation would go stale. Items that were never an
> active requirement for this patient come back as NOT_ASSESSABLE with the
> reason, rather than being silently omitted.

### `create_uctsm_router.record_occurrence_fact` — L1596–L1627

[Read source and line explanations](walkthrough.html#file=27&line=1596).

Source documentation (check against implementation):

> Record a performed / missed / booked fact (or correct one), then
> re-evaluate. A booking never moves the rule; a missed session keeps its
> number. Tenant-scoped; the service writes the audit event.

### `create_uctsm_router.record_patient_activity` — L1630–L1650

[Read source and line explanations](walkthrough.html#file=27&line=1630).

Source documentation (check against implementation):

> Record an intra-day actual time; dependent activity times follow it.

### `create_uctsm_router.run_parity_check` — L1653–L1678

[Read source and line explanations](walkthrough.html#file=27&line=1653).

Source documentation (check against implementation):

> Compare this patient's engine schedule against the operational one.
>
> Records the result, whatever it is. A cutover is a decision someone made
> on evidence, and the evidence has to still exist afterwards.

### `create_uctsm_router.get_read_mode` — L1681–L1688

[Read source and line explanations](walkthrough.html#file=27&line=1681).

Source documentation (check against implementation):

> Where this trial's visit dates are read from, and whether it can move.

### `create_uctsm_router.set_read_mode` — L1691–L1711

[Read source and line explanations](walkthrough.html#file=27&line=1691).

Source documentation (check against implementation):

> Move a trial's visit reads between the operational store and the engine.
>
> Moving TO the engine requires a matching parity run for every patient
> against the version they are pinned to. Moving back needs no gate:
> reversing a cutover must never be harder than making one.

### `create_uctsm_router.accept_parity_run` — L1714–L1746

[Read source and line explanations](walkthrough.html#file=27&line=1714).

Source documentation (check against implementation):

> Sign off one DIFFERENCES_FOUND parity run (AD-1a).
>
> Lets readiness() pass on this patient without the underlying difference
> changing - for the case where the engine is correctly right and the
> operational schedule is what's wrong (e.g. ENGINE_UNDATED), which would
> otherwise leave the gate permanently unclearable. One run, one reason,
> no bulk accept.

### `create_uctsm_router.get_patient_anchor_statuses` — L1749–L1771

[Read source and line explanations](walkthrough.html#file=27&line=1749).

Source documentation (check against implementation):

> What is known about each anchor for this patient (doc 1 s22).
>
> The vocabulary matters: PLANNED is an expected date and never becomes
> ACTUAL on its own, so a follow-up calculated from an expected surgery date
> is never presented as if surgery had happened. AWAITING_EVENT names the
> event it is waiting on, which is what makes an undated visit explicable
> rather than broken.

### `create_uctsm_router.create_unscheduled_visit` — L1774–L1802

[Read source and line explanations](walkthrough.html#file=27&line=1774).

Source documentation (check against implementation):

> Create an occurrence of a protocol-defined unscheduled visit.
>
> Doc 10 s13-s15. Until a site does this the definition is visible but not
> due, so it never generates a reminder and never counts as missed. Creating
> one affects this patient only; the master schedule is untouched.

### `create_uctsm_router.list_patient_conditions` — L1805–L1844

[Read source and line explanations](walkthrough.html#file=27&line=1805).

Source documentation (check against implementation):

> Protocol conditions relevant to this patient, with their current state.
>
> Conditions that have never occurred are listed too, so the Patient Profile
> can show the full checklist without those items entering the dated schedule.

### `create_uctsm_router.preview_condition_impact` — L1847–L1881

[Read source and line explanations](walkthrough.html#file=27&line=1847).

Source documentation (check against implementation):

> Show what activating or resolving a condition would do, without applying it.

### `create_uctsm_router.preview_assignment_impact` — L1884–L1920

[Read source and line explanations](walkthrough.html#file=27&line=1884).

Source documentation (check against implementation):

> Show what moving a patient between protocol groups would do (doc 8 s22).
>
> Reassignment changes WHICH visits the patient has, so it never applies
> directly. Confirming the returned proposal is what commits it, and the
> proposal goes stale if the patient's inputs move in the meantime.

### `create_uctsm_router.preview_patient_schedule_impact` — L1923–L1962

[Read source and line explanations](walkthrough.html#file=27&line=1923).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.confirm_patient_schedule_impact` — L1965–L1993

[Read source and line explanations](walkthrough.html#file=27&line=1965).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.cancel_patient_schedule_impact` — L1996–L2012

[Read source and line explanations](walkthrough.html#file=27&line=1996).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.get_patient_event` — L2015–L2032

[Read source and line explanations](walkthrough.html#file=27&line=2015).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.record_occurrence` — L2035–L2106

[Read source and line explanations](walkthrough.html#file=27&line=2035).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.record_patient_anchor` — L2109–L2141

[Read source and line explanations](walkthrough.html#file=27&line=2109).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.preview_anchor_impact` — L2147–L2175

[Read source and line explanations](walkthrough.html#file=27&line=2147).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.record_patient_state` — L2178–L2184

[Read source and line explanations](walkthrough.html#file=27&line=2178).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_router.preview_patient_state_impact` — L2187–L2213

[Read source and line explanations](walkthrough.html#file=27&line=2187).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 29. backend/app/api/uctsm_mongo.py

HTTP entry points to the explicitly configured Mongo patient plane and repository-port workflows.

549 lines. [Open annotated source](walkthrough.html#file=28).

### `get_plane_uow` — L54–L66

[Read source and line explanations](walkthrough.html#file=28&line=54).

Source documentation (check against implementation):

> The configured Mongo patient-plane unit of work (503 when not configured).

### `ActualOccurrenceIn` — L69–L85

[Read source and line explanations](walkthrough.html#file=28&line=69).

Source documentation (check against implementation):

> One performed / missed / booked fact for a logical occurrence.
>
> ``visit_date`` and ``treatment_date`` are separate facts: attending with
> treatment withheld is a visit date and no treatment date.

### `PendingAnchorCandidate` — L88–L93

[Read source and line explanations](walkthrough.html#file=28&line=88).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ActualOccurrenceOut` — L96–L104

[Read source and line explanations](walkthrough.html#file=28&line=96).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `CalendarOut` — L107–L117

[Read source and line explanations](walkthrough.html#file=28&line=107).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router` — L120–L549

[Read source and line explanations](walkthrough.html#file=28&line=120).

Source documentation (check against implementation):

> ``operational_provisioner(user, description)`` (async) records the synthetic
> pilot's operational relationship documents in the SAME store the production
> relationship provider reads, so a seeded pilot is reachable through the normal
> staff relationship rules - never through a mode-based access bypass.

### `create_uctsm_mongo_router.locate_owner` — L132–L140

[Read source and line explanations](walkthrough.html#file=28&line=132).

Source documentation (check against implementation):

> Persisted canonical owner of the plane patient (R2).

### `create_uctsm_mongo_router.resolve` — L149–L156

[Read source and line explanations](walkthrough.html#file=28&line=149).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.pending` — L158–L163

[Read source and line explanations](walkthrough.html#file=28&line=158).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.record_actual_occurrence` — L166–L196

[Read source and line explanations](walkthrough.html#file=28&line=166).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.needed_capability` — L199–L204

[Read source and line explanations](walkthrough.html#file=28&line=199).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.refuse` — L206–L215

[Read source and line explanations](walkthrough.html#file=28&line=206).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.preview_command` — L218–L247

[Read source and line explanations](walkthrough.html#file=28&line=218).

Source documentation (check against implementation):

> Calculate what a command would do. Nothing clinical is written.

### `create_uctsm_mongo_router.authorise_proposal` — L249–L265

[Read source and line explanations](walkthrough.html#file=28&line=249).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.ConfirmIn` — L267–L269

[Read source and line explanations](walkthrough.html#file=28&line=267).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.confirm_command` — L272–L290

[Read source and line explanations](walkthrough.html#file=28&line=272).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.cancel_command` — L293–L304

[Read source and line explanations](walkthrough.html#file=28&line=293).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.is_staff` — L306–L308

[Read source and line explanations](walkthrough.html#file=28&line=306).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.strip_private` — L310–L325

[Read source and line explanations](walkthrough.html#file=28&line=310).

Source documentation (check against implementation):

> Remove authority / reason from the operational layers of a diff.

### `create_uctsm_mongo_router.seed_pilot` — L330–L359

[Read source and line explanations](walkthrough.html#file=28&line=330).

Source documentation (check against implementation):

> Create (idempotently) the isolated synthetic pilot ``MTB-UCTSM-PILOT-001``.
>
> Local/development only: refused unless ``UCTSM_PILOT_MODE`` is enabled. The
> caller is the editor; the approver is a fixed synthetic local actor, so the
> pilot's approval passes the shared maker-checker boundary as a different
> person without involving any real account.

### `create_uctsm_mongo_router.seed_pilot_amendment` — L362–L378

[Read source and line explanations](walkthrough.html#file=28&line=362).

Source documentation (check against implementation):

> Add (idempotently) the synthetic Amendment 1 of the pilot: a new protocol and
> schedule version that declares it amends Pilot 1.0, approved by the fixed synthetic
> approver. Local/development only (``UCTSM_PILOT_MODE``). No patient is moved.

### `create_uctsm_mongo_router.list_patients` — L381–L420

[Read source and line explanations](walkthrough.html#file=28&line=381).

Source documentation (check against implementation):

> A stable page of the plane's patients for one trial: scoped to the
> caller's organization, then filtered by the operational relationship
> BEFORE serialisation. ``next_cursor`` is explicit; no total is returned.

### `create_uctsm_mongo_router.list_commands` — L423–L429

[Read source and line explanations](walkthrough.html#file=28&line=423).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `create_uctsm_mongo_router.schedule_report` — L434–L472

[Read source and line explanations](walkthrough.html#file=28&line=434).

Source documentation (check against implementation):

> P10-05: targets, windows, actuals, deviations, versions, commands and amendment state for the
> patients the caller may see, from the committed projection. Patients never use this route.

### `create_uctsm_mongo_router.for_role` — L474–L484

[Read source and line explanations](walkthrough.html#file=28&line=474).

Source documentation (check against implementation):

> Authority, reasons and command identity are treating-staff detail: a
> patient or sponsor/CRO response carries the dates but not who authorised
> an exception or why.

### `create_uctsm_mongo_router.get_calendar` — L487–L531

[Read source and line explanations](walkthrough.html#file=28&line=487).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 30. backend/app/services/review_base.py

Shared session/repository access and frozen-version guards for SQL review.

95 lines. [Open annotated source](walkthrough.html#file=29).

### `ReviewBase` — L25–L95

[Read source and line explanations](walkthrough.html#file=29&line=25).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewBase.__init__` — L26–L31

[Read source and line explanations](walkthrough.html#file=29&line=26).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewBase.mark_material_edit` — L34–L41

[Read source and line explanations](walkthrough.html#file=29&line=34).

Source documentation (check against implementation):

> Record WHO made a material change, on the version, in this transaction.

### `ReviewBase._provenance` — L43–L59

[Read source and line explanations](walkthrough.html#file=29&line=43).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewBase.enforce_maker_checker` — L61–L71

[Read source and line explanations](walkthrough.html#file=29&line=61).

Source documentation (check against implementation):

> Raise ``MakerCheckerViolation`` when the policy forbids this approver.
>
> This is the lowest shared approval boundary: approve-version,
> approve-table, the review endpoint and direct service calls all end in
> ``approve`` below, so the rule cannot be bypassed by choosing an alias.

### `ReviewBase._organization_id` — L73–L80

[Read source and line explanations](walkthrough.html#file=29&line=73).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewBase._revision` — L82–L88

[Read source and line explanations](walkthrough.html#file=29&line=82).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewBase._audit` — L90–L95

[Read source and line explanations](walkthrough.html#file=29&line=90).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 31. backend/app/services/review_editing.py

Implements draft validation, event corrections, footnote resolutions and review decisions.

305 lines. [Open annotated source](walkthrough.html#file=30).

### `ReviewEditing` — L23–L305

[Read source and line explanations](walkthrough.html#file=30&line=23).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewEditing.validate` — L24–L39

[Read source and line explanations](walkthrough.html#file=30&line=24).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewEditing.correct_event` — L41–L72

[Read source and line explanations](walkthrough.html#file=30&line=41).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewEditing.bulk_confirm_all_fields` — L74–L131

[Read source and line explanations](walkthrough.html#file=30&line=74).

Source documentation (check against implementation):

> Confirm every required field with ONE reviewer action - and say so.
>
> Doc s14: "the current 'Confirm All' behavior must NOT be treated as
> equivalent to field-level review... do not claim 'every field
> individually reviewed' when the user only pressed one bulk button."
>
> This still writes the same per-field ``ReviewDecision`` rows
> ``approve()`` checks for (so the field-confirmation gate keeps working
> exactly as before), but their comment says plainly that they came from
> one bulk action, and ONE additional, distinctly-named audit event
> (``SCHEDULE_BULK_CONFIRMED``) records the action itself - so a later
> reviewer of the audit trail sees "bulk-confirmed, N fields, by X, on
> DATE, because REASON" rather than N identical-looking individual
> confirmations that imply N separate looks.

### `ReviewEditing.resolve_qualifier` — L133–L243

[Read source and line explanations](walkthrough.html#file=30&line=133).

Source documentation (check against implementation):

> Turn one extracted footnote into reviewed schedule meaning (doc s6).
>
> Extraction deliberately refuses to interpret a footnote: it carries the
> marker, its text and its evidence, and marks it UNRESOLVED, which blocks
> approval. That was only half a workflow - nothing could ever clear the
> block, so any protocol with a footnote produced a draft that could never
> be approved. This is the other half.
>
> The reviewer states what the footnote MEANS; this method records that
> statement, never guesses at it. ACCEPT keeps the extracted reading, EDIT
> replaces it, NOT_APPLICABLE records that it does not govern this
> schedule, and ESCALATE leaves it unresolved on purpose so it stays
> blocking. Every outcome is a ReviewDecision plus an AuditEvent carrying
> the before/after value, who decided, and why.

### `ReviewEditing.record_decision` — L245–L279

[Read source and line explanations](walkthrough.html#file=30&line=245).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewEditing.resolve_extraction_issue` — L281–L305

[Read source and line explanations](walkthrough.html#file=30&line=281).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 32. backend/app/services/review_approval.py

Implements SQL-backed submission, approval gates, approval stamps and rejection.

169 lines. [Open annotated source](walkthrough.html#file=31).

### `ReviewApproval` — L22–L169

[Read source and line explanations](walkthrough.html#file=31&line=22).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewApproval.submit_for_review` — L23–L31

[Read source and line explanations](walkthrough.html#file=31&line=23).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewApproval.approve_reviewed_table` — L33–L58

[Read source and line explanations](walkthrough.html#file=31&line=33).

Source documentation (check against implementation):

> One explicit table confirmation, with existing gates and bulk audit.
>
> The API commits this transaction atomically. Source findings are never
> dismissed by this action.

### `ReviewApproval.approve` — L60–L157

[Read source and line explanations](walkthrough.html#file=31&line=60).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewApproval.reject` — L159–L169

[Read source and line explanations](walkthrough.html#file=31&line=159).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 33. backend/app/services/review_queue.py

Queries the review queue and pending schedule versions.

72 lines. [Open annotated source](walkthrough.html#file=32).

### `ReviewQueue` — L20–L72

[Read source and line explanations](walkthrough.html#file=32&line=20).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `ReviewQueue.list_pending_review` — L21–L72

[Read source and line explanations](walkthrough.html#file=32&line=21).

Source documentation (check against implementation):

> Every IN_REVIEW schedule version across this org's trials.
>
> The cross-trial "what needs my decision" list `schedule-review.tsx`
> used to provide from a separate `schedule_reviews` Mongo collection -
> this is its canonical replacement, queried directly off the same
> tables `approve`/`reject` already act on rather than a second,
> independently-maintained record of "what's pending".

## 34. backend/app/services/patient_schedule_reads.py

Loads recorded facts, history and calendar data for SQL-backed patients.

108 lines. [Open annotated source](walkthrough.html#file=33).

### `PatientScheduleReads` — L19–L108

[Read source and line explanations](walkthrough.html#file=33&line=19).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.__init__` — L20–L22

[Read source and line explanations](walkthrough.html#file=33&line=20).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.result_date` — L25–L30

[Read source and line explanations](walkthrough.html#file=33&line=25).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.logical_key_for_row` — L32–L37

[Read source and line explanations](walkthrough.html#file=33&line=32).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.current_events` — L39–L49

[Read source and line explanations](walkthrough.html#file=33&line=39).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.protected_event_ids` — L51–L62

[Read source and line explanations](walkthrough.html#file=33&line=51).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.evaluate_result` — L64–L89

[Read source and line explanations](walkthrough.html#file=33&line=64).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleReads.patient_conditions` — L91–L108

[Read source and line explanations](walkthrough.html#file=33&line=91).

Source documentation (check against implementation):

> Current condition state per code, newest non-superseded record wins.

## 35. backend/app/services/patient_schedule_context.py

Assembles patient-specific inputs for the shared deterministic evaluator.

156 lines. [Open annotated source](walkthrough.html#file=34).

### `PatientScheduleContext` — L20–L156

[Read source and line explanations](walkthrough.html#file=34&line=20).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleContext._context` — L21–L156

[Read source and line explanations](walkthrough.html#file=34&line=21).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 36. backend/app/services/patient_schedule_persistence.py

Writes evaluated occurrences, activities and confinements through SQL repositories.

116 lines. [Open annotated source](walkthrough.html#file=35).

### `PatientSchedulePersistence` — L18–L116

[Read source and line explanations](walkthrough.html#file=35&line=18).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientSchedulePersistence._persist_activities` — L19–L57

[Read source and line explanations](walkthrough.html#file=35&line=19).

Source documentation (check against implementation):

> Materialize the day-wise activity schedule for one visit occurrence.

### `PatientSchedulePersistence._persist_activities.as_datetime` — L31–L34

[Read source and line explanations](walkthrough.html#file=35&line=31).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientSchedulePersistence._persist_confinements` — L59–L116

[Read source and line explanations](walkthrough.html#file=35&line=59).

Source documentation (check against implementation):

> Materialize this evaluation's confinement episodes (AD-8).
>
> Before this, ``result.confinements`` was computed on every evaluation
> and then discarded - every consumer (the operational bridge, the
> deviation report, reminder delivery) recomputed it fresh from a
> transient ``EvaluationResult`` instead of reading a persisted row, the
> same way ``PatientEvent`` already is.
>
> Mirrors ``evaluate()``'s own event reconciliation: a DISCHARGED
> episode's actual dates are protected across re-evaluation once
> recorded, found via ``episode_code`` as the stable logical key
> (Step 1's identity discipline - an episode is not per-occurrence
> indexed the way an event is, so there is nothing else to key on).

### `PatientSchedulePersistence._persist_confinements.as_date` — L83–L84

[Read source and line explanations](walkthrough.html#file=35&line=83).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

## 37. backend/app/services/patient_schedule_recording.py

Records patient occurrence/activity facts and validates actual dates.

253 lines. [Open annotated source](walkthrough.html#file=36).

### `PatientScheduleRecording` — L20–L253

[Read source and line explanations](walkthrough.html#file=36&line=20).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleRecording.create_unscheduled_visit` — L21–L75

[Read source and line explanations](walkthrough.html#file=36&line=21).

Source documentation (check against implementation):

> Create one occurrence of a protocol-defined unscheduled visit.
>
> Doc 10 s13-s15. The definition must already exist in the patient's own
> approved schedule and be marked ON_DEMAND: this creates an OCCURRENCE of
> something the protocol allows, never a new protocol requirement, so the
> master schedule and every other patient are untouched.

### `PatientScheduleRecording.record_activity` — L77–L135

[Read source and line explanations](walkthrough.html#file=36&line=77).

Source documentation (check against implementation):

> Record an intra-day actual time or completion decision.
>
> Corrections supersede rather than overwrite, so the execution history a
> reviewer sees stays append-only and auditable.

### `PatientScheduleRecording.record_occurrence_fact` — L137–L228

[Read source and line explanations](walkthrough.html#file=36&line=137).

Source documentation (check against implementation):

> Record a performed / missed / booked fact for one logical occurrence.
>
> PT-02: append-only. A correction names the row it supersedes; that row
> stays as history (its PatientEvent stays protected) and the new value is
> what later evaluations use. PT-04: a booking never sets an actual date
> and never changes the rule. PT-07: nothing here is derived from a date
> passing - a performed fact is only what a person records.

### `PatientScheduleRecording.record_patient_fact` — L230–L253

[Read source and line explanations](walkthrough.html#file=36&line=230).

Source documentation (check against implementation):

> Append one reserved-code WP3 fact (process transition, assignment
> fact, site calendar) to the patient's append-only state history.

## 38. backend/app/services/patient_schedule_evaluation.py

Coordinates SQL-backed evaluation, idempotency, history preservation and recorded actuals.

222 lines. [Open annotated source](walkthrough.html#file=37).

### `PatientScheduleEvaluation` — L20–L222

[Read source and line explanations](walkthrough.html#file=37&line=20).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.

### `PatientScheduleEvaluation.evaluate` — L21–L222

[Read source and line explanations](walkthrough.html#file=37&line=21).

Source documentation (check against implementation):

> Evaluate one patient against a schedule version.
>
> AD-4d: ``schedule_version_id`` lets a caller that already resolved the
> target version (``impact_service.confirm()``, which is in the middle of
> MOVING a patient to a new version) pass it explicitly instead of relying
> on a just-written ``patient.current_schedule_version_id`` being visible
> to a second, independent read inside this method. When omitted, the
> patient's currently pinned version is used -- resolved exactly once,
> here, and threaded through the rest of this call rather than re-read
> later.

### `PatientScheduleEvaluation.evaluate.as_date` — L115–L116

[Read source and line explanations](walkthrough.html#file=37&line=115).

No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.
