# Claude Code continuation prompt: close P1 integration and verification gaps

Copy the prompt below into Claude Code in MTB-APP. This continues the partial P1 result; it does not authorize a global Mongo scheduling cutover.

---

Finish the remaining P1-01–P1-05 gaps in `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`. Read `AGENTS.md`, `docs/p1-occurrence-recalculation-plan.md`, the existing P1 prompt, current source, and working-tree diffs first. Preserve all uncommitted changes.

P1 currently has version-scoping fixes and in-memory service tests, but no HTTP route invokes the Mongo-port actual-occurrence command, real Mongo checks were skipped, concurrent-request tests were not added, and repeated-occurrence service/API behavior is incomplete. Do not treat these as optional completion items.

Before selecting an API approach, verify the actual persistence boundaries. The legacy live patient flow uses MongoDB; UCTSM HTTP patient/schedule routes use SQLAlchemy; newer Mongo application services exist. “All live patients are SQL” is too broad. Per-trial `schedule_read_mode` changes legacy-versus-engine visit reads; it does not automatically select SQL-versus-Mongo repositories, synchronize patient IDs, or make the Mongo command reachable.

Do not pick a blind SQL-to-Mongo mirror, wait for an unrelated read-mode switch, or port a scoping filter into SQL and call the full Mongo-targeted P1 requirement complete. None of these alone proves the required workflow.

First give a concise decision assessment supported by source:

1. Trace existing operational enrolment/canonical bridges, trial/protocol/patient ID mappings, schedule assignment, anchors, and current API lookups. Inspect `app/application/operational_bridge.py`, its services counterpart, API wiring, and port/parity tests where relevant.
2. Determine whether a narrowly scoped Mongo-backed canonical request adapter can use existing, verified Mongo trial/patient/version records. Do not create or pin a patient's schedule using guessed values.
3. Explain the smallest viable route/service integration, its selected authority, compatibility behavior, and configuration. Keep legacy scheduling authority and trial read modes unchanged.
4. Identify which decisions are routine engineering and which genuinely change clinical data authority. Proceed with the former. If the latter lacks a supported choice, ask one concrete question backed by verified options while completing independent SQL defect investigation, missing tests, and local-Mongo preparation. A decision blocker is not a reason to leave those tasks uninvestigated.

Close the version-scoping issue in any affected live SQL history paths if inspection proves the same defect. Add a red regression first and repair the minimal scope. Preserve immutable history, event/index identity, approved version boundaries, correction restrictions, and existing client behavior. This is a protection for the current path, not a substitute for Mongo API integration.

For a supported Mongo request path, reuse `app.application.patient_schedule.PatientScheduleService.record_actual_occurrence` and existing repository/unit-of-work contracts. Resolve scoped identities from persisted records, use explicit backend configuration without silent fallback, preserve visit-versus-treatment semantics and pending anchor confirmation, return changed/unchanged diffs, and refresh the applicable calendar. Do not build another command service or expose an endpoint that can never resolve the app's patient IDs.

Audit idempotency when patient schedule versions change between first request and replay. Verify which request fields are material, including horizon and expected version/revision where applicable. Preserve existing valid receipts without allowing different semantic operations under the same key. Add tests rather than assuming the current payload hash handles every case.

Add service/API regressions for a repeating event with missed occurrence 0, out-of-order actuals, version reuse, and correction/replay. Audit `references_event` for repeated/self-referential occurrence dependencies and add a test for any confirmed omission. The source occurrence must not disappear from the applicable latest calendar and unrelated baseline dates must remain fixed.

Inspect the local environment for MongoDB/containers and available disk space. If an existing local replica set is usable, use it. Otherwise prepare a task-owned local replica set on D: using supported available tooling when feasible, without changing global PATH, production settings, or unrelated files. Follow the project fixtures' local-only checks. Explain a concrete environment blocker if setup is unavailable; do not simply report every real-Mongo test as skipped and claim atomicity.

Run required real-Mongo tests with `UCTSM_REQUIRE_MONGO=1` and an explicit local `UCTSM_TEST_MONGO_URL`. Add meaningful concurrent identical-command, conflicting-command, latest-pointer, unique-index, and rollback/failure-injection cases. Prove one occurrence/evaluation/outbox result for identical concurrent requests and no partial transaction on failure. A memory fake cannot prove storage concurrency or replica-set rollback.

Keep date fixtures fixed. Prove Visit 2 actual **13 October 2026 +14 days = 27 October 2026**, baseline imaging unchanged, planned-relative timing unchanged, completed history preserved, and a typed API response from the implemented reachable route.

Use targeted pytest invocations and the repository's applicable curated gate. Run relevant frontend tests/scoped checks only if frontend changes. Respect existing test/environment constraints and report real-Mongo failures or missing prerequisites honestly. Do not weaken or skip tests to obtain a completion claim.

Update `docs/p1-occurrence-recalculation-plan.md` to distinguish service correctness, live SQL protection, Mongo route reachability, actual frontend integration, and real-Mongo verification. Finish with `git diff --check` and a self-contained P1 status table, endpoint example, architecture rationale, actual test results, and precise remaining blocker if one exists.

Do not flip `UCTSM_AUTHORITATIVE`, alter trial read modes, deploy, commit, or push. Complete every independently feasible part of the authorized work before asking for any remaining essential decision.
