# Generalization work, 13 September 2026

Status: roughly **60% implemented, 40% remaining** for the requested complete workflow. This is an engineering estimate, not a measured accuracy score or a claim that 60% of protocols work. The UI, approval and date foundation exists; extraction generalization and independent validation remain substantial work. The revised extractor is selected for local testing only.

## Changes in this continuation

- **Missing anchor recovery:** after semantic extraction, identify anchor codes referenced by rules but never defined. Make at most one source-backed recovery call. Accept only requested codes with usable citations; reject duplicate definitions. An absent, uncited or failed answer leaves `UNKNOWN_ANCHOR` blocking approval. No baseline or patient date is synthesized. This step adds at most one model call per reconciled schedule.
- **Assessment-to-occasion coverage:** reconciliation must place every inventoried assessment on a visit or recurring obligation. A matching header label/code remains supported for existing table representations. Missing links trigger one reconstruction repair. Accept it only if it preserves existing schedules, visit codes, source links and assignment evidence and passes consistency checks. Failed repairs keep the original result and visible blockers. Existing source uncertainties remain open. This adds at most one reconciliation call per document.
- **Evaluation checkpoints:** each model request/response is persisted immediately. `--resume` reuses completed identical requests only, with the same source hash, document context, provider/model and request arguments. Changed prompts or documents make new calls. Calls and reused responses are reported separately. This is evaluation tooling, not resumable production upload processing. The final audit may need another live call because assembled entity UUIDs differ between runs. Checkpoints are not independent repeated accuracy trials.
- **Real corpus runner:** use the source-first pipeline directly across selected documents. The previous corpus runner exercised the older extraction implementation. Results, logs and checkpoints are saved separately for each document; no aggregate accuracy percentage is inferred from event counts or blockers.
- **Adaptive source packets:** malformed/truncated multi-page source responses are retried as smaller packets, down to one page. The split tree is bounded; successfully read neighbors are retained. Transport/quota failures do not trigger splitting. An unreadable final page remains a visible failure. This handles output-shape failures, not silent model omissions or every dense single-page table.
- **Table-local footnote symbols:** a reused symbol is compared within its explicitly cited table legend. Different page numbers alone never establish different scopes. Unknown/global scope and conflicting definitions in the same table remain flagged. Revalidating the captured hourly result reduced marker conflicts from two to one; the remaining conflict was not automatically dismissed.
- **Overview/detail links:** explicit source-to-occasion links take precedence over broad overview labels. Prompts require citations to both overview and detailed assessment cells, and ask for neutral time-axis origin labels when the source does not establish a named clinical event. These prompt changes have not yet been validated by another fresh extraction.

## Verification

The latest focused extraction, ingestion, native import, workflow, footnote and checkpoint run passed **163 tests**: [test log](source-generalization-tests.log). This includes changed model/document/context invalidation, source-packet splitting, shared packet counters, failure handling and explicit opt-in reuse. These tests verify program behavior, not LLM accuracy.

After all pipeline changes in this pass, a [fresh synthetic extraction](source-generalization-live-smoke.json) completed in **8 calls / 64.14 seconds**, returning one schedule with three visits and no extraction issues. A separate saved-response replay through native import, table approval, patient enrollment and date persistence passed alongside the three previous captures: [4 passing workflow replays](source-generalization-live-workflow.log). The new result preserves September 1 / September 29 / October 30 visit dates with the supplied first/last-dose anchors and the stated windows. These are direct extraction/service checks using the synthetic protocol and an isolated test database, not a physical-phone upload test or proof of arbitrary-protocol accuracy.

The full curated backend gate initially returned 393 passes and two date-sensitive confinement-test failures when the actual date reached the fixture's planned discharge. The tests now specify their evaluation dates: an admitted patient is in confinement before planned discharge; after planned discharge, the episode remains open as `DISCHARGE_PLANNED` until an actual discharge is recorded. Production confinement behavior was unchanged. The rerun passed **395 tests**, recorded in [the backend gate log](source-generalization-backend-gate.log). This gate overlaps the focused run; the counts should not be added as unique tests.

The preceding real PDF result still had major omissions: [one schedule, five events, 10 calls](source-grounding-live-nct00129740-v2.json). Its source audit identified missing ongoing assessments, and three event rules referenced an absent `first_dose` anchor. That result motivated general fixes; it is not evidence that the new fixes have corrected this protocol yet.

## Independent source checks for the initial broader sample

The local corpus contains 62 numbered documents. This initial sample is deliberately different from the prior narrative CML protocol. Source facts below were read from the PDFs before inspecting their new extraction outputs; they are partial acceptance checks, not complete gold annotations.

| Corpus document | Pattern | Source facts to verify |
| --- | --- | --- |
| 2, SPIL Ipratropium | Three-period crossover, table continuation | Three pages extend one participant schedule. Preserve Period 1/2/3 identities; do not merge their identically named Day 1 columns. Informed consent is marked only at screening. Vital signs span screening and each period's check-in, Day 1 and checkout/EOS. Footnote definitions are absent in this excerpt; do not guess washout duration or screening signs from damaged text. |
| 29, Fever Synopsis | Hourly sampling, overview plus detailed treatment table | One participant schedule. Overview screening is Hour -4 to 0; treatment detail runs Hour 0 to 24, including half-hour timepoints. Preserve Hour 26, 48 and 72. Temperature has no mark at Hour 26; the safety lab does. Pregnancy-test applicability stays on that activity. |
| 30, GCP checklist | Negative control | An inspection checklist does not establish a patient visit schedule. Return no schedule with a clear explanation; do not turn checklist rows into patient visits. |

Outputs are written under [corpus-2026-09-13](corpus-2026-09-13/). The first crossover attempt was interrupted during source reconstruction and did not produce a completed result; it must not be counted as a pass or accuracy failure. The subsequent sample completed before the later packet/legend/overview fixes:

| Document | Fresh calls / seconds | Observed result and limits |
| --- | --- | --- |
| 30, checklist | 1 / 11.25 | No schedule, with an explicit inspection-checklist explanation. This negative control behaved correctly. |
| 2, crossover | 9 / 177.12 | One schedule, 12 events; distinct Period 1/2/3 encounters and washout periods retained. Missing source definitions, timing and quote checks still block approval. |
| 29, hourly | 9 / 200.31 | One schedule, 27 events. Hour 0.5 remains 30 minutes; Hour 26 carries safety labs without temperature; Hour 20 has no marked discrete assessments in the source and none were invented; Hour 48/72 retain temperature, vital signs and transfusion monitoring. There are 27 unknown-anchor findings, five overview-cell coverage findings and quote/qualifier issues. |

These are partial source checks, not complete gold scoring. Both protocol results remain unsuitable for approval. The recovery call returned no anchors in these two excerpts; it did not invent a reference date to remove the blockers. Their model audits reported no discrepancies despite unresolved coverage, further demonstrating why the final LLM audit cannot certify correctness. No claim is made that all 62 documents have been evaluated with this candidate.

Run from `backend` (paid calls):

```powershell
python eval/source_first_corpus.py 30 2 29 --output-dir ../docs/audits/corpus-2026-09-13 --resume
```

## Still required

Complete and review the broader live sample; fix discovered failures by pattern; expand to cyclic, multi-arm, conditional, calendar-month and scanned inputs; evaluate source-to-cell coverage and date correctness against independent expected results. Variable intervals and physician-discretion schedules need an explicit scheduling policy or recorded event, because the source alone may not determine a unique patient date. Missing pages must remain visible. Do not claim every protocol can produce fully determined dates from a baseline alone.

For one participant schedule with `B` successful reconstruction packets, the ordinary path uses `7 + B` application model calls: source map, `B` reconstruction calls, reconciliation, four semantic groups, and source audit. Format repairs, packet splitting, missing-assessment repair or missing-anchor recovery add bounded calls. Multiple independently assigned schedules each need their own semantic compilation. The Add Trial metadata call, if enabled by that upload path, is separate from these evaluation counts. A negative document can stop after the source map.

Local test connection was corrected to `http://192.168.1.2:8000` after the Wi-Fi address changed. This is a local environment change, not a deployed endpoint.
