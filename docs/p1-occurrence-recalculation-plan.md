# P1-01..P1-05 — occurrence-driven recalculation: assessment and results

Branch `universal-schedule-engine`. Scope: the five P1 tasks. No `UCTSM_AUTHORITATIVE`, no trial read-mode change, no legacy scheduling screen migrated, nothing committed or deployed.
Last updated 14 Oct 2026 after the completion pass.

## 1. Persistence boundaries (verified against source)

| Concern | Store | Evidence |
|---|---|---|
| Legacy product: Trial, Protocol, Patient, visit writes | MongoDB via Motor, `DB_NAME` (`server.py`) | `AGENTS.md`; `server.py` `db.patients`, `db.visits` |
| UCTSM HTTP API (`/api/uctsm/*`) | PostgreSQL via SQLAlchemy, `UCTSM_DATABASE_URL` | `app/api/uctsm.py` `Db = Depends(get_session)`; `app/db/base.py` |
| Legacy patient ↔ canonical (SQL) patient | `external_patient_id` = legacy patient id; trial link `uctsm_trial_id` | `server.py:_enroll_operational_patient_sync` → `app/services/operational_bridge.py` (SQL) |
| UCTSM Mongo port (repository ports) | its own database, `UCTSM_MONGO_URL` + `UCTSM_MONGO_DB` (must differ from `DB_NAME`) | `app/persistence/mongodb/settings.py` |
| Who populates the Mongo port's patient plane | only the port's own `app.application.operational_bridge` (used by parity tests/`parity.py`); **no live flow** | grep: `server.py` imports the SQL bridge only |

`schedule_read_mode` selects legacy-vs-engine visit reads. It does not choose SQL-vs-Mongo, sync patient ids, or reach the Mongo command.
Production re-pins a patient's version only through an approved impact proposal (SQL `impact_service.py:322`, port `impact.py` `confirm`); `pin_schedule_version` has no production caller.

## 2. Decision: how the Mongo command is reachable

Chosen: **an additive, explicitly-configured route group on the Mongo patient plane**, `app/api/uctsm_mongo.py`, mounted in `server.py` beside the SQL router.

* `POST /api/uctsm/mongo-plane/trials/{canonical_trial_id}/patients/{external_patient_id}/actual-occurrences`
* `GET  /api/uctsm/mongo-plane/trials/{canonical_trial_id}/patients/{external_patient_id}/calendar`
* Identity is **resolved, never created**: `tx.patients.find_by_external_id(organization, trial, external_patient_id)`. The operational (legacy) patient id is the key the app already uses; unknown → 404; no enrolment, pinning or assignment happens in the route.
* Organization and actor come from the authenticated user (`_identity`); a body carrying `organization_id` is rejected (`extra=forbid`).
* No configuration → **503** (`UCTSM_MONGO_URL`/`UCTSM_MONGO_DB` unset). There is no fallback to SQL and no silent no-op.
* Work is done by the existing `PatientScheduleService.record_actual_occurrence`; the route only maps HTTP ⇄ command and errors (404 / 409 / 422).

Rejected: a SQL→Mongo mirror (creates a second authority and guesses assignment); waiting for a read-mode switch (does not make the command reachable); porting a filter to SQL and calling P1 done (does not exercise the Mongo path).

**Not decided here (changes clinical-data authority):** whether any live flow should enrol patients into the Mongo plane. Until one does, the route resolves only patients created by the port's own enrolment. See §6.

## 3. Requirement matrix (final)

| Req | State | What it rests on |
|---|---|---|
| P1-01 explicit basis | **Satisfied** (pre-existing, unchanged) | `test_uctsm_temporal_basis.py` 35; `test_uctsm_occurrence_history.py` 12 |
| P1-02 version-scoped history | **Satisfied in the Mongo port**; SQL characterised, not changed | §4 |
| P1-03 reconcile across evaluations | **Satisfied** | many-re-evaluations, queryable-history, repeating-event tests (§5) |
| P1-04 atomic command | **Satisfied** service + HTTP route + real Mongo; **route not yet consumed by any frontend or live enrolment** | §2, §5 |
| P1-05 acceptance tests | **Satisfied** (§5), real Mongo required and run | §7 |

## 4. P1-02 — what changed and why

Defect (confirmed, red-first): the port read `list_occurrences(patient)` with no version filter in the context, outcomes, protected keys, calendar, correction lookup, deviation report, anchor status and dashboard, so a version that reused an event code inherited the other version's actual date and completion.

A strict single-version filter would have been wrong: an **approved reassignment is the explicit mapping** and must carry completed visits (the amendment scenario). Implemented as an explicit lineage, `PatientScheduleService.version_lineage`:

1. the version itself;
2. every version the patient was approved to move **from** (assignment history, `AssignmentRecord.previous_schedule_version_id`, transitive);
3. the proposal's source version while previewing/confirming the move (`carry_from_versions`).

An unrelated version that merely reuses a code contributes nothing. Loading is one indexed query per version in the lineage, never per event.
`(event_code, occurrence_index)` is a sound logical key within a version (a duplicate code in a version is a `DuplicateRecordError`), so no extra id or migration was added. Event-definition ids are per version, which is why code is the cross-version key and why the lineage, not the id, defines the mapping.

SQL path (live): inspection shows the same by-code feed, but a patient can only gain a second version through a confirmed proposal, which is exactly when completed visits must carry. **No accidental contamination is reachable**, so SQL behaviour is unchanged. `test_uctsm_p1_sql_lineage.py` pins it: a v1 actual drives a v2 rule only after confirm; v2 approval alone moves nothing.

`references_event` audit: a repeating visit whose only self-reference was `dependencies`, `recurrence_plan` or `calendar_recurrence` was reported unreferenced (so recording it would skip re-evaluation). Fixed in `relevance.py`; test added. Over-reporting remains deliberate and cannot move unrelated dates (evaluation is deterministic, covered by the chain/imaging tests).

## 5. Idempotency (P1-04)

Receipt scope: organization + patient + key, storage-unique (`uq_command_receipt`). Material inputs: logical key, kind, visit date, treatment date, effective-at, superseded record, **horizon**, **expected schedule version** (the last two join the hash only when supplied, so receipts stored before still replay). Not material: the clock and free-text reason.
New: the resolved `schedule_version_id` is stored on the outcome/receipt. A replay after the patient moved to another version raises `ScheduleVersionChangedError` (409) instead of replaying; a request naming a stale `expected_schedule_version_id` is refused without writes. The patient (tenant-scoped) is resolved before any receipt is honoured, so another organization cannot probe a key.

## 6. Remaining blocker / decisions

1. **One data-authority decision (needs you):** should live patient enrolment also create the patient in the Mongo plane (so the new route resolves real patients), or is the Mongo plane to stay unpopulated until the planned SQL→Mongo port cutover? The route is correct either way; today it can resolve only port-enrolled patients. Nothing was enrolled or mirrored.
2. **Frontend not integrated.** `recordOccurrenceFact` / `PatientScheduleActions.tsx` still call the SQL route; no frontend file changed, so no frontend test was run. Adopting the new route (typed `outcome.changes`, calendar refresh) is a follow-up gated on (1).
3. **Derived anchor candidates:** the SQL `/patient-events/{id}/occurrences` route derives `PENDING_CONFIRMATION` candidates from an actual. The Mongo command does not derive them (it never confirms one either); the response lists existing pending candidates. Porting derivation is not done.
4. Activity entries and unscheduled visits are still patient-level (not version-scoped); outside P1.
5. Local-only prerequisites for real-Mongo runs: a replica set on `127.0.0.1` (see §7).

## 7. Verification actually run (from `backend/`)

Environment: C: has 0 GB free (shell output must go to D:). No MongoDB was installed; a task-owned `mongod` 7.0.14 (vendor zip, SHA-256 verified against the vendor's `.sha256`) was extracted to `D:\mtb_mongo` and run as single-node replica set `rs0` on `127.0.0.1:27017`, then stopped. Tests ran with `UCTSM_REQUIRE_MONGO=1` and `UCTSM_TEST_MONGO_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0`.
To rerun: `D:\mtb_mongo\bin\mongod.exe --replSet rs0 --bind_ip 127.0.0.1 --port 27017 --dbpath D:\mtb_mongo\data --logpath D:\mtb_mongo\log\mongod.log` (the replica set is already initiated in that data dir).

| Module (isolated invocation) | Result (memory + real Mongo, none skipped) |
|---|---|
| `test_uctsm_p1_occurrence_recalculation.py` | 56 passed |
| `test_uctsm_p1_concurrency.py` (real Mongo only; 3 consecutive runs) | 6 passed ×3 |
| `test_uctsm_p1_api.py` | 17 passed |
| `test_uctsm_p1_sql_lineage.py` | 1 passed |
| `test_uctsm_occurrence_history.py` / `test_uctsm_temporal_basis.py` | 12 / 35 passed |
| `tests/contract/test_patient_contract.py` / `test_schedule_contract.py` | 36 / 32 passed |
| `test_mongo_parity_*` (9 modules) | 138 passed |
| `tests/test_architecture_boundaries.py` | 4 passed |
| `contract/test_authoring_contract`, `test_audit_contract`, `test_mongo_platform`, `test_mongo_extraction_runner` | 16 / 12 / 9 / 8 passed |
| Curated gate (handoff §E, 33 files, deleted extraction/footnote tests omitted, none missing) | 375 passed |

Concurrency proof (real Mongo, 8 racing threads): identical commands → 1 occurrence, 1 evaluation, 1 outbox record, 1 business audit entry, 1 receipt, 7 replays; conflicting bodies under one key → exactly one winner, the rest 409; different keys → gap-free evaluation sequences, pointer = newest evaluation, one projection per visit per evaluation; unique indexes refuse duplicate receipt / projection / outbox key while a second fact for the same logical visit is accepted; an injected outbox failure leaves counts, pointer and calendar unchanged.

## 8. Headline scenario (through the HTTP route, in-memory and real Mongo)

Baseline 1 Oct 2026; V2 planned 10 Oct; `POST …/actual-occurrences` with `visit_date=2026-10-13` → `calendar[V3:0].planned_start == "2026-10-27"` (13 Oct + 14 d); baseline-relative `IMAGING:0` stays `2026-10-31` and is reported unchanged ("does not depend on V2"); the current-planned-relative rule stays `2026-10-24`; V2 shows `completed`, `actual_visit_date 2026-10-13`, `original_planned 2026-10-10`. Asserted in `test_uctsm_p1_api.py`.
