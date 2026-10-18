# Independent follow-up on the P1-P5 closure report

Reviewed 14 October 2026 against the current source and `docs/p1-p5-closure-report.md`. Application code was not changed. Existing work remains uncommitted.

## Verdict

P1-P5 remain open. Focused regression runs support the reported R1-R6 repairs, but an additional treatment/visit recording sequence reproduces a defect in R6 on both memory and real local Mongo. R7's version check addresses the wrong-version case without resolving the inconsistent read/write plane. R9 has source changes but no component-level proof.

This review does not repeat the implementer's full gate, browser/device, typecheck, or PostgreSQL claims as independently verified evidence.

## 1. Reopen R6: recording attendance clears an independent treatment actual

Source: `backend/app/application/patient_schedule.py::_history`, particularly the PERFORMED branch that calls `treatment.pop(key, None)` when that visit record carries no treatment date.

Reproduction using the current P5 fixture and command service:

1. Record treatment-only for C1D1:0, treatment date **15 January 2026**.
2. Calendar shows actual treatment **15 January**, no visit actual, and LAB_AFTER_TX:0 on **22 January**.
3. Record visit-only for that same occurrence, visit date **8 January 2026**, without superseding the treatment record.
4. Calendar now shows visit actual **8 January**, treatment actual **null**, and LAB_AFTER_TX:0 **BLOCKED**, with no planned date.
5. Both facts remain persisted: TREATMENT(15 January) and PERFORMED(8 January, treatment=null).

Observed on the in-memory UoW and an isolated migrated local Mongo replica-set database. The Mongo reproduction used the repository's configured client builder, including standard UUID and timezone-aware decoding, and removed only its own randomly named test database.

An omitted treatment field in an independent attendance record must not erase an acting treatment fact. The new tests cover treatment-only and visit-only separately, and a combined record, but do not cover treatment-first followed by separately recorded attendance.

Repair: fold visit attendance/outcomes and treatment facts independently. Apply explicit supersession/correction semantics to remove or replace facts; do not interpret missing input as a silent withdrawal of another fact. Preserve legacy combined-record correction behavior deliberately. Add memory, real-Mongo and HTTP regressions for both recording orders, different actual dates, treatment/visit corrections, reload, and repeated-occurrence identity. Keep visit-relative and treatment-relative downstream rules independent.

## 2. R7 remains a workflow correctness blocker

`planeConsistency(displayedVersionId, planeVersionId)` returns ready when version UUIDs match. `PatientScheduleScreen::load` still uses SQL schedule, projection, anchor, deviation and condition APIs. The command panel still commits to Mongo and calls the parent's SQL reload.

Matching definition versions does not imply matching patient facts, command ledgers, anchors, conditions, current evaluations or authority. A Mongo command can change future dates without changing the SQL calendar under the same version UUID. The mitigation blocks the mismatched-version case but does not prevent that inconsistency.

The panel also renders only a Mongo calendar visit count after reload; it does not replace the persistent SQL schedule presentation with the committed Mongo projection. This remains source-verified; this review did not exercise a real browser or a dual-plane patient screen.

Closure requires one supported read/write/evaluation path, or a proven supported integration contract that keeps the displayed clinical state coherent. Until then, disable commands in the mixed-authority state, rather than enabling on version equality alone. A disabled panel is a fail-safe, not PDF P5-05 completion.

## 3. The pilot proposal needs correction before asking for a trial

`ParityService` receives a SQL session and compares legacy operational visits with the SQL canonical schedule service. Its read-mode mechanism chooses LEGACY versus ENGINE; it is not a SQL versus Mongo command-plane selector. The operational bridge in `app/services/operational_bridge.py` is also SQL-backed today.

Consequently, enrolling a copy into Mongo, running existing parity, and keeping SQL display does not establish Mongo command correctness or display consistency. Passing parity before a command does not keep two independent stores synchronized after that command.

Prepare the missing integration: supported cohort, canonical IDs/version transfer, anchors/conditions/history, authority enforcement, displayed calendar source, preview/commit source, evaluation identity, and SQL/Mongo comparison evidence where needed. Preserve the existing legacy-engine parity gate. A named trial alone does not authorize or implement these pieces.

Unpinning a Mongo copy after commands have committed disables access; it does not undo clinical facts, reconcile the SQL projection, reverse ledger commands or withdraw outbox/reminder effects. Define rollback with the retained command/fact history and effects explicitly. Do not delete history or claim unpinning alone restores clinical parity.

No mirror, cutover, enrolment or policy approval was performed in this review.

## 4. R9 and remaining verification

The panel now includes generation checks and identity-bound resets. Current frontend tests exercise pure builders/probe/version helpers, not React effects or async response ordering. No existing component harness is a reason to add a small suitable harness or perform an actual browser/device check; it is not lifecycle proof.

Verify delayed A-to-B patient switches, version changes, stale preview/confirm results, unmount, failed post-commit reload, access revocation, denied/unavailable states and retry. Check that a successful commit followed by reload failure is not reported as though nothing was saved.

R8's explicit stop control is present; its actual mounting/use remains unverified here. The unchanged large-screen typecheck, browser/device, relevant PostgreSQL locking, real PERIGON and registry-provider limits remain as described in the implementer's report.

The R2 HTTP regressions still use `FakeOperationalAccess`; they verify canonical owner resolution with a permitted relationship, not production persisted assignment/invitation lookup. Retain that evidence distinction and cover the production relationship path before claiming end-to-end scope closure.

The report should also align R1 denial wording with source: obsolete complete/renew raise `ExtractionClaimLostError`, while obsolete failure currently returns without changing the job. Both can be safe, but they are not the same observable outcome.

## 5. Independent checks run

All mutable Mongo checks targeted explicit local `127.0.0.1` replica-set settings. Test modules ran in separate processes and used isolated contract databases.

| Focused check | Passed |
|---|---:|
| P2 obsolete worker, lease renewal, winning retry and takeover race | 9 |
| P3 Mongo cross-org owner/forged resource HTTP regressions | 8 |
| P5 history privacy sentinels | 2 |
| P5 unknown/stated window and independent actual regressions | 11 |
| P3 unknown/malformed/unsupported role selections | 15 |
| **Backend total** | **45** |
| Frontend patient-command script | **10** |

No skips occurred in these selected runs. The new treatment-first/visit-only reproduction fails the intended retention invariant despite these existing tests passing. No component/browser tests, full curated gate or frontend typecheck were run by this reviewer.

## 6. Progress interpretation

The PDF still contains 70 numbered tasks, 25 in P1-P5. **35.7% is task-area coverage, not certified completion or effort.** R6 must be reopened, R7 remains unresolved and R9 remains unverified at the component level.

Do not make a browser walkthrough a blanket prerequisite for every domain-only PDF row. Assign acceptance states against each row's actual criteria: an enum/serialization unit requirement can have complete evidence while a production command/UI requirement remains open. Keep overall epic closure and production approval separate. The closure report's blanket statement that no row is verified solely because one of these shared limits exists should be reassessed criterion by criterion.

Owner policy approval, genuine P2-05 documents and the unre-audited sensitive wrappers/list bounds remain explicit gates. Missing extraction track proposals alone need not block closure if the required evidence-based manual review workflow is reachable and verified.
