# P1–P5 closure report (14 Oct 2026)

**Verdict: not closed** (updated 15 Oct 2026 after the selected-decision pass). The owner-choice blockers are resolved by delegation (`docs/p1-p5-project-decisions.md`): Mongo-authoritative pilot, target matrix, stricter self-grant, maker-checker ON, PI extraction. What still prevents acceptance is engineering/environment evidence: **no browser/device walkthrough of the production screens**, **no authentic amendment/version pair**, PostgreSQL locking, and the pilot is synthetic (no real cohort is migrated by design). No row is VERIFIED_ACCEPTANCE for its production-UI criteria; domain/API criteria are verified where stated.

States: V = VERIFIED_ACCEPTANCE, I = IMPLEMENTED_NOT_FULLY_VERIFIED, R = NEEDS_REPAIR, O = BLOCKED_OWNER_DECISION, E = BLOCKED_EXTERNAL_EVIDENCE.

## 1. Task rows

| Task | Implementation / API | Reachable UI | Real-store evidence | State and what is open |
|---|---|---|---|---|
| P1-01 | explicit bases, typed round trip | review screens | memory + Mongo | I – no device walk-through |
| P1-02 | occurrence history + version lineage | calendar | Mongo | I |
| P1-03 | stable logical ids, immutable evaluations, unique projection | calendar | Mongo | I |
| P1-04 | Mongo-plane actual route, atomic, scoped idempotency, shared ledger, **one authority decision on every writer incl. the older route and replay** | pilot screen `clinical/plane-schedule` (plane-authoritative patients only); disabled on the SQL screen | Mongo + HTTP, memory | I - UI not walked through |
| P1-05 | 13 Oct -> 27 Oct, imaging 31 Oct, 24 Oct case; separate treatment/visit facts | pilot screen | Mongo + HTTP (`test_p5_pilot`) | I - UI not walked through |
| P2-01 | identity store, validators, indexes | review UI | Mongo | I |
| P2-02 | gate at live upload entry points | upload | Mongo + HTTP | I – legacy wrappers not re-audited this session |
| P2-03 | deterministic classes, duplicate reuse, **fenced ownership (R1)** | – | Mongo race | I |
| P2-04 | quarantine review/resume | review UI | Mongo | I – no browser run |
| P2-05 | genuine registry PDF `Prot_003.pdf` (sha256 recorded) accepted with registry corroboration; wrong-expected-id copy CONSTRUCTED from genuine content is quarantined; duplicate of the genuine copy detected; live ClinicalTrials.gov v2 adapter | - | live registry GET + memory | **I** - no authentic mislabelled upload or amendment pair exists locally (`source-manifest.json` says so); amendment/version linkage remains synthetic |
| P3-01 | catalog v2 + `SUPPORTED_ROLES` + `RECIPIENT_ROLES`; status `SELECTED_UNDER_OWNER_DELEGATION` (no external clinical sign-off) | - | - | **I** - selected and implemented; not an external signatory |
| P3-02 | central policy, **unknown roles denied (R4)**, owner resolution (R2) | – | HTTP | I |
| P3-03 | manifest (76 routes: the 65 of the original pass plus the 11 P6 amendment routes), history profiles (R3), **all five legacy upload/consume/build wrappers mapped to `schedule.extract`** (ownership + P2 identity recheck retained) | - | HTTP + source-completeness test | I |
| P3-04 | maker-checker, provenance | review UI | memory + Mongo | I |
| P3-05 | policy/HTTP matrix incl. recipient eligibility (CRC high-impact, Site/SMO data entry), stricter self-grant, sponsor-side write denial, production assignment provider | - | HTTP | I |
| P4-01 | dependency outcomes + occurrence selectors; TRIGGER/PRECONDITION never demand an actual by themselves; `CURRENT` blocked for new/edited drafts (`DEPENDENCY_SELECTOR_AMBIGUOUS`), approved legacy keeps evaluating | - | domain | I |
| P4-02 | typed basis, review control | basis control | domain | I |
| P4-03 | 15 Jan / 12 Feb / 12 Mar | – | domain | I |
| P4-04 | approval-time validation incl. unverifiable starts | review | domain + SQL | I |
| P4-05 | cap, provisional termination, month-end | – | domain | I |
| P5-01 | tracks, membership, **review sheet incl. stop policy (R8)** | review screen | SQL API + memory/Mongo store | I – sheet and screen mount type-check clean, no walk-through; extraction proposes no tracks |
| P5-02 | policies, stable-date effective periods, chronology | – | memory + Mongo | I |
| P5-03 | typed commands, ledger, reversal, treatment fact; authority enforced centrally | pilot screen panel | memory + Mongo | I |
| P5-04 | one calculation, canonical replay, stale refusal | panel | memory + Mongo + HTTP | I |
| P5-05 | forms, preview, history, reload from the committed plane evaluation; protocol / appointment / exception / actual shown separately | `clinical/plane-schedule` reached from the Action Board; disabled on SQL-reading screens | Mongo + HTTP | **I** - controller + presentation unit-tested, typecheck clean; **no browser/device walkthrough** |

## 2. R1–R9 outcomes

| # | Resolution | Evidence |
|---|---|---|
| R1 | claim token = attempt; ownership checked atomically. Obsolete **complete/renew raise** `ExtractionClaimLostError`; an obsolete **fail returns silently** without touching the job (both safe, different observable outcome); winner retry idempotent | 5 regressions, real-Mongo race leaves exactly one result |
| R2 | persisted owner located after the relationship check; owner tenant used for reads/writes; ambiguity and forged ids denied | 4 HTTP tests (calendar, actual, preview, confirm; revoked/unassigned/forged) |
| R3 | RESTRICTED profile for non-staff incl. replays/diffs | sentinel test over body and headers; staff keep detail |
| R4 | `UNSUPPORTED_ROLE` before grants | policy + parametrised existing tests updated |
| R5 | unknown ≠ zero-width; partial bounds not over-claimed | memory + Mongo |
| R6 | independent `TREATMENT` fact; visit-relative rules still wait. **Reopened and repaired:** `_history` no longer treats an omitted treatment date as withdrawal of a separately recorded treatment fact; a combined fact is replaced by *supersession* only | both recording orders, differing dates, explicit correction, HTTP preview/confirm/replay/reload on memory + real Mongo |
| R7 | **Resolved for the selected scope, not for existing cohorts.** Backend: one `require_command_plane_authority` decision on every schedule-changing writer (commands, older actual route, corrections, activities, state facts, impact preview/confirm, replay); the red bypass is closed. UI: SQL-reading screens are `mixed` (commands off); the pilot screen reads the plane and enables commands only when `authority_plane == "MONGO"`. Existing SQL/legacy patients keep their authority and stay disabled | `test_p5_authority` (12, memory + Mongo, service + HTTP), controller + plane-schedule tests |
| R8 | tri-state stop policy control, never defaulted; round-trip through the review API | `test_p5_track_review` (4); sheet typecheck clean; not walked through in a browser |
| R9 | lifecycle in a plain state machine behind a mount-safe binding (`createControllerBinding`): A to B switch, version change, obsolete preview/confirm, **slow cancellation of OLD cannot clear NEW**, **disposed controller sends no request of any kind**, setup/cleanup/setup leaves a live instance, committed-but-refresh-failed, lost-confirm retry key | 15 controller tests; **not a React render test or browser run** (the repo has no renderer) |

## 3. Plane / authority contract

* Authoritative patient store is unchanged (SQL canonical + legacy Mongo operational). The command plane is MongoDB (ports); a patient is command-eligible only if (a) it exists in the plane for its canonical trial + operational id, (b) the relationship check passes, and (c) the plane's pinned version equals the displayed version.
* Unsupported cohort: every patient displayed by the SQL screen (the usual case), and every plane copy of a SQL-authoritative patient (`authority_plane="SQL"` is refused by the ledger).
* **Corrected premise.** `ParityService` compares *legacy operational* visits with the *SQL canonical* schedule; it does not compare SQL with the Mongo command plane, and `operational_bridge` is SQL-backed. Neither proves plane correctness, and parity before a command does not keep two stores in step after it. Unpinning is not rollback once commands exist.
* **Integration decision for the owner (`docs/p1-p5-integration-decision.md`).** Recommended: make the Mongo plane authoritative for a named pilot cohort end to end (enrol natively, display from the plane, commands in the plane) - never "SQL display + Mongo commands". It needs a plane enrolment path, plane display endpoints for the patient screen, an explicit `authority_plane` flip with SQL frozen for that cohort, and a SQL-to-plane comparison at the migration moment.

## 4. Verification actually run (local replica set `rs0`, `UCTSM_REQUIRE_MONGO=1`, db `mtb_p5_test`)

| Command / suite | Result |
|---|---|
| curated gate files present + P4/P5 modules (one process) | 564 passed, 2 skipped (in-memory concurrency; Mongo variants ran) |
| contract, P1 api/recalculation/concurrency, authz manifest/http/policy/mongo-plane, identity store+domain | 419 passed |
| identity workflow 22, journey 14, review-edit 12, Mongo parity review 20, maker-checker 18+16, extraction endpoints 9 | passed |
| frontend `uctsm-patient-commands` 11, `uctsm-visit-schedule` 27, `uctsm-command-controller` 9, `test:protocol-identity` 5 | all passed |
| scoped `tsc` per file (project JSX/alias settings, no suppression) | clean: PatientCommandsPanel, PatientScheduleScreen, TrackReviewSheet, ProtocolScheduleScreen (now completes) |
| `git diff --check` | no whitespace errors |

Counts are from these runs; overlapping modules (the gate list includes P5 modules also named in other groups) are not summed as unique coverage.

## 4b. Deferred audits (this pass)

| Item | Finding |
|---|---|
| Legacy upload / consume wrappers (`/protocols/extract`, `/protocols/extract-details`, `/trials/{id}/extract-schedule`, `.../consume`) | Guarded by legacy `require_roles('sponsor','cro','pi')`, legacy ownership checks (`_trial_in_caller_org` / `_pi_owns_trial`) and the P2 identity gate (`_require_verified_draft`, rechecked at consume). They are **not** in the UCTSM capability catalog; `schedule.extract` is sponsor/CRO only while the legacy path also admits PI, so central mapping would change who may upload. Needs an owner policy; not changed. Journey (14) and extraction-endpoint (9) suites pass. |
| Cross-organization list bounds | The candidate set is a bounded 500, now ordered by id so the bound is deterministic. Pagination and a truncation signal are **not** built: a user tied to more than 500 foreign trials is silently truncated. Open. |
| R2 through the production relationship provider | Evidenced only with `FakeOperationalAccess`; the production persisted assignment/invitation lookup was not exercised. Open. |
| Genuine PERIGON / amendment fixtures, live registry | Not present in authorised local inputs. Needed: the mislabelled NCT02088541 PERIGON PDF, the correct NCT02088554 PDF, a genuine amendment with provenance, and registry provider credentials/configuration. Open (external). |

## 5. Not verified

Browser/device walkthrough of every flow in the brief; PostgreSQL locking; genuine PERIGON/amendment documents; a real registry provider (results stay NOT_CHECKED/UNAVAILABLE); `npm run lint` (needs `yarnpkg`); legacy upload/consume wrapper catalog mapping; cross-organization list bounds.

## 6. P6 handoff

Unresolved prerequisites: plane/authority decision for the live cohort; owner approval of the P3 matrix; CURRENT semantics; TRIGGER/PRECONDITION rule; track proposals from extraction (manual review is the only path); real-document verification. **Amendment adoption (`amendment.adopt`) is now built** (16 Oct 2026): immutable lineage and approval hashes, semantic comparison, a reviewed event mapping, read-only simulation, explicit remain / immediate / event / cycle / date adoption, a worker for deferred execution, and a reviewed return - persisted on the Mongo plane, behind the central policy, with a reachable review screen and patient panel. It is still held by nobody by default (no role default; PI only; never self-grantable). Evidence, limits and exact commands: [p6-completion-record.md](p6-completion-record.md); status of every task: [pdf-requirements-acceptance-matrix.md](pdf-requirements-acceptance-matrix.md). The unresolved P1-P5 prerequisites listed above are unchanged by that work.


## 7. Selected-decision pass (15 Oct 2026) - what was built and how it was checked

| Item | Evidence |
|---|---|
| Grant eligibility (`RECIPIENT_ROLES`): CRC/Site/SMO/Sponsor/CRO cannot hold high-impact or data-entry rights even with a valid grant; checked at request time and at grant issuance; Site/SMO enrolment retained | `test_uctsm_authz_policy` (160), `test_p5_command_api` incl. CRC preview/confirm/replay refusal |
| Policy metadata `uctsm-authz.v2` / `SELECTED_UNDER_OWNER_DELEGATION` with provenance (`external_clinical_signoff: false`) | policy + HTTP + identity-workflow tests |
| Maker-checker default ON; the suite-wide opt-out is **removed**; the selected journey runs with distinct editor/approver and asserts the maker-checker refusal (403 `Maker-checker`) before the independent approval | `test_journey_upload_to_patient` (14), `test_mongo_parity_review` (20), `test_uctsm_maker_checker` (19, incl. default-on pin) |
| Authority bypass closed centrally | `test_p5_authority` |
| Native pilot `MTB-UCTSM-PILOT-001` (synthetic, isolated): track-aware schedule, approved by a different actor through the shared boundary, native patients with explicit `MONGO` authority, first evaluation; refuses a non-pilot trial with the same code; `POST /mongo-plane/pilot/seed` gated by `UCTSM_PILOT_MODE`; paged patient list (50/200, explicit cursor, relationship-filtered) | `test_p5_pilot` (memory + Mongo) |
| Pagination: new plane list; cross-org candidate scan now pages through all candidates and answers 503 instead of silently truncating | `test_p5_pilot`, authz suites |
| Registry adapter: ClinicalTrials.gov API v2 behind `RegistryLookup` (bounded timeout, one retry, cache, honest UNAVAILABLE/NOT_FOUND/NOT_CHECKED, identifier only); conservative corroboration (acronym AND organization in the front matter) | `test_p2_genuine_perigon` (9, live GET + injected-transport stand-ins, labelled) |
| R2 through the PRODUCTION assignment provider (`server._OperationalAccess` over persisted `patients`): assigned, unassigned, revoked, forged id; owner-tenant write | `test_p5_production_access_provider` (2) |
| Wrapper mapping | `test_p5_wrapper_mapping` (3) + journey/extraction suites |

Verification of this pass (local replica set, `UCTSM_REQUIRE_MONGO=1`, isolated databases): curated gate + new modules in one process **595 passed, 2 skipped**; contract/P1/authz/identity/genuine-document **475 passed**; journey 14, review-edit 12, WP3 8, parity 20, maker-checker 19+16, extraction endpoints 9, clinical-operation authz 3, authz-scoping 17, audit-scoping 12, creation 4, cutover 23 passed; frontend `test:uctsm` 170, controller 15, protocol-identity 5 passed; scoped `tsc` clean for the pilot screen, action board, command panel, track sheet, protocol schedule screen.

### Still open (exact impact)

* **Browser/device walkthrough** of upload -> identity -> track review -> two-person approval -> pilot enrolment -> delay with fixed imaging -> separate actual dates -> appointment inside/outside/unknown window -> pause/resume/rebase/stop/cancel/reversal -> lost-response retry -> stale refusal -> privacy/denial -> persisted reload. Not run (no browser harness in this environment). Blocks the production-UI criteria of P1-04/05, P2-04, P5-01/03/05.
* **Authentic amendment / version pair and an authentic mislabelled upload** (P2-05). The official PDF is the genuine content; the wrong-id case is constructed and labelled.
* **Real-patient migration**: not selected, not performed; its freeze/race/rollback design in `p1-p5-integration-decision.md` remains a future design.
* PostgreSQL locking; `npm run lint` (needs `yarnpkg`).
* High-impact authorization still follows the command's own type for the plane API; the older SQL impact API's proposal-operation mapping to the high-impact grant is not changed here.
