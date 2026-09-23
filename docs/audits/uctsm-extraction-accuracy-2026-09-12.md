**Extraction accuracy continuation — 12 September 2026**

The revised source-first candidate is selected in the local backend `.env` for the user's requested testing: `UCTSM_EXTRACTION_PIPELINE=source_first`. The code and example-environment default remain `category`. This is a local test selection, not a claim of production readiness or a change to `UCTSM_AUTHORITATIVE` or per-trial read modes. New uploads use the revised extractor; existing saved schedules are not re-extracted automatically.

**Failures addressed**

| Observed problem | Implemented correction | Evidence and limit |
| --- | --- | --- |
| “7 Patients Evaluated” and “19 Patients Evaluated” became visits | Explicit patient-schedule/trial-monitoring/background source classification; a deterministic population-count guard excludes these labels from the patient inventory and records the exclusion | Replay catches both erroneous visits in the real captured output. Other model classification errors still require evaluation |
| A page-selection instruction still sent the whole PDF to every reconstruction call | Physically subset the PDF and text for each reconstruction packet and each reconciled schedule; include an attached-page → original-physical-page map | Tests inspect packet page counts/order/dimensions and ensure unrelated text is absent for both Gemini and Anthropic transports |
| Referenced material could be omitted without a specific explanation | Follow explicit appendix/annex/table references to document headings, transitively; report missing referenced sources | The real PDF references Appendix B on physical page 19; its heading is absent from this upload. This is a retrieval backstop, not a universal document-reference parser |
| One malformed category could erase valid sibling categories | Retain valid claim arrays, retry missing/malformed category output once, and report only categories still missing. Malformed JSON gets one format repair; transport/quota errors do not trigger that repair | Scripted failures verify retention and bounded retry. Semantic mistakes are not fixed by JSON repair |
| Variable assessment cadence became an unexplained unresolved row | Preserve exact linked timing/visit source statements in the unresolved rule, including page/source IDs; show them in expanded table details | The rule remains unresolved and blocks approval. The code does not choose an unsupported fixed interval or invent patient dates |
| Continuation-table content could be lost or appear as a separate timeline | Retain reconciled visit identity across source tables and verify per-cell coverage | Regression retains ECG and CBC from two source tables on one visit. This verifies the contract, not that every model reconciliation is correct |

Correction to the initial audit description: the two false visits came from the current protocol's **aggregate statistical stopping-boundary table** on physical page 21. They were initially described as historical-study material. Direct page inspection corrected that diagnosis; the guard distinguishes trial-level monitoring from individual-patient visits. Patient-specific discontinuation and safety rules remain eligible schedule context.

The source map and final audit still inspect the whole original document. Reconstruction and schedule compilation receive bounded packets. Original page numbers are retained rather than treating a packet's first page as original page 1. PDF subsetting does not establish that an LLM interpreted a cell correctly; it removes unrelated input and reduces page-number ambiguity.

**Measured local result**

[Deterministic replay](source-grounding-nct00129740.json) of the 32-page protocol and captured candidate output:

- Model calls: **0**.
- Population counts caught as non-visits: **2**, the exact two erroneous labels observed previously.
- Missing referenced source: **Appendix B**, referenced on physical page 19.
- No newly discovered appendix pages were added because Appendix B was not found in this upload.

This is a guard replay, not a fresh extraction and not an accuracy percentage. The earlier live comparison remains **23 calls / 148.16 seconds** for category extraction and **10 calls / 149.67 seconds** for the original candidate. Those measurements do not describe the revised candidate.

Provider access subsequently worked again, without changing billing limits or credentials. The [fresh synthetic smoke run](source-grounding-live-smoke.json) used **8 calls / 49.33 seconds**, retained three visits, and reported no extraction issues. Its approved schedule/date persistence replay passed. The [revised real-protocol run](source-grounding-live-nct00129740-v2.json) used **10 calls / 134.28 seconds**, produced one schedule with five events, and remained blocked. It recovered fixed ECG timepoints, but omitted several recurring assessments and referenced undefined anchors. Its final source audit correctly reported omissions. These results do **not** establish reliable extraction for arbitrary protocols.

**Verification**

Focused backend extraction, ingestion, native import and workflow checks: **118 passed** in the September 12 run, recorded in [the test log](source-grounding-tests.log). Frontend checks: **90 passed**, recorded in [the frontend log](source-grounding-frontend-tests.log); the [scoped TypeScript check](source-grounding-typecheck.log) passed. The preceding UI/date-repair pass also passed the curated 390-test backend gate and six isolated protocol/API tests; those are distinct runs, not additive unique coverage. See the [September 13 continuation](uctsm-extraction-generalization-2026-09-13.md) for subsequent fixes and tests.

Browser verification uses synthetic API fixtures and checks source wording, disabled approval for unresolved timing, table review, activities, evidence and preview-before-save. It does not create real approvals. See [the check script](../../backend/eval/verify_schedule_ui.py) and [browser results](ui-review/checks.json).

**Testing and remaining work**

After restarting the backend with the local environment, upload a protocol again to test this candidate. To compare both extractors directly, run from `backend`:

```powershell
python eval/source_first_compare.py "path-to-protocol.pdf" --compare --output "comparison.json"
```

This command makes paid model calls. The existing deterministic replay can be repeated without model access:

```powershell
python eval/source_grounding_replay.py "../NCT00129740__Prot_SAP_000 (1).pdf" "../docs/audits/source-first-paired-nct.json" --output "../docs/audits/source-grounding-nct00129740.json"
```

Still required before claiming broad accuracy: fresh paired extraction on reviewed protocols, robust handling/evaluation of scanned and complex table layouts, changing or ranged recurrence with explicit scheduling semantics, and resumable long-document processing. Valid JSON, retained inventoried cells, and a clean model audit each check only part of correctness. None certifies arbitrary-protocol support.

The local test choice can be reverted with `UCTSM_EXTRACTION_PIPELINE=category` followed by a backend restart. No existing approved version changes when this setting changes.
