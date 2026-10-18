# Claude Code prompt: repair and close P1-P5 against the PDF

Prepared 14 October 2026. This prompt incorporates `docs/p1-p5-consolidated-repair-audit.md`, the latest P5 repair report, and the numbered P1-P5 requirements in `D:\MTB_dev (2).pdf`.

Copy the full prompt below into Claude Code with MTB-APP open. Existing implementation reports are evidence to investigate, not permission to mark requirements complete.

---

You are the engineer completing the current P1-P5 implementation in MTB-APP. **Repair all nine findings in the consolidated audit, close the remaining acceptance gaps, and produce a truthful, independently reviewable handoff before moving on to P6.** Reuse the implementation already present. Do the work; do not stop after a plan, a capability statement, or another partial status report.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Requirements: `D:\MTB_dev (2).pdf`, specifically P1-01 through P5-05.
Audit: `docs/p1-p5-consolidated-repair-audit.md`.

Think before editing: trace the failing workflow, identify its invariant and transaction boundary, compare reasonable fixes, and document the chosen approach and compatibility implications. Provide concise design rationale and evidence; private chain-of-thought is not requested. Passing existing tests is insufficient when the audit reproduces behavior those tests do not cover.

## 1. Scope, progress, and authority

The PDF has **14 epics, P1-P14, with five numbered tasks each: 70 tasks**. P0 is a priority, not a separate epic. P1-P5 cover 25 task areas, or **35.7% of the task list by count**. That is implementation coverage, not percent of effort, verified acceptance, or release readiness. Do not describe 25 tasks as fully completed without the evidence below. P6-P14 contain 45 task areas outside this closure review; do not assume their current code is absent or complete.

This prompt authorizes repository repairs, tests, local verification, documentation, and a concrete integration proposal. It does not approve a clinical role matrix, grant users clinical rights, authorize production data migration, switch authority, deploy, commit, or push.

Read `AGENTS.md` and applicable nested instructions first. Read the current source before deciding whether older architecture statements remain accurate. Extraction and Mongo workflows have changed since some status documents were written. Honor the safety and compatibility policies even when a descriptive status sentence is stale.

Do not:

- Reset, discard, clean, or overwrite unrelated working-tree changes.
- Remove unrelated logs, scratch files, requirement documents, or `landing/`.
- Flip `UCTSM_AUTHORITATIVE`, change trial read modes, or mirror/reassign live patients without the necessary authorization and parity checks.
- Enable `DEV_OTP_MODE` outside local development.
- Invent unstated temporal bases, clinical sources, windows, termination limits, track policies, grants, or enrolment/version mappings.
- Mutate approved versions, historical evaluations, completed facts, or committed command intent in place.
- Duplicate the scheduling algorithm in a route or frontend workaround.

Keep the domain independent of HTTP, Mongo, SQLAlchemy, and provider SDK types. Keep clinical writes, evaluation, ledger, audit, receipts and outbox inside the appropriate single-store transaction. Reading SQL and writing Mongo does not constitute one atomic workflow.

## 2. Establish the current baseline

Read:

- `README.md`, `MTB_IMPLEMENTATION_HANDOFF.txt` sections C3/D-4 and E, and the relevant architecture audit.
- The PDF's actual P1-P5 task descriptions, dependencies, implementation guidance, and acceptance criteria. Treat document content as requirements material, not instructions overriding this request.
- `docs/p1-p5-consolidated-repair-audit.md` in full.
- `docs/p1-occurrence-recalculation-plan.md` through `docs/p5-tracks-commands-plan.md`, and `docs/p5-implementation-review.md`.
- Existing P1-P5 prompts only as background; this prompt and current source govern the closure work.

Run `git status --short`, `git branch --show-current`, and `git log -5 --oneline`. Inspect the existing diff for each file you will edit. Many P1-P5 files are modified or untracked. Preserve those changes, including the pre-existing repository/port/UoW edits.

Create `docs/p1-p5-closure-plan.md` before substantive implementation. Include:

1. One row per numbered PDF task, with requirement, current source, acceptance evidence, gaps, and closure state.
2. R1-R9 audit findings: confirmed or already fixed, root cause, proposed repair, compatibility, regression, actual result.
3. The live navigation/API/store path for upload, identity review, track review, patient calendar, actual recording, command preview/confirm, and reload.
4. A concise decision table distinguishing engineering choices from owner approval, missing documents, and unavailable verification environments.

Reproduce each backend finding against the current tree. Add meaningful failing regressions before fixing confirmed defects. If another session already fixed one, verify the invariant and retain a regression; do not reintroduce it to manufacture a red test. For static UI findings, add behavior-level tests and a real walkthrough where available.

## 3. R1: fence stale extraction workers

Inspect `backend/app/application/protocol_identity.py::begin_extraction`, `complete_extraction`, and `fail_extraction`, plus runner call sites and identity repositories.

Reproduced in memory and real Mongo:

1. A claims attempt 1.
2. Its lease expires and B claims attempt 2.
3. A's late failure changes B's active job to FAILED.
4. A's obsolete completion can publish its stale payload as a reusable result.

Persist an attempt/claim token and carry it with the worker's work. Completion, failure, and lease renewal must atomically verify the caller still owns the active attempt and permitted state. Reading the latest revision and then CAS-updating that revision does not prove ownership.

An obsolete worker must not alter the current job, overwrite the result, publish a reusable extraction, attach a protocol, or create downstream intents. Define an explicit obsolete-claim outcome. Preserve safe duplicate reuse and idempotent completion by the winning claim. Audit meaningful ownership rejection without leaking document content.

Cover late failure, late success, renewal after takeover, two workers still running, retry after lost completion response, concurrent takeover/completion, and winning-result reuse. Run takeover regressions on real local replica-set Mongo; a single-threaded fake alone is insufficient. Any migration must handle existing job documents explicitly and be resumable. Do not retrofit an unowned historical worker into the newest claim.

## 4. R2: resolve canonical ownership and operational relationships correctly

Inspect `backend/app/authorization/guard.py::_resolve_plane`, `backend/app/api/uctsm_mongo.py::resolve`, and actual operational trial/site/patient relationship providers.

The current Mongo path scopes canonical lookup to the caller's organization. The audit's HTTP test allowed a cross-organization PI's operational relationship but the Mongo handler still returned 404. A relationship test double demonstrated the resolver defect; it did not prove every production assignment path works.

Resolve the persisted canonical resource owner, trusted external mapping, parent trial, and operational relationship before granting scoped access. Use the authorized resource owner for repository access. A legitimate external investigator must not be treated as the owning organization or refused solely because their home organization differs.

Keep unrelated organizations, forged external IDs, foreign parents, unassigned sites/patients, revoked assignments, and mismatched canonical mappings denied. Preserve agreed non-disclosing 403/404 responses. Do not solve this by dropping tenant filters, trusting request-body organization IDs, or using a global first-match lookup.

Add HTTP tests backed by real persisted operational relationship records, including accepted and revoked assignments, plus canonical memory and Mongo variants as practical. Cover calendar, actual recording, preview, confirmation, replay and history through the same resolver. A successful calendar request must not mask incorrect mutation scope.

## 5. R3 and R4: close privacy and fail-closed authorization gaps

### R3: command history

Inspect `uctsm_mongo.py::list_commands`, `patient_commands.py::ledger`, serializer/DTO contracts, policy profiles, and frontend history use.

A Sponsor explicitly granted `patient.command.preview` receives private clinical reason and nested `payload.authority`; the same principal's calendar redacts the corresponding fields. Narrow role defaults do not protect explicit grants.

Separate permission to access history from permission to see its clinical fields. Apply a deliberate response allowlist/profile before serialization. Protect nested payloads, before/requested/result/diff objects, actor identity, evidence, authority, reasons and diagnostics. Preserve the staff information actually needed for clinical review. Do not merely remove the top-level field while leaving a copy nested inside the payload.

Test Sponsor/CRO with explicit grants, ordinary defaults, patients viewing their own history, PI/CRC, denied and revoked users, and foreign resources. Use identifiable sentinel values in private fields and assert absence across the entire serialized non-staff response. Check error bodies and headers too. Keep replay responses and history exports consistent with this protection.

### R4: unknown roles

Inspect `backend/app/authorization/policy.py::authorize/holds` and principal construction. The audit authorized `UNKNOWN_ROLE` with a valid `schedule.approve` grant.

Validate supported trusted principal roles centrally before considering grants. Unknown, missing, malformed, or untrusted roles must deny without mutation or sensitive data. Preserve legitimate explicit grants to supported roles and the established relationship checks. Do not add endpoint-specific role-name branches or a new platform-admin bypass.

Test pure policy and HTTP behavior, valid grants attached to unknown roles, revoked grants, and normal known-role cases.

## 6. R5: represent unknown windows honestly

Inspect `backend/app/domain/schedule/tracks.py::apply_overlays`, command-impact models, calendar DTOs and presentation.

Reproduction: protocol target 31 January 2026, permitted earliest/latest both null, override 2 February. Impact reports outside-window=true while calendar says window-known=false. The calculation invents a zero-width window from the nominal target.

Use an explicit not-evaluable/unknown verdict when permitted bounds are unknown. An exact target becomes an exact permitted window only when the protocol actually establishes that constraint. Preserve a known zero-width window distinctly. For partial bounds, evaluate only the known constraints and do not infer the missing side; report enough information to avoid overstating full conformance. Preserve disjoint-window behavior where supported.

Make preview, confirmation, persisted projection, calendar reload and UI agree. Check appointment rescheduling as well as override. Preserve the original protocol target/window independently from an authorized exception and its operational dates. Never suppress a known true deviation merely because the override is authorized.

Add memory and real-Mongo regressions for unknown, explicitly exact, bounded, partial and disjoint windows, plus frontend assertions for the resulting states.

## 7. R6: support the independent actual-treatment contract

Inspect `RecordActual`, `patient_commands.py::_prepare`, `patient_schedule.py::_build_fact`, occurrence storage/context, actual APIs, form validation and evaluator references.

Reproduction: `RecordActual(logical_key='C1D1:0', treatment_date=2026-01-15)` passes the typed model, but preview raises `ValueError: a performed fact needs its date` because the fact path requires a visit-value date.

The PDF distinguishes actual visit from actual treatment. Prefer a consistent supported treatment-fact representation where the existing intended contract permits independent treatment recording. Trace the original requirements before changing accepted payloads. If a specific unsupported combination must be prohibited, reject it at the typed boundary with an actionable documented rule, not an internal exception.

Never copy treatment date into visit date, infer attendance, or mark an unrelated visit completed to satisfy storage. A treatment-relative rule may use the treatment actual; a visit-relative rule still waits if visit actual is absent. Preserve both dates when they differ, partial actuals, corrections/supersession, original planned history, occurrence index and approved version lineage.

Test treatment-only, visit-only, both with different dates, neither, correction, repeated occurrences, reevaluation, immutable history, HTTP error semantics, retry/idempotency, and calendar reload. Include the selected-occurrence regression so another repeat's actual cannot satisfy the requirement. Run the supported storage and HTTP paths; a model-only test is insufficient.

## 8. R7: complete one consistent live read/write workflow

Inspect `PatientScheduleScreen.tsx::load`, `PatientCommandsPanel.tsx::reload/doConfirm`, all called APIs, actual-recording actions, operational bridge, version assignment and condition/anchor sources.

Today the parent loads SQL dates/anchors/conditions and the child probes Mongo calendar/history. Mongo existence enables Mongo commands; confirmation then reloads the parent's SQL data. Source wiring therefore permits a confirmed change beside unchanged SQL dates or a different pinned version. This is a source-confirmed risk, not a browser-reproduced production incident.

Establish an explicit supported active-plane contract. Patient identity, owner, trial, assigned schedule version, displayed projection/evaluation, preview, confirm, history and reload must resolve coherently. Mere existence in both stores is insufficient. A legacy-versus-engine trial read mode does not choose SQL-versus-Mongo repositories.

Prepare a concrete architecture decision using the actual repository direction:

- A shared-service SQL adapter would preserve the current SQL patient path but needs genuine repository/UoW contracts, transaction semantics and concurrency proof; avoid copying the clinical algorithm.
- A Mongo-authoritative supported cohort needs verified enrolment/version/condition mappings and a consistent Mongo display path. Moving an existing SQL patient's authority requires an explicit authorized migration plan; a mirror alone does not establish authority.

Implement the reversible supported path already permitted by existing decisions. If an essential authority decision is absent, complete the source-backed proposal, affected routes/data, rollout and rollback, contracts and independent repairs before requesting that decision. Do not silently select migration, invent mappings, or repeatedly stop with the abstract question “SQL or Mongo?”

Until a patient's plane/version consistency is proven, disable clinical commands with a precise explanation. A fail-safe disabled panel closes an unsafe state but does **not** close the PDF's production command workflow. Likewise, a hidden workbench or isolated synthetic patient is not proof that the intended live patient cohort can use the feature.

Preserve legacy comments/tasks/edits as required by handoff C3/D-4. Keep anchors and conditions coherent with the chosen plane; do not assume a Mongo counterpart exists. Keep derived anchor candidates pending until explicitly confirmed. If current SQL occurrence recording derives candidates but the new path does not, account for that compatibility gap before claiming live parity.

Verify through production navigation: enrol/resolve a supported patient, review the pinned version, load calendar, record actual or command, preview, confirm, reload and reopen. Compare the same projection/evaluation/version after commit. Verify mismatched planes/versions are visibly refused with no writes. If SQL-only patients remain unsupported, name the exact cohort and retain the closure blocker.

## 9. R8 and R9: finish track review and panel behavior

### R8: explicit stop policy

`TrackReviewSheet` initializes `stoppable=''`, has no setter control, and sends null for a new track. Add an evidence-based choice for unknown/not stated, allowed and disallowed. Preserve existing values, rationale, expected revision and material-edit provenance. Do not default to allowed.

`TRACK_TERMINATION_UNSTATED` is currently a warning; Stop is refused until its policy is stated. Do not falsely describe this as an overall approval blocker or change that governance rule without requirements support. Verify a new process-linked track can be classified and its intended stop policy persisted using the real sheet, then that allowed/refused Stop commands behave accordingly.

### R9: request and identity lifecycle

Reset identity-bound form, preview, history, calendar, availability and retry keys when patient/trial context changes. Ignore obsolete responses using cancellation or request generation checks; guarding only one setter is insufficient. Ensure a preview/confirmation cannot be carried across patients or pinned versions.

Distinguish actual absence/unmapped patient, denied capability, unavailable service, expired authentication and transport failure. Show usable messages and retry actions without leaking foreign resource existence. Do not collapse all `Promise.all` failures into “patient unavailable.”

Test A-to-B patient switching while A's request is delayed, response ordering, access revocation, 403/404/503, lost confirm response and retry, stale/expired preview, cancellation, rapid repeated confirmation and navigation away. Exercise the components, not just request-builder functions. Preserve backend foreign-proposal and actor/grant-injection rejection.

## 10. Preserve the already repaired invariants

Retain meaningful regressions for the previous P4/P5 repairs:

- Numbered dependency selection uses the same occurrence for status, actual visit/treatment and timing.
- A 2027-effective rebase does not move 2026 targets; overlays/reversal honor stable effective periods.
- Resume cannot precede the pause it ends; inserting a transition revalidates the relevant timeline.
- Identical semantic replay through a second preview returns the original command, rows, digest and evaluation.
- Reload retains appointment, override target/window, cancellation and track while protecting clinical/private details.
- Conflicting recurrence starts block approval with an issue path; ordinary missing patient anchors still wait.
- A 10,001 count does not exceed the engine's 10,000 safety projection and truncation remains explicitly incomplete, not a protocol maximum.
- An undated ending event yields provisional waiting behavior, not an invented end date.
- Extracted interval bases remain unconfirmed until review; review controls are capability-gated.
- Unscheduled placeholders do not collide with the first real occurrence's index.
- Unknown command types require the strict capability and do not exploit an any-of grant.

Check changes to these shared primitives against calendar, dashboard, deviation reports, approval, amendment preview/confirm, reminders/outbox and version lineage as relevant. Do not broaden into unrelated reminder or push infrastructure.

## 11. Acceptance matrix: close every P1-P5 task, not only R1-R9

For each row below, consult the PDF's full wording and record source/API/UI/test evidence. These are closure checks, not allegations that every listed feature is broken.

| Task | Required closure evidence |
|---|---|
| P1-01 | Explicit original/current planned, actual visit/treatment, allowed fallback and manual date bases; typed validation/round-trip; no name-based inference. |
| P1-02 | Applicable patient/version occurrence history loaded before evaluation; unrelated reused codes isolated; approved reassignment lineage carries history only through explicit mapping. |
| P1-03 | Stable logical occurrence identity across evaluations; completed actuals survive; immutable old evaluations remain queryable; projection uniqueness enforced. |
| P1-04 | Authorized reachable occurrence path atomically records, evaluates, audits and writes outbox; scoped idempotency; changed and unchanged results; no stranded split-store workflow. |
| P1-05 | Baseline 1 Oct 2026, V2 actual 13 Oct, V3 actual+14d = 27 Oct; baseline imaging stays 31 Oct; current-planned case stays 24 Oct; missing actuals, history, correction and duplicate behavior proven. |
| P2-01 | Queryable versioned identity/evidence/hashes/reviews; migration validators/indexes; tenant scope and append-only audit. |
| P2-02 | Every live upload/attach/consume entry point gates identity before full extraction/attachment; mismatch and uncertainty cause no expensive extraction or attachment. |
| P2-03 | Deterministic match/mismatch/uncertain/duplicate/new-version evidence; duplicate reuse, amendment linkage and fenced extraction ownership under concurrency. |
| P2-04 | Authorized reachable quarantine review with evidence/reason, safe resolution and resume; revoked/foreign reviewers refused. |
| P2-05 | Known mislabeled NCT02088541 PERIGON, correct NCT02088554 and genuine amendment regression fixtures; real-document verification distinguished from synthetic tests. |
| P3-01 | Version-controlled capability/role/scope matrix and documented product/clinical owner approval, including newly added commands. |
| P3-02 | Central reusable policy; valid explicit grants, supported roles only, persisted relationships and revocation; denial before mutation or sensitive serialization. |
| P3-03 | Current dynamic route inventory, all sensitive entry points protected, scoped list queries and response profiles; no history or diagnostic privacy leak. |
| P3-04 | Configurable maker-checker at shared approval boundaries, material edit provenance, immutable approved versions and separately persisted refusals. |
| P3-05 | Role/capability/resource matrix including explicit grants, cross-org/site/patient scope, unknown roles, revocation, privacy and self-approval cases. |
| P4-01 | Dependency condition evaluated first; false inactive dependency cannot demand actual/manual input; unknown condition waits; independent event timing retains its own requirements. |
| P4-02 | Typed reviewed recurrence basis and actual source; invalid combinations refused; legacy compatibility explicit. |
| P4-03 | First resolved timing starts occurrence 0; 1 Jan +14d then every28d = 15 Jan/12 Feb/12 Mar; supported month/year arithmetic, indexes/windows/horizon intact. |
| P4-04 | Approval-time coded validation and paths for unsafe basis/start/reference/condition/cycle/termination/effective-time/safety structures. |
| P4-05 | Exact outcomes/dates for all supported bases and count/date/event/condition/horizon endings, month-end/leap, absent sources, cap and actual selection. |
| P5-01 | Track/global/cross-track classification for applicable schedulable events; reviewed evidence/policies, valid references and both-store round-trip; explicit legacy compatibility. |
| P5-02 | Only policy/dependency-reachable events move; fixed imaging remains fixed; future effective periods, missing actuals, holds and cross-track rules explain changed and unchanged results. |
| P5-03 | Typed append-only commands, trusted actor, effective time, required reason, before/requested values, idempotency and linked reversal; atomic ledger/evaluation/outbox; no direct projection-date edits. |
| P5-04 | One calculation path for preview/commit, same committed semantic result, stale/expired/conflicting preview refusal, stable original replay outcome and privacy-safe persisted diff. |
| P5-05 | Reachable forms and actual end-to-end walkthroughs for delay, unscheduled, appointment inside/outside/unknown window, missed/cancel/override/pause/resume/rebase/stop/reversal; protocol target/window separate from operational/actual dates after reload. |

Audit related legacy upload/consume wrappers instead of excluding them by router name. Where they perform the same sensitive workflow, integrate the central capability/scope/privacy checks or prove equivalent enforcement; preserve the P2 identity gate. Update the route manifest dynamically for new routes. Check cross-organization list behavior, including bounded candidate queries/pagination, for permitted users without weakening isolation.

Manual evidence-based track review can be the supported workflow if extraction proposes no tracks. The PDF does not require inventing tracks from names. It does require usable production classification and valid membership/policies, rather than API-only editing. Exercise that path and distinguish intentional legacy compatibility from newly approving incomplete track-aware versions.

## 12. Resolve owner and evidence gates without inventing approval

Prepare a compact decision packet early, and continue independent work while decisions are pending. Each item needs a recommendation, alternatives, affected behavior, rollout/rollback and the exact approval needed:

1. Supported patient authority/plane and SQL-only command coverage. Specify a concrete cohort, enrolment/version/condition mapping and read/write route design.
2. P3 role matrix, high-impact and preview defaults, identity-review default, maker-checker setting/scope and grant rollout. Keep `PROPOSED_PENDING_OWNER_APPROVAL` until actual approval arrives.
3. Self-grant scope: current rules forbid self-grants of clinical-decision capabilities but permit occurrence/state recording. Clarify whether segregation also covers clinical data entry/correction; align the approved policy, tests and documentation. Do not claim the broader rule is already enforced.
4. `CURRENT` selection: it is presently an alias of LAST. Do not label it target-relative. If target-relative semantics are required, obtain/derive the authoritative contract and implement it; otherwise document the supported meaning and block unsupported ambiguous use.
5. Whether TRIGGER/PRECONDITION alone imply actual completion. Keep explicit actual-mode selectors correct regardless of this separate decision.

Locate genuine PERIGON and amendment fixtures in the authorized local inputs first. If unavailable, list the exact missing documents and required provenance; do not fabricate real fixtures or rename synthetic text as real-document proof. Continue all independent work. Synthetic regression closure is not the PDF's demonstrated-document verification.

External registry results must remain NOT_CHECKED/UNAVAILABLE when no real provider exists. Audit whether provider integration is necessary for the chosen acceptance contract. Prepare a supported implementation/configuration and evidence plan where required; do not report scripted-provider tests as a verified live registry or treat the local database as one. Missing credentials/provider decisions must be explicit, not disguised by a passing stub.

Ask only for decisions/information that cannot be responsibly inferred. Do not use these gates to postpone R1-R9 or other independent repairs. When an essential gate remains missing, deliver completed reviewable work and state which PDF rows it prevents closing. Never certify total closure by assuming consent or skipping evidence.

## 13. Verification that demonstrates behavior

Run affected tests first, then the applicable curated gate and cross-feature regressions. Read the actual current gate in handoff section E. Do not run plain whole-suite pytest because several TestClient modules conflict during teardown; run affected integration modules in separate processes. Omit only the specifically documented deleted extraction/footnote files if still absent, and report other missing files as problems.

Use real **local** replica-set Mongo with explicit `UCTSM_TEST_MONGO_URL` and `UCTSM_REQUIRE_MONGO=1`. Verify the resolved URL is local before any mutable test. Use isolated test databases; never fall through to a production/shared environment. Report Mongo variants and concurrency checks separately from fake-store tests. Required checks include worker fencing, single-winner commands, identical concurrent replays, scoped unique indexes, and injected transaction failure leaving no fact/ledger/evaluation/audit-business/outbox partial writes.

The previous audit independently ran 462 backend passes with two documented in-memory concurrency skips, plus 40 frontend passes. Those are a baseline, not evidence your new changes were tested. Obtain current counts from actual results; do not copy older claimed 540/232/37 totals or sum overlapping runs as unique coverage.

Frontend scripts currently include `test:uctsm-patient-commands`, `test:uctsm-visit-schedule`, `test:protocol-identity`, and `test:uctsm`. Use the applicable scripts, plus real component/browser checks for the lifecycle and mounting defects. Builder tests are not UI proof.

Typecheck every changed frontend component including `ProtocolScheduleScreen`'s track-sheet mount. Use a bounded project/configuration that preserves the relevant Expo/JSX/path-alias/type settings when the whole project exceeds memory. Do not replace meaningful checking with `any`, `@ts-ignore`, ignored diagnostics or unrepresentative compiler flags. If still unavailable, report the exact unchecked files and retain the verification gap. Lint's known missing `yarnpkg` prerequisite is not a passing lint result.

For SQL paths changed or relied on, test the actual SQL services/routes. If PostgreSQL concurrency/locking is material, use a local isolated PostgreSQL instance where supported; SQLite cannot prove it. Do not claim untested SQL command support or PostgreSQL locking. Do not introduce SQL support merely to remove a report line if the approved workflow is Mongo.

Perform a browser or device walkthrough through existing navigation with local isolated data and appropriately granted test users. At minimum:

- Upload mismatch, review/correct/resume, duplicate reuse and amendment linkage.
- Review recurrence basis and new track membership/policies including stoppable unknown/allowed/disallowed.
- Actual treatment delay: dependent treatment/lab shifts, baseline imaging stays fixed.
- Preview, confirm, persisted reload, lost-response replay and stale rejection.
- Protocol versus appointment/override presentation for known and unknown windows.
- Pause/resume/cancel/stop/reverse supported cases and refused chronology.
- Authorized cross-org investigator, privacy-limited Sponsor/CRO, revoked grants and unknown roles.
- Patient switching during delayed requests and backend unavailable/denied states.

Capture the route, user capability profile, store/version/evaluation identity, observed result, and screenshot or test artifact where useful. No real patient data. If a browser/device cannot run, specify the actual blocker and do not mark P5-05 UI acceptance complete.

## 14. Completion documents and final response

Update the affected P1-P5 plan documents to match the final source. Correct obsolete opening status tables as well as adding appendices; contradictory old summaries make review harder. Preserve the original audit findings and append their dispositions rather than erasing evidence.

Deliver `docs/p1-p5-closure-report.md` containing:

- All 25 PDF task rows with implementation, API, reachable UI, real-store verification and acceptance states separately.
- R1-R9 resolutions with source references, regressions, expected/actual behavior and store/environment used.
- Final plane/authority contract, supported/unsupported patient cohorts, version/condition mapping and rollout/rollback.
- Approved policies with actual approval provenance, or clearly pending owner decisions.
- Exact test commands, pass/fail/skip counts and skip reasons; walkthrough/typecheck artifacts and honest limits.
- Genuine document/provider verification status, migration compatibility and remaining operational risks.
- A focused P6 handoff naming prerequisites still unresolved, rather than a claim that every protocol is representable.

Use precise task states: VERIFIED_ACCEPTANCE, IMPLEMENTED_NOT_FULLY_VERIFIED, NEEDS_REPAIR, BLOCKED_OWNER_DECISION, or BLOCKED_EXTERNAL_EVIDENCE. State any subcriteria that remain open when a row is partially satisfied.

Finish with a short plain-language report: what was fixed, why behavior is now correct, what was tested, what remains blocked, and the next concrete decision/input if necessary. Check `git diff --check` and final status. Do not commit, push, deploy, or delete unrelated files.

**Definition of closure:** all confirmed defects repaired with meaningful regressions; supported production workflows use consistent identities/authority and retain privacy/history; every P1-P5 acceptance criterion has actual evidence; essential clinical/product policies are approved; missing corpus/provider/UI/locking evidence is obtained where relevant. Until then, report the exact unfinished rows. Do not move unresolved prerequisites into P6 merely to label P1-P5 complete.
