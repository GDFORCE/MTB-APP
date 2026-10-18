# Review of the latest R6 / authority / controller repairs

Reviewed 14 October 2026 against the pasted implementation update, current source, closure report and integration decision. This review did not modify application source, approve a cohort/policy, switch authority, enrol patients or commit work.

## Verdict

The previously reproduced R6 treatment-first/visit-only loss is repaired in the cases independently checked. R7 remains unresolved and its backend fail-safe has an independently reproduced bypass. R9's tested controller improves the evidence, but additional controller action sequences expose gaps. P1-P5 are still not closed.

## 1. R6: the recording-order repair passes

Independently ran:

- Both independent recording orders, retaining visit 8 January and treatment 15 January.
- The lab remains 22 January and fixed imaging remains 31 January.
- Explicit supersession of a combined visit/treatment fact.
- HTTP preview, confirm, replay and calendar reload.

Memory and real local Mongo variants ran, with no skips. These verify the repaired cases; this is not a claim that all possible clinical fact corrections have been audited.

## 2. R7 fail-safe bypass: the older actual route ignores authority

Sources:

- `backend/app/application/patient_commands.py::_version` checks `authority_plane` for preview/confirm.
- `backend/app/api/uctsm_mongo.py::record_actual_occurrence` calls `PatientScheduleService.record_actual_occurrence` directly.
- `backend/app/application/patient_schedule.py::record_actual_occurrence` does not check authority before writes or stored receipt replay.

HTTP reproduction using the actual router/guard, test authentication and `AllowAllAccess`, first with memory and then an isolated real local Mongo database:

1. Create the current P5 fixture and persist `authority_plane="SQL"` on its canonical patient.
2. POST `commands/preview`: **409 PLANE_NOT_AUTHORITATIVE**.
3. POST the older `actual-occurrences` route for the same patient: **201**.
4. The Mongo store contains **one new occurrence fact and one command ledger row**.

The old route is intentionally ledger-integrated, so this is an alternative schedule-changing entry point that bypasses the new rule. UI disablement does not protect direct HTTP clients.

Required repair: one shared authority decision at the relevant application mutation boundary, enforced before any mutation and before replay. Apply it across old actual recording, correction, commands and other equivalent schedule-changing entry points. Preserve readable retained history and agreed denial semantics. Add HTTP and direct-service regressions that assert no partial facts, ledger, evaluation, receipts or outbox changes for non-authoritative patients, including after authority changes between preview and confirm or before replay.

Compatibility concern, not a separately proven existing mirror incident: `authority_plane=None` is deliberately treated as native Mongo. The enrolment constructor defaults to None. The field's absence cannot independently prove whether an imported historical patient is native or mirrored. Document and enforce the trusted native/import construction paths, use explicit authority/provenance for copies, and do not allow user-controlled authority or silent reassignment. A typed/persisted authority contract and a deliberate compatibility classification are needed; do not arbitrarily relabel existing clinical patients.

## 3. R9 controller: delayed cancellation clears a newer preview

Source: `frontend/src/features/uctsm/commandController.ts::discard/preview` and panel command-type/reversal actions.

Controller reproduction with the actual controller and a deferred API cancellation:

1. Preview OLD for patient A.
2. Call Discard; its cancellation request is still pending.
3. Request and receive preview NEW for the same patient/version.
4. Complete cancellation of OLD.
5. Snapshot changes from `preview.proposal_id=NEW` to **preview=null**.

`discard` carries only the identity generation, not the proposal identity/request generation. Both previews have the same patient generation, so the old cancellation clears the newer proposal. Discard also does not enter a busy/action state that prevents competing actions. Existing tests cover identity switches but not this same-identity race.

Required repair: bind cancellation outcomes to the exact proposal and action revision, or serialize competing proposal actions deliberately. A stale cancellation may not clear/overwrite a later preview. Test command switching, reversal preview while cancellation is pending, cancellation failure, rapid discard, and cancellation versus confirmation. Preserve per-proposal retry keys and explicit unknown-outcome handling.

## 4. R9 controller disposal does not prohibit new work

Reproduction with the actual controller:

1. Set a supported Mongo-display identity and obtain a preview.
2. Call `dispose()`.
3. Call `confirm()`.
4. **The API confirm function is invoked once.**

`publish` suppresses disposed callbacks, but public actions do not check disposal before starting API work. Existing unmount tests check absence of updates rather than absence of new requests. This reproduction invokes the controller directly; a mounted React/browser user interaction after unmount was not reproduced.

Guard all public actions after disposal. Already dispatched mutations cannot simply be unsent; retain correct handling of their unknown/committed outcomes. Also verify the React binding's setup/cleanup/setup lifecycle: it creates the controller once in a ref and permanently disposes it on cleanup. Do not assume a controller that was disposed can be reused without a new lifecycle. This is a source review concern requiring a binding test, not a reported browser incident.

## 5. Integration decision: improved, still a proposal

The corrected decision now separates SQL/Mongo authority from legacy/engine read mode and retains facts/commands during rollback. Those are necessary corrections, not implemented migration evidence.

There are two different bridge implementations: the live SQL bridge at `app/services/operational_bridge.py`, and the existing repository-port bridge at `app/application/operational_bridge.py`. Reuse and assess the latter for native plane enrolment; the live HTTP enrolment/display/authority handoff still needs implementation and verification. Do not say no port bridge exists or treat it as a live approved cutover.

The proposal should also make authority enforcement for every legacy/SQL/Mongo writer concrete, freeze/migration race handling explicit, and rollback feasible for all exported command effects. Setting a field on the Mongo patient alone does not freeze SQL writes. Retention/export of ordinary actual facts alone does not prove that SQL can represent track rebase/hold/override semantics during rollback.

No pilot trial or policy is approved by this review. Complete the independent repairs and concrete design work before asking for an essential authority decision.

## 6. Verification and reporting limits

| Independently run check | Result |
|---|---|
| P5 separate actual orders, lab retention, combined correction and current authority-denial regression | 10 passed |
| P5 HTTP treatment/visit recording, replay and reload | 2 passed |
| Actual controller tests, directly through Node | 9 passed |
| Additional authority-bypass reproduction | Confirmed on memory and real Mongo HTTP |
| Additional deferred-cancel and disposed-confirm reproductions | Confirmed through the real controller |

All mutable Mongo operations used explicit local replica-set settings and isolated databases. Only the review's own random databases were removed.

The frontend package does **not** currently define `test:uctsm-command-controller`; the tests were run with `node --experimental-strip-types --test tests/uctsm-command-controller.test.ts`. Add a real script/gate entry so the new suite is not omitted from routine verification. This review did not rerun the full curated gate, frontend scoped typecheck, browser/device walkthrough or PostgreSQL locking.

The latest closure report still has stale version-only eligibility in P1-05/P5-05 and its plane contract, despite R7's newer SQL-screen disablement. Update original rows consistently. Continue acceptance classification per actual PDF row; do not make missing browser verification a blanket reason to withhold a complete domain-only requirement.

Genuine PERIGON/amendment evidence, approved policies, production relationship-provider coverage, supported production commands, cross-org pagination/truncation visibility, and relevant UI/locking verification remain open. Ordering the first 500 results does not resolve silent omission after that bound.

PDF progress remains **25 of 70 task areas reached (35.7% by count)**, not a certified completion percentage.
