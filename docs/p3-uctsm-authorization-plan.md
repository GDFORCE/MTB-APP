# P3 — capability-based UCTSM authorization: design, manifest and verification record

Scope: P3-01–P3-05 of `D:\MTB_dev (2).pdf`. Not in scope: P4–P14, scheduling-authority migration, replacing authentication, new permission-management UI. P1/P2 behaviour is preserved (see §8).

> **Owner approval status — read this first.** The role→capability matrix in §3 is a **PROPOSAL, not an approved policy** (`MATRIX_STATUS = PROPOSED_PENDING_OWNER_APPROVAL`, surfaced by `GET /api/uctsm/authorization/me` and `/grants`). It is deliberately *narrower* than pre-P3 behaviour and gives **no** role a default right to correct, review, approve or reject a schedule. Those rights exist only through explicit, persisted grants. The product/clinical owner must review §3 before this is called approved. The maker-checker default (OFF) and scope (`LAST_EDITOR`) in §6 are also proposals.

## 1. Requirement matrix

| Task | Before | Change | Enforcement point | Evidence |
|---|---|---|---|---|
| P3-01 catalog + role map | No capability model. Roles `sponsor/cro/smo/site/pi/crc/patient/admin`, `org_admin` flag; `permissions` only read for P2 identity review and had **no protected writer** | `app/authorization/catalog.py`: 29 typed capabilities, proposed defaults, legacy alias map, clinical-decision set; persisted grants (`uctsm_capability_grants`) with an org-admin-only grants API | catalog is data; decisions made in `policy.authorize` | `test_uctsm_authz_policy.py` (97) |
| P3-02 policy + scope resolution | Only `_assert_schedule_tenant` / `organization_id ==` filters; several routes had **no** check (e.g. `enrolment-options` served any schedule to any caller) | pure `authorize(principal, capability, resource)`; guard resolves resource → trial → relationship from persisted records (SQL trial/patient `external_*_id` → operational `_can_access_trial` / `_can_access_patient`), verifies child-to-parent consistency, fails closed on unmapped links | `RouteAuthorizer` dependency replacing the user dependency on both routers | `test_uctsm_authz_http.py` (42), `test_uctsm_authz_mongo_plane.py` (16) |
| P3-03 route + privacy enforcement | tenant filter only; lists were org-wide | every one of the 58 registered routes declared in `manifest.py`; undeclared route ⇒ 403; lists constrained **in the query** (`trial_ids`) | router-level, before handler | `test_uctsm_authz_manifest.py` (6) |
| P3-04 maker-checker | none (approval gate required the *approver's own* confirmations only) | provenance columns on the version + port header; material-edit marking at every edit path; `enforce` at the lowest shared approval boundary of both planes; refusal audited in its own transaction | `ScheduleReviewService.approve` (SQL) / `_approve` (port) | `test_uctsm_maker_checker.py` (18), `test_uctsm_maker_checker_port.py` (16 = 8 × memory/Mongo) |
| P3-05 matrix + integration | — | parameterised policy matrix, HTTP matrix on real routes, real-Mongo port tests, full journey with designated approver | — | §7 |

## 2. Access model found (verified in source)

* `server.py::current_user` re-reads the user per request (suspension, forced logout). `current_uctsm_user` adds the organization UUID. Neither is a capability check.
* SQL canonical routers used `_identity()` + `_assert_schedule_tenant()` or inline `organization_id ==`. Role and trial/site/patient relationship were not consulted.
* Operational relationships live in legacy Mongo (`_can_access_trial`, `_can_access_patient`, `org_trial_access`, accepted invitations, `pi_id`/`crc_id`, `patients.user_id`). Canonical rows link to them through `external_trial_id` / `external_patient_id` (nullable).
* The Mongo patient plane (`/api/uctsm/mongo-plane`) resolved patients by organization + trial + `external_patient_id` only.
* `protocol_identity_runtime.can_review` allowed `org_admin` **or** a `permissions` string on the user document. Nothing in the repository writes that field, so it was not a protected grant source.
* Remote-audit capability grants and `app/domain/schedule/capabilities.py` are different catalogs and are not reused.

## 3. Capability catalog and PROPOSED role defaults

| Capability | Role defaults (PROPOSED) | Org-admin compat | Maker-checker | Meaning |
|---|---|---|---|---|
| `schedule.read` | crc, cro, pi, site, smo, sponsor | no | no | Read a trial's schedule definitions and versions |
| `schedule.evidence.read` | crc, cro, pi, sponsor | no | no | Read review views, source evidence, accountability and extraction runs |
| `schedule.export` | cro, pi, sponsor | no | no | Export a schedule version |
| `schedule.author` | cro, sponsor | no | no | Create trials, protocols and protocol versions |
| `schedule.extract` | cro, sponsor | no | no | Queue schedule extraction for a protocol version |
| `schedule.validate` | cro, pi, sponsor | no | no | Re-run validation on a draft |
| `schedule.correct` | **none - explicit grant only** | no | no | Correct draft schedule content (a material edit) |
| `schedule.review` | **none - explicit grant only** | no | no | Submit, confirm and record review decisions on a draft |
| `schedule.approve` | **none - explicit grant only** | no | yes | Approve a schedule version |
| `schedule.reject` | **none - explicit grant only** | no | no | Reject a schedule version in review |
| `patient.enrol` | crc, pi, site, smo | no | no | Enrol a patient onto an approved schedule |
| `patient.schedule.read` | crc, cro, patient, pi, sponsor | no | no | Read a patient's schedule (patient: own only; sponsor/CRO: de-identified) |
| `patient.schedule.evaluate` | crc, pi | no | no | Evaluate / recompute a patient's schedule |
| `patient.occurrence.record` | crc, pi | no | no | Record or correct an actual visit/activity occurrence |
| `patient.anchor.record` | crc, pi | no | no | Record or confirm a patient anchor date |
| `patient.state.record` | crc, pi | no | no | Record patient state or condition changes |
| `patient.unscheduled.create` | crc, pi | no | no | Create an unscheduled visit |
| `patient.impact.preview` | crc, pi | no | no | Preview the effect of a change on a patient's schedule |
| `patient.impact.confirm` | pi | no | no | Confirm or cancel a schedule impact proposal |
| `amendment.adopt` | **none - explicit grant only** | no | no | Adopt an approved amendment for a patient (reserved) |
| `protocol_identity.read` | cro, pi, sponsor | no | no | See protocol identity results for one's own uploads |
| `protocol_identity.review` | **none - explicit grant only** | yes | no | Resolve and resume quarantined protocol uploads |
| `trial.read_mode.read` | cro, pi, sponsor | no | no | Read a trial's read mode and cutover readiness |
| `trial.read_mode.set` | **none - explicit grant only** | no | no | Move a trial's visit reads between stores |
| `parity.run` | cro, pi, sponsor | no | no | Run a parity comparison for a patient |
| `parity.accept` | **none - explicit grant only** | no | no | Sign off a parity run with differences |
| `dashboard.read` | crc, cro, pi, sponsor | no | no | Read the action dashboard (scoped to authorised trials) |
| `demo.seed` | **none - explicit grant only** | no | no | Seed demonstration data (also needs UCTSM_DEMO_MODE) |
| `authorization.grants.manage` | **none - explicit grant only** | yes | no | Manage capability grants within one's organization |

Rules that are *not* proposals (they are structural):

* Unknown role, absent grant, unmapped capability, unknown route ⇒ **deny**.
* Platform role `admin` holds **no** UCTSM capability by default and there is no global bypass. Emergency-access infrastructure was not touched.
* `org_admin` does **not** confer clinical rights. Its only defaults are `authorization.grants.manage` (administration) and `protocol_identity.review` (existing P2 behaviour, kept for compatibility and listed as an owner decision).
* Nobody can grant a clinical-decision capability to themselves; grants cannot cross organizations (`may_grant`). A grant to a foreign or unknown user answers 404 identically.
* The `patient` role can only ever hold `patient.schedule.read`, only on its own record; a grant cannot make a patient a clinician.
* `amendment.adopt` is catalogued but no route exists and none was built; nobody holds it.

**Grant sources.** `uctsm_capability_grants` (operational Mongo): `{id, organization_id, user_id, capability, trial_id|null, granted_by, reason, created_at, revoked_at, revoked_by}`. Read fresh on every request, so revocation is effective on the next call. Written only by `POST/DELETE /api/uctsm/authorization/grants`, which require `authorization.grants.manage` and are audited. No client field, header or token is a grant.

## 4. Scope resolution

| Resource | Resolution | Missing/unmapped |
|---|---|---|
| trial | SQL `Trial` → owner `organization_id`; `external_trial_id` → operational `_can_access_trial`; owning organization's sponsor/CRO is `ORGANIZATION`, any other operational tie is `ASSIGNED` (legitimate cross-organization participation) | no `external_trial_id`: only the owning organization's sponsor/CRO is established; everyone else `UNMAPPED` ⇒ 404 |
| protocol / protocol version | protocol → trial; the version in the URL must belong to the protocol | not found / mismatch ⇒ 404 |
| schedule version | joined to its trial; `event_id` / `issue_id` in the path must belong to that version | foreign child ⇒ 404 |
| patient | patient → trial relationship **and** operational patient relationship (`pi_id`/`crc_id`/site, or sponsor's org); patient role: own record via `patients.user_id`; `candidate_id` must belong to the patient | no `external_patient_id`: sponsor/CRO of the owner org only; others `UNMAPPED` |
| impact proposal / patient event | → patient (as above) | — |
| extraction run | run → protocol version → protocol → trial | — |
| parity run | `trial_id` in the path must equal the run's trial | mismatch ⇒ 404 |
| Mongo plane | operational patient relationship on `external_patient_id`; the handler's `find_by_external_id(org, trial, patient)` binds canonical trial ↔ patient | — |
| organization-wide lists | candidate trials of the caller's organization (plus, for `pi/crc/smo/site`, up to 500 trials of other organizations that carry an `external_trial_id`), each passed through the relationship + capability check; the allowed set is applied **inside the SQL query** | — |

When the relationship is cross-organization (`ASSIGNED`), the handler receives the **owner's** `organization_id`, so existing tenant filters keep working and the caller acts inside the trial's tenant.

**HTTP semantics.** 401 — authentication (unchanged). **404** — resource absent *or* no persisted relationship to it (byte-identical body, no reason header). **403** — the caller can already see the resource but lacks the capability; the machine-readable reason travels in `X-Authz-Reason` / `X-Authz-Capability` / `X-Authz-Policy`, while `detail` stays a plain string because existing screens render it as text. Cross-organization lists show only the caller's organization's trials unless `trial_id` is supplied.

**Denial audit.** `uctsm.authorization.denied` rows in the operational `audit_logs`: actor, organization, role, capability, stable `reason_code`, `policy_version`, method, route, resource kind, canonical resource id, trial id, correlation id (`X-Request-ID` or generated). Written outside any business transaction, so a rollback cannot erase it. No names, notes, patient codes or document evidence. A denied request changes no schedule, evaluation, anchor, receipt, outbox or extraction state (asserted by row counts).

## 5. Endpoint manifest (runtime-verified: 58 routes = 56 SQL + 2 Mongo plane)

| # | Method | Path (under `/api/uctsm`) | Capability (any of) | Resource / params | Privacy | Mutation boundary |
|---|---|---|---|---|---|---|
| 1 | GET | `/dashboard/actions` | `dashboard.read` | NONE - list scoped to authorised trials | PATIENT_DEIDENTIFIED | - |
| 2 | POST | `/demo/seed` | `demo.seed` | NONE | NONE | DemoService.seed |
| 3 | GET | `/extraction-runs/{run_id}` | `schedule.evidence.read` | EXTRACTION_RUN (run_id) | EVIDENCE | - |
| 4 | POST | `/mongo-plane/trials/{trial_id}/patients/{external_patient_id}/actual-occurrences` | `patient.occurrence.record` | PATIENT (trial_id, external_patient_id) [mongo plane] | PATIENT_CLINICAL | app.application.patient_schedule.PatientScheduleService.record_actual_occurrence |
| 5 | GET | `/mongo-plane/trials/{trial_id}/patients/{external_patient_id}/calendar` | `patient.schedule.read` | PATIENT (trial_id, external_patient_id) [mongo plane] | PATIENT_CLINICAL | - |
| 6 | GET | `/patient-events/{patient_event_id}` | `patient.schedule.read` | PATIENT_EVENT (patient_event_id) | PATIENT_CLINICAL | - |
| 7 | POST | `/patient-events/{patient_event_id}/occurrences` | `patient.occurrence.record` | PATIENT_EVENT (patient_event_id) | PATIENT_CLINICAL | PatientScheduleService.record_occurrence |
| 8 | POST | `/patients/{patient_id}/activities` | `patient.occurrence.record` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleService.record_activity |
| 9 | GET | `/patients/{patient_id}/anchors` | `patient.schedule.read` | PATIENT (patient_id) | PATIENT_CLINICAL | - |
| 10 | POST | `/patients/{patient_id}/anchors` | `patient.anchor.record` | PATIENT (patient_id) | PATIENT_CLINICAL | AnchorService.record |
| 11 | POST | `/patients/{patient_id}/anchors/{candidate_id}/impact-preview` | `patient.impact.preview` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleImpactService.preview |
| 12 | POST | `/patients/{patient_id}/assignment/impact-preview` | `patient.impact.preview` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleImpactService.preview |
| 13 | GET | `/patients/{patient_id}/conditions` | `patient.schedule.read` | PATIENT (patient_id) | PATIENT_CLINICAL | - |
| 14 | POST | `/patients/{patient_id}/conditions/impact-preview` | `patient.impact.preview` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleImpactService.preview |
| 15 | GET | `/patients/{patient_id}/deviations` | `patient.schedule.read` | PATIENT (patient_id) | PATIENT_CLINICAL | - |
| 16 | POST | `/patients/{patient_id}/occurrence-facts` | `patient.occurrence.record` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleService.record_occurrence_fact |
| 17 | POST | `/patients/{patient_id}/parity-check` | `parity.run` | PATIENT (patient_id) | NONE | ParityService.compare_patient |
| 18 | GET | `/patients/{patient_id}/schedule` | `patient.schedule.read` | PATIENT (patient_id) | PATIENT_CLINICAL | - |
| 19 | POST | `/patients/{patient_id}/schedule/evaluate` | `patient.schedule.evaluate` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleService.evaluate |
| 20 | POST | `/patients/{patient_id}/schedule/impact-preview` | `patient.impact.preview` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleImpactService.preview |
| 21 | GET | `/patients/{patient_id}/schedule/projection` | `patient.schedule.read` | PATIENT (patient_id) | PATIENT_CLINICAL | - |
| 22 | POST | `/patients/{patient_id}/states` | `patient.state.record` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleService.record_state |
| 23 | POST | `/patients/{patient_id}/states/impact-preview` | `patient.impact.preview` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleImpactService.preview |
| 24 | POST | `/patients/{patient_id}/unscheduled-visits` | `patient.unscheduled.create` | PATIENT (patient_id) | PATIENT_CLINICAL | PatientScheduleService.create_unscheduled_visit |
| 25 | POST | `/protocols/{protocol_id}/versions` | `schedule.author` | PROTOCOL (protocol_id) | NONE | create_protocol_version |
| 26 | POST | `/protocols/{protocol_id}/versions/{protocol_version_id}/extract-schedule` | `schedule.extract` | PROTOCOL (protocol_id, protocol_version_id) | NONE | ExtractionService.queue |
| 27 | POST | `/schedule-impact-proposals/{proposal_id}/cancel` | `patient.impact.confirm` | IMPACT_PROPOSAL (proposal_id) | NONE | PatientScheduleImpactService.cancel |
| 28 | POST | `/schedule-impact-proposals/{proposal_id}/confirm` | `patient.impact.confirm` | IMPACT_PROPOSAL (proposal_id) | PATIENT_CLINICAL | PatientScheduleImpactService.confirm |
| 29 | GET | `/schedule-versions/pending-review` | `schedule.review`, `schedule.approve` | NONE - list scoped to authorised trials | SCHEDULE | - |
| 30 | GET | `/schedule-versions/{left_id}/diff/{right_id}` | `schedule.read` | SCHEDULE_VERSION (left_id, right_id) | SCHEDULE | - |
| 31 | GET | `/schedule-versions/{schedule_version_id}` | `schedule.read` | SCHEDULE_VERSION (schedule_version_id) | SCHEDULE | - |
| 32 | GET | `/schedule-versions/{schedule_version_id}/accountability` | `schedule.evidence.read` | SCHEDULE_VERSION (schedule_version_id) | EVIDENCE | - |
| 33 | POST | `/schedule-versions/{schedule_version_id}/approve-table` | `schedule.approve` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleReviewService.approve_reviewed_table |
| 34 | POST | `/schedule-versions/{schedule_version_id}/approve-version` | `schedule.approve` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.approve |
| 35 | POST | `/schedule-versions/{schedule_version_id}/bulk-confirm` | `schedule.review` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleReviewService.bulk_confirm_all_fields |
| 36 | GET | `/schedule-versions/{schedule_version_id}/enrolment-options` | `schedule.read` | SCHEDULE_VERSION (schedule_version_id) | SCHEDULE | - |
| 37 | POST | `/schedule-versions/{schedule_version_id}/events` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.add_event |
| 38 | PATCH | `/schedule-versions/{schedule_version_id}/events/{event_id}` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.edit_event |
| 39 | PUT | `/schedule-versions/{schedule_version_id}/events/{event_id}` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.edit_event |
| 40 | GET | `/schedule-versions/{schedule_version_id}/export` | `schedule.export` | SCHEDULE_VERSION (schedule_version_id) | EVIDENCE | - |
| 41 | POST | `/schedule-versions/{schedule_version_id}/extraction-issues/{issue_id}/resolve` | `schedule.review` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleReviewService.resolve_extraction_issue |
| 42 | POST | `/schedule-versions/{schedule_version_id}/holiday-calendars` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.attach_holiday_calendar |
| 43 | GET | `/schedule-versions/{schedule_version_id}/projection` | `schedule.read` | SCHEDULE_VERSION (schedule_version_id) | SCHEDULE | - |
| 44 | GET | `/schedule-versions/{schedule_version_id}/qualifiers` | `schedule.evidence.read` | SCHEDULE_VERSION (schedule_version_id) | EVIDENCE | - |
| 45 | POST | `/schedule-versions/{schedule_version_id}/qualifiers/{qualifier_id}/resolve` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.resolve_qualifier |
| 46 | POST | `/schedule-versions/{schedule_version_id}/review` | `schedule.approve`, `schedule.reject` | SCHEDULE_VERSION (schedule_version_id) - narrowed by body `decision` | NONE | ScheduleReviewService.approve|reject |
| 47 | POST | `/schedule-versions/{schedule_version_id}/review-decisions` | `schedule.review` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleReviewService.record_decision |
| 48 | POST | `/schedule-versions/{schedule_version_id}/review-questions/{question_id}/resolve` | `schedule.correct` | SCHEDULE_VERSION (schedule_version_id) | NONE | ReviewWorkflow.resolve_question |
| 49 | GET | `/schedule-versions/{schedule_version_id}/review-view` | `schedule.evidence.read` | SCHEDULE_VERSION (schedule_version_id) | EVIDENCE | - |
| 50 | POST | `/schedule-versions/{schedule_version_id}/submit-review` | `schedule.review` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleReviewService.submit_for_review |
| 51 | POST | `/schedule-versions/{schedule_version_id}/validate` | `schedule.validate` | SCHEDULE_VERSION (schedule_version_id) | NONE | ScheduleValidator |
| 52 | POST | `/trials` | `schedule.author` | NONE | NONE | create_trial |
| 53 | GET | `/trials/{trial_id}/approved-schedules` | `schedule.read` | TRIAL (trial_id) | SCHEDULE | - |
| 54 | POST | `/trials/{trial_id}/parity-runs/{run_id}/accept` | `parity.accept` | PARITY_RUN (trial_id, run_id) | NONE | ParityService.accept_run |
| 55 | POST | `/trials/{trial_id}/patients` | `patient.enrol` | TRIAL (trial_id) | PATIENT_CLINICAL | PatientScheduleService.enrol |
| 56 | POST | `/trials/{trial_id}/protocols` | `schedule.author` | TRIAL (trial_id) | NONE | create_protocol |
| 57 | GET | `/trials/{trial_id}/read-mode` | `trial.read_mode.read` | TRIAL (trial_id) | NONE | - |
| 58 | POST | `/trials/{trial_id}/read-mode` | `trial.read_mode.set` | TRIAL (trial_id) | NONE | ParityService.set_read_mode |

Entry points that live in `server.py` and are guarded by the same policy (not part of the 58): `GET /api/protocol-identities`, `GET …/{id}`, `POST …/{id}/resolve`, `POST …/{id}/resume`, `GET …/{id}/extraction` (review = `protocol_identity.review`, reads scoped to the uploader or a reviewer; `permissions` field no longer honoured), and `GET /api/uctsm/authorization/me`, `GET|POST|DELETE …/grants` (`authorization.grants.manage`).

`POST /api/trials/{id}/protocol-extractions/{id}/consume`, `/protocols/extract*` and `/trials/{id}/extract-schedule` remain governed by their existing legacy trial-access checks plus the P2 identity gate; they are not UCTSM routes and were not re-keyed to the new catalog. **Open item:** they should map to `schedule.author` / `schedule.extract` in a follow-up.

## 6. Maker-checker (P3-04)

* **Policy `maker-checker.v1`** (`app/authorization/maker_checker.py`, pure). Deployment-owned: `UCTSM_MAKER_CHECKER=on|off`, `UCTSM_MAKER_CHECKER_SCOPE=LAST_EDITOR|ANY_EDITOR`, read from the server environment on every approval. No request field/header can change it (an attempt to send one is a 422); the setting in force is stamped on the approval audit. **Default OFF** (compatibility) — a *proposal*; **default scope `LAST_EDITOR`** is the check the requirement states; `ANY_EDITOR` excludes every material editor of the draft and is available if the owner wants the stricter rule.
* **Provenance** (SQL columns via migration `20261014_0018`; port header fields): `last_material_editor_id`, `last_material_edit_at`, `material_editor_ids`. Written where the edit happens, in the same transaction, never inferred from event status or the latest audit row.
* **Material** = timing, window, recurrence, applicability, visit mode / dependency mode, event type, activities/conditions, qualifier meaning (EDIT / NOT_APPLICABLE / category change), anchor interpretation (CHOOSE_ORIGIN, DEFINE_ANCHOR, REMAP_ANCHOR), holiday calendars, adding a visit. **Not material**: label rename, row confirmation, `CONFIRM_ANCHOR`, qualifier ACCEPT, view/export/validate.
* **Enforcement** at the lowest shared boundary of each plane: SQL `ScheduleReviewService.approve` (reached by `approve-version`, `approve-table`, `POST …/review` APPROVE and any direct service call; also checked early in `approve_reviewed_table` so no confirmation is written for a refused approver); port `ScheduleReviewService._approve` (reached by `approve` and `approve_reviewed_table`). Read from the row locked `FOR UPDATE` (SQL) / the transaction's header (Mongo), so a concurrent edit cannot slip between check and commit; a stale `expected_revision` is still 409.
* **Refusal**: HTTP 403 with `X-Authz-Code: MAKER_CHECKER`; business transaction rolled back (status, `approved_by`, protocol pointer unchanged, no APPROVE decision); a `SCHEDULE_APPROVAL_DENIED_MAKER_CHECKER` audit row committed separately. Success audit `SCHEDULE_APPROVED` carries policy version, scope, outcome code, last editor, approver and revision.
* **Independent approver** passes the *existing* gates unchanged (they require the approver's own confirmations; `approve_reviewed_table` records them). Nothing was weakened or auto-created.
* **Legacy drafts** (provenance columns NULL, edited before this change): unknown authorship is never invented and nothing is rewritten. If the draft carries human CORRECT/ADD decisions that are not provably presentation-only, those reviewers are excluded (conservative); otherwise there is no maker to segregate. Approved versions are immutable and are not retrofitted.
* **Open product decisions:** default on/off per environment or per organization; `LAST_EDITOR` vs `ANY_EDITOR`; whether organization admins may be exempt (not implemented — no exemption exists).

## 7. Verification (14 Oct 2026)

Environment: local MongoDB replica set `rs0` on 127.0.0.1 (restarted this session — it had stopped), databases `mtb_p3_test` / per-test `uctsm_test_*`, `UCTSM_REQUIRE_MONGO=1`; the shared Atlas URL in `.env` was overridden by the shell and never used. SQL: in-memory SQLite (HTTP/maker-checker tests) and a fresh SQLite file for the alembic chain.

| Module | Result |
|---|---|
| test_uctsm_authz_policy.py | 97 passed |
| test_uctsm_authz_manifest.py | 6 passed |
| test_uctsm_authz_http.py (real guard, real SQL routes) | 42 passed |
| test_uctsm_authz_mongo_plane.py (memory + real Mongo) | 16 passed |
| test_uctsm_maker_checker.py (SQL, HTTP + service) | 18 passed |
| test_uctsm_maker_checker_port.py (memory + real Mongo) | 16 passed |
| test_protocol_identity_workflow.py (live routes; +4 P3 tests) | 22 passed |
| test_journey_upload_to_patient.py (full journey, real server) | 14 passed — **only after designating a reviewer/approver grant** (see §9) |
| curated handoff gate §E, 33 existing files (2 deleted ones omitted) | 375 passed |
| P1 modules (p1_api, occurrence_recalculation, concurrency, sql_lineage, cutover, operational_projection) | 17 + 56 + 6 + 1 + 23 + 3 passed |
| P2 modules (identity domain/store/workflow, extraction endpoints, protocol creation, uctsm_identity, mongo extraction runner/parity) | 21 + 20 + 22 + 9 + 4 + 2 + 16 + 18 passed |
| review modules (view, edit_approval, accountability, bugfixes, constraints, wp3, bulk_confirm, inbox, enrolment_sources, mongo_parity_review) | all passed |
| legacy authz/audit (test_authz_scoping, test_audit_scoping, test_clinical_operation_authz) | 17 + 12 + 3 passed |
| `alembic upgrade head` → `downgrade -1` → `upgrade head` on a fresh SQLite | clean; columns present |

Modules were run one at a time (the documented anyio/event-loop caveat for TestClient modules); the curated gate was run as one session as the handoff specifies. Skips: none in the P3 modules.

## 8. P1 / P2 regression and carried-over limits

* P2 identity quarantine, deduplication, new-version lineage and review behaviour are unchanged; its review permission now comes from the catalog. Existing P2 tests pass unmodified; four new tests cover the grant source, grant administration, denial audit and `authorization/me`, and assert the old `permissions` field grants nothing.
* P1: the Mongo patient-plane router is now guarded; the P1 modules listed in §7 pass unchanged against the real replica set. P1's own outstanding items are not re-assessed or re-claimed here.
* Still true from P2: synthetic PERIGON fixtures, no external registry provider, no browser/device verification.

## 9. Intentional access changes (review these)

1. **Correct / review / approve / reject are grant-only.** Before P3 any organization member could. The journey test now designates its sponsor as reviewer/approver exactly as an org admin would. Until grants exist, nobody can approve. *Operational consequence for rollout:* an organization admin must grant these before the first approval.
2. **Clinical patient actions** (record actuals, anchors, states, unscheduled visits, evaluate) are `pi`/`crc` only; sponsor/CRO read de-identified schedule/dashboard but can no longer write.
3. **`GET …/enrolment-options` is now tenant- and relationship-scoped** (it previously accepted any schedule version id from any authenticated caller).
4. **Read-mode change, parity acceptance, demo seed** need explicit grants.
5. **Users whose canonical trial has no `external_trial_id`** are visible only to the owning organization's sponsor/CRO; assigned investigators get 404 until the link exists (no mapping is invented).
6. **`user.permissions`** is no longer an authorization source for identity review.

## 10. Remaining decisions and limits

* Owner approval of §3 (esp. `patient.impact.confirm` = `pi`, `schedule.validate`, `schedule.export`, and `protocol_identity.review` org-admin default).
* Maker-checker default and scope (§6).
* No frontend change was made: existing screens show the 403 string; a "what may I do" UI using `GET …/authorization/me` is available but not wired.
* Cross-organization lists need `trial_id` to include another organization's trial unless the caller is `pi/crc/smo/site` (bounded to 500 candidates).
* `/protocols/extract*`, `/trials/{id}/extract-schedule` and the consume endpoint are not yet re-keyed to the catalog (§5).
* The grants API has no UI.
* Legacy-Mongo operational relationship rules are reused as-is; their own known gaps are inherited.

## Update 15 Oct 2026 - selected target matrix applied (project decision)

Source: `docs/p1-p5-project-decisions.md` section 2/3, selected under the owner's delegation. This is *selected and implemented*; it is not an external clinical signatory's approval and no live user was granted anything.

| Change | Code | Tests |
|---|---|---|
| `schedule.extract` defaults: sponsor, CRO **and PI** | `catalog.py` | `test_extraction_default_holders_are_sponsor_cro_and_pi` |
| `protocol_identity.review`: explicit grant only, **org-admin default removed** | `catalog.py` (`org_admin_default` dropped) | policy test updated; identity-workflow fixtures now grant reviewers explicitly |
| Sponsor/CRO may never hold patient clinical-write capabilities, even by explicit grant | `PATIENT_CLINICAL_WRITE_CAPABILITIES`, `policy.authorize` | parametrised role x capability test |
| Stricter self-grant: every clinical write/decision right plus identity review, read-mode and parity sign-off | `SELF_GRANT_PROTECTED`, `policy.may_grant` | parametrised self-grant test |
| Maker-checker **ON, LAST_EDITOR** unless a deployment sets `UCTSM_MAKER_CHECKER` to off/0/false/no | `maker_checker.py` | `test_default_policy_is_on_...`; legacy fixtures opt out in `tests/conftest.py` |

Fixture consequences (policy-driven, not weakened assertions): demo/enrolment/WP3 suites that previously wrote patient data as a *sponsor* now act as a *PI* with an operational trial/patient mapping (`map_operational_ids`); the identity-review fixtures grant reviewers explicitly and the cross-tenant test uses a designated reviewer in the other organization.

Results (local replica set): curated gate + P4/P5 modules 564 passed / 2 skipped; contract + P1 + authz + identity store/domain 457 passed; identity workflow 22, journey 14, review-edit 12, WP3 8, parity review 20, maker-checker 19 + 16, authz-scoping 17, audit-scoping 12, clinical-operation authz 3, extraction endpoints 9.

Still open: the default-on maker-checker is exercised by dedicated suites only (the general suite opts out); legacy upload/consume wrappers remain outside the catalog; PostgreSQL locking and a browser walkthrough are unverified.
