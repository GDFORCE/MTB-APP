# Integration decision: one authoritative plane for patient commands

Prepared 14 Oct 2026. Updated 15 Oct 2026: **Option B is selected under the owner's delegated project decision.** Begin with the new isolated synthetic local pilot `MTB-UCTSM-PILOT-001`; `docs/p1-p5-project-decisions.md` records the authoritative scope and policy. Implementation is pending. No existing real cohort, live migration or authority flip is selected/performed here. The existing-patient migration outline below remains a future design to validate, not an implemented rollback guarantee.

## Facts (source-checked)
* The clinical patient screen (`clinical/patient-schedule`) reads SQL: schedule, anchors, conditions, projection, deviations.
* Commands (ledger, preview/confirm, evaluation, outbox) exist only on the MongoDB ports. SQL has no port-based unit of work.
* `ParityService` compares *legacy operational* and *SQL canonical* schedules and drives `LEGACY|ENGINE` read mode. It is not a SQL-vs-Mongo check. `operational_bridge` is SQL-backed.
* Therefore "SQL display + Mongo commands", with or without equal version ids, can show unchanged dates after a committed command.

## Options
| | A: SQL command adapter | B: Mongo-authoritative pilot cohort (recommended) |
|---|---|---|
| Change | implement ~10 repositories/UoW over SQLAlchemy; a second persistence under the same algorithm | native plane enrolment + plane display API for the patient screen; SQL read-only for that cohort |
| Authority | unchanged | `authority_plane="MONGO"` for the cohort, set once, auditable |
| Direction fit | against the repo's Mongo-port commits | with it |
| Risk | concurrency/locking proof on PostgreSQL; double maintenance | one-time migration of facts; the cohort must be moved completely |

## B in detail
1. **Cohort:** a named trial whose patients are not yet clinically active in SQL, or all of whose facts are exported. Never partially.
2. **Identity/version mapping:** plane `trial_id` = canonical trial id; plane patient keyed by `(trial_id, external_patient_id)`; the plane's approved schedule version is created from the SQL approved version by an id-preserving export (same `schedule_version_id`), recorded in a migration manifest.
3. **Fact ownership at the flip:** anchors, conditions, occurrence facts, unscheduled visits, states and assignment facts are copied once as append-only history with original recorded times and actors; the SQL rows are frozen (read-only) and retained.
4. **Display:** the patient screen reads the plane calendar (plus anchors/conditions endpoints still to be added) for cohort patients; `displayPlane` becomes `"MONGO"`, which is what enables the panel.
5. **Transaction boundary:** every command is one Mongo transaction. SQL is never written for the cohort after the flip.
6. **Comparison evidence before the flip:** evaluate each patient in both engines from the same exported facts and diff targets, windows and statuses (a new plane-comparison run, distinct from `ParityService`); zero unexplained differences required. `ParityService` stays as the legacy-versus-engine gate.
7. **Rollback:** unpinning is not rollback once commands exist. Rollback = (a) stop commands (`authority_plane` away from the command plane, already enforced); (b) retain every plane fact, command, audit and outbox row; (c) export plane facts newer than the flip into SQL as append-only facts and re-evaluate there; (d) reconcile reminder intents - the outbox is append-only, so cancel intents are emitted for commands re-expressed in SQL, never deleted. Medication reminders are unaffected.
8. **Retention:** plane history is retained for the study's record-retention period; nothing is deleted.

## Selected scope and next work

Implement option B for the new local/development synthetic pilot `MTB-UCTSM-PILOT-001`, through generic product navigation and coherent explicit Mongo authority. Do not ask for an existing trial name to begin. Existing SQL/legacy cohorts keep their authority and mixed-plane commands remain disabled. Enforce authority on every equivalent write/replay path, including the older actual route; the latest audit demonstrates that preview/confirm checks alone do not achieve this.

Reuse the repository-port bridge at `app/application/operational_bridge.py` where appropriate. The live bridge at `app/services/operational_bridge.py` is SQL-backed; the two are distinct. Preserve the legacy-engine parity gate separately from SQL/Mongo comparison. Later real-patient migration requires an actually mapped cohort, enforced write freeze and verified recovery semantics for every supported command effect. Do not assume SQL can replay all Mongo track/override commands or that a field on a Mongo patient freezes SQL writers.


## Implementation status (15 Oct 2026)

Built for the selected synthetic pilot: `app/application/pilot.py` (native schedule, two-person approval, native enrolment with `authority_plane="MONGO"`), `POST /api/uctsm/mongo-plane/pilot/seed` (local only, `UCTSM_PILOT_MODE`), a paged relationship-filtered plane patient list, the plane `calendar` now carrying `authority_plane`, and the `clinical/plane-schedule` screen (reached from the Action Board) that displays the committed plane evaluation and enables commands only for `MONGO`-authoritative patients. The central authority decision (`app/application/authority.py`) guards every schedule-changing writer. Existing SQL/legacy cohorts are untouched and their commands remain disabled. The existing-patient migration steps above are still a future design.
