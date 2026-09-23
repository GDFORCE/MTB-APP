**UCTSM extraction comparison and UI delivery — 11 September 2026**

UI verification and handoff updated 12 September 2026. Subsequent extraction corrections are recorded in the [12 September accuracy continuation](uctsm-extraction-accuracy-2026-09-12.md).

The simpler table and one-action review flow are implemented. The replacement extraction orchestrator is implemented as an experimental option, but the measured real-protocol result does **not** justify production activation. `UCTSM_EXTRACTION_PIPELINE` still defaults to `category` in code. On 12 September, the revised candidate was selected explicitly in the local environment for the user's requested testing; see the continuation linked above. No trial read mode or authoritative-mode flag was changed.

The current pipeline's arbitrary-protocol readiness is provisionally **5/10**. This is an engineering assessment, not 50% accuracy. Neither implementation has a sufficiently large, independently annotated benchmark to support an accuracy percentage or a promise to handle every protocol.

**Comparison and decision**

| Approach | What it checks | Main limitation | Decision |
| --- | --- | --- | --- |
| Legacy schedule builder/confirmer | A second model pass reviews generated output | Both passes can omit the same source information; agreement is not proof | Do not rebuild around model agreement |
| Current UCTSM category extraction | Schedule discovery, separate semantic claims, evidence links, typed assembly and domain validation | Many model calls; missing content and table placement are not fully verified by schema validation | Retain as the active path while measuring alternatives |
| New source-first candidate | Source page inventory, reconstructed cells/passages, schedule reconciliation, four groups of semantic claims, source coverage and a final source audit | Inventory and audit remain model readings; real test exposed background-study leakage, source-page/quote problems and unresolved complex recurrence | Keep experimental; do not claim improved accuracy yet |

The intended replacement reads original PDF layout and text, inventories the current protocol's tables and narrative requirements, reconciles continuation tables into participant timelines, and then compiles evidenced rules into the existing UCTSM model. Clinical/admin activities, timing, windows, applicability, footnotes and provenance remain part of the model. The deterministic evaluator continues to calculate patient dates. Rewriting storage and date arithmetic offers no demonstrated extraction benefit.

Multiple source tables still need reconciliation. Two tables do not automatically mean two schedules. Separate schedules require evidence of independent participant assignment; arms/cohorts can instead be applicability within one schedule. Reconciliation is different from generating two speculative complete schedules and treating their agreement as validation.

Provider-enforced JSON structure can improve schema adherence, but it does not establish that visits or dates mean what the source says. Google's [structured-output guidance](https://ai.google.dev/gemini-api/docs/structured-output) explicitly separates syntactic correctness from semantic correctness. Its [PDF documentation](https://ai.google.dev/gemini-api/docs/document-processing) describes visual document understanding; PDF input capability alone is not an accuracy evaluation.

**Measured evidence**

The paired test used the local 32-page `NCT00129740__Prot_SAP_000 (1).pdf`. These are measured application-level model calls and wall-clock durations, excluding Add Trial's separate trial-metadata call, transport retries, and upload time. Counts of events/issues are diagnostic, not accuracy scores.

| Run | Calls | Seconds | Schedules | Events | Reported issues |
| --- | ---: | ---: | ---: | ---: | ---: |
| Category pipeline | 23 | 148.16 | 1 | 7 | 10 |
| Source-first candidate | 10 | 149.67 | 1 | 16 | 46 |
| Source-first synthetic smoke protocol | 8 | 57.05 | 1 | 3 | 0 |

The candidate used fewer calls on the real PDF but was not faster. It incorrectly produced “7 Patients Evaluated” and “19 Patients Evaluated” as visits. Closer source inspection on 12 September established that these are aggregate statistical stopping boundaries on physical page 21, not historical-study visits; the initial description of their origin was incorrect. It also left phase-dependent assessment frequencies unresolved. The final model audit returned no discrepancies, demonstrating why an audit model cannot certify its own inventory. The category result also contained unresolved timing and missing/ambiguous evidence; it is not ground truth.

Artifacts: [paired complete outputs](source-first-paired-nct.json), [successful synthetic extraction](source-first-final-smoke.json), and [blocked scope recheck](source-first-scope-recheck.json). Earlier failed prototype artifacts are retained; the successful smoke result does not erase those failures.

Scope instructions were subsequently tightened at mapping, reconstruction, reconciliation and audit to distinguish current protocol requirements from historical study descriptions and patient counts. **That prompt correction has not been verified live**: its next request failed with Gemini HTTP 429 `RESOURCE_EXHAUSTED`, reporting that the project exceeded its monthly spending cap. No billing limit or API configuration was changed, and no repeated paid retries were made after that error.

Normal single-schedule Add Trial currently makes about **24 model calls**: discovery + 22 category/structure/evidence calls + trial metadata. The candidate's extraction count is `3 + reconstruction_batches + 4 × schedules`, plus one metadata call for Add Trial and any explicit retry. The successful one-batch, one-schedule smoke extraction was eight calls, or nine including metadata. This is not a fixed count for every protocol.

**Delivered user flow**

After extraction, Add Trial shows the schedule before saving and keeps trial metadata behind an expandable link. The phone table has Visit, Timing and Window columns. Expanding a visit shows Clinical and Administrative activity groups, attendance, applicability, status, activity timing, footnotes and source evidence. Scheduled, repeating and conditional visits are separated when relevant. Missing windows say “Not stated”; missing timing remains visibly unresolved. No unstated values are filled to make the table look complete.

The saved schedule uses the same compact table. Review checks remain available but collapsed. A reviewer enters one review note and chooses “Confirm table and approve.” The new API transaction uses existing tenant checks, validation and approval gates, records a bulk confirmation and approval audit, and cannot dismiss source findings. Archived and superseded versions do not offer approval. Existing detailed review/rejection routes remain available.

The domain now permits a protocol-numbered day inside a nominal visit window. This preserves the distinction between Day 1 and an elapsed offset of one day. A Pydantic error-serialization fix prevents rejected model claims from crashing extraction-result serialization.

**Verification and remaining work**

- Curated backend gate: **390 passed** across the handoff's 35 listed files.
- Focused source-first, workflow-reliability and extraction tests: **75 passed**.
- Frontend UCTSM tests: **89 passed**. Scoped TypeScript check passed.
- Isolated protocol upload/API tests: **6 passed**.
- Both saved real-provider synthetic outputs pass import → table approval → patient preview → enrollment/date persistence. Baseline 1 September and last dose 30 September produce visits on **1 September, 29 September and 30 October 2026**; the Week 4 window is **26 September–2 October**.
- Test counts overlap; do not add them together as unique coverage. Unit/integration tests do not establish arbitrary-protocol extraction accuracy.

Browser verification uses the running Expo web application at a 390-pixel phone width and a 1280-pixel desktop width. All API traffic is intercepted with synthetic fixtures; it creates no real trial, patient or approval. It exercises upload → extraction table before save, collapsed metadata, Clinical/Admin expansion, source evidence, review-note gating, a single approval request, and archived-version behavior. Backend persistence and validation are covered separately by the service/API tests above. This is not a physical Expo Go device test. [Reproducible check](../../backend/eval/verify_schedule_ui.py), [check results](ui-review/checks.json), [phone table](ui-review/phone-table.png), [activity groups](ui-review/phone-activities.png), [approval](ui-review/phone-approval.png), [extraction preview](ui-review/phone-extraction-preview.png).

Visual inspection found and corrected a narrow-phone approval-panel overflow and a brief upload-dialog flash after successful extraction. The shared button now exposes its disabled/busy state for assistive technology. Equal-unit visit windows are compacted for readability while preserving asymmetric and zero tolerances.

Before activating the candidate, finish a paired benchmark against independently reviewed expected visits, assessment cells, timing rules, windows, conditions, source references and schedule counts. Include scanned/rotated and multi-page tables, narrative-only schedules, changing recurrence intervals, cycle/day conventions, intra-day sampling, independent cohorts, conflicting footnotes and amendments. Record omissions and unsupported generated content separately; a low issue count cannot substitute for those measurements.

The remaining architectural work is reliable source-scope classification and layout association, faithful representation of changing/ranged recurrence without inventing dates, focused recovery from failed source/semantic batches, and resumable long-document execution. For future events whose anchors or triggering conditions are not known at enrollment, show the dependency and recalculate when it becomes known; baseline alone cannot determine dates the protocol ties to future clinical events.

This work completes the UI simplification and implements a reviewable extraction candidate. It does **not** complete or validate universal-protocol extraction. Live re-evaluation additionally requires restoration of provider capacity.
