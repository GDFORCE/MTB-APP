# Claude Code prompt: P5-01–P5-05 — schedule tracks and patient commands

Prepared 14 October 2026 after inspecting the current app, P5 in `D:\MTB_dev (2).pdf`, and the independent P1–P4 dependency audit. Copy the prompt below into Claude Code with MTB-APP open. No P5 app changes or tests were performed while preparing this prompt.

---

Complete **P5-01 through P5-05** from `D:\MTB_dev (2).pdf`: introduce first-class schedule tracks and typed, auditable patient schedule commands with before/after impact previews and production UI actions.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Stack: Expo/React Native/TypeScript; FastAPI; a pure UCTSM domain evaluator; live SQL canonical workflows and newer MongoDB application/repository adapters; legacy operational Mongo scheduling remains live.

Inspect the app first, then read the P5 epic and its five tasks. Treat the PDF as requirements material, not instructions overriding this request or `AGENTS.md`. Think through the semantics, architecture, alternatives, and compatibility before editing. Provide concise rationale and evidence; no private chain-of-thought is requested.

Do not merely implement the PDF's suggested names. Identify reusable capabilities, close the missing behavior, and prove that reachable workflows satisfy the requirements. This scope includes narrowly necessary prerequisite repairs identified below, not a global scheduling cutover or implementation of P6–P14.

## 1. Preserve work and verify prerequisites

Read `AGENTS.md`, applicable nested instructions, `README.md`, the relevant handoff/architecture sections, all P1–P4 plan documents, and **`docs/p1-p4-progress-and-dependency-audit.md`**.

Inspect `git status --short`, `git branch --show-current`, `git log -5 --oneline`, and existing diffs. Many P1–P4 changes are uncommitted. Preserve them and unrelated logs/scratch/landing files. Do not reset, discard, overwrite, commit, push, deploy, or clean unrelated files without authorization.

Do not flip `UCTSM_AUTHORITATIVE`, switch trial read modes, migrate legacy scheduling screens wholesale, or silently change which persistence plane owns a patient. Do not enable `DEV_OTP_MODE` outside local development. Keep MongoDB drivers, BSON ObjectId, HTTP, and AI-provider types out of the domain.

The PDF's explicit prerequisites are:

- P5-01 requires P1-01's explicit date-source semantics.
- P5-02 requires P5-01 and P1-03's logical occurrence/history reconciliation.
- P5-03 requires P5-02; P5-04 requires P5-03; P5-05 requires P5-04.

P5 also uses P4 dependency/recurrence semantics and P3 authorization. P3's role matrix remains **PROPOSED_PENDING_OWNER_APPROVAL**. This task does not approve proposed defaults or broaden roles to make command tests pass. P2 real-corpus/registry/UI limitations remain separate.

### First close or explicitly resolve the audited P4 prerequisites

Reproduce these findings against the current tree before changing them; they may have been fixed since the audit:

1. Event timing baseline +14 days, stated FIRST_RESOLVED basis, recurrence start offset +1 month: validation returned no blockers, but evaluation became UNRESOLVED. A fixed-anchor series using another independent anchor also passed validation before failing at evaluation. Block structurally conflicting/unresolved starts at approval with actionable issue paths, while keeping legitimate missing patient inputs as waiting.
2. COUNT 10,001 with a sufficiently long horizon generated 10,001 occurrences despite the reported 10,000 safety cap. Separate protocol count from the resource cap, enforce the cap consistently, and report incomplete projection when it interrupts a longer protocol series.
3. EVENT termination with an undated ending event silently used the horizon. Add a regression and implement evidenced explicit waiting/provisional semantics with explanation. Do not guess an ending date or silently claim the series is complete.
4. Newly extracted interval repeats need a reviewer-stated basis, but the app has no control to enter it. Add a narrow capability-aware basis/actual-source review control with rationale and expected revision so a production user can resolve the prerequisite.
5. Dependency satisfaction currently uses any resolved/completed occurrence under a source event code. Establish which dependencies are event-level and which require a particular occurrence; add FIRST/LAST/CURRENT/numbered-source regressions before using this logic for track propagation.

The TRIGGER/PRECONDITION “must actually have happened” rule is an unresolved owner decision. Preserve supported explicit temporal/dependency modes; do not introduce an implicit actual requirement or treat planned resolution as clinical completion without evidence.

Record prerequisite repair separately from P5 progress. Independent domain/model work may proceed while a genuinely essential clinical policy decision is pending; dependent acceptance must stay visibly blocked.

## 2. Current implementation to reuse and verify

- `backend/app/domain/schedule/timing.py`, `models.py`, `identity.py`, `dependency.py`, `evaluator.py`, and `validator.py`: P1 temporal basis/history and P4 dependency/recurrence logic.
- Existing `ProcessDefinition`, `ProcessTransition`, `RepeatBlock`, conditional pause/resume behavior, and assignment facts solve related problems. They are not automatically equivalent to a versioned `ScheduleTrack` or generic patient-command ledger.
- `backend/app/application/patient_schedule.py`: actual occurrences, missed/booked facts, supersession, current calendar, immutable evaluations, version lineage, idempotency receipts, outbox, and actual-date reevaluation.
- `backend/app/application/impact.py` and SQL `app/services/impact_service.py`: existing preview/confirm/cancel proposals for anchor/condition/state/process/assignment/version changes, input fingerprints, stale/expired checks, and schedule diffs.
- `backend/app/application/patients.py`, `records.py`, `ports.py`: occurrence/evaluation/impact/receipt/outbox records and persistence interfaces. A command receipt is not the full append-only clinical intent ledger required by P5.
- `backend/app/persistence/mongodb/` and memory adapters: UUID conversion, migrations, replica-set unit of work, transaction retries, current pointers, and repository contracts.
- `backend/app/api/uctsm.py` and `uctsm_mongo.py`: actual registered SQL/Mongo routes. The Mongo patient plane is explicitly configured and does not invent enrolment/assignment for a missing patient.
- `backend/app/authorization/`: P3 catalog, guards, manifest, grants, privacy profiles, material-edit provenance, and maker-checker.
- `frontend/src/features/uctsm/PatientScheduleActions.tsx`: existing process/group previews and direct booked/performed/missed fact recording. `PatientScheduleScreen.tsx` has impact and unscheduled-visit surfaces; inspect their production route/callers and selected persistence plane.
- `frontend/src/features/uctsm/patientActions.ts`, `api.ts`, and `types.ts`: request construction and DTOs to extend deliberately.
- `frontend/src/features/uctsm/VisitScheduleSheets.tsx` and `visitSchedule.ts`: current review editing, including the missing recurrence-basis control.
- Legacy clinical visit detail/editor routes still directly operate on operational schedules. Inventory conflicts, but do not migrate their comments/tasks/edit features or disable legacy authority as incidental P5 work.

Search for existing track/command models before adding new ones. Inspection did not find first-class `ScheduleTrack` or a complete `PatientCommand` ledger in the reviewed domain/application paths; verify the entire current tree.

## 3. Design checkpoint and scope decision

Create `docs/p5-tracks-commands-plan.md` before implementation with:

- P5-01–P5-05 matrix: current capabilities, missing behavior, prerequisite status, changes, tests, and runtime/UI reachability.
- The track-policy and command-semantics tables, including what changes protocol meaning versus an operational booking or patient-specific exception.
- Ownership of definitions, facts, commands, proposals, evaluations, projections, and reminders; actual identifier mapping across stores.
- Which real patient route/screen reaches each command and which transaction/persistence plane it uses.
- Compatibility for old schedules without tracks and existing fact/action APIs.
- Policy/version/revision/hash design, concurrency/replay/reversal behavior, and privacy/authorization mapping.

Choose the smallest coherent application boundary using existing ports and pure evaluator. P5's canonical Mongo command/track records must be implemented with real-Mongo verification; that alone does not prove integration with the currently SQL-backed clinical screen.

Explain and implement the necessary adapter/integration for each supported live plane without mixing writes across databases in one supposed transaction. A Mongo command journal plus SQL evaluation is not atomic merely because each side has a transaction. Do not invent canonical counterparts or introduce silent fallback between planes.

If production UI integration would require an unauthorized change in data authority, prepare the exact supported alternatives and request the essential decision after completing independent implementation. Do not describe a test-only Mongo route or a development workbench as P5-05 complete.

Proceed with routine supported decisions after the assessment; do not impose a blanket approval pause. Never guess unstated clinical track membership, rebase/pause policy, window, visit mode, maximum, or dependency semantics.

## 4. P5-01 — approved schedule tracks and event membership

Add a versioned typed `ScheduleTrack` with portable UUID, organization/trial/schedule-version scope, code, type/display name, anchor policy, rebase policy, pause/resume policy, termination policy, evidence, and review state.

Support Treatment, Imaging, Laboratory, Safety, Survival, and other evidence-backed track codes without hardcoding their clinical behavior. A label such as “Imaging” is not sufficient evidence to infer a fixed baseline policy.

Each schedulable event must reference a valid track or have explicit global/cross-track classification. Define cross-track relationships and references; do not place every unclassified event in a fabricated default track. Activities should inherit their event's approved policy unless an explicit distinct relationship requires review.

Create Mongo `schedule_tracks` records with UUID references from event definitions, strict validation, bounded documents, and indexes for organization/trial/version/code uniqueness and lookup. Validate ownership and references in repositories/application services. Use versioned migrations; do not edit an applied migration and assume existing databases change.

Include tracks in canonical JSON, schema round trips, extraction/review APIs, immutable version content, material-edit provenance, and semantic fingerprint. Editing an approved track policy requires a reviewed version, not a patient-level rewrite of the approved protocol.

Old schedules without tracks need a documented compatibility/unresolved path preserving existing dates. New track-aware drafts require reviewed membership and policies before approval. Extraction may propose only evidence-supported classification and must preserve uncertainty. Add a reachable review path for unresolved membership/policy; do not create another approval block that users can resolve only through a manually crafted API request.

## 5. P5-02 — explicit track rebase and pause semantics

Implement typed strategies for fixed-from-anchor, previous-planned occurrence, previous-actual occurrence, manual rebase, and no-rebase policies. Keep `TrackRebasePolicy`, P1 `TemporalBasis`, and P4 `RecurrenceBasis` distinct and validate that they compose consistently.

Define which input each policy changes and which targets remain fixed. Reachability through explicit dependencies and approved track/cross-track rules determines movement; sharing a track name does not automatically shift all its events. An appointment change alone must not rebase protocol targets. Actual treatment and actual attendance remain separate facts.

Baseline imaging stays fixed when treatment is delayed, unless an explicit cross-track rule says otherwise. A laboratory event tied to actual treatment may move while a baseline-fixed laboratory event does not. No guessed “all labs move with treatment” rule.

Define pause interval inclusion, resume behavior, catch-up, termination/stop, and whether cadence is held, restarted, or remains on a fixed grid. Reuse compatible existing process/conditional pause logic. An unknown resume/rebase rule stays unresolved; condition resolution does not silently imply resume where the approved policy requires an explicit command.

Preserve original planned dates, actuals, completed/missed identity, approved assignment lineage, and immutable historical evaluations. Validate stable numbered occurrence selection so one completed cycle does not satisfy a later cycle's missing dependency.

Return track/policy/basis/reason metadata for both changed and unchanged results. Clearly distinguish a protocol target change from an appointment, status, or patient-specific override change.

## 6. P5-03 — append-only typed patient commands

Implement typed command payloads and application handlers for:

- Record actual visit/treatment occurrence; create unscheduled visit.
- Reschedule an operational appointment; mark missed; cancel a future occurrence.
- Patient-specific date/window override with an explicit reason and authority.
- Pause/resume/rebase/stop a track.
- Reverse an earlier reversible command through a linked compensating command.

Persist Mongo `patient_commands` with UUID, organization/patient/schedule-version/track/logical-occurrence scope, actor derived from authentication, effective clinical date/time, recorded UTC timestamp, reason, typed payload, before/requested values, proposal/revision references, idempotency identity, and reversal linkage.

Define command intent as immutable. If lifecycle status changes are needed, model append-only transitions or a separate operational state; do not rewrite the original intent or delete reversed commands. Corrections/invalidations of actual facts must preserve the original clinical record and explicit supersession history. Reversing a booking or override does not erase an actual visit that later occurred.

Use existing actual/missed/booked recording as shared underlying facts, not a duplicate write/evaluation path. An occurrence request replay must not become two commands or two evaluations because both old and new handlers generate their own receipts. Preserve existing API compatibility explicitly or mark a controlled deprecation; do not leave alternate active routes bypassing audit or preview.

Do not mutate generated event targets/windows in place. Commands become controlled inputs to a new evaluation/projection. Operational appointments and exceptions remain distinguishable from the approved protocol target/window and actual dates. An outside-window appointment must retain deviation metadata; an override cannot pretend the appointment met the original protocol window.

Every committed P5 schedule command must preserve intent/history and produce the required immutable evaluation or a precisely documented compatible no-effect outcome. Do not silently inherit P1's “irrelevant actual may skip evaluation” optimization if it contradicts P5 acceptance; explain compatibility and establish the appropriate behavior.

Command/fact/evaluation/projections/current pointer/audit/receipt/outbox must commit atomically within the selected store. Use unique scoped indexes, optimistic revision checks, and safe replica-set retry callbacks. No provider/notification side effect inside a retried transaction.

Scope idempotency to the actual semantic operation. Hash material payload, patient/version/occurrence/track, effective time, relevant horizon, and reversal target. Same key/same operation replays the original result after current authorization checks; changed meaning under the same key conflicts without writes. Handle simultaneous requests through storage constraints, not UI button disabling.

Reversal must recompute current effects through the same evaluator, not restore an old snapshot over intervening commands. Reject or explicitly resolve incompatible reversals of completed history, superseded commands, newer dependent commands, or changed assignments. Double reversal and cross-patient/version targets must be deterministic, authorized, and auditable.

## 7. P5-04 — one calculation path for impact preview and commit

Extend the existing impact proposal/service where compatible; avoid an independent preview algorithm.

Preview a proposed command against the latest committed evaluation and approved policy. Return changed and unchanged occurrences with old/new protocol targets/windows, operational appointments/overrides, actuals/status, track, policy/dependency reasons, deviations, and notification create/update/cancel intents.

A preview may persist its own immutable proposal/audit record, but must not apply clinical facts, advance the schedule pointer, or send notifications. Preserve date-only versus instant semantics.

Bind confirmation to a server-owned proposal ID, canonical payload hash, evaluation/patient revision, active schedule version, relevant facts/commands, track/policy version, horizon, expiry, and calculation/evaluator version. Reject a stale/expired preview if any material input changed, and return a useful refresh path.

At commit, recheck actor capability and resource scope, rerun the same calculation, and verify the semantic impact matches the preview before writing. Generated IDs and audit timestamps may differ; clinical targets/windows/statuses/policies and notification intents must not. Serialize concurrent commits so both cannot approve the same stale base.

Persist command/evaluation/diff evidence and proposal confirmation together. Replaying confirmation returns the stored result without another command/evaluation/outbox message. Cancellation must not apply the command. Preview/commit must also support reversal commands.

Use the existing notification outbox and projection services for intent; do not claim actual push delivery or rewrite the independent medication-reminder system. A reversal must cancel stale visit reminder intents idempotently where applicable.

## 8. P5-05 — production forms, previews, and end-to-end tests

Expose track-aware commands through actual clinical patient workflow/navigation. Reuse `PatientScheduleActions`, the patient schedule/details surface, and impact confirmation UI where appropriate. Prove the identifiers and selected store correspond to the patient shown, rather than relying on fixtures or an orphaned route.

Provide typed forms for the required commands, explicit target track/occurrence, effective date, reason, and required authority. Show a before/after confirmation view for P5 changes and reversal; show unaffected events as well as changed ones. Handle stale preview, authorization denial, missing policy/input, unsupported rule, no active schedule, and backend unavailable states visibly.

Show **protocol target/window**, **operational appointment or override**, and **actual visit/treatment** separately. Do not relabel a rescheduled appointment as the protocol target. Track labels, pending status, outside-window deviations, and reversal history must remain understandable to clinical staff.

Map every command/preview/commit/read route to P3 capabilities and persisted scope; add manifest coverage. High-impact override/rebase/reversal permissions need an explicit proposal/approved mapping rather than assigning them to all staff by default. Use narrowly scoped existing rights only where their semantics support the action. No Sponsor/CRO clinical-write shortcut, no client actor/grant fields, and no implicit org-admin bypass.

Patient/Sponsor/CRO responses retain P3 field allowlists and de-identification. UI may display allowed actions from `/authorization/me`, but the server rechecks on preview, commit, and replay. Track-definition edits remain material for maker-checker/revision purposes; do not silently extend or disable maker-checker on unrelated commands without a policy decision.

## 9. Acceptance matrix and verification

Use fixed dates, explicit evidence/policy fixtures, and injected clocks. At minimum prove:

1. Every new track-aware schedulable event has valid membership/global classification; foreign/missing/version-mismatched references and unresolved policies block approval.
2. Treatment actual delay changes only approved downstream treatment/lab rules; independent baseline imaging remains fixed, including unchanged reasons.
3. Each rebase policy has distinct exact-date tests and composes correctly with all supported temporal/recurrence bases, month-end arithmetic, and actual-treatment-versus-visit values.
4. Appointment inside the protocol window changes only operational booking; outside the window remains visible as a deviation. Neither fabricates an actual or changes protocol policy.
5. Unscheduled visit, missed, cancellation, date/window override, pause/resume/rebase/stop produce explicit audited outcomes without mutating approved definitions or old evaluations.
6. Pause/resume/rebase affects only the selected track and explicit cross-track dependents, preserves completed history, and does not silently catch up or restart an unstated cadence.
7. Reversal preserves original command/fact/evaluation history, respects intervening changes, recomputes impact, and cannot target another patient/version or erase an actual record.
8. Preview and commit have matching semantic results; new actual, command, assignment, policy/version/revision, or expiry between them causes a stale rejection with no clinical writes.
9. Identical concurrent commands/confirmations create one business result; conflicting bodies or stale bases fail predictably. Injected evaluation/audit/outbox failure rolls back all clinical state.
10. Missing actuals, inactive/unknown conditions, repeated-source occurrence selection, COUNT safety caps, and unresolved EVENT end boundaries follow the repaired explicit P4 semantics.
11. Authorized real API requests and production UI actions execute these scenarios; direct API/canonical wrappers cannot bypass capability, scope, preview, reversal, or idempotency checks.
12. Memory/Mongo contract suites prove tracks/commands round-trip, indexes/validators enforce invariants, and a real local replica set proves transactional/concurrent behavior. Exercise any supported SQL runtime adapter too; SQLite does not prove PostgreSQL locking.
13. P1–P4 regressions, identity review/quarantine, maker-checker, role privacy, old approved schedules, and existing clinical comments/tasks remain intact.

Use existing impact, patient-plane, unscheduled, conditional/process, review, projection, notification, and authorization suites where relevant, plus focused new track/command/reversal tests. Add meaningful frontend request/preview/state tests and a browser/device walkthrough when available. State UI limitations explicitly if unavailable; unit/typecheck success alone is not device verification.

Use isolated local test databases, verify resolved Mongo hosts, and run with `UCTSM_REQUIRE_MONGO=1` for affected replica-set suites. Never use the shared cluster from a developer `.env` for mutation tests. Keep scratch output off a full C: drive when necessary, without deleting unrelated files.

Follow repository verification rules: targeted isolated pytest invocations, then the applicable curated handoff gate with file-existence checks/deleted modules omitted; no unrestricted whole-backend pytest. Use actual frontend test scripts and a viable scoped TypeScript check; no plain `npm test`, no whole-project typecheck that exhausts memory, and disclose Expo lint's missing `yarnpkg` limitation.

## 10. Final completion record

Run `git diff --check`, compare against the initial tree, and update the plan with actual results. Separate prerequisite repairs, P5 implementation, pending policy approval, runtime/UI reachability, and release readiness.

Final response must include:

- P5-01–P5-05 status table with actual code/test/API/UI evidence.
- Which audited P4 findings were fixed, reproduced, or remain blocked, with regressions.
- Track/rebase/command semantics, persistence ownership, migration and legacy compatibility.
- Actual preview/commit/reversal endpoints, their capability/scoping rules, and a sample diff proving treatment delay with fixed imaging.
- Production route/screen/navigation used, including visible protocol-versus-appointment/actual values.
- Real-Mongo transaction/concurrency/rollback and relevant SQL/frontend test results, actual commands, skips, and remaining limits.
- Outstanding owner decisions, including P3's proposed rights and any unsupported clinical policies. Do not claim those were approved by this implementation request.

Do not call P5 complete if only domain/service tests pass, the production UI still performs direct date mutations, the supported patient store cannot reach the command ledger, preview and commit can differ, or the prerequisite defects silently remain. Start with the repository assessment, then complete the authorized work through a reviewable verified result.
