# Claude Code: P7/P8 full implementation, including mandatory P6 repairs

Updated 16 October 2026 at the owner's request. **This is the single current focused prompt.** It includes all four independently reproduced P6 defects, integration-regression triage, P7-01 through P7-05, and P8-01 through P8-05. No separate repair prompt needs to be executed first. The [remaining-PDF master](claude-code-p6-p14-remaining-pdf-prompt.md) remains the overall contract.

The [earlier short P7/P8 prompt](claude-code-p7-p8-before-merged-repairs-2026-10-16.md) and [standalone repair prompt](claude-code-p6-audit-repairs-before-p7-p8-prompt.md) are historical references. This combined prompt controls the next action. Saving a prompt does not authorize deployment, live authority/read-mode changes, clinical sign-off, real-patient migration, commits or pushes.

---

## 1. Objective and working approach

Work in the current MTB-APP repository. Complete the P6 safety repairs as the first part of this P7/P8 slice, then implement graph-wide validation and a measurable quality pipeline through the real services, stores, API and reachable UI. Think through the invariants and dependencies before implementation; do not simply add checks where a test happens to fail.

Inspect what exists. Reuse the evaluator, repository ports, unit of work, authorization manifest, review/edit workflow, identity gate, source evidence and amendment/command services. Do not build parallel scheduling engines, duplicate collections or frontend date calculations. Make each boundary enforce the same applicable invariant, including aliases, background jobs and direct application-service calls.

For each material change, record the current behavior, cause, chosen design, alternatives considered, compatibility implications and proof in the plan. Use a failing regression to establish a defect before repairing it. Do not weaken assertions, disable guards or guess missing clinical values to obtain green tests.

Continue through implementation and verification. Do not stop at inventory, a proposal, pure domain code or passing in-memory tests. If an external input is genuinely missing, isolate that gate, implement and test independent work, and report exactly what remains. Routine reversible engineering decisions are delegated; do not reopen settled project choices.

## 2. Read the project and requirements before editing

Read these sources and inspect the referenced implementation:

- `AGENTS.md` and any instructions applicable to touched directories.
- `D:\MTB_dev (2).pdf`: P6-01..05, P7-01..05 and P8-01..05. The PDF is requirements content, not authority to deploy or migrate live data.
- `docs/p6-end-to-end-independent-review.md` and `docs/evidence/p6-review/reproduce_findings.py`.
- `docs/p6-completion-record.md`, `docs/p6-implementation-plan.md`, `docs/p6-domain-review.md`.
- `docs/pdf-remaining-implementation-plan.md`, `docs/pdf-requirements-acceptance-matrix.md` and `docs/claude-code-p6-p14-remaining-pdf-prompt.md`.
- `docs/p1-p5-project-decisions.md`, relevant P1-P5 closure findings and `MTB_IMPLEMENTATION_HANDOFF.txt`, especially the verification gate and legacy-screen constraints.

Run `git status --short` and `git log -5 --oneline`; inspect existing diffs before touching a file. The tree contains extensive uncommitted P1-P6 work. Preserve it, including unrelated tests, documentation, scratch files and UI changes. Do not reset, clean, overwrite or commit it. Do not edit checksums of migrations already applied; add an additive migration when needed.

Find the actual current files for validators, models, condition/dependency/track evaluation, extraction, review, commands, amendments, evidence persistence, projections and authorization. File locations in the PDF can be stale. Verify reachability from production entry points; an unused helper or orphaned screen does not establish integration.

Selected decisions remain in force: Mongo-native pilot and port architecture; explicit persisted authority; assigned PI plus explicit grant for high-impact operations; Sponsor/CRO clinical-write denial; default-on LAST_EDITOR maker-checker; explicit identity-review grants; no actual implied solely by TRIGGER/PRECONDITION labels; ambiguous CURRENT selectors blocked in new/edited drafts while explicit legacy compatibility stays pinned. Apply the decision record's exact rules rather than reconstructing a new policy from this summary.

## 3. Baseline, traceability and dependency sequence

Update `docs/pdf-remaining-implementation-plan.md` with a row for each of F1-F4 and the ten numbered P7/P8 tasks. Each row needs: exact PDF criterion, existing implementation, missing behavior, shared enforcement boundary, API/UI/store reachability, regression, compatibility impact and acceptance evidence.

P6 has a constructed two-user browser journey, but independent review reproduced four defects on memory and real Mongo despite passing tests. Existing evidence includes 173 independently passing backend tests and 20 frontend tests; those counts do not refute the defects. Preserve verified hash/mapping sub-behaviors while repairing the integrated paths.

Execute in this order:

1. Reproduce and repair F1-F4 with safe-behavior regressions; triage known integration failures.
2. Implement shared P7 issue contracts and validation passes, then wire authoring/review/command/adoption boundaries.
3. Implement unsupported/manual-only persistence and its authorized visible workflow.
4. Build P8 schema, manifest, scoring, execution scenarios and CI gate using repaired P6/P7 behavior.
5. Complete regression, real-store, UI and quality-gate verification; reconcile documentation and the next checkpoint.

Independent work can progress while a specific external gate remains open. Do not represent a dependent workflow as accepted until its safety prerequisites pass. Label proposed budgets and missing clinical sign-off honestly.

## 4. F1: bind route authorization to the records being mutated

Inspect `backend/app/api/uctsm_amendments.py` (`declare_lineage`, `prepare_pair`), `application/amendment_service.py`, the manifest and `RouteAuthorizer`.

Reproduction: one organization has trials A and B with distinct persisted external ids. The provider permits A and denies B. Direct GET of B's amendment overview is 404. Posting B's child and parent version ids under A's lineage URL returns 201 and changes B. Posting B's approved versions under A's pairs URL returns 404 **after** creating B's pair/mapping records.

Required behavior:

* Resolve both version headers from persisted records and prove tenant and URL-trial membership before any business side effect. Authorizing A does not authorize arbitrary B ids in the body.
* Carry an explicit expected-trial scope into the relevant application transaction, or an equally strong shared boundary, so a later handler check cannot become the sole protection. Preserve same-protocol and parent/child lineage checks.
* Cover first requests and existing-pair replays. Replays must re-check relationship and scope.
* Return the project's not-found semantics for unrelated resources without leaking foreign header contents. A denial audit may be written independently; lineage, pair, mapping and business-success audit records must not change.
* Add same-organization/different-trial negatives with a restricted provider, cross-organization negatives, forged/mixed version ids and positive legitimate operations. Verify storage after the HTTP response on memory and Mongo. A status-code-only test would miss the existing write-before-404 defect.
* Exercise the production relationship provider with persisted assignments for a representative negative, rather than relying exclusively on AllowAllAccess or a mock returning true.

Audit sibling P6 routes for the same pattern and fix any demonstrated equivalent defect within scope. Do not broaden a caller's relationship to make tests pass.

## 5. F2: preview the result that reconciliation will actually commit

Inspect `amendment_plan.translated_prior/build_plan`, `amendment_adoption._apply`, `amendment_impact.build_visit_impacts` and shared patient evaluation/reconciliation.

Reproduction: the pilot's `C1D1:0` is planned for 8 January 2026. Preview adopting on 1 March 2027: target shown is 11 January 2026. Confirm and execute on 1 March: committed target is 8 January with protected history. The raw target evaluation feeds preview, while persistence additionally reconciles and protects pre-effective visits.

Required behavior:

* Establish one pure calculation of the **final reconciled projection**, including protected history, effective-date boundaries, additions/removals, mapped identities, statuses and windows. Preview and commit must consume the same semantics. Reuse the shared evaluator; do not create another scheduling engine.
* Build the displayed impact from that final projection. Compare with the committed calendar on reload, not just the raw evaluator result or decision status.
* Preserve the selected rule for pre-effective history. Do not solve the mismatch by moving earlier visits that should be protected or by omitting them from the preview.
* P6-03 requires the same frozen as-of context and horizon under both versions. Thread the explicit business clock through calculations and bind it to provenance. Keep a displayed current stored calendar separate from a recomputed frozen comparison baseline if both are useful. Do not silently rely on `date.today()` in one branch and an explicit date in another.
* For date adoption, use the chosen effective date consistently in preview and execution. For event/cycle points not yet known, state which results are provisional and which facts must trigger reconfirmation. Do not invent the date or promise exact future targets that will be silently protected differently.
* Include all material target/window/status/history differences in any policy that claims SAME_IMPACT. Keep SAME_STRUCTURE limited to its documented contract; it cannot waive a missing adoption trigger.
* Test immediately before, at and after the effective point; completed and missed history; changed windows; mapped renames and the return path; and stale inputs. Assert complete preview-versus-persisted semantic equality for deterministic cases on both stores. Include a browser case showing the effective-date choice, preview and reloaded committed outcome.

## 6. F3: revalidate the trigger in the transaction that applies adoption

Inspect `_claim`, `_execute_claimed`, `trigger_state`, occurrence supersession and patient/input concurrency guards.

Reproduction: decide to adopt when C1D1:0 is performed; record that performed fact; claim execution; correct it through the normal service to MISSED; execute under the valid lease. Fresh trigger resolution says `due=False`, yet the patient moves to V2 using the old date.

Required behavior:

* Within the final adoption transaction, resolve the exact approved event occurrence, cycle start or date again from effective facts, including corrections and supersession. Verify that the point is due and that the effective date is still valid.
* If the trigger was withdrawn, do not apply. Use an explicit safe lifecycle outcome such as waiting or needs-reconfirmation, with a coded reason and audit. If its date changed, evaluate the changed point under the approved policy and require reconfirmation when necessary. Never reuse a superseded trigger snapshot merely because a lease is held.
* Serialize against concurrent fact/correction writes with a common patient/input revision or equivalent proven guard. A read alone under snapshot isolation may allow a write skew; explain and test the invariant used.
* Retain fencing, lease-expiry and replay protections. They establish worker ownership, while trigger validation establishes clinical eligibility; both are necessary.
* Add deterministic interleaving tests: correction between claim and apply, changed actual date, removed cycle-start anchor, wrong occurrence, expired/reclaimed lease and duplicate workers. Use real Mongo transactions and failure injection. A refused application must leave no assignment, carried facts, target evaluation/projection, applied receipt or applied outbox; any lifecycle/audit change must be deliberate and separately asserted.

## 7. F4: ensure bounded, fair deferred-worker progress

Inspect `process_due_once`, `pending_execution`, both amendment repositories and their indexes.

Reproduction: batch limit one; oldest decision waits until 2099; the next decision is due on 1 March 2027. Multiple passes on 1 March process only the first waiting decision. At the default limit, 100 older waiting decisions starve every later due decision, across organizations.

Required behavior:

* Choose an explicit bounded scan/scheduling contract with fair progress: keyset cursor, rotating checkpoint, next-check scheduling or another justified approach. Future date/event/cycle decisions and unexpired leases must not monopolize each pass.
* Preserve organization/patient scope and one-open-decision/idempotency/fencing rules. Do not bypass authorization when progressing past waiting entries.
* Apply limits and ordering in Mongo queries with appropriate indexes; do not load all candidates then slice in Python. Keep memory and Mongo repository semantics consistent.
* Prove progress with more than one batch, including at least 100 waiting decisions followed by a due one, multiple organizations, a held lease, an expired lease and an authorization-provider error. Repeated passes must reach eligible later records without duplicating application or spinning indefinitely.
* Durable queue infrastructure remains P13 work. This repair must make the current worker correct even before that infrastructure exists.

## 8. Triage the failing integration modules as engineering work

Re-run and classify the files listed in section 8 of `p6-completion-record.md` with a healthy explicit local Mongo environment. Do not use a Mongo-down sweep as equivalent baseline evidence, and do not infer unrelatedness from not editing `app/services`.

Independently repeated examples:

* `test_mongo_parity_patient_plane.py`: occurrence writes fail with `PlaneAuthorityError` for unclassified fixtures.
* `test_extraction_compiler.py::test_toxicity_hold_and_resume_become_pause_and_resume_block_actions`: expects no issues but now receives `RECURRENCE_BASIS_UNCONFIRMED`.

For every failure, distinguish product defect, intentional policy change requiring fixture/expectation repair, test-harness defect and environment error. Record evidence. For positive clinical-write scenarios, explicitly create patients with appropriate authority rather than weakening fail-closed behavior. For extraction, assert that an unstated basis needs review, and separately show the positive case after an explicit reviewed edit; do not guess the basis. Keep negative cases and independent maker-checker actors intact.

Use the documented curated gate and isolated TestClient modules as appropriate. A smaller selected passing list does not discharge known failures. Do not omit a failing file merely to report zero failures. Where a utility is not actually a supported automated test, document the harness correction rather than suppressing meaningful coverage.

## 9. P7: graph-wide clinical validation at every applicable boundary

Implement all five PDF tasks below. First map each rule to an existing validator or a missing pass. Extend the current pipeline rather than adding independent route-specific copies. Invalid definitions and valid rules awaiting patient facts require different outcomes.

### 9.1 P7-01 — graph identity, references, ownership and reachability

Inventory supported nodes and edges: events, activities, anchors/derivations, dependencies, conditions, processes/transitions, repeat/cycle structures, tracks/membership/cross-track links, population/assignment applicability and amendment mappings.

Validate, where applicable:

- Duplicate identifiers and duplicate codes in their declared namespace. Do not require unrelated activity codes to be globally unique if the schema deliberately scopes them to an event.
- Existence, type and ownership of every reference. A valid UUID belonging to another organization, trial, version, event or parent is still invalid for the current object.
- Cycles in graphs that must be acyclic, including indirect cycles. Respect explicitly supported recurrence/process transitions; do not reject every legitimate repeat as a dependency cycle.
- Reachability and orphan detection using declared roots and conditional entry points. A visit reachable only after a condition is not necessarily an orphan; prove the semantics rather than deleting it.
- Parent-child containment, track membership, source/target mapping coverage and conflicting/duplicate edges.
- Mapping occurrence rules naming listed source/target events and valid supported occurrence identities. Structural coverage alone establishes neither reviewed readiness nor a valid destination occurrence.

Return deterministic structured issues with stable codes, severity, exact field paths and related resource/evidence references. Preserve existing public issue contracts where possible; document additive fields. Ordering must remain stable across collection ordering and database reconstruction. Do not leak sensitive patient details.

Test every applicable graph family with valid cases, missing/foreign references, duplicates, indirect cycles and orphan/reachability cases. Include malformed payloads at import/edit/approval and mapping service boundaries, not only direct validator calls. Verify failed validation cannot commit an approved version or adoption-ready mapping.

### 9.2 P7-02 — explicit, supported and bounded temporal semantics

Validate temporal bases, source selectors, units, offsets, target/window order, recurrence basis, positive interval, start, termination/count/maximum, horizon and effective timestamps. Preserve a known zero-width window separately from an unknown window; partial bounds are evaluated only on stated sides.

Missing actuals or patient anchors on valid rules produce waiting states. They do not justify guessed values or invalidating the protocol. Unstated/contradictory rule bases block automatic approval. Distinguish protocol maxima from engine resource caps, and expose incomplete/provisional projections if a cap is reached. Never invent an ending-event date.

Cover first-anchored versus previous-planned repeats, month-end, leap years, year boundaries, supported positive/negative offsets, date-only versus timestamp semantics, and timezone/DST where the model operates on instants. Do not convert date-only clinical days through UTC and shift their displayed date. Derive exact expectations independently from the implementation.

Preserve explicit compatibility for already-approved schedules; new validation cannot silently change enrolled patients' historical dates. New/edited unsafe drafts must fail current approval rules. Test both cases.

Include the mandatory recurrence example: baseline 1 January 2026, first offset 14 days, every 28 days produces 15 January, 12 February and 12 March. Cover actual-relative waits, occurrence-specific source facts, independent visit/treatment actuals, absent ending events, safety caps and ambiguous selector handling.

### 9.3 P7-03 — reviewed evidence and explicit conflict resolution

Audit persistence of extraction claims, source documents/pages, evidence records, reviewer edits and approval provenance. Validate evidence against the correct document/version, page, section/source locator, quote and claim path. An existing unrelated citation does not support a rule. Do not invent a page, quote, reviewer or source.

Define which critical executable fields require reviewed evidence from the PDF and actual decision records. Timing, windows, recurrence basis/termination, dependencies, applicability, tracks and amendment equivalence cannot become executable merely because model confidence is high. Synthetic/programmatic fixtures must state their source category and cannot masquerade as clinical evidence.

Preserve conflicting claims and source evidence. Add an append-only resolution decision with authenticated reviewer, reason, chosen interpretation or unsupported outcome, affected claim/rule identity and the revision/content reviewed. Editing a claim invalidates stale readiness. Re-extraction cannot erase conflicting sources or silently reuse their approval.

Expose missing/conflicting evidence with precise paths and source locations in review API/UI. Provide reachable controls to inspect sources and record an authorized resolution through existing capabilities. Apply maker-checker to material edits and approval. Read-only roles cannot resolve conflicts through a hidden flag.

Test missing, malformed, cross-document/version and stale citations; two conflicting synthetic claims; permitted resolution; rejected/unresolved decisions; grant revocation; re-extraction; and direct-service/alias approval bypass attempts. Critical unresolved evidence blocks approval while original evidence remains queryable.

### 9.4 P7-04 — cross-feature validation of tracks, commands and amendments

Define a shared applicable validation context for approval, command preview/confirm/replay, mapping review, simulation, adoption and deferred execution. Use persisted patient/version/authority and input revision. Revalidate at commit; preview alone is not a concurrency guarantee.

Check recurrence basis versus rebase policy, track membership versus target event, cross-track links, pause/resume/stop chronology, command effective windows, override bounds and stated authority/reason, and dependent event effects. Do not invent clinical override limits absent an approved policy; distinguish unsupported policy from a valid explicitly authorized operation.

For amendments, validate both version owners/lineage/hashes, reviewed mapping coverage/conflict freedom, valid destination occurrences, supported activity transfer, retained facts, protected history, explicit adoption points and final reconciled impact. Include F1-F4 in this cross-feature matrix.

Contradictory actions return coded issues before business writes. A refusal cannot leave a partial assignment, command ledger row, patient fact, evaluation/projection or applied outbox. Audit according to the existing transaction contract. Filter sensitive reason/authority from non-staff nested responses and diagnostics.

A validator does not implement missing transfer semantics. Keep activity-level mapping for re-coded/split visits, carry of existing pause/stop/rebase/override commands, history-bearing re-adoption and ambiguous many-to-one returns explicit and safely blocked until supported. Such blocks are functional limitations, not proof of universal amendment support.

### 9.5 P7-05 — typed unsupported and approved manual-only handling

Locate DEC-07 if available. An identifier alone does not supply clinical policy. Implement the safe workflow without inventing clinical sign-off.

Persist unsupported/manual-only state with affected construct/path, document/version/evidence, reason, responsible owner, review status, authenticated decisions and permitted action. Distinguish unsupported definitions, invalid definitions, patient-specific waiting and approved manual handling. Unsupported rules produce no automatic target.

Keep the state through extraction, storage, review, API, evaluation and projection reload. It must remain visible in the relevant reachable review and patient screens; backend-only metadata is insufficient. Show actionable explanations and role-appropriate source access. A missing date must not resemble a completed or zero-width visit.

If manual handling is permitted, use an explicit authorized audited fact/command with reason/evidence and independent approval where required. Keep its clinical date distinct from the protocol-generated target, with provenance. A manual date cannot silently prove a visit or treatment actually occurred. Refuse unauthorized bypasses.

Expose safe diagnostic counts by code/state without PHI, raw protocol text or patient-id metric labels. Test no automatic date, dependent waits/blocks, approved manual provenance, pending/rejected manual actions and state visibility after reload.

## 10. P8: measurable protocol quality and deterministic execution

Build an executable quality toolchain tied to P2 identity, P6 amendments and P7 safety. Visit counts and confidence are not accuracy metrics. Use production extraction/evaluation entry points; testing a separate implementation does not prove production behavior.

### 10.1 P8-01 — machine-readable gold schema and loader

Provide versioned Pydantic/JSON schema models and a loader for expected identity, executable rule fields, evidence, unsupported/manual states, patient scenarios, commands and amendment outcomes. Include dataset/record/schema versions, document hashes/versions, provenance, lawful-use/data-control metadata, capability tags, reviewer/sign-off records and tool versions.

Expected rules must express source/anchor identity, temporal basis, occurrence selector, offset/unit, window presence/bounds, recurrence basis/start/termination, conditions, applicability, tracks and mapping review. Scenarios need exact dates/statuses/waits/history, not a model's prose answer.

Separate engineering fixtures from eligible signed clinical gold with an explicit trust category. Exclude invalid/unsigned records from the signed set and list reasons. Exclusion cannot silently produce a green gate over an empty or narrowed corpus. Development fixtures may run in an explicit engineering mode but cannot satisfy clinical release sign-off.

Validate duplicate ids, missing/foreign documents, file-hash mismatches, invalid expectations, unsupported schema versions, stale approval and data-control metadata. Loading must be deterministic. Do not fabricate reviewer identities or signatures to create a passing clinical example.

### 10.2 P8-02 — risk-based corpus manifest and coverage

Inventory existing documents and fixtures first. Use `docs/evidence/protocol-identity/NCT02088554/Prot_003.pdf` with its source manifest and verified hash. It is one real document, not a full corpus or authentic amendment pair. A wrong-id example constructed from it retains that label.

Create a machine-readable manifest containing source/version/hash, availability, provenance, lawful-use/de-identification metadata, risk, construct tags, expected unsupported cases and review readiness. Do not copy private patient data or invent licenses. Unknown source permissions are an explicit gate.

Cover linear schedules, cycles, actual-relative timing, branching, multiple tracks/cross-track links, recurrence, device protocols, amendments, conflicts and unsupported constructs. Include positive/negative examples, treatment-only actuals, repeated-source selection and missing end/anchor cases. Fill engineering gaps with deterministic synthetic cases clearly separated from real-document and signed-gold coverage.

Generate a coverage report mapping every supported construct to available examples, reviewed gold, executable scenarios and missing cells. Unsupported cases need expected no-date outcomes. Numerous near-identical cases cannot hide missing constructs. Do not claim broad protocol support from one PERIGON PDF.

Authentic amendment pairs and external review/licensing may remain external gates. Complete manifest/schema/coverage machinery and synthetic scenarios independently.

### 10.3 P8-03 — field-level extraction, evidence and safety scoring

Define matching and normalization before calculating metrics. Compare every supported rule component, identity decision, evidence citation and unsupported classification. Use stable reviewed identities or explicit amendment mappings; a reused event code alone cannot identify the correct visit. Detect missing, extra, duplicate and ambiguous predictions.

Report TP/FP/FN, precision, recall and F1 with raw numerators/denominators by field family and severity. Report exact match separately from supported semantic equivalence. Normalize incidental UUID/order differences only by documented rules. Never normalize away temporal basis, occurrence identity, unknown versus zero window, treatment versus visit, date versus instant or recurrence termination. Month arithmetic cannot become fixed-day arithmetic.

Classify errors into wrong identity; source/anchor/selector/basis; offset/unit; window; recurrence; dependency/condition/applicability; track/command/amendment mapping; evidence citation; unsupported-state loss; missing/extra executable rule; unsafe generated date. Identify every critical false positive by document/scenario/rule/field path and permitted source reference.

Evidence scoring distinguishes an actual supporting source from an existing unrelated citation. Model confidence or an opaque model judge cannot be the sole clinical oracle. Use deterministic typed comparisons for safety-critical fields and explicitly reviewable equivalence rules elsewhere.

A zero denominator is N/A/insufficient evidence, not a perfect score. Missing output and crashed extraction are recorded failed/incomplete runs, not dropped cases. Provider failures remain distinct from rule errors while still preventing a release pass over ignored documents.

Retain JSON plus readable reports tied to dataset/document/gold hashes, source revision, provider/model/prompt/schema/compiler/validator/evaluator versions and configuration. Redact secrets/PHI and protect artifact access.

Test with independent hand-calculated examples proving metrics, critical-FP detection, duplicate matching, missing predictions, normalization boundaries, zero denominators and corpus eligibility. Calling the scorer twice is not an accuracy oracle.

### 10.4 P8-04 — fixed-clock patient and change scenarios

Build a deterministic runner using approved supported snapshots and production application services. Freeze as-of date, horizon, timezone where applicable, policy/evaluator/schema versions and material inputs. Keep effective and recorded times distinct. Normalize only incidental values.

Assert exact dates/windows/statuses, waits/blockers, source/basis explanations, logical identity, protected history, version and authority. Compare final committed projections to preview and reload. Exercise commands/amendments through transactional application boundaries, not only raw evaluation.

Minimum scenario pack:

1. Baseline 1 October 2026, V2 actual 13 October, dependent +14 days gives 27 October; baseline imaging stays 31 October and the independent planned rule stays 24 October.
2. Baseline 1 January 2026, first +14 days and every 28 days gives 15 January, 12 February and 12 March; month-end basis variants differ as intended.
3. Missing actual, treatment-only actual, visit-only actual, occurrence-specific source, superseded actual and conflicting conditions. Another occurrence cannot supply a missing fact.
4. Treatment delayed seven days moves dependent events while baseline imaging stays fixed. Appointment, override and actual stay separate; unknown window stays unknown.
5. Track pause/resume/stop/rebase effective windows, reversal, contradictory chronology and unauthorized refusal with no clinical write.
6. Reviewed rename and supported split/merge; protected/removed/added visits; remain/immediate/event/cycle/date adoption; preserved history; supported return and explicit unsupported-return blockers.
7. F1-F4 as safe-behavior assertions: foreign versions cannot be mutated, reconciled preview equals commit, withdrawn triggers prevent adoption, later due work progresses past waiting prefixes.
8. Evidence conflict, unconfirmed recurrence basis and unsupported/manual-only constructs produce expected waits/blocks and no invented target.
9. Stale/expired preview, grant revocation, duplicate confirm, worker lease takeover and injected failure produce one complete valid outcome or no business write.

All safety-critical scenarios must pass exactly. Many easy visits cannot average away a dangerous date mismatch. Retain seeds/inputs for randomized failures. The full evaluator remains the oracle if later P9 optimization is introduced.

### 10.5 P8-05 — executable CI quality/release gates

Add repeatable CLI/scripts and actual CI invocation for schema validation, eligibility/coverage, field scoring, execution scenarios and threshold evaluation. Inspect existing CI before extending it. Normal engineering checks should be deterministic/offline; clearly separate provider integration with required access. Never fake provider success.

Version threshold/configuration schemas. Use actual approved clinical thresholds/sign-off if available. Otherwise provide labelled proposed development thresholds and keep the clinical release gate unsatisfied. An engineering run without clinical gold may pass its development checks, but must explicitly state that it does not qualify a clinical release. Separate modes so production cannot inherit permissive defaults.

Any unsafe invented date is an unconditional release failure despite high aggregate F1. Zero unsafe dates without eligible coverage is insufficient evidence. Empty, unsigned-only, unreadable or hash-mismatched corpora cannot pass the clinical gate. Missing scenarios, critical failures, stale/unreviewed changed gold, failed provider runs and unapproved thresholds need inspectable nonpassing outcomes.

Baseline results and publish trends only against declared comparable datasets/configurations. Make exclusions, reduced coverage and threshold changes visible and reviewed. Preserve manifests/configuration, field failures, scenario outcomes and safe logs as controlled CI artifacts. Document proposed artifact retention; do not invent production retention policy.

Provide a machine-readable gate result with decision/reason codes, failed criteria, eligible/excluded counts, denominator visibility and artifact references. Clinical failure or insufficient evidence must return an appropriate nonzero process outcome. Distinguish execution errors from quality failures. A Markdown checklist alone does not implement a gate.

Define and test the versioned manifest contract consumed by P13: source revision, dataset/configuration/tool hashes, tests/scenarios/metrics, sign-off state and gate decision. Build the contract now; durable jobs, deployed monitoring and actual release orchestration remain later work. Do not claim a release was performed.

Exercise a passing deterministic engineering run and deliberate failures: wrong expected basis/window, unsafe date, missing source file, unsigned/empty gold, provider failure, stale sign-off and changed thresholds. Prove unsafe/insufficient cases cannot be labelled a clinical pass. Do not fabricate real reviewer approval for positive test coverage.

## 11. Integration and verification contract

First establish F1-F4 as failing safe-behavior regressions, then repair them. The saved audit script asserts unsafe behavior deliberately; a successful run documents defects. Preserve/archive it as historical evidence when its assumptions no longer hold; put safe-behavior regressions in the supported suite.

Run focused tests, then memory/real-Mongo contracts and isolated HTTP modules. Cover P6 mapping/hash/approval/adoption/worker/atomicity, P7 validation/evidence/manual-only, P8 loader/scorer/scenarios/gate, authority/authz/production relationship provider, maker-checker, impacted P1/P4/P5, parity, identity and review. Finish the required curated gate and triage every supported failing integration module listed in completion-record section 8. Do not select a smaller passing list to hide failures.

Use explicit local replica-set settings: `MONGO_URL`, `UCTSM_TEST_MONGO_URL`, `UCTSM_REQUIRE_MONGO=1`, task-specific `DB_NAME` and relevant `UCTSM_MONGO_*`. Verify actual URL/database before writes. Do not inherit shared-cluster settings. Mongo variants must run. Remove only task-owned random databases, never prior journey databases or unrelated data.

Use D: task temporary/output directories if C: remains full. Do not run Metro export beside Mongo suites on this constrained machine. A crashed mongod invalidates that run; restore the local environment and rerun affected checks. Suspected environment failures do not count as passes.

Frontend: run actual feature scripts, scoped TypeScript preserving Expo/JSX/aliases, and meaningful controller/render or browser coverage for touched flows. Attempt the available browser/render harness before declaring it unavailable. Helper tests are not React render evidence. Missing yarnpkg/lint or typecheck OOM must be resolved or explicitly reported.

Exercise product navigation with production relationship/capability checks and isolated synthetic users for: separate-user review/approval; blocked critical validation and correction; evidence conflict resolution; visible unsupported/manual state; deferred-date preview versus committed reload; and denied cross-trial request with no write. Capture screenshots, assertions and configuration. Distinguish newly run evidence from inspected historical screenshots. Do not patch scheduling behavior just for a green walkthrough.

If touched SQL behavior relies on locking guarantees, verify with isolated real PostgreSQL or report the exact gap. Do not add a SQL dependency to the Mongo pilot just for an unrelated fixture.

## 12. Required artifacts and acceptance

Maintain the existing implementation plan and all 70 acceptance rows. Include F1-F4 results and all ten P7/P8 rows with exact code/test/API/UI/store evidence. Add a descriptive P7/P8 completion record with commands, outcomes/skips, before/after proofs, quality reports, browser scope and remaining gates. Keep schemas, corpus/manifests, metric/gate configurations, scenarios, CI commands and release-manifest contracts reviewable.

Use established states: NOT_ASSESSED, NEEDS_REPAIR, IMPLEMENTED_NOT_FULLY_VERIFIED, VERIFIED_ACCEPTANCE, BLOCKED_EXTERNAL_EVIDENCE, AWAITING_OPERATIONAL_APPROVAL. Separate functional implementation from external evidence/operational acceptance. Correct stale primary tables; do not leave a green verdict above a contradictory appendix.

F1-F4 close only with safe-behavior regressions on the relevant real paths and stores; F2 also needs committed-calendar comparison and affected UI verification. P7 cannot close if checks are reachable only through helpers or unsupported rules disappear from views. P8 engineering tooling can be complete while signed corpus/approved thresholds remain external gates, but clinical release acceptance must remain nonpassing.

Keep P6 restrictions visible unless implemented: activity transfer for re-coded/split visits, existing-command carry, history-bearing re-adoption and ambiguous many-to-one return. The worker may remain in-process until P13; fairness and trigger correctness must be fixed now. Missing authentic documents do not excuse reproducible defects, and synthetic fixtures do not replace clinical evidence.

The PDF has 70 numbered tasks. P1-P6 address 30 identifiers; P7/P8 add ten. Do not report 40/70 as accepted merely because code/tests were added. Measure criteria and state functional, verification and external gaps. Addressed task count is not production-readiness percentage.

Save each new full prompt as descriptive Markdown under `docs/`, maintain `docs/claude-code-prompts-index.md`, and update `docs/claude-code-next-prompt.md` when the next action changes. Preserve history. After verifying this combined slice, continue the next independent work under the master with its prerequisite gates recorded; do not recreate completed work.

## 13. Final handoff and scope

Report resulting behavior, F1-F4 before/after proofs, each P7/P8 task state, actual tests/Mongo/browser evidence, metric/coverage/gate artifact locations, compatibility risks, unsupported constructs, remaining external/operational gates and the next PDF work.

Do not claim universal protocol representation, authentic amendment evidence, clinical sign-off, migration or deployment without actual proof. No commit, push, deployment, external messages, real-cohort expansion, authority/read-mode flip or settled clinical policy change is authorized by this prompt.
