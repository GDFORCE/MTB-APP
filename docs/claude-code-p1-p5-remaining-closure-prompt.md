# Claude Code continuation: close the remaining P1-P5 gaps

Read `docs/claude-code-p1-p5-final-closure-prompt.md` as the full requirements contract, then `docs/p1-p5-closure-followup-review.md` and the current closure report. Execute this continuation against the current working tree. Preserve the completed repairs and all unrelated work; do not restart P1-P5 or move unresolved prerequisites into P6.

## 1. Reopen and repair the R6 recording-order defect

Add a failing regression before editing:

- Record C1D1:0 treatment-only actual on 15 January 2026.
- Confirm the treatment-relative lab is dated 22 January and visit actual remains unknown.
- Separately record C1D1:0 visit actual on 8 January, without superseding the treatment fact.
- Both visit 8 January and treatment 15 January must survive reload; the lab must remain 22 January, and fixed imaging must remain fixed.

The current code drops the treatment in `_history` when it processes the later PERFORMED fact with no treatment field. Independently reproduced on memory and real Mongo. Fold the independent facts correctly; omitted input must not silently erase another acting fact. Preserve deliberately supported corrections of legacy combined facts. Do not fix this by copying treatment into visit, requiring redundant resubmission, or changing completion semantics.

Cover both recording orders, differing visit/treatment dates, explicit correction/supersession, repeated occurrences, applicable version lineage, HTTP preview/commit, idempotent replay and calendar reload. Run memory and real-Mongo regressions.

## 2. Close R7 beyond version equality

Version UUID equality is not authority/evaluation/fact equality. The current screen still reads SQL and commits Mongo commands. Make the permitted workflow use a consistent patient state and projection after confirmation. Add an explicit authority/active-plane contract and enforce it at the appropriate backend boundaries as well as in UI eligibility.

Until that path is supported, visibly disable mixed-plane commands even when versions match. This mitigates risk but does not satisfy production command acceptance; retain the blocked row honestly.

Prepare a concrete integration decision for the intended cohort with exact source changes, identity/version mappings, condition/anchor/fact/ledger ownership, display APIs, transaction boundary, rollout and rollback. Reuse existing approved architecture decisions where available; an actual authority change requires the owner's decision.

Correct the prior pilot recommendation: existing `ParityService` is legacy operational versus SQL canonical, not SQL versus Mongo command-plane parity. Existing `operational_bridge` is SQL-backed, not an already-built Mongo enrolment bridge. Preserve that parity gate, and add the necessary supported plane comparison/integration rather than claiming the existing gate proves it.

Do not ask only “which trial?” before preparing the missing design. A pilot copy that keeps SQL display while changing Mongo does not close the defect. Unpinning is not full clinical rollback once facts/commands/outbox effects exist. Define retention, reconciliation, command effects and reminder/outbox handling without deleting history.

Prove a command's committed evaluation and persisted calendar are the ones presented through production navigation after reload. Cover equal versions with divergent facts/evaluations, absent plane, stale assignment/version, and revoked access.

## 3. Verify R9 in actual components and R8 through its mounted sheet

Add a small appropriate component test setup or use an actual local browser/device harness. Existing pure helpers are insufficient. Do not treat the absence of an existing harness as permission to claim effects were tested.

Test delayed patient A requests after switching to B, version changes, obsolete previews and confirms, unmount, unavailable/denied/absent states, retry, and successful commit followed by failed reload. Show a committed-but-refresh-failed outcome accurately; do not imply nothing was saved or invite a new duplicate command.

Exercise the new process-linked stop policy control (not stated/allowed/disallowed), material-edit provenance and API round-trip. Complete a scoped typecheck that actually includes the `ProtocolScheduleScreen` mount, preserving Expo/JSX/alias configuration without suppression. If environment limitations persist, report the exact unverified behavior/files.

## 4. Finish the previously deferred audits

Re-audit live legacy upload/consume wrappers for central capabilities, trusted scope and P2 identity enforcement; test entry points rather than relying on router names. Re-audit cross-org list bounds/pagination for permitted users and tenant privacy. The approved grant matrix remains a separate owner decision.

For R2, add evidence through the production persisted operational assignment/invitation provider, not only `FakeOperationalAccess`. Preserve forged-ID, foreign-parent, ambiguity and revocation denials, plus canonical-owner writes.

Keep genuine PERIGON/amendment fixtures and registry verification explicit. Search authorized local inputs; if unavailable, list the exact needed files/provider configuration and continue independent work. Never relabel synthetic fixtures or scripted providers as real evidence.

Do not invent approval of the role matrix, self-grant scope, CURRENT target-relative meaning, or TRIGGER/PRECONDITION actual semantics. Prepare recommendations and concrete questions after the reviewable code and policy packet are ready.

## 5. Verification and final handoff

Follow repo test instructions and run affected regressions first, then the applicable curated gate in appropriate separate processes. Use an explicit local replica-set Mongo and isolated databases. Run actual frontend behavior checks, relevant scripts, scoped typechecks and a production-route walkthrough where available. Report failures/skips and unsupported cohorts honestly.

Update the closure report and plan at their original tables, not only in appended sections. Reopen R6, accurately describe R7 as unresolved, distinguish source R9 changes from component verification, and correct the pilot/rollback/parity assertions. Align obsolete-failure wording with whether it raises or safely ignores.

Reassess each of the 25 PDF rows against its own acceptance criteria; do not withhold domain-only verification solely because a different UI flow lacks a walkthrough. Do not call the complete epic accepted while its production workflows, owner approval or mandatory external evidence remain blocked.

Finish with resolved findings, exact evidence and remaining row-level gates. Do not commit, push, deploy, mirror live patients, switch authority or change trial read mode without the required authorization. No claim of P1-P5 closure until the full applicable acceptance criteria are met.
