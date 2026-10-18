# P6 independent review after the end-to-end completion report

Reviewed 16 October 2026 against the current uncommitted tree and the P6 rows in `D:\MTB_dev (2).pdf`.

**Verdict: P6 is substantially built, but it is not closed. Four defects reproduced on both memory and a real local Mongo replica set. Repair them before treating P6 as a safe foundation for P7/P8.** This review changes documentation only; no application fix, commit, deployment, authority change or real-patient migration was performed.

The implementer's [completion record](p6-completion-record.md) preserves useful evidence. Its claims of verified acceptance are superseded where the findings below contradict them. In particular, a successful browser happy path does not exercise authorization substitutions or a correction between the worker's two transactions.

## F1 — High: URL trial authorization does not constrain the versions being written

Affected: `backend/app/api/uctsm_amendments.py`, `declare_lineage` (line 173) and `prepare_pair` (line 184), and the corresponding application-service boundaries in `amendment_service.py`.

Reproduction uses two trials in the same organization, distinct persisted external trial ids, and an access provider granting only trial A. Direct access to trial B's amendment overview returns **404**, establishing that B is outside this actor's relationship. Both supplied versions belong to B.

* `POST /trials/A/amendments/lineage` with B's child and parent returns **201** and changes B's lineage.
* `POST /trials/A/amendments/pairs` with B's versions returns **404**, but B's comparison and mapping set have already been committed. The trial comparison happens after `AmendmentService.prepare_pair` returns from its transaction.

The handler checks A; the service checks organization and consistency between the two supplied versions, but neither binds those versions to A before writing. A not-found response after a committed write is not authorization enforcement.

Repair: resolve and bind both persisted version headers to the authorized URL trial before any write, ideally again within the application transaction through an explicit expected-trial argument. Retain tenant, protocol, lineage and child-parent checks. Test the response and absence of lineage, pair, mapping and business-audit changes on a denied request, including replay of an existing pair. Do not add a guessed relationship or weaken scope checks.

## F2 — High: deferred adoption's displayed impact differs from the committed calendar

Affected: `application/amendment_plan.py:252` and `:415`, `application/amendment_adoption.py:265`, and the shared reconciliation calculation in `application/patient_schedule.py`.

Using the existing synthetic pilot, preview adoption on **1 March 2027**. `C1D1:0` has an earlier-version target of **8 January 2026**. The preview reports its amended target as **11 January 2026**. Execute the confirmed decision on 1 March: the committed projection instead keeps **8 January 2026**, with `protected_history=True`.

Cause: `translated_prior` marks occurrences before the effective point as protected for persistence, while `build_plan` derives impact from a raw target-version evaluation and the original protected-key set. `_apply` subsequently runs the shared evaluator plus reconciliation, which preserves those earlier dates. The preview and persisted result therefore use different effective calculations.

Keeping earlier dates can be correct; showing a different outcome for the same decision is the defect. Use one side-effect-free evaluation **and reconciliation** result for preview, execution checks and persistence. For an effective point that is not known yet, label the result provisional and apply an explicit reconfirmation policy; do not promise raw dates that persistence will silently override. Thread and bind the business clock/as-of context explicitly, as P6-03 requires. Keep the displayed current calendar distinguishable from a recomputed comparison baseline.

## F3 — High: withdrawing the trigger after a lease claim does not prevent adoption

Affected: `application/amendment_adoption.py:373` (`_claim`) and `:402` (`_execute_claimed`), with `amendment_plan.trigger_state`.

Reproduction:

1. Confirm a decision to adopt when `C1D1:0` is performed.
2. Record it as performed, then claim the deferred decision's execution lease.
3. Through the normal occurrence-correction service, supersede that performed fact with **MISSED** before the execution transaction begins.
4. A fresh `trigger_state` now returns `due=False` and no effective date.
5. `_execute_claimed` nevertheless returns **APPLIED** and moves the patient to V2 using the old trigger date.

Cause: the claim stores the trigger snapshot; the application transaction trusts that snapshot. Material patient inputs are legitimately volatile during a deferred wait, and the structural-impact policy does not replace checking that the approved point still exists.

Repair: re-resolve the exact trigger from effective facts in the final transaction, compare the effective point, and use a shared patient/input revision or equivalent serialization guard against a concurrent correction. Withdrawn or changed triggers must not create an assignment, carried facts, evaluation, receipt or applied outbox. Settle safely as waiting or needing reconfirmation with an audit reason, according to an explicit lifecycle rule. Retain lease fencing; it protects worker ownership, which is a different concern.

## F4 — Medium: a waiting prefix can starve every later due decision

Affected: both amendment repositories' `list_pending_execution` and `api/uctsm_amendment_worker.py:process_due_once`.

The query always takes the oldest open WAITING/EXECUTING records, ordered by creation. A not-yet-due record remains in that prefix. With a batch limit of one, create a first decision for 2099 and a second for 1 March 2027. Two worker passes on 1 March both return only the first WAITING record; the due second patient remains on V1. This is the same defect with 100 older waiting records under the production default batch size of 100. The small-batch reproduction exercises the real worker and stores, without fabricating patients.

Repair bounded, fair progress across the candidate set, with cursor/rotation/next-check scheduling or an equivalent explicit contract. Future event/cycle decisions and held leases must not monopolize every pass. Apply bounds in the Mongo query rather than fetching all rows and slicing in Python. Durable queue infrastructure can remain P13 work; reaching due decisions correctly is needed now.

## Independent verification

* **155 passed:** mapping repair, content hash, amendment impact, adoption, adoption cases, atomicity, worker and amendment contracts; local replica set required, Mongo variants ran.
* **18 passed:** `test_p6_amendment_api.py`, run separately against memory and real Mongo.
* **20 passed:** `npm run test:uctsm-amendments`.
* All four findings above reproduced on **both stores** using [the saved reproduction script](evidence/p6-review/reproduce_findings.py). It uses task-owned random Mongo databases, drops only those databases and leaves the implementer's journey databases untouched. F1 substitutes authentication and a deliberately restricted access provider; it is not a fresh production-provider test.
* Inspected the stored browser journey and an after-reload screenshot. They support the constructed happy-path evidence. I did **not** rerun Edge or perform a new browser/device walkthrough, the full 46-file gate, all 271 P6 tests, all frontend tests or a new scoped typecheck.

Re-run the reproduction from the repository root:

```powershell
python -X utf8 docs/evidence/p6-review/reproduce_findings.py --store both
```

Its assertions deliberately document the defects as observed. It is not a passing acceptance gate; replace those assertions with safe-behavior regressions in the backend test suite when repairing the code.

## Existing failing suites need engineering triage

Two independently repeated failures with Mongo available:

* `test_mongo_parity_patient_plane.py -x`: **2 passed, 1 failed**; the fact-writing case raises `PlaneAuthorityError` for an unclassified patient.
* `test_extraction_compiler.py -x`: **44 passed, 1 failed**; `test_toxicity_hold_and_resume_become_pause_and_resume_block_actions` expects no issues, but the compiler produces `RECURRENCE_BASIS_UNCONFIRMED`.

These are consistent with earlier P4/P5 behavior changes. This review does not establish whether they predate P6. Editing no `app/services` file does not establish causality: shared domain, validators, authorization and fixtures can affect services indirectly. Triage all files listed in completion-record section 8. Update a stale expectation only when it contradicts the selected policy, and add the appropriate positive case; do not disable authority checks or default an unstated recurrence basis to make tests green. A sweep with Mongo down is not an equivalent baseline for Mongo-dependent behavior.

## Acceptance and next action

P6-01/02 have a route-scope defect; P6-03 has a preview-consistency defect; P6-04/05 have a withdrawn-trigger race, and P6-05 has worker starvation. Mark the affected integrated rows `NEEDS_REPAIR`, preserving their existing successful test evidence. Hash/immutability and the original mapping-order repairs remain useful verified sub-behaviors.

P6-02 also has a **functional limitation**, not just missing evidence: activity-level mapping for re-coded/split visits is unsupported. Existing command carry, history-bearing re-adoption and ambiguous inverse mappings remain explicit restrictions. Blocking safely is preferable to dropping facts, but it does not establish universal amendment support. Authentic signed amendment-pair evidence remains unavailable; do not manufacture it or equate constructed data with it.

The PDF contains **70 numbered tasks**. P1-P6 account for **30 addressed tasks (42.9% by task count)**, with repairs and acceptance gaps; P7-P14 account for **40 tasks still to assess/complete**. That is coverage of task identifiers, not a measured percentage of finished implementation or production acceptance. The current matrix does not justify saying P1-P6 are fully closed.

Current action: [Combined P6 repairs + P7/P8 prompt](claude-code-p7-p8-after-p6-prompt.md), including all four repairs, regression triage and the ten P7/P8 tasks in one file under the remaining-PDF master.
