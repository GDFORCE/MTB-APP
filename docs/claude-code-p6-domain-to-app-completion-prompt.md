# Claude Code continuation: repair the P6 domain contracts and complete amendments end to end

Prepared 16 October 2026. This is the current focused slice of `docs/claude-code-p6-p14-remaining-pdf-prompt.md`, not a replacement for the remaining PDF scope. P6 domain scaffolding exists; persistence, service, API and UI acceptance remain open.

---

Continue P6 from the current tree. Read `docs/p6-domain-review.md` first, reproduce the mapping findings, fix them with meaningful regressions, then complete all P6-01 through P6-05 layers. Do not stop after repository ports, a migration or another domain-only report. Preserve existing work and return to the remaining master prompt after this slice.

## 1. Read and plan from source

Read AGENTS.md and nested instructions, the actual P6 PDF rows, the selected project decisions, relevant P1-P5 audits, P6-P14 master prompt, current amendment module/tests, authoring headers, schedule repository/review boundary, patient history/impact/command services, authorization and frontend routes.

Check git status/log and existing diffs. Preserve unrelated uncommitted work. Update the implementation plan with each P6 requirement, existing behavior, repair, API/UI/store target and expected verification. Give concise reasoning for invariants, transaction design, compatibility and alternatives; private chain-of-thought is not requested. Continue into implementation after planning.

The independent domain plus architecture run passed 26 tests. That is a baseline, not evidence the additional negative cases or application workflow work. Do not repeat earlier counts as your own new run.

## 2. Fix mapping determinism and reversal before storage

Add red regressions for:

1. CONFIRMED SPLIT A:0 -> B:0 and A:0 -> C:0. Current validation accepts it; reversing rule order changes translation but not mapping_revision.
2. CONFIRMED MERGED A:0 -> C:0 and B:0 -> C:0. Current reverse translation chooses whichever source appears first, with the same fingerprint.
3. A complete PROPOSED mapping whose coverage function currently returns no issues.

Require a unique fact transfer wherever the API returns one occurrence. Reject conflicting source pairings or return typed unresolved/blocking results. A many-to-one event relation does not establish which older occurrence a new actual belongs to. Either a reviewed provenance-aware transfer policy supplies the unique answer or reversal is blocked. Do not choose the first row, duplicate completion, overwrite conflicting actuals, silently discard a rule or ban all split/merge graph changes just to avoid designing fact transfer.

Validate duplicate/conflicting occurrence rules, target collisions and cross-mapping overlaps. Distinguish structural event correspondence from a permitted occurrence/fact transfer. Keep originals queryable even when a future projection references them through mappings. Accepted rule reordering must not change behavior under the same fingerprint.

Separate structural coverage from review readiness explicitly, or make a combined validator return both kinds of coded issues. Services must enforce the complete readiness gate. PROPOSED/REJECTED/missing/ambiguous mappings never carry patient facts by default. Resolved split/merge transfers need enough explicit rules; AUTO_CONFIRMED cannot bypass this. Rejected proposals remain audited, and their valid replacements supply effective coverage.

Add dedicated reverse tests for every mapping type/status and unambiguous round trips; V2-only facts or ambiguous merge inverses produce an actionable refusal. Patient reversal is a separate authorized command/evaluation with current-state impact, not this helper alone.

At API boundaries, do not accept client-supplied AUTO_CONFIRMED, reviewer/approver identity, grants, trusted provenance or content hashes as authority. Recompute server-owned automatic matches against the referenced version snapshots. Persist authenticated human review decisions with reason and revision. Similar names are proposals, not decisions.

## 3. Make content identity and simulation binding precise

Deep-copy a single schedule, alter one material field and assert hash changes. Cover timing/basis/windows/dependencies/conditions/recurrence/tracks/links/anchors/populations/assignments/evidence and relevant schema/semantic versions. Test stable round-trip, map-key ordering, lifecycle-only changes and persisted reconstruction; do not rely on fresh UUIDs making a weak hash test pass.

Document content hash versus lifecycle/header integrity. Status/creation exclusions must not permit changed clinical content. If effective_from remains excluded, protect it separately and bind simulation/adoption to its relevant value and explicit effective point. Include the hash algorithm version in the persisted contract. Do not silently change previously stored hash interpretation; version/migrate it deliberately.

Compute and stamp approved content hash in the same guarded approval write on both supported review aliases/direct service boundaries. Reject changes to approved content/immutable lineage. Handle historical hashless approved records explicitly through compatible verification; do not mutate V1 content or declare it verified without evidence.

## 4. Complete ports, records and migration

Use existing UUID-based repository/UoW contracts, not a second amendment engine. Add typed immutable/versioned records as needed for:

- Schedule amendment lineage/change sets and complete semantic changes.
- Mapping sets/revisions and append-only review decisions/provenance.
- Frozen patient simulations and expiry/material input references.
- Adoption policy/decision/result and linked reversal.
- Deferred execution state, receipts and outbox intent references.

Scope records by trusted organization/trial/patient/from-version/to-version as applicable. Validate parent/version ownership, approved status, protocol lineage, UUID references and immutable content. No unbounded embedded histories or ObjectId/driver types in domain/application/API contracts.

Implement equivalent memory and Mongo repositories. Add a new resumable schema/index migration; do not edit a migration already applied. Enforce scoped uniqueness, version/revision conflicts, simulation/decision relationships and idempotency. A hash without guarded immutability is not sufficient storage protection. Test actual database validators/indexes and cross-tenant/foreign-parent refusal.

## 5. Implement semantic comparison and read-only simulation

Extend existing diff code for every supported schedule concept, not just event codes: timing/basis/window, activities, dependency/conditions, recurrence/process, track policy/membership/links, anchors, populations/assignments and evidence/provenance. Keep event identity matching separate from rule equality: a timing change can affect the same logical visit, while an unrelated reused code must not inherit facts.

Evaluate V1 and V2 from the same frozen as-of patient context/horizon, with selected reviewed mappings and preserved completed history. Return changed, unchanged, protected, waiting and blocked results with old/new target/window and reasons. Simulation changes no active assignment, facts, projections or notification outbox. A retained simulation record is non-clinical provenance.

Bind it to organization/patient, current authority/version/evaluation, both schedule content hashes, material context revision, mapping semantic/review revisions, policy/schema/evaluator/hash versions, as-of/effective parameters and expiry. Reject stale or forged bindings at confirmation. Keep approved legacy version semantics explicit.

## 6. Implement explicit adoption, deferred execution and safe reversal

Support remaining on V1, immediate adoption, event occurrence, cycle start and calendar date. Never default a patient to adoption when V2 is approved. Validate the actual patient/version event or cycle identity, existence/termination constraints and the intended triggering basis. Do not guess that every cycle start equals occurrence index N-1 unless the materializer contract and tests prove it. Missing future actual/trigger stays waiting, with no invented effective date.

Record authenticated decision maker, required capability, reason, policy/effective point and exact simulation/mapping/version bindings. A remain decision is auditable without changing the active version. Apply scoped idempotency and state/revision checks; same key/different material operation conflicts without writes.

Execute adoption atomically: decision/result, assignment/pointer transition, evaluation/projections, business audit, receipt and notification outbox. All V1 documents and original actual fact IDs remain queryable. Carry facts only through valid explicit mapping; do not feed all prior-version event codes into context as implicit equivalence or rewrite facts as if recorded in V2.

Deferred decisions need fenced/retryable execution state. At the defined point, re-check authority, referenced versions/mappings, approval validity and material context; recompute and require renewed confirmation when the approved impact no longer applies. Expected future events must have an explicit policy rather than using a permanently stale preview or silently guessing. Concurrent immediate/deferred/reversal requests must not double-assign or produce partial results.

A return to V1 is a new linked decision/command with preview and a new evaluation. Preserve V2-only and post-adoption facts. Re-evaluate against actual current history; block unsupported/ambiguous inverses or conflicts with later actions. Never delete V2 records, blindly repin V1 or reuse an old evaluation as the current result.

## 7. Authorization, API and product reachability

Use the selected central policy, including `amendment.adopt` for adoption and the applicable high-impact rights for reversal/override operations. Recheck on confirm, deferred execution and replay. Keep native Mongo authority, persisted production relationships, tenant/trial/site/patient scope, role-safe projections and denial semantics. Map every new route in the manifest. No fake reviewer account, permissive relationship provider or weaker legacy alias in acceptance proof.

Expose typed comparison/mapping review, simulation, adoption selection/confirmation, deferred status/history and supported reversal through API and reachable UI. Reuse existing navigation/components. Show both version identities, material changes/evidence, unresolved mappings, changed/unchanged/protected visits, effective-point choice and consequences before confirmation. Permission/expiry/staleness/missing input/refusal must be actionable.

The displayed post-adoption schedule must be the committed Mongo evaluation, including after reload/reopen. Do not leave a SQL-reading host screen beside Mongo adoption writes. Preserve protected legacy comments/tasks/edit features. A test endpoint or API-only edit does not complete P6.

Recheck the unfinished P1-P5 F1-F5 prerequisites before claiming this workflow reachable; repair dependencies in scope rather than building on an inaccessible pilot or permissive identity/authority guard. The synthetic pilot remains selected; do not ask for a real trial to start.

## 8. Verification and completion

Run focused defect tests first, memory and real local Mongo contracts/services, then isolated API modules and affected P1-P5/default-on maker-checker/parity/history/command/journey regressions. Follow AGENTS.md process isolation and curated-gate instructions. Explicit local replica-set settings and isolated databases; required Mongo variants must run, not silently skip.

Required cases include unreviewed/rejected mappings, ambiguous forward/inverse rules, rule-order fingerprints, rename/split/merge/add/remove, repeated occurrence identity, completed/missed/independent treatment facts, both version histories, deferred event/cycle/date, remain/immediate, V2-only actual reversal, injected transaction failure, concurrent confirm/replay, expired/stale simulation, changed authority/grants and cross-tenant/parent IDs.

Perform actual product UI/binding checks and the two-user review/approval/adoption journey with local synthetic data. Distinguish React render/browser proof from pure helpers and scoped typechecking. Genuine amendment pair evidence remains separate from constructed version fixtures; do not fabricate signed corpus or clinical approval.

Update original implementation/acceptance tables and add a P6 completion record: source, requirement matrix, hash/mapping contracts, migration compatibility, exact commands/results, UI artifacts, actual supported cases and remaining gates. All five PDF P6 rows require more than domain-only checks. Do not claim P6 complete until their applicable persistence/API/UI/history/transaction criteria are met.

Continue subsequent independent work under the remaining-PDF master after this slice. Keep prompts saved as Markdown and the index/current launcher accurate. No commit, push, live patient migration, live read-mode/authority flip or deployment.
