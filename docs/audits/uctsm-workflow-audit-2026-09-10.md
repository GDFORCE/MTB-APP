# UCTSM workflow audit and repair — 10 September 2026

## Status

For the subsequent table simplification, one-action approval, real-PDF comparison and experimental source-first decision, see the [11–12 September continuation](uctsm-source-first-decision-2026-09-11.md). The findings below describe the earlier repair pass.

The identified workflow repairs are implemented and automated checks pass. This is a repair of the existing extraction and scheduling architecture, not a completed replacement extractor or proof of accuracy on arbitrary protocols. No production deployment, patient migration, global authoritative switch, or per-trial read-mode change was performed.

The working tree already contained substantial changes when this continuation started. This report describes the workflow fixes verified in this session; it does not attribute the entire Git diff to this work.

| Requested step | Current result | Verification limit |
| --- | --- | --- |
| Upload PDF or text | Native upload handles PDF and UTF-8 text, retains the source, and returns draft table projections. Trial metadata uses the same configured native provider. | PDF/text API tests use a stubbed provider. Live provider run used synthetic text. |
| Read extraction before saving | Add Trial and the existing trial's schedule-version screen show visit tables, activities, applicability, timing, windows, status, issues, and source passages before saving drafts. | Typechecked and presentation-tested; no interactive browser/device walkthrough performed. |
| Review and approve | Table review supports field confirmation and explicit, audited resolution of extraction findings. Open blockers survive revalidation. Rejected drafts can be resubmitted. | Service and workflow tests exercise approval gates. |
| Add patients after approval | Canonical-linked enrollment validates the selected current, effective approved schedule and assignment. UI exposes missing schedules instead of disappearing. | The existing Add Patient product flow sends an invitation; actual creation/enrollment follows acceptance. |
| Calculate dates | Preview and enrollment use the same evaluator, schedule version, baseline, additional anchors, and assignment. Preview shows windows, waiting reasons, and its horizon. | Missing future anchors stay undated. Repeats can continue beyond the materialization horizon. |

## What the pipeline actually does

Locally, `UCTSM_NATIVE_ADD_TRIAL=true` selects the native extractor and `UCTSM_EXTRACTION_PROVIDER=gemini` selects its model transport. These settings were inspected, not changed. The native flag still defaults off in code, so other deployments must be checked individually.

The active flow is:

1. `/protocols/extract` stores the upload, reads pages, extracts trial metadata, and detects independent schedule options.
2. `app/extraction/graph.py` runs separate semantic extraction stages for events, anchors, activities, timing, windows, recurrence, conditions, applicability, evidence, and completeness. Later calls now receive an identifier registry from earlier stages.
3. The assembler validates the proposed objects against UCTSM. The response includes draft schedule projections and findings for the frontend table.
4. Explicit saving consumes prepared extractions and imports canonical draft versions. Approval uses field review plus deterministic validation.
5. Patient preview and operational enrollment resolve the approved version and use deterministic date evaluation.

Two extraction implementations and two scheduling stores still exist. These are separate from the bug where an LLM interprets two printed continuation tables as two schedules. Mongo holds operational records; Postgres holds canonical schedules and conditions. The existing parity-gated read-mode mechanism remains intact. No global cutover was justified by the synthetic smoke test.

## Breaks found and repairs

| Break | Why it failed | Repair location and behavior |
| --- | --- | --- |
| Continuation tables became separate schedules | Early option discovery encouraged phase/table separation before participant timelines were reconciled. | `app/extraction/prompts.py`: table count is explicitly separate from schedule count; screening, treatment, follow-up, and continuations remain together unless the source establishes independent assignment. This reduces the failure; it does not constitute a structural grouping algorithm. |
| Plausible timing JSON became unresolved | Handwritten prompt examples disagreed with the actual schema: nominal windows, cycle fields, deadlines, and condition operands. | Prompt schemas now come from the actual Pydantic domain unions. Cycle/day conventions are stated explicitly. |
| Different stages lost visit identity | Independent calls invented identifiers; fuzzy matching could conflate numeric visit labels. | `claude_provider.py`: shared compact registry; ordered visit numbers must match; uncertain mappings become review findings. |
| Extraction failures appeared complete | Discovery errors, missing JSON lists, or a failed schedule option could look like no findings or disappear. | Strict response parsing; explicit failed-option results; surviving schedules carry blockers; persisted multi-option runs can be PARTIAL. |
| Validation removed extraction blockers | Revalidation recomputed structural findings without retaining source omissions. | Extraction-origin findings survive in `validator.py`; source resolution requires a reason and review/audit records. Failed discovery/options require re-extraction. |
| Large inputs silently lost pages | The document prefix limit could omit later tables or footnotes. | `runner.py` rejects oversized input explicitly. Automated section retrieval remains future work. |
| Source links were incomplete or untrustworthy | Timing claim evidence was absent from row projections; cited text was not consistently checked. | `projection.py` includes claim evidence; textual quotes are checked against cited page text; mismatches block approval pending review. Image-page evidence still needs visual review. |
| Repeated visits started at the wrong point | Recurrence anchor alone lost a delayed first visit's explicit offset. | Optional `RecurrenceRule.start_offset`, extraction/import derivation, and evaluator support preserve the first date and subsequent windows. Existing rules without this field retain their behavior. |
| Draft results were invisible before saving | Upload returned identifiers/counts, with useful schedule data only available later. | `ExtractionPreview.tsx`, Add Trial, and `ProtocolUploadReview.tsx` render the extracted table before draft saving. |
| Canonical build failure led to confusing fallback/retry | Failure could leave the user in a legacy editor or duplicate trial creation on retry. | Add Trial surfaces the failure and reuses its created trial ID. Existing trials have an upload/re-extraction entry point. |
| Recovery used the wrong source batch | Canonical native import could select the earliest upload instead of the latest uploaded batch. | `server.py` groups the latest native definitions by the uploaded storage key. Replacement approval can recover an unapproved protocol; it does not automatically activate an amendment over an approved protocol. |
| Patient preview disagreed with enrollment | Legacy preview computed dates separately from canonical approval and assignment. | `enrolment_preview.py`, `server.py`, and `operational_bridge.py` share canonical version resolution, anchor inputs, and evaluation. Stale versions, foreign trials, unknown anchors, and invalid dimension relationships are rejected. |
| Enrollment looked ready without a schedule | Assignment fields silently disappeared or had permissive initial state. | `ProtocolAssignmentFields.tsx` exposes loading/errors/readiness, requires an effective approved schedule, and permits explicit selection between independent schedules. |
| Dates required manual interpretation | Baseline had a sample default; extra anchor inputs and explanatory waiting states were missing. | Add Patient starts with an empty baseline, accepts known protocol anchors, automatically refreshes preview, rejects stale responses, and shows dates/windows or reasons. |

## Verification evidence

- Curated handoff backend gate: **390 passed**, across 35 files (the handoff describes it as a 34-file gate).
- Focused extraction/import/ingestion/operational workflow group: **90 passed**. Two additional recovery cases were then added; the final reliability module alone passed **18 tests**. These runs overlap and should not be added as unique totals.
- Isolated protocol creation/upload API module: **6 passed**, including native PDF and text uploads.
- Frontend UCTSM suite: **88 passed**.
- Scoped frontend TypeScript check: `node node_modules/typescript/bin/tsc --project tsconfig.uctsm-workflow.json --pretty false`.
- `git diff --check` passed; Git reports existing LF/CRLF conversion warnings. Backend/frontend checks report dependency deprecation/module-format warnings.

One live provider extraction was completed against [the synthetic two-table protocol](../../backend/eval/workflow-smoke-protocol.txt). It returned **one schedule, three visits, and zero extraction findings**. The [captured response](uctsm-live-smoke.json) is replayed through normal import, field confirmation, approval, preview, and enrollment in `test_uctsm_workflow_reliability.py`.

With baseline 1 September 2026 and last dose 30 September 2026, that replay verifies:

| Visit | Expected date | Additional assertion |
| --- | --- | --- |
| Cycle 1 Day 1 | 1 September | Vitals and ECG retained |
| Week 4 / Day 29 | 29 September | Window 26 September–2 October; Vitals and ECG retained |
| Follow-up | 30 October | Last-dose anchor used; adverse event review retained |

Preview and persisted enrollment dates match. This proves this concrete case and the tested invariants, not general extraction accuracy. The live smoke command is `python eval/workflow_smoke.py --live` from `backend/`; it makes real model calls and overwrites the captured artifact, so normal regression tests replay the saved response instead.

## Recommended next architecture

Keep the UCTSM domain, approval mechanism, deterministic evaluator, and table components. Replace extraction orchestration incrementally with a source-table-first process:

1. Reconstruct a source inventory: page/table/cell coordinates, merged headers, continuation relationships, footnotes, and referenced prose. Preserve page images where text extraction loses layout.
2. Create one document-wide visit/activity inventory before semantic extraction. Assign stable source-linked identifiers and reconcile overlapping tables explicitly.
3. Resolve whether tables belong to one participant timeline or independently assignable schedules. Present uncertain grouping as a review decision with the relevant tables visible.
4. Ask the LLM to interpret focused source regions into domain-generated schemas. Separate source assertions from derived timing rules. Retry specific unresolved claims using their local context.
5. Check coverage in both directions: each source visit/activity mark/footnote must map to a retained rule or explicit unresolved finding; each rule must cite source evidence. Detect duplicates and contradictory timing across sections.
6. Show the reconciled table and unresolved decisions, approve an immutable version, and calculate dates exclusively in the evaluator.

The current repairs implement schema alignment, shared identifiers, stronger failure handling, and the workflow surfaces. They **do not yet implement** cell reconstruction, a complete source-coverage ledger, deterministic table reconciliation, or focused retry orchestration.

## Work left before claiming broad reliability

1. Build a representative, human-reviewed protocol corpus and measure visit/activity/footnote coverage, grouping errors, anchor correctness, and exact dates. Include continuation tables, genuinely independent schedules, multiple within-cycle days, arm/cohort hierarchies, amendments, scans, conditional assessments, and event-relative follow-up.
2. Implement the source inventory and reconciliation process above, prioritizing the real failure cases revealed by that corpus.
3. Add resumable background jobs and automatic retrieval of referenced sections for long protocols. The live Add Trial endpoint still waits for a multi-stage extraction; an extended timeout is not durable job orchestration.
4. Perform browser and device acceptance runs covering upload, draft review, source corrections, approval, invitation acceptance, and patient schedule display. Review actual deployed flags and run per-trial parity before any read cutover.
5. Validate amendment activation and existing-patient impact through the explicit lifecycle. This repair only automatically recovers an initially unapproved protocol.

“Every visit date” must respect the protocol's semantics: an unknown last-dose/progression date cannot yield a truthful final follow-up date; a permitted range is not necessarily a nominal appointment; an indefinite repeat has no finite last row. The product should calculate every determinable date, clearly name missing inputs, and extend repeating schedules automatically as their horizon advances.
