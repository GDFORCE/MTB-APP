# Adaptive Protocol Brain: product requirements

Version: 1.0, 30 September 2026. Status: implementation specification.
Companion: [technical design](adaptive-protocol-brain-design.md).
Implementation instructions: [Claude Code prompt](adaptive-protocol-brain-claude-prompt.md).

## 1. Product objective

After a user uploads a clinical-trial protocol, MTB must interpret its scheduling
requirements and produce a readable, editable, evidence-backed Visit Schedule.
An authorized user reviews and approves the draft. The approved model generates
patient schedules as assignments, anchors, observations and treatment events become
known. The model must support new combinations of rules without trial-specific
code branches.

The user wants a reusable reasoning system. A growing catalogue of handlers named
after individual trials, document titles or example visit names does not satisfy
this requirement. Examples below are acceptance probes for general operators.

"Adaptive" means constructing a versioned executable trial model from source
evidence, revising candidate interpretations during validation, and reevaluating
patient obligations when recorded facts change. It does not authorize inventing
clinical instructions, automatic production code generation, or changing approved
rules through unreviewed learning.

## 2. Scope and completion boundary

The initial implementation covers document ingestion, interpretation, evidence,
source coverage, rule composition, deterministic scheduling, draft review,
approval, patient dates, recalculation, export and existing operational projection.

It must represent intervention-specific hold/stop/resume decisions and consume
externally authorized dose/assignment decisions. Automated dose calculation,
statistical adaptation/randomization algorithms and new push delivery infrastructure
are separate future capabilities. Their source statements must remain visible and
classified, with a named dependency where they affect scheduling. Do not label a
trial fully automated when one of its required capabilities remains unsupported.

No promise of perfect interpretation for arbitrary documents is a release
criterion. Honest coverage and explicit unsupported behavior are required.

## 3. User journeys

### J1: Upload to draft

1. An authorized trial user uploads a protocol using the existing trial workflow.
2. MTB reports meaningful progress: reading, interpreting, checking, preparing draft.
3. MTB returns a draft Visit Schedule, source coverage summary and review questions.
4. Failures preserve recoverable work and show the reason and next action.
5. A retry reuses compatible completed work and does not duplicate drafts.

### J2: Review to approval

1. The reviewer filters All / Pending / Ready and expands a row.
2. The reviewer sees activities, timing meaning, applicable groups and exact sources.
3. The reviewer resolves a shared question or edits a semantic rule.
4. MTB revalidates and shows the resulting changes.
5. Approval creates an immutable executable version only when server-side gates pass.

### J3: Enrollment to patient calendar

1. The site selects a permitted approved version and records known assignments.
2. Dates are explicitly planned or actual; unknown future events remain unknown.
3. MTB shows dated obligations and clearly named waiting states.
4. Recording an actual event updates dependent future obligations and reminders.
5. Completed history is preserved; corrections remain auditable.

### J4: Amendment

A new document/version produces a new draft and semantic diff. Approval does not
silently migrate enrolled patients. Existing impact-preview and confirmation
mechanisms govern explicit reassignment and reconciliation.

## 4. Functional requirements

### Ingestion, coverage and interpretation

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| IN-01 | Use existing supported upload formats and validation; inventory all pages and supplied attachments. | Rejected inputs produce actionable errors; accepted inputs retain original page identity and document hash. |
| IN-02 | Discover requirements across tables, prose, footnotes, figures and referenced appendices. | A restriction outside the SoA is retained and linked to the relevant activity. |
| IN-03 | Track source coverage, including unreadable, missing and excluded material with rationale. | A page budget limit or missing required appendix cannot produce a misleading complete result. |
| IN-04 | Store resumable extraction stages and versioned artifacts. | Retry after a stage failure reuses compatible completed artifacts and does not repeat successful remote work unnecessarily. |
| IN-05 | Preserve invalid model items and unknown semantic fields as inspectable issues. | A field carrying an extra restriction cannot disappear through permissive parsing. |
| IN-06 | Treat protocol text as source data. | Embedded instructions to ignore validation or run tools cannot change application behavior. |
| IR-01 | Produce atomic cited requirements before executable rules. | Each compiled semantic field links to source evidence or an explicitly recorded authorized interpretation. |
| IR-02 | Resolve entities using scope and occurrence context. | Two similarly named scans in different phases remain distinct; duplicate mentions of one requirement link together. |
| IR-03 | Reconcile statements globally across batches. | Splitting conflicting or complementary statements between batches does not hide the relationship. |
| IR-04 | Distinguish conjunction, alternatives, duplicates, exceptions, supersession and contradiction. | Two compatible restrictions compose; conflicting instructions remain unresolved. |
| IR-05 | Record a disposition for every discovered requirement. | A compiled primary statement cannot conceal an uncompiled additional statement about the same activity. |
| IR-06 | Bound model repair/retrieval loops and preserve uncertainty at exhaustion. | The result is actionable incomplete work rather than invented semantics or endless retries. |

### Executable rule language and evaluator

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| RL-01 | Use typed composable operators with explicit versions and capability declarations. | A new protocol composed entirely of supported operators needs no title/name-based execution branch. |
| RL-02 | Represent activities separately from display visits and occurrence instances. | One visit can contain independently timed activities; recurrent templates retain distinct occurrences. |
| RL-03 | Preserve units, clinical day numbering, date precision and reference events. | Calendar months remain months; missing Day 1 meaning or required precision produces a named issue. |
| RL-04 | Evaluate conjunctions of constraints across multiple anchors. | Feasible bounds reflect every applicable restriction, including a less restrictive one retained for audit. |
| RL-05 | Represent alternative feasible sets and actual decision branches distinctly. | Disjoint alternatives stay disjoint; the evaluator does not choose an arm or treatment branch. |
| RL-06 | Support nested predicates with TRUE/FALSE/UNKNOWN semantics. | Missing patient information cannot silently become a negative applicability decision. |
| RL-07 | Support intervals, weekdays, cycle days, phased recurrence and event/condition termination. | Combined recurrence/calendar/condition scenarios retain count and boundary semantics. |
| RL-08 | Keep count basis and missed-occurrence policy explicit. | "30 slots" and "30 delivered fractions" cannot silently become the same rule. |
| RL-09 | Make dependency, hold/resume, holiday and phase-boundary policies explicit when material. | Missing policies block only the interpretation or execution that depends on them; none is guessed. |
| RL-10 | Support patient, intervention/process, site and trial scopes. | Trial accrual suspension never becomes a patient visit unless an explicit linked rule requires one. |
| RL-11 | Maintain independently scoped process states and dated assignment history. | Stopping one drug can preserve another intervention and follow-up; a crossover does not rewrite earlier assignments. |
| RL-12 | Identify unsupported operators before approval. | Unsupported rules list their evidence and affected dependents; acknowledgement alone cannot make them executable. |
| RL-13 | Detect unresolved dependencies, invalid references and infeasible constraints. | Return involved rule IDs and missing inputs or conflicting bounds without fabricating a date. |
| RL-14 | Bound materialization of open-ended rules using an explicit horizon. | Horizon extension appends stable occurrences without implying treatment or follow-up has ended. |

### Patient facts, persistence and operational behavior

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| PT-01 | Keep planned and actual anchors distinct through UI, API, storage and evaluation. | Planned treatment start cannot satisfy an actual-only downstream requirement. |
| PT-02 | Preserve occurrence identity, effective time, recording time and correction history. | Late-entered facts and corrections can be replayed deterministically. |
| PT-03 | Recompute dependent future obligations on confirmed relevant changes. | Independent obligations remain unaffected; historical actuals stay intact. |
| PT-04 | Separate protocol target, feasible dates, booked appointment and performed time. | A booked slot outside the permitted set is flagged and never changes the rule to make itself compliant. |
| PT-05 | Preserve immutable approved versions and explicit patient migration. | Existing patients do not change protocol version when a new version is approved. |
| PT-06 | Reconcile projections and notification intents idempotently. | Retrying a job creates no duplicate visits/reminders; moved or cancelled obligations supersede stale intents. |
| PT-07 | Never treat a time passing as proof an event occurred. | A past planned dose or discharge remains unconfirmed until its actual fact exists. |

### Required Visit Schedule screen

Use the supplied reference's structure: mobile title/header, extraction badge,
counts, expandable explanation, filter chips, rounded table, Add Visit and approval
action. Reuse the app's theme and components. Do not copy apparent OCR mistakes,
unverified numbers or medical interpretations from the reference screenshot.

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| UI-01 | Upload leads to the draft Visit Schedule in the active product workflow. | A working journey demonstrates upload through draft persistence and display. |
| UI-02 | Show row status, visit name, Timing and Window. | Timing can show relative events, calendar units, recurrence and conditional activation without flattening everything to day offsets. |
| UI-03 | Expand rows for assessments, scope, anchors, conditions, full constraints, evidence and review questions. | All material restrictions represented by the row are accessible, including those omitted from its compact summary. |
| UI-04 | Provide All / Pending / Ready filters with well-defined counts. | Row counts remain distinct from issue counts and patient occurrence totals; document-level issues remain visible under every filter. |
| UI-05 | Distinguish Not stated, Needs review and Not applicable. | An absent window is not displayed as a zero-day window; an unresolved window is not treated as absent. |
| UI-06 | Resolve shared questions once with affected-row links. | A shared anchor conflict does not require separately editing every dependent visit. |
| UI-07 | Edit and Add Visit modify versioned semantic rules and revalidate. | A label change cannot silently rewrite timing; new manual obligations have recorded origin, rationale and review state. |
| UI-08 | Approval status comes from the backend. | Hiding a warning or bypassing a disabled button cannot approve an invalid version. |
| UI-09 | Export the reviewed information with provenance and status. | Export includes timing, constraints, evidence references and draft/approved label; it cannot silently discard details that do not fit a flat row. |
| UI-10 | Distinguish draft protocol timing from patient-specific dates. | No patient dates or occurrence-completion claims are invented during protocol upload. |
| UI-11 | Work on narrow screens and with assistive technology. | Labels wrap, expandable rows announce state, controls have accessible labels, and color is not the only review indicator. |
| UI-12 | Surface recovery paths for partial extraction and canonical build failures. | Failures show a reason and retry/resume action instead of silently redirecting into a legacy editor. |

### Review, evidence and approval

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| RV-01 | Material missing evidence or uncompiled restrictions block affected approval. | A warning acknowledgment does not erase an unenforced requirement. |
| RV-02 | Separate model interpretation from authorized human decisions. | Both preserve author/source, scope, rationale and version; unsupported execution remains blocked. |
| RV-03 | Missing future patient anchors are normal runtime waiting when rule meaning is complete. | A valid definition can be approved before a patient begins treatment. |
| RV-04 | Initially use whole-version approval. | Partial activation cannot bypass unresolved dependency effects; it requires a separately proven design. |
| RV-05 | Reject stale reviews and approval attempts. | Concurrent edits force revalidation against the new draft revision. |

## 5. Nonfunctional requirements

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| NF-01 | Deterministic execution without model calls. | Identical pinned rules, facts, policies, as-of time and horizon yield equivalent semantic output. |
| NF-02 | Reproducible model-assisted interpretation. | Record model/provider identifier, prompt/schema/compiler versions, document hash, calls and artifacts; do not promise identical model output on rerun. |
| NF-03 | Explainable outcomes. | Each date/status exposes its relevant source rules, input facts and calculation or blocking reason. |
| NF-04 | Bounded resources and visible partial outcomes. | Configurable page/call/time/occurrence limits stop or checkpoint without claiming completeness. |
| NF-05 | Recoverable persistence. | Worker retry and SQL/Mongo partial-write scenarios converge without duplicate or lost obligations. |
| NF-06 | Existing authorization and tenant isolation. | Trial documents, draft artifacts, exports and patient events enforce current access rules on the server. |
| NF-07 | Sensitive-data hygiene. | Patient facts are unnecessary for protocol extraction; secrets and full sensitive documents are excluded from ordinary diagnostic logs. |
| NF-08 | Compatible evolution. | Existing approved schedules retain their prior meaning through version-aware loading and migration tests. |
| NF-09 | Measured performance and quality. | Report stage duration, model usage, unresolved/unsupported counts and critical missed/incorrect obligations by rule family; thresholds are explicitly set before deployment evaluation. |

No unmeasured latency, extraction accuracy or universal-coverage percentage may
be presented as achieved. Configure technical limits explicitly; clinical policy
values require source evidence or authorized interpretation.

## 6. Acceptance probes for generalization

These probes verify shared semantics. They must not become trial-specific branches.
Use synthetic source excerpts for deterministic offline tests. Real-protocol
accuracy requires the original document and independently reviewed answer keys.

| ID | Probe | Required outcome |
| --- | --- | --- |
| AT-01 | One scan has a range from anchor A and a minimum delay from anchor B. | Intersect bounds; show incomplete for missing B and infeasible for an empty intersection. |
| AT-02 | The same restriction is duplicated, then replaced with a complementary restriction. | Deduplicate only the duplicate; preserve and enforce the complementary constraint. |
| AT-03 | Two sections state incompatible QoL anchors. | Preserve both sources and block interpretation; do not pick the most convenient date. |
| AT-04 | One valid statement and one unsupported additional restriction describe an activity. | Return a capability gap and prevent false readiness. |
| AT-05 | Weekday recurrence with a Friday start, missed session and a holiday. | Apply only explicitly configured count, holiday and replacement policies; preserve session identity. |
| AT-06 | A cycle is delayed during a hold. | Different approved dependency/resume policies yield their specified distinct results. |
| AT-07 | One intervention stops while another and follow-up continue. | Only explicitly targeted future obligations change. |
| AT-08 | A planned start is recorded, then an actual start, then a correction. | Actual-only rules wait, then resolve; corrections preserve performed history and update future impact. |
| AT-09 | Follow-up frequency changes at a boundary and ends in an open-ended phase. | No duplicate/lost boundary occurrence; extending horizon preserves existing identities. |
| AT-10 | A condition recurs after resolution. | A new episode has distinct occurrence identity and does not overwrite the first episode. |
| AT-11 | A crossover assignment is entered late with an earlier effective date. | Evaluate the affected period using effective history; preserve recorded-time audit. |
| AT-12 | Batch order, formatting or duplicate delivery changes. | Semantically equivalent facts yield equivalent normalized rules and no duplicate runtime effects. |
| AT-13 | The SoA is readable but a referenced appendix is missing. | Draft can be inspected; required coverage remains incomplete and approval stays blocked. |
| AT-14 | A schema-valid model answer has a fabricated or mismatched quotation. | Evidence validation blocks material use until source verification. |
| AT-15 | A draft is edited between review and approval. | Stale approval is rejected without partial activation. |
| AT-16 | SQL commit succeeds and Mongo projection fails. | Durable retry reconciles using stable keys; no second clinical decision or duplicate occurrence is created. |
| AT-17 | An unfamiliar combination uses existing operators with new names. | Compile and evaluate without adding name-based rules. |
| AT-18 | A patient result permits two disjoint time intervals. | Display/export preserve both; no slot in the excluded gap becomes allowed. |
| AT-19 | A month-based recurrence crosses month-end or a time-based activity crosses DST. | Apply explicit versioned calendar/time semantics; absent material policy/precision remains visible. |
| AT-20 | A malformed item, budget exhaustion or model timeout interrupts extraction. | Preserve artifacts, flag coverage and resume safely; no empty-success schedule. |

## 7. Definition of done

Implementation is complete only when the active upload-to-review-to-patient journey
works with persisted data, all in-scope requirements are traced to implementation
and checks, and relevant regression gates pass. A schema, mock screen or collection
of domain unit tests alone does not satisfy completion.

The implementing agent must maintain a traceability report with requirement ID,
code location, verification evidence, status and limitation. Use IMPLEMENTED,
VERIFIED, PARTIAL, BLOCKED or OUT_OF_SCOPE accurately; file existence is not proof
of behavior. Distinguish offline fixture success from real-document extraction
quality and from production readiness.

Preserve current user work and repository safety boundaries. Do not enable
`DEV_OTP_MODE` outside local development, flip `UCTSM_AUTHORITATIVE`, or perform a
trial read-mode cutover merely to demonstrate the new feature. Any later cutover
must use the existing per-trial mechanism with its required parity evidence.

## 8. Materials available and unresolved product inputs

Available: repository, the design documents, the user's Visit Schedule reference
image in the conversation, and a pasted prior assessment of a sample trial.
The original sample protocol PDF and an independently validated answer key were
not supplied with this request. Do not treat the assessment's dates or section
claims as authoritative source evidence.

If the implementing agent cannot see the image, the screen requirements above are
sufficient for functional implementation using existing MTB styling. A real
extraction-quality assessment remains pending until the original protocol is
available. Continue offline implementation and verification in the meantime.
