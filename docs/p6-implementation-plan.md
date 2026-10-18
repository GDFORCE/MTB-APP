# P6 implementation plan: immutable amendments, semantic diff, explicit adoption

Prepared 16 October 2026 for the focused slice in [claude-code-p6-domain-to-app-completion-prompt.md](claude-code-p6-domain-to-app-completion-prompt.md); a slice of the [P6-P14 master](claude-code-p6-p14-remaining-pdf-prompt.md). Findings that opened the slice: [p6-domain-review.md](p6-domain-review.md). The result and evidence are in [p6-completion-record.md](p6-completion-record.md).

## 1. Starting point

The 26 passing domain/architecture tests were a baseline, not acceptance: `amendment.py` existed with a content hash, an event mapping and adoption-point validation, but nothing was persisted, nothing ran at the approval boundary, and there was no simulation, adoption, API or UI. `amendment.adopt` was catalogued and held by nobody. The worktree already carried uncommitted P1-P5 work (authority guard, tracks, commands, pilot, protocol identity); all of it was preserved, and P6 builds on it rather than beside it.

## 2. Invariants the design protects

1. **An approved version is an exact artefact.** Its content hash is computed from the content read in the same transaction as the approval, stamped in the same guarded write, bound to the header revision it was read at, and re-verified before anything is compared, simulated or adopted from it.
2. **A fingerprint identifies behaviour.** Two mapping sets with the same fingerprint translate every occurrence identically; ordering never matters.
3. **A fact moves only through a unique, reviewed destination.** No first-match, no duplicated completion, no guess at an inverse.
4. **Nobody is moved by default.** Approval of V2 changes no patient. Adoption is a per-patient, authenticated, authorised, simulated, bound decision.
5. **History is append-only.** V1 documents, original fact ids and all V2 records stay queryable; a carried fact is a NEW row with its own provenance, never a rewritten one.
6. **One open decision per patient**, enforced by the store, so immediate, deferred and return operations cannot double-assign.

## 3. Requirement matrix

| Req. | What existed | Repair / build | Store | API / UI | Verified by |
|---|---|---|---|---|---|
| **P6-01** lineage, hash, immutability | `content_hash` field only | Hash contract `schedule-content.v1` with canonicalised collections and a version string; `header_integrity_hash` (covers `effective_from`, lineage, approver); stamped in `mark_approved` on both approval paths and by the repositories when a direct caller supplies none; revision-bound (`expected_revision`) and content edits move the header revision; `set_lineage` (once, before approval); legacy hashless versions reported `UNVERIFIED_LEGACY`, never declared verified or rewritten | migration `0009`: typed header fields, APPROVED requires a hash (validation `moderate` so history is never rejected), `ix_version_based_on` | `POST /trials/{id}/amendments/lineage`; versions list on the amendment screen | `test_p6_content_hash` (25), `test_p6_approval_hash` (22 incl. real-store validator, migration, concurrent approve/edit) |
| **P6-02** change set + mapping | event-code diff only | `diff.py` covers anchors, tracks, links, track membership, populations/arms/cohorts/dimensions, processes, calendars, epochs, conditional definitions, confinement, residual event attributes and evidence; every change traced to protocol text on both sides; mapping-aware presentation (a re-coded visit is one change); event mapping repaired (see s4) and persisted with append-only authenticated review decisions | `amendment_pairs`, `amendment_mapping_sets` (guarded revision), `amendment_mapping_decisions` (append-only) | compare / review routes; `AmendmentReviewScreen` | `test_p6_semantic_diff` (11), `test_p6_mapping_repair` (10), `test_p6_amendment_service` (24) |
| **P6-03** simulation | none | `build_plan`: one read-only calculation, V1 vs V2 under one frozen context, mapping-aware per-visit impact (unchanged / moved / status / protected / added / removed / waiting / blocked), bound to patient, both content hashes and header integrity, effective dates, mapping revision, authority, patient/pointer revision, material-context fingerprint, policy/evaluator/schema versions, adoption point; 30-minute expiry; stale or forged bindings refused at confirmation | `amendment_simulations` (non-clinical provenance) | `POST .../amendments/simulate`; `PatientAmendmentPanel` | `test_p6_adoption`, `test_p6_amendment_impact` (7) |
| **P6-04** explicit choice | none | remain / immediate / event / cycle / date; point validated against the CURRENT version; cycle start by the evaluator's own contract (`actual_cycle_start`), not occurrence index N-1; a waiting trigger never invents a date; decision records actor, capabilities, reason, simulation, bindings, material hash | `amendment_decisions` | confirm / execute / cancel routes | `test_p6_adoption`, `test_p6_adoption_points` (10), `test_p6_amendment_api` (18) |
| **P6-05** atomic execution + reversal | none | one transaction: carried facts, assignment, pointer, evaluation + projections, audit, receipt, outbox, decision close; fenced lease for deferred execution; renewed preconditions and an explicit future-impact policy; a return is a NEW decision with its own preview and a NEW evaluation, V2 facts preserved, unsupported inverses block | open-decision partial unique index, idempotency index | `Idempotency-Key`; return preview + confirm | `test_p6_adoption_atomicity` (11, real concurrency + failure injection) |
| Authorization | `amendment.adopt` unheld | manifest entries for all 11 new routes; adopt = `amendment.adopt`; return also `patient.command.high_impact`; trial routes check the persisted trial relationship; recheck on confirm, replay and execution | - | 403 / 404 semantics | `test_uctsm_authz_manifest`, `test_p6_amendment_api`, `test_p6_amendment_production_access` (production relationship provider) |
| UI reachability | none | route `clinical/amendment-review`; panel on `clinical/plane-schedule`; pilot chooser can add the synthetic amendment | - | real browser journey | `backend/scripts/p6_ui_journey.py`, `frontend/tests/uctsm-amendments.test.ts` (20) |

## 4. Mapping repair (before any storage)

Reproduced `docs/p6-domain-review.md` A-C, then:

* a source occurrence sent to two visits is rejected at validation; exact repeats collapse; rules are stored in canonical order, so reordering changes neither behaviour nor fingerprint;
* forward and reverse translation return a unique answer or `UNRESOLVED` with a reason; two rows naming one visit are ambiguous, never first-row;
* a many-to-one relation has no unique inverse from the mapping alone, so a reversal over it is blocked until a reviewed transfer policy exists (a visit with nothing recorded on it simply has no counterpart);
* structural coverage (`mapping_coverage_problems`) and review readiness (`mapping_readiness_problems`) are separate, and `mapping_problems` returns both; the service gate adds provenance: an `AUTO_CONFIRMED` row must be re-derivable from the two snapshots, a reviewed row must have an authenticated decision by the same reviewer, and the stored fingerprint must match the stored rows;
* `AUTO_CONFIRMED` is refused for splits/merges and for any basis but an exact or no-counterpart match; a rejected proposal stays on record while its replacement supplies coverage.

## 5. Transaction design and alternatives

* **Why one transaction, retried.** The Mongo unit of work re-runs the callback on transient errors, so every function here is deterministic and free of side effects (carried rows are built in memory, written once).
* **Refusals that must leave a trace** (stale/expired simulation, a due decision that no longer applies) are committed first and reported after, as in the impact workflow.
* **Deferred execution is two transactions** by design: claim a fenced lease (committed), then apply under it. A crash between them leaves an expired lease the next attempt reclaims; a worker that lost its lease fails on the revision guard.
* **Rejected alternative: carry facts by lineage** (what the impact flow does). It treats every shared event code as equivalent, which is exactly what P6 forbids. `AMENDMENT_ADOPTION` / `AMENDMENT_RETURN` assignments are skipped by `version_lineage`; facts travel as explicit provenance-marked rows.
* **Rejected alternative: a second amendment engine.** Evaluation, projections, assignments, receipts, outbox and audit reuse the existing services and ports.
* **Compatibility.** New header fields are optional; the migration is additive and resumable; existing approved versions stay readable and are reported `UNVERIFIED_LEGACY`; no existing route changed behaviour.

## 6. Verification plan

Focused defect tests first, then memory + real local Mongo contracts, isolated API modules, concurrency and failure injection, the production relationship provider, the manifest, then the affected P1-P5 suites (maker-checker, parity, history, commands, journey), scoped TypeScript, the Node tests, and a real-browser two-user journey. Mongo variants run with `UCTSM_REQUIRE_MONGO=1` against an explicit local replica set; none is skipped.
