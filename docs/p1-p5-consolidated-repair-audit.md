# Consolidated foundations / P1–P5 repair audit

Reviewed 14 October 2026 against the current uncommitted working tree, the PDF, updated implementation records, and targeted tests. No application source was modified. All existing work was preserved.

## Scope and verdict

The PDF has P1–P14, five numbered tasks each, 70 tasks total. **P0 is a priority label, not a separate epic.** This audit covers the implemented foundations and P1–P5 workflows. It does not certify all P0-priority work in later groups such as P7/P14.

The latest repairs are present and their regression suites pass. However, P1–P5 still have additional correctness, authorization, concurrency, and integration gaps. The 25 task areas have implementation work; that is not 25 fully closed acceptance items or a release-readiness claim.

| Area | Current evidence | Remaining closure |
|---|---|---|
| P1 | Real-Mongo occurrence/history/concurrency and API checks pass | Mongo cross-organization access and independent treatment-date contract below; active-plane integration remains unresolved. |
| P2 | Identity/classification/store/workflow suites pass | Expired extraction-claim ownership is unsafe; real PERIGON corpus and external registry verification remain unavailable. |
| P3 | Policy/manifest/HTTP suites pass | Unknown-role fail-closed gap, command-history privacy, cross-organization Mongo scope, pending owner matrix/defaults, and legacy-wrapper mapping. |
| P4 | Domain and persistence/approval suites pass, including recent fixes | CURRENT remains documented as LAST; TRIGGER/PRECONDITION actual semantics remain an owner decision; production SQL locking unproven. |
| P5 | Track/command/API/review and frontend builder suites pass | Unknown-window exception classification, mixed-plane display/write integration, stop-policy review control, and UI lifecycle/probe checks. |

## A. Reproduced backend repairs

### R1 — High: stale P2 extraction workers can alter a newer claim

Source: `backend/app/application/protocol_identity.py::begin_extraction`, `complete_extraction`, and `fail_extraction`.

Lease takeover increments the job attempt/revision, but completion/failure reads the current job and updates using its current revision without verifying that the caller still owns that attempt. CAS protects the update itself, not ownership of the long-running work that produced it.

Reproduction, memory and real local Mongo:

1. Worker A claims attempt 1.
2. Advance beyond `JOB_LEASE`; worker B claims attempt 2.
3. Worker A reports a late failure. **Attempt 2 becomes FAILED.**
4. Another claim can start attempt 3 while B may still be working.
5. Worker A completes using its obsolete plan. **Its stale payload becomes the published result.**

Required repair: persist a claim/attempt fencing token, check ownership and state atomically on complete/fail/lease renewal, and refuse obsolete workers without changing the active job/result. Do not use the latest fetched revision as proof that the worker owns it. Add real-Mongo takeover/late-failure/late-completion regressions, including two still-running workers.

### R2 — High: assigned cross-organization investigators cannot reach the Mongo patient plane

Source: `backend/app/authorization/guard.py::_resolve_plane` and `backend/app/api/uctsm_mongo.py::resolve`.

The plane resolver sets the resource owner to the caller's organization. The handler then searches the canonical patient using that organization. Unlike the SQL guard, it does not resolve the persisted trial/patient owner before translating the request tenant.

HTTP reproduction with the actual policy/router, memory-backed canonical records, and an operational relationship test double explicitly allowing the investigator:

- PI in the canonical patient's owner organization: **200** calendar.
- PI in another participating organization, with the relationship lookup permitting access to that patient: **404**, “Patient not found in the MongoDB patient plane for this trial.”

This is distinct from legitimate denial of an unrelated organization. Required repair: resolve the actual canonical owner and trial/patient relationship from trusted records, then scope reads/writes to that owner. Keep cross-tenant and foreign-parent denial tests. Do not globally trust a supplied external ID or relax organization checks.

### R3 — High: command history exposes private details through a granted read capability

Source: `uctsm_mongo.py::list_commands` and `patient_commands.py::ledger`.

Calendar serialization redacts non-staff authority/reason fields, but the history endpoint returns the raw command ledger, including nested payload and clinical reason. The capability system permits an explicit `patient.command.preview` grant to a Sponsor/CRO even though those roles have no default.

HTTP reproduction with the real guard and a Sponsor principal holding only the relevant read grants:

- GET commands returns **200**, with `reason=PRIVATE_CLINICAL_NOTE` and `payload.authority=PRIVATE_AUTHORITY`.
- GET calendar for the same principal redacts `override_reason`.

Required repair: distinguish permission to read history from permission to see clinical/private fields. Apply an explicit role/capability-aware allowlist recursively, or deny the clinical history profile to unsupported recipients. Test explicit grants as well as default roles, including nested payload, before/requested/result fields and actor identity. A currently narrow role default is not a privacy boundary.

### R4 — High: an unknown account role can authorize through an explicit grant

Source: `backend/app/authorization/policy.py::authorize/holds`.

Reproduction: `Principal(role='UNKNOWN_ROLE', grants=(Grant('schedule.approve'),))` with an otherwise permitted resource returns **ALLOWED**. The policy checks capabilities and relationships but does not reject unknown roles before looking up grants.

This contradicts the P3 fail-closed rule that unknown roles deny. Required repair: validate trusted supported principal roles centrally, preserve approved explicit grants for supported roles, and add unknown/missing/invalid-role tests with valid grants. Do not introduce a platform-admin bypass as part of the fix.

### R5 — Medium: an override fabricates an outside-window verdict when no window is known

Source: `backend/app/domain/schedule/tracks.py::apply_overlays`.

Reproduction, memory and real Mongo:

- Imaging protocol target: **31 January 2026**.
- Protocol earliest/latest: **null/null**.
- Override target: **2 February 2026**.
- Command impact says `outside_protocol_window=true`.
- Calendar simultaneously reports `window_known=false` and `override_outside_window=true`.

The override calculation falls back to the nominal target as both bounds, treating an unknown window as zero width. Required repair: preserve unknown permitted bounds and return an unknown/not-evaluable deviation state unless an explicit exact constraint establishes a boundary. Distinguish a truly zero-width permitted window from an unstated window. Cover appointment and override paths, partial/disjoint bounds, command diff, calendar and UI flags.

### R6 — Medium: treatment-only actual input passes its model but fails the fact workflow

Source: `backend/app/domain/schedule/commands.py::RecordActual`, `patient_commands.py::_prepare`, and `patient_schedule.py::_build_fact`.

Reproduction: a valid `RecordActual(logical_key='C1D1:0', treatment_date=2026-01-15)` with no visit date passes typed payload validation, but preview raises **ValueError: “a performed fact needs its date.”** The model permits either actual date; persistence requires the visit-value field.

Required repair: make model, API, UI, fact storage and evaluator agree. Support independent treatment actuals where required by the intended contract; otherwise reject the unsupported shape at the typed boundary with an explicit rule and usable message. **Never copy treatment date into visit date** to satisfy storage. A treatment fact must not fabricate attendance/completion or date unrelated visit-based rules.

## B. Source-verified integration/UI repairs

### R7 — High: one patient screen reads SQL while the command panel writes Mongo

Source: `frontend/src/features/uctsm/PatientScheduleScreen.tsx::load` and `PatientCommandsPanel.tsx::reload/doConfirm`.

- Main schedule, anchors, conditions and projections use SQL UCTSM APIs.
- The panel independently probes Mongo calendar/history and enables commands if a matching patient exists there.
- Confirmation writes Mongo, then `onChanged` invokes the parent's SQL reload.
- The panel displays the loaded Mongo calendar only as a visit count, not as the persistent schedule; it does not compare active version/evaluation with the SQL screen.

Existence in both stores is not evidence of authority or parity. The wiring permits a confirmed Mongo change beside unchanged SQL dates, or a command against a different pinned version. This is source-confirmed; no live two-store production patient or browser walkthrough was used in the audit.

Required repair: establish an explicit supported active-plane contract and make displayed calendar/projection, command preview/commit, and reload use it consistently. Validate version/identity mapping before enabling commands. Block an inconsistent combination visibly. Do not solve it through an unapproved mirror or authority cutover; prepare the owner decision where necessary. A trial's legacy-versus-engine read mode alone does not select SQL-versus-Mongo repositories.

### R8 — Medium: the track sheet cannot configure the stop permission it stores

Source: `TrackReviewSheet.tsx` and `trackReview.ts::buildTracksRequest`.

New track state initializes `stoppable=''`, and the sheet has no control to change it. The builder therefore submits `stoppable=null` even when the reviewer wants a confirmed, stoppable treatment process.

Important distinction: backend validation emits **non-blocking `TRACK_TERMINATION_UNSTATED`**, and Stop commands are refused until a policy is stated. This is **not an overall approval blocker**. Existing tracks retain a prior value, but a new track's Stop policy cannot be completed through this UI.

Required repair: add an explicit unknown/allowed/disallowed choice based on evidence, retain rationale/material-edit/revision behavior, and exercise the actual sheet with a new process-linked track. Do not default it to true.

### R9 — Medium: probe and patient-switch state need lifecycle handling

Source: `PatientCommandsPanel.tsx::reload/useEffect`.

Any failed calendar/history request becomes `available='no'` and “patient is not available,” conflating 403, 503, transport failure and actual absence. The initial effect does not reset form/preview/history/availability on a patient change; its `live` guard covers availability, but `reload` still writes calendar/history directly when an older request resolves.

This is a static lifecycle finding, not a browser-reproduced incident. Required repair: clear identity-bound state, ignore obsolete responses, distinguish failure classes with retry, and verify patient-switch races, revoked access and unavailable backend in component/browser tests. Preserve backend cross-patient proposal checks as an additional defense.

## C. Policy decisions and verification limits, not newly proven defects

- **Self-grant scope:** `may_grant` refuses self-grants only for `CLINICAL_DECISION_CAPABILITIES`. An organization admin can self-grant `patient.occurrence.record` or `patient.state.record`; the list excludes them. This was reproduced in the pure policy. Clarify whether the approved segregation rule covers clinical data-entry/correction rights too, then align code, tests and the report's broader “no clinical self-grants” wording. Do not infer owner approval.
- P3 role/default/high-impact matrix remains proposed. Legacy upload/consume wrappers remain outside full catalog integration, and cross-org lists remain bounded.
- CURRENT is still LAST, not target-relative. Confirm/document the intended contract or keep unsupported interpretations explicitly unresolved.
- Whether TRIGGER/PRECONDITION alone implies actual completion remains open. Explicit actual requirements must still obey selected occurrence identity.
- SQL-only patient command support and persistence authority need an explicit integration decision. No mirror/cutover was authorized or performed here.
- Actual PERIGON documents, external registry provider, PostgreSQL locking, browser/device walkthrough, and the large ProtocolScheduleScreen typecheck remain unverified.

## Independent verification performed

All mutable Mongo runs used explicit local `127.0.0.1` replica-set settings, isolated test databases, and `UCTSM_REQUIRE_MONGO=1`. TestClient modules were run separately. No shared cluster was used.

| Focused run | Result |
|---|---|
| P5 tracks + patient commands | 92 passed, 2 skipped |
| P5 command API | 14 passed |
| P5 track-review API | 3 passed |
| P4 dependency/recurrence + persistence/approval | 56 passed |
| P2 identity domain + store | 41 passed |
| P2 identity workflow | 22 passed |
| P1 occurrence recalculation + real-Mongo concurrency | 62 passed |
| P1 API | 17 passed |
| P3 policy + route manifest | 113 passed |
| P3 HTTP | 42 passed |
| **Total** | **462 passed, 2 skipped** |

The two skips are documented single-threaded memory concurrency variants; Mongo variants ran. Passing these regressions does not cover the new exploratory failures above.

Frontend: patient commands **8**, visit schedule **27**, protocol identity **5** — **40 passed**. The current patient-command script contains eight tests; this review does not repeat the older reported count of ten as current evidence.

Additional isolated real-Mongo reproductions confirmed R1 and R5. HTTP reproductions R2/R3 used the real policy/router with memory canonical records, test authentication and an explicitly permitted operational relationship double. R4/R6 used the real pure policy/shared application service. R7–R9 were verified in source/builder contracts, not through a browser/device.

The full curated 540-test run, broad journey, all remaining backend modules, PostgreSQL concurrency and whole-screen typecheck were not rerun here. `git diff --check` returned no whitespace errors and existing line-ending notices.

## Recommended repair sequence

1. Close P2 stale-worker fencing and P3 role/privacy/cross-org owner-resolution gaps with regression tests.
2. Correct unknown-window verdicts and the independent treatment-date contract without fabricated defaults.
3. Make the selected production plane consistent for reads, commands and reload; resolve the authority/SQL-only decision concretely.
4. Complete stop-policy controls and robust panel lifecycle/error handling, then perform a real UI walkthrough.
5. Resolve pending owner policies separately; rerun affected regressions and the applicable curated gate.

Keep P6–P14 work separate. These repairs make the implemented prerequisites more reliable; they do not certify universal protocol coverage or authorize deployment.
