# P1–P4 progress and dependency audit

Reviewed 14 October 2026 against the current working tree, the task/dependency columns in `D:\MTB_dev (2).pdf`, and the P1–P4 implementation records.

## Count and meaning

The PDF contains 14 epic headers and 70 numbered tasks (five per epic). P1–P4 account for **20 implemented tasks, approximately 29% by task count**. This is an implementation count, not a release-readiness percentage, an effort estimate, or confirmation that all acceptance criteria are closed. P5–P14 account for the remaining 50 tasks not completed in this sequence; pre-existing code in those areas is not counted as newly verified completion.

| Group | Current implementation record | Remaining closure/integration items |
|---|---|---|
| P1, 5 tasks | Explicit date sources, version/history reconciliation, Mongo command and HTTP route, local-Mongo concurrency/rollback/API tests | Mongo patient-plane route is not used by the frontend or live enrolment; pending-anchor derivation differs from SQL. Preserve the current authority boundary. |
| P2, 5 tasks | Identity gate, duplicate/version rules, review UI/API, local-Mongo tests | Actual PERIGON corpus verification, real external registry integration, browser/device verification. Synthetic fixtures are not the actual source documents. |
| P3, 5 tasks | Capability guards, grants, scope/privacy checks, maker-checker, matrix tests | Owner approval of proposed role/default policies; legacy upload/consume wrappers not fully integrated into the catalog; bounded cross-org listing; SQL production-locking verification. |
| P4, 5 tasks | Conditional outcomes and explicit recurrence bases implemented; 48 existing tests passed again during this review | Confirmed approval-validation and safety-cap gaps below; missing-termination-event policy/coverage; no app control to resolve extracted interval bases; dependency-type owner decision. |

Nothing in this audit approves P3's proposed policy or changes clinical-data authority. No application source was modified during this review.

## PDF dependency ordering

The dependency column confirms:

- P1-02 -> P1-01; P1-03 -> P1-02; P1-04 -> P1-03; P1-05 -> P1-04.
- P2 and P3 each have their own five-task sequential chain.
- P4-02 -> P1-01; P4-03 -> P4-02; P4-04 -> P4-01 and P4-03; P4-05 -> P4-04.
- P5-01 -> P1-01; P5-02 -> P5-01 and P1-03; P5-03 -> P5-02; P5-04 -> P5-03; P5-05 -> P5-04.
- P6 epic -> P1, P3, P4, P5; P7 -> P2, P4, P5, P6; P8 -> P2, P6, P7; P9 -> P1, P4, P5, P7; P10 -> P1, P3, P5, P6, P7.
- P11 -> P6, P9, P10, P13; P12 -> P3, P5, P6, P10; P13 -> P1–P12; P14 -> P1–P13.

The P1–P4 prompt sequence respects the stated dependencies. However, an implemented prerequisite with unresolved acceptance or policy decisions is not a fully closed prerequisite. Later epics also have cross-cutting/cyclic readiness dependencies (for example P11 requires P13, while P13 includes P11); numerical order alone cannot be a release plan. Build and verify operational/platform controls incrementally, then close the relevant release gates together.

Before P5, preserve and verify P1 identity/history, P4 date/basis semantics, and P3 authorization for every new command. Keep approval rights and clinical dependency semantics explicit; do not invent them to unblock implementation.

## Findings from this review

### 1. Approval validation misses conflicting recurrence starts

In `backend/app/domain/schedule/validator.py::_interval_recurrences`, conflict detection depends on matching references and comparable unit families. When those checks fail for an explicitly stated basis, the contradictory start can pass without a blocking issue.

Two deterministic reproductions using the existing P4 fixtures:

- Event timing: baseline +14 days; `FIRST_RESOLVED_OCCURRENCE`; recurrence `start_offset`: +1 calendar month. Validator returns **no blockers**. Evaluation returns **UNRESOLVED**, citing 15 January versus 1 February 2026.
- Event timing: BASELINE +14 days; `FIXED_ANCHOR_SERIES`; grid: OTHER +14 days. With BASELINE on 1 January and OTHER on 2 January, validator returns **no blockers**. Evaluation returns **UNRESOLVED**, citing 15 versus 16 January.

This leaves approval and evaluation inconsistent. A structurally unsupported/ambiguous reconciliation must block approval with an actionable path, rather than first becoming unusable for a patient. Patient-specific missing inputs must still be distinguished from invalid protocol structure.

### 2. COUNT termination bypasses the reported 10,000-occurrence safety cap

`evaluator.py::_resolve_termination` replaces `bound.max_count` with the protocol COUNT value, and interval expansion loops to that value. A COUNT of **10,001**, one-day cadence, and a sufficiently long horizon generated **10,001 events**, with no approval blockers and no safety marker.

The reported 10,000 cap currently bounds non-COUNT intervals, not all interval expansions. Keep protocol count and engine safety cap separate, apply the resource bound consistently, and report incompleteness when the cap interrupts a longer valid protocol series. Do not mislabel it as a clinical maximum.

### 3. Missing EVENT termination silently uses the horizon

`_resolve_termination` sets an EVENT boundary only when `context.event_values` contains the ending event. Otherwise it retains the horizon without indicating a missing ending source.

Reproduction: weekly LABS ending at STOP, with STOP waiting for its missing END anchor. LABS generated five resolved targets through the horizon while STOP remained undated. No termination warning was returned.

Whether projected provisional visits are acceptable before an ending event occurs requires explicit supported semantics. The P4 prompt required missing source/termination coverage and prohibited a silent substitution of horizon termination. Add a regression and make the behavior visibly provisional/waiting or otherwise match an evidenced policy; do not guess an end date. The existing P4 tests cover an available EVENT end, not this missing-source case.

### 4. Extracted-repeat approval is blocked without a production UI resolution path

The compiler sets every extracted interval recurrence to `basis_status=UNCONFIRMED`. Approval correctly blocks it. However, the app's `visitSchedule.ts::buildEditRequest` only builds name/timing/window changes; the existing edit sheet supplies no recurrence-basis control.

The journey succeeds by supplying a typed API edit, not by proving a sponsor can resolve the basis through the app. Add a capability-aware review control for basis/status/actual source and appropriate start semantics, with rationale and revision handling. Keep the existing permission and maker-checker checks. This is a concrete product integration gap even though the backend API exists.

### 5. Repeated-source dependency selection needs explicit verification

`dependency.py::evaluate_dependency` treats a source as satisfied if **any** occurrence status is RESOLVED or COMPLETED. It receives an event-code-wide status list, not the referenced occurrence selection. That may be appropriate for an explicitly event-level prerequisite, but is not proof that a particular FIRST/LAST/CURRENT or numbered source occurrence is available.

This review does not declare that all such uses are defective. Add occurrence-specific regressions before applying these results to P5 commands/tracks, and document which dependency semantics are event-level versus occurrence-level. The TRIGGER/PRECONDITION actual-occurrence question remains an owner decision; retaining explicit dependency-mode behavior is not equivalent to approving a new implicit rule.

## Independent verification performed

Both targeted modules were rerun in isolated invocations with local-only Mongo settings:

```powershell
$env:UCTSM_TEST_MONGO_URL='mongodb://127.0.0.1:27017/?replicaSet=rs0'
$env:UCTSM_REQUIRE_MONGO='1'
$env:MONGO_URL='mongodb://127.0.0.1:27017/?replicaSet=rs0'
$env:DB_NAME='mtb_p4_review_test'
# Run each from backend/ in a separate invocation:
python -m pytest -q tests/test_p4_dependency_recurrence.py
python -m pytest -q tests/test_p4_persistence_and_approval.py
```

- Domain module: **35 passed**.
- Persistence/approval module: **13 passed**, including real local Mongo variants; no skips.
- The additional edge-case reproductions above executed against the current shared domain evaluator/validator and produced the documented results. They were exploratory checks, not added repository regression tests.
- The full 423-test gate, journey, and complete P1/P2/P3 suites were not rerun by this audit. Their reported results remain the previous engineer's verification record.

## Recommended next step

Close P4's approval-validation, safety/termination, and review-control gaps with regression tests before treating P4 as a completed prerequisite. Obtain the outstanding P3 policy decisions separately. P5 design can proceed using the verified P1 contracts, but later acceptance and rollout must not silently inherit these open assumptions.
