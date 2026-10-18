# P6 completion record: immutable amendments, semantic comparison, explicit adoption

> Subsequent independent review (16 October 2026): P6 is not closed. Four defects reproduced on memory and real Mongo contradict parts of the acceptance verdict below. Read [p6-end-to-end-independent-review.md](p6-end-to-end-independent-review.md); the [current combined repair + P7/P8 prompt](claude-code-p7-p8-after-p6-prompt.md) takes precedence over starting P7. The original implementation report and evidence are preserved.

Completed 16 October 2026 for the slice in [claude-code-p6-domain-to-app-completion-prompt.md](claude-code-p6-domain-to-app-completion-prompt.md) (a slice of the [P6-P14 master](claude-code-p6-p14-remaining-pdf-prompt.md)). Plan and invariants: [p6-implementation-plan.md](p6-implementation-plan.md). Findings that opened the slice and their resolution: [p6-domain-review.md](p6-domain-review.md). Task-by-task status: [pdf-requirements-acceptance-matrix.md](pdf-requirements-acceptance-matrix.md).

## 1. Verdict

All five P6 rows are implemented end to end - domain, application services, memory and real-MongoDB storage with a migration, central authorization, API, a worker for deferred adoption, and reachable screens - and verified with actual runs, including real concurrency and a two-user journey in a real browser. **Scope of that claim: constructed/synthetic fixtures.** The pilot's "Amendment 1" is a synthetic schedule built for this work; no authentic signed amendment pair exists locally, so that evidence gate stays `BLOCKED_EXTERNAL_EVIDENCE` and nothing was fabricated. P6-02 remains `IMPLEMENTED_NOT_FULLY_VERIFIED` (activity-level mapping rules are not supported; see s7).

No commit, push, deployment, live patient migration, read-mode or authority flip was made. Existing uncommitted P1-P5 work was preserved.

## 2. Requirement matrix

| Row | State | What was built and where | Proof |
|---|---|---|---|
| P6-01 lineage, hash, immutability | VERIFIED (constructed fixtures) | `domain/schedule/amendment.py` (`schedule-content.v1`, `header_integrity_hash`, `verify_content_hash`); `application/schedule_documents.approval_stamp`; stamped in `mark_approved` by the review service AND by both repositories when a direct caller gives none; `expected_revision` binding; content edits move the header revision; `set_lineage` once before approval; `transition(..., "APPROVED")` refused; migration `0009` | `test_p6_content_hash`, `test_p6_approval_hash` |
| P6-02 change set + mapping | IMPLEMENTED_NOT_FULLY_VERIFIED | `domain/schedule/diff.py` (anchors, tracks, links, membership, populations, processes, calendars, epochs, conditionals, confinement, residual attributes, evidence, source tracing, mapping-aware reading); `application/amendment_service.py`; stores `amendment_pairs`, `amendment_mapping_sets`, `amendment_mapping_decisions` | `test_p6_semantic_diff`, `test_p6_mapping_repair`, `test_p6_amendment_domain`, `test_p6_amendment_service`, contract suite |
| P6-03 simulation | VERIFIED (constructed fixtures) | `application/amendment_plan.build_plan`, `domain/schedule/amendment_impact.py`; `amendment_simulations` | `test_p6_adoption`, `test_p6_amendment_impact` |
| P6-04 explicit choice | VERIFIED (constructed fixtures) | `AdoptionPoint`, `trigger_state`, `actual_cycle_start`; `AdoptionService.simulate/confirm`; `amendment_decisions` | `test_p6_adoption`, `test_p6_adoption_cases`, `test_p6_adoption_points`, `test_p6_amendment_api` |
| P6-05 atomic execution + reversal | VERIFIED (constructed fixtures) | `AdoptionService._apply/_claim/_execute_claimed/execute/cancel`, return via the same plan; `api/uctsm_amendment_worker.py`; `server.py` startup wiring | `test_p6_adoption_atomicity`, `test_p6_amendment_worker`, `test_p6_adoption*`, browser journey |

PDF record names map to: `ScheduleChangeSet` = `AmendmentPairRecord.change_set`; `EventMapping` = `AmendmentMappingSetRecord.mappings` + `MappingDecisionRecord`; `PatientMigrationPolicy` = the adoption point plus `FuturePolicy` bound into the decision; `PatientMigrationDecision` = `AdoptionDecisionRecord`; `PatientMigrationResult` = the closed decision (`result_evaluation_id`, `result_assignment_id`, `carried_fact_count`) with its assignment, evaluation, audit, receipt and outbox rows.

## 3. Contracts

**Content hash** (`schedule-content.v1`, the first persisted version; nothing was stored under an earlier interpretation). SHA-256 over the schedule dump with top-level collections canonically ordered (so draft and reconstruction hash alike); excludes only `status`, `created_at`, `validation_issues` and `effective_from`. The algorithm version is stored with the hash; a different version is `UNSUPPORTED_VERSION`, never reinterpreted. Verification states: `VERIFIED`, `MISMATCH` (refuses everything), `UNVERIFIED_LEGACY` (hashless historical version: never declared verified, never rewritten; usable as the EARLIER version with a visible warning, refused as the later one), `UNSUPPORTED_VERSION`.

**Header integrity** (`schedule-header.v1`) covers what the content hash cannot: `effective_from`, lineage, approver, approval time, protocol/definition ids and the content hash itself. It is stamped in the same guarded write and re-verified on every load.

**Guarded approval.** The review service reads header and content, computes the stamp, and calls `mark_approved(..., expected_revision=header.revision)`. A concurrent edit moves the revision (and write-conflicts with the approval in Mongo), so a hash can never describe other content; the property is exercised under real concurrency. Mongo's validator additionally refuses an APPROVED document without a hash (`validationLevel: moderate`, so historical documents are never rejected).

**Event mapping.** Types EQUIVALENT / RENAMED / SPLIT / MERGED / REMOVED / ADDED with confidence, provenance and review state. Only an exact code+name+type match (or no counterpart) is `AUTO_CONFIRMED`, and the SERVER re-derives it from the two snapshots - a stored claim the snapshots do not support fails the gate. Everything else is a proposal; a person confirms, rejects (kept on record) or replaces it, each as an append-only decision with reviewer, reason, base and resulting revision. Occurrence rules are canonical, conflict-free and fingerprint-stable. Facts move only to a unique reviewed destination; a many-to-one inverse is unresolved and blocks a return.

**Simulation bindings** (all compared again at confirmation): patient, organization, trial, both schedule versions, both content hashes and header integrity hashes, both `effective_from` values, pair and mapping revision, authority plane, patient and pointer revision, a fingerprint of every material patient input, adoption point, horizon, policy / evaluator / schema versions. Expiry 30 minutes. Old values are the patient's current stored projection (what staff see); new values are evaluated from the same context and horizon.

**Decision lifecycle.** `WAITING` -> `EXECUTING` (fenced lease) -> `APPLIED` | `NEEDS_RECONFIRMATION` | `BLOCKED`; `REMAINING` and `CANCELLED` are terminal; one OPEN decision per patient is enforced by a partial unique index. `FuturePolicy.SAME_STRUCTURE` (default: the same visits correspond, dates of still-future visits may differ) or `SAME_IMPACT` decides whether a deferred decision needs a new confirmation. A decision's idempotency key is scoped (organization, patient); the same key with a different material operation is a conflict.

## 4. Storage and migration

Migration `0009_amendments` (additive, idempotent, resumable; the ledger refuses an edited applied migration): collections `amendment_pairs`, `amendment_mapping_sets`, `amendment_mapping_decisions`, `amendment_simulations`, `amendment_decisions` with validators and the indexes `uq_pair_versions`, `uq_mapping_set_pair`, `uq_decision_idempotency`, `uq_decision_open_patient` (organization + patient, partial on `is_open`), plus typed `schedule_versions` header fields and `ix_version_based_on`. No existing collection, route or record changed meaning; existing approved versions stay readable. The in-memory repositories enforce the same rules, and one contract suite runs on both.

## 5. Routes, authorization, screens

Eleven routes under `/api/uctsm/mongo-plane` plus `POST /pilot/seed-amendment`, all in the authorization manifest (now 76 declared routes): trial level `GET /trials/{id}/amendments`, `POST .../lineage` (`schedule.author`), `POST .../pairs` (`schedule.review`), `GET .../pairs/{id}` (`schedule.read`), `POST .../pairs/{id}/mappings` (`schedule.review`); patient level `GET .../amendments`, `POST .../simulate` (`patient.impact.preview`), `POST .../simulations/{id}/confirm` (`amendment.adopt`; a return also `patient.command.high_impact`), `POST .../decisions/{id}/execute` and `/cancel` (`amendment.adopt`). Every request model forbids unknown fields (a client-supplied actor, grant or provenance is a 422); the actor and organization come from authentication; the capabilities are rechecked on every call including replays and deferred execution; the worker re-checks the DECIDER's right through the production grant store and operational assignment. `amendment.adopt` still has no role default, is PI-only and cannot be self-granted.

Screens: `clinical/amendment-review` (compare, change list with protocol-text sources, mapping decisions, readiness) and a panel on `clinical/plane-schedule` (current version, the amendment, point choice, preview with consequences and visit groups, reason, confirm, open-decision handling, history, return). The calendar shown after adoption is the committed Mongo evaluation, re-read on reload.

## 6. What the actual runs showed

**Backend, final run on a local isolated replica set** (`UCTSM_REQUIRE_MONGO=1`; no Mongo variant skipped). P6 files: 271 tests passed across 15 files.

| File | Result |
|---|---|
| `tests/test_p6_adoption.py` | 60 passed, 1 warning in 38.75s |
| `tests/test_p6_adoption_atomicity.py` | 11 passed, 1 warning in 12.72s |
| `tests/test_p6_adoption_cases.py` | 18 passed, 1 warning in 14.55s |
| `tests/test_p6_adoption_points.py` | 10 passed, 1 warning in 1.05s |
| `tests/test_p6_amendment_api.py` | 18 passed, 1 warning in 15.94s |
| `tests/test_p6_amendment_domain.py` | 22 passed, 1 warning in 1.00s |
| `tests/test_p6_amendment_impact.py` | 7 passed, 1 warning in 0.85s |
| `tests/test_p6_amendment_production_access.py` | 4 passed, 5 warnings in 6.93s |
| `tests/test_p6_amendment_service.py` | 26 passed, 1 warning in 12.01s |
| `tests/test_p6_amendment_worker.py` | 10 passed, 1 warning in 12.53s |
| `tests/test_p6_approval_hash.py` | 25 passed, 1 warning in 8.91s |
| `tests/test_p6_content_hash.py` | 25 passed, 1 warning in 0.96s |
| `tests/test_p6_mapping_repair.py` | 10 passed, 1 warning in 0.88s |
| `tests/test_p6_semantic_diff.py` | 11 passed, 1 warning in 0.89s |
| `tests/contract/test_amendment_contract.py` | 14 passed, 1 warning in 3.13s |

**Affected regressions, same run** (maker-checker, parity, history, commands, pilot, production relationship provider, review/approval, identity, journey, contracts):

| File | Result |
|---|---|
| `tests/contract/test_schedule_contract.py` | 32 passed, 1 warning in 4.55s |
| `tests/contract/test_mongo_platform.py` | 9 passed, 1 warning in 8.20s |
| `tests/contract/test_patient_contract.py` | 36 passed, 1 warning in 4.32s |
| `tests/contract/test_authoring_contract.py` | 16 passed, 1 warning in 3.15s |
| `tests/contract/test_audit_contract.py` | 12 passed, 1 warning in 2.92s |
| `tests/test_uctsm_authz_manifest.py` | 6 passed, 1 warning in 2.27s |
| `tests/test_architecture_boundaries.py` | 4 passed, 1 warning in 2.37s |
| `tests/test_p5_pilot.py` | 19 passed, 1 skipped, 1 warning in 9.43s |
| `tests/test_p5_pilot_production_access.py` | 2 passed, 5 warnings in 6.27s |
| `tests/test_p5_production_access_provider.py` | 2 passed, 5 warnings in 5.64s |
| `tests/test_p5_command_api.py` | 20 passed, 1 warning in 10.96s |
| `tests/test_p5_patient_commands.py` | 78 passed, 2 skipped, 1 warning in 31.14s |
| `tests/test_p5_authority.py` | 14 passed, 1 warning in 6.84s |
| `tests/test_p5_sql_impact_authz.py` | 6 passed, 1 warning in 2.79s |
| `tests/test_p4_persistence_and_approval.py` | 13 passed, 1 warning in 5.11s |
| `tests/test_review_edit_approval.py` | 12 passed, 1 warning in 4.79s |
| `tests/test_review_wp3.py` | 8 passed, 1 warning in 4.76s |
| `tests/test_review_constraints.py` | 8 passed, 1 warning in 3.17s |
| `tests/test_review_view.py` | 11 passed, 1 warning in 3.84s |
| `tests/test_scenario_amendment.py` | 2 passed, 1 warning in 2.10s |
| `tests/test_uctsm_maker_checker.py` | 19 passed, 1 warning in 6.96s |
| `tests/test_mongo_parity_cutover.py` | 18 passed, 1 warning in 9.09s |
| `tests/test_mongo_parity_review.py` | 20 passed, 1 warning in 9.16s |
| `tests/test_mongo_parity_extraction.py` | 18 passed, 1 warning in 7.13s |
| `tests/test_mongo_parity_demo.py` | 6 passed, 1 warning in 4.51s |
| `tests/test_mongo_parity_operational_bridge.py` | 14 passed, 1 warning in 7.53s |
| `tests/test_mongo_extraction_runner.py` | 16 passed, 1 warning in 5.06s |
| `tests/test_p2_genuine_perigon.py` | 13 passed, 2 warnings in 5.95s |
| `tests/test_protocol_identity_workflow.py` | 22 passed, 5 warnings in 7.88s |
| `tests/test_uctsm_authz_policy.py` | 160 passed, 1 warning in 1.15s |
| `tests/test_canonical_journey.py` | 9 passed, 1 warning in 3.12s |

**Wider backend sweep** (every `tests/test_*.py` outside `test_remote_audit_*`, each file in its own process, same Mongo settings) was run before the last small edits and showed only failures that this work did not cause - see s8. The final run above re-ran every file this work touches or depends on after the last edit.

**Frontend:** `npm run test:uctsm` 190 passed, 0 failed (20 are the new amendment helper/controller tests); scoped `tsc -p tsconfig.uctsm-amendments.json` (the new route, screens, controller, helpers, `api.ts`, `PlaneScheduleScreen.tsx` and the new test) exits 0. A whole-project `tsc` is not run (documented out-of-memory limit).

**Real-browser journey** (`backend/scripts/p6_ui_journey.py`, Edge headless via Playwright against the real Expo web export, the real API on local Mongo, same-origin proxy `backend/scripts/serve_web.py`; evidence in [evidence/p6](evidence/p6): 13 screenshots + `journey.json` with each page's text). Three people are created through the product's own register and grants APIs. Steps: the PI opens the synthetic pilot and adds the amendment; sees the list; is refused the comparison (no `schedule.review`); opens the patient and is told a reviewer must compare first; the reviewer compares, sees the changes with their protocol-text sources, is blocked by an unreviewed match, confirms it under their name with a reason; the PI previews (consequences, changed/new/protected visits), confirms, and after a reload the calendar is the amendment schedule (`C1D1` moves 8 -> 11 January, `SAFETY_CALL` appears, history shows the decision); the PI previews and confirms a return and the calendar is Pilot 1.0 again. The script asserts the text at each step. The export was patched ONLY in the generated bundle (not the source) to point at the same origin, because the committed `.env` names a LAN address and release builds require HTTPS.

## 7. Supported cases and honest limits

Verified (memory and real Mongo unless stated): remain; immediate; date, event and cycle points; unreviewed, rejected, ambiguous and tampered mappings; rule-order fingerprints; rename, split (explicit rules, one destination, return by unique origin), merge (domain), removal (history stays on V1), addition; repeated occurrences with non-contiguous indexes; completed, missed and independent treatment facts; corrected facts (only the effective value is carried); both version histories queryable after adoption and return; V2-only fact blocks a return; stale, expired and forged simulations; idempotent replay and key conflict; injected transaction failure at two points (evaluation, audit) leaves nothing behind; concurrent confirms, replays, deferred-versus-immediate, worker races; a lost lease cannot commit and an expired one is reclaimed; changed grants (API revoke, worker re-check); cross-tenant, cross-patient and cross-trial ids; hashless historical versions; content/header tamper; effective-date tamper.

Not supported, kept visible (each is a blocker with an actionable message, never a silent drop): activity-level occurrence rules for a re-coded or split visit (activity records on such a visit block the move); patient-specific commands (pause, stop, rebase, override, cancellation) on the earlier version are not carried, so they block the move until reversed; re-adoption onto a version the patient already has history on; reversal over a many-to-one mapping without a reviewed transfer policy; deferred execution is an in-process worker (a durable queue belongs to P13).

## 8. Failures in the tree that this work did not cause

The wider sweep shows the failing files below. The sweep taken before any P6 edit (with Mongo down) shows identical counts for every file it reached (the `test_mongo_parity_*`, extraction, baseline, journey and scenario files); the `test_uctsm_*` files fall after that sweep's cut-off. P6 changed no file under `app/services/` and no SQL repository, and every one of these exercises those paths, the validator, or fixtures that predate the P5 authority guard: `test_mongo_parity_impact` (16), `test_mongo_parity_patient_plane` (14), `test_mongo_parity_dashboard` (6), `test_mongo_parity_read_models` (4) - these build operational-bridge mirror patients with no explicit authority, which the P5 authority guard refuses (`PlaneAuthorityError`, by design, F4); `test_extraction_compiler` (6), `test_extraction_wp3` (4), `test_baseline_resolution` (1), `test_scenario_conditions` (1), `test_journey_upload_to_patient` (1), `test_uctsm_qualifier_resolution` (2), `test_uctsm_operational_bridge` (1), `test_uctsm_version_effect` (4), `test_uctsm_workflow_reliability` (1) - SQL-path or extraction tests that fail on the validator's "activity has no executable condition" finding or on the SQL authorization wiring; `test_files` (2 errors). They are in-progress P1-P5 work, left untouched (preserve existing work); the P7 slice should triage them. In addition, `test_uctsm_authz_manifest::test_server_level_uctsm_entry_points_are_guarded_in_source` pinned an out-of-date wiring string for `create_uctsm_mongo_router`; its needle was updated to the current wiring (a test-only change).

## 9. Environment notes

* Memory is short and the system drive is full (a VS Code updater keeps refilling it): run Mongo suites with `TEMP`/`TMP` on `D:`, and never run a Metro export beside them - doing so exhausted memory and crashed mongod once (the interrupted run was discarded and re-run; no crashed run is counted above).
* The journey databases `mtb_p6_ui`, `mtb_p6_ui2`, `uctsm_p6_ui`, `uctsm_p6_ui2` and the test database `mtb_p6_test` hold only synthetic data and were left in place (only task-owned random test databases are dropped automatically).

## 10. Files

New: `backend/app/domain/schedule/amendment.py`, `amendment_impact.py`; `backend/app/application/amendments.py`, `amendment_common.py`, `amendment_plan.py`, `amendment_service.py`, `amendment_adoption.py`; `backend/app/api/uctsm_amendments.py`, `uctsm_amendment_worker.py`; `backend/app/persistence/memory/amendment_repository.py`, `mongodb/amendment_repository.py`, `mongodb/migrations/0009_amendments.py`; `backend/scripts/p6_ui_journey.py`, `serve_web.py`; `frontend/src/features/uctsm/amendments.ts`, `amendmentController.ts`, `PatientAmendmentPanel.tsx`, `AmendmentReviewScreen.tsx`; `frontend/app/(app)/clinical/amendment-review.tsx`; `frontend/tsconfig.uctsm-amendments.json`; the tests listed in s6; `docs/evidence/p6/`.

Changed (narrowly): `diff.py` (more concepts, tracing, mapping-aware reading), `authoring.py`, `ports.py`, `patients.py`, `patient_schedule.py` (assignment types that carry facts through a mapping), `schedule_documents.py`, `schedule_review.py` (approval stamp), both unit-of-work modules and `schedule_repository.py` (stamp, lineage, revision bump on content edits, `transition` refuses APPROVED), `errors.py`, `pilot.py` (synthetic amendment), `guard.py` (`trial_context`), `manifest.py`, `catalog.py` (wording), `uctsm_mongo.py` (registration + seed route), `server.py` (worker wiring), `PlaneScheduleScreen.tsx`, `api.ts`, `package.json`, `AGENTS.md`, the migration-count pin in `test_mongo_platform.py`, the route count and server-wiring needle in `test_uctsm_authz_manifest.py`, and the docs listed in the prompt index.
