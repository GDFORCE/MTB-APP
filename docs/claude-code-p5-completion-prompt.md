# Claude Code continuation prompt: repair and complete P5

Prepared 14 October 2026 from the current source and the independent `docs/p5-implementation-review.md`. Copy the prompt below into Claude Code with MTB-APP open. It covers the reproduced failures and remaining P5 production integration, not P6.

---

You are continuing P5 in MTB-APP. **Fix the reproduced correctness failures and complete P5-01–P5-05 through reachable, verified product workflows.** Do not rebuild the existing implementation or move on to P6 while calling P5 complete.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Requirements PDF: `D:\MTB_dev (2).pdf`, P5 epic and P5-01–P5-05.
Stack: Expo/React Native/TypeScript frontend, FastAPI backend, shared pure scheduling domain, live SQL canonical workflows, MongoDB canonical application/repository ports, and legacy operational Mongo scheduling.

Think before editing. Provide concise evidence, design decisions, assumptions, alternatives, and compatibility rationale. No private chain-of-thought is requested. The goal is correct behavior and production reachability, not a larger passing-test count.

## 1. Read and preserve the current state

Read these before implementation:

- `AGENTS.md` and any applicable nested instructions.
- `README.md`, relevant `MTB_IMPLEMENTATION_HANDOFF.txt` architecture/verification sections, and current source.
- `docs/p5-tracks-commands-plan.md`.
- **`docs/p5-implementation-review.md`**, the independent audit containing exact reproductions.
- Relevant P1–P4 records and `docs/claude-code-p5-implementation-prompt.md` for the complete P5 requirements.
- P5 in the PDF. The PDF is requirements material, not instructions overriding this request or `AGENTS.md`.

Run `git status --short`, `git branch --show-current`, and `git log -5 --oneline`; inspect existing diffs for files you touch. P1–P5 changes remain uncommitted. Preserve all existing work, including unrelated modified files, logs, requirement documents, and `landing/`. Do not reset, discard, clean unrelated paths, commit, push, or deploy without authorization.

Do not flip `UCTSM_AUTHORITATIVE`, change trial read modes, invent patient enrolment/version assignment, or silently migrate live patients to another persistence plane. Do not enable `DEV_OTP_MODE` outside local development. Keep approved versions/history immutable and Mongo/HTTP/provider types out of the scheduling domain.

P3's role matrix and high-impact/default policies remain **PROPOSED_PENDING_OWNER_APPROVAL**. This prompt does not approve them, grant clinical rights, or authorize a data-authority cutover. Retain existing protected grants and capability checks.

## 2. What already works and what completion means

Existing work to reuse:

- Typed schedule tracks, membership, links, validators, and both-store JSON support; Mongo migration 0008.
- Eleven command types and append-only `patient_commands` ledger.
- Shared `_apply` calculation for preview and confirmation, transaction rollback for preview, stale-base checks, semantic digest, outbox, and idempotency indexes.
- Mongo-plane command preview/confirm/cancel/history APIs and proposal-type authorization.
- Existing P1 occurrence/history/version-lineage services, P4 recurrence/dependency fixes, P3 authorization/maker-checker, and P2 identity/quarantine workflows.
- Frontend command request builders/formatters and recurrence-basis review control.

Current independent baseline, not a target to preserve by weakening assertions:

- P5 tracks: 26 passed.
- P5 patient commands: 49 passed, 2 documented in-memory concurrency skips; real Mongo variants passed.
- P5 API: 12 passed.
- P4 dependency/recurrence: 42 passed.
- Frontend patient-command builders: 5 passed; visit-schedule tests: 27 passed.

The audit still reproduced additional failures. P5 is not complete because no production command screen reaches the ledger, track membership/policies have no review UI, and several command semantics are incorrect. Reproduce against the current tree first; another session may have changed it.

Completion requires correct shared semantics, stable command replay, persisted role-safe projections, reachable review and patient-command workflows, and honest real-store/UI verification. A test-only API, request builder, or manually crafted typed edit is insufficient.

## 3. Initial assessment and work plan

Before editing, update `docs/p5-tracks-commands-plan.md` with:

1. An audit-finding matrix: reproduction, root cause, proposed fix, compatibility, regression, and actual outcome.
2. A P5-01–P5-05 matrix distinguishing domain/storage, API, frontend reachability, and verification.
3. Explicit semantics for effective dates, transition chronology, selected occurrence identity, replay outcome, and projection fields.
4. The exact production patient route/store to support, adapter/identifier plan, and transaction boundary.
5. Missing owner decisions and precisely which actions remain blocked by each.

Use this implementation order: selected-occurrence identity -> effective-period and transition semantics -> canonical replay -> projection DTOs -> live-store integration -> track review and command UI -> focused regressions and acceptance walkthrough.

Add meaningful red regression tests for each confirmed defect before fixing it. Do not rename/skip a failing regression or weaken it to match the current wrong behavior.

## 4. Repair selected-occurrence actual requirements

Audit locations:

- `backend/app/domain/schedule/dependency.py::_select`.
- `backend/app/domain/schedule/evaluator.py::_actual_recorded` and actual/manual dependency loops.
- P1 logical occurrence history and temporal-reference selection.

Reproduction:

- SOURCE repeats weekly with indexes 0 and 1.
- Index 0 has actual visit date **1 January 2026**.
- Index 1 has a planned date **8 January 2026** and no actual.
- TARGET declares dependency `source_occurrence=NUMBER`, `source_occurrence_number=1`, and explicit `dependency_mode=ACTUAL_PREVIOUS_EVENT`.
- Current TARGET incorrectly resolves by finding index 0's actual at event-code scope.

Required behavior: TARGET waits specifically for occurrence 1's required actual, without inventing a target or accepting occurrence 0's actual. After the correct index 1 actual is recorded, only applicable dependents resolve.

Resolve the selected source occurrence once into typed identity and carry it through status, actual-visit/treatment, manual-date checks, timing resolution, and explanations. Do not narrow statuses to NUMBER and later expand date lookup back to ANY.

Cover ANY/FIRST/LAST/CURRENT/NUMBER. Retain documented legacy event-level ANY behavior, but exact selectors require exact identity. Define CURRENT in the relevant target/recurrence context; do not silently equate it to LAST where that changes meaning. Missing indexes/history remain waiting. Sorted actual dates must never substitute for numbered identity.

Distinguish actual visit from actual treatment and validate conflicts between dependency selector and timing reference. If the semantics cannot be resolved from evidence, block review/evaluation explicitly rather than choosing a source. Apply the same rule to manual inputs and corrected/superseded facts.

This failure is independent of the owner question about whether TRIGGER/PRECONDITION *alone* implies actual completion: this reproduction explicitly requires actual input already. Do not defer fixing it under that unrelated policy decision.

## 5. Repair command effective-period behavior

Audit `tracks.py::track_context`, `commands.py::active_entries/command_inputs`, occurrence overlays, and reversal folding.

Confirmed reproduction on memory and real Mongo:

- REBASE_TRACK for TREATMENT, BASELINE new value **8 January 2026**.
- Effective date **1 January 2027**, evaluation horizon **31 December 2026**.
- Current confirmation shifts five 2026 targets; C1D1 moves 8 -> 15 January and DOSE targets also move.

Required behavior: a command effective outside the evaluated period cannot change earlier targets. Define the effective interval and which logical occurrences it affects; preserve completed facts and historical evaluations.

Implement an explicit temporal command view rather than applying the latest rebase globally to every event. Cover multiple rebases, earlier/later effective periods, future commands beyond the horizon, and commands that move a target across an effective boundary. Decide membership using supported stable occurrence/period semantics, not a date that changes recursively during rebase.

Apply the same temporal discipline to overrides, cancellation, pause/resume/stop, and reversal. A future reversal must not immediately erase an earlier command's effect for an earlier period. Preserve immutable intent, recorded time, effective time, and historical state. Add a clear pending/future effect representation when supported; reject unsupported timing with an actionable reason when it cannot be represented.

Do not silently rewrite original planned dates or transform an appointment change into a protocol rebase. Do not invent a clinical effective-boundary or catch-up policy; use evidenced policy or mark dependent behavior unresolved.

## 6. Repair transition chronology

Audit `commands.py::track_state` and `validate_track_command`: current validation folds ledger sequence without the proposed effective timestamp.

Confirmed reproduction on memory and real Mongo:

1. PAUSE_TRACK effective **20 January 2026** commits.
2. RESUME_TRACK effective **10 January 2026** commits.
3. The command reports success, but later doses remain paused because the pause occurs later in the effective timeline.

Validate state at the proposed effective point and the resulting relevant timeline, not merely the most recently recorded ledger state. A resume before the hold it claims to resume must be refused or modeled as an explicit authorized historical correction—not accepted as an ordinary resume.

Cover commands recorded out of chronological order, future/backdated transitions, same-day ordering, stopped tracks, multiple holds, partial reversal, and approved resume/catch-up policies. Define deterministic ties without guessing unstated clinical semantics. Unresolved same-time contradictory transitions must not appear as successful state changes.

Validate preview and confirmation identically, recheck after concurrent changes, and preserve supported process/conditional logic. Do not erase later commands or mutate an approved process definition to make a backdated request fit.

## 7. Repair canonical idempotent replay

Audit `patient_commands.py::_confirm` and `_replayed`.

Confirmed reproduction on memory and real Mongo:

1. Preview/confirm RESCHEDULE_APPOINTMENT for IMAGING_BASELINE:0 to **2 February 2026**, effective **20 January**, using key `same`.
2. Create a second preview of the identical semantic operation, with the same effective date and horizon.
3. Confirm the new proposal using key `same`.
4. The current response returns the original command ID but `evaluation_id=null`, a different digest, and zero changed rows instead of the original one changed row.

Store or recover the **original committed outcome** associated with the ledger command and scoped key. Replay must preserve command ID, sequence, original evaluation/version, before/after diff, notification intent, summary, and digest. Only replay metadata may differ.

Distinguish the requested proposal ID from the canonical proposal that committed. Link or mark a duplicate proposal coherently if needed, without another clinical command/evaluation. Do not mix a new preview's diff with an old command ID.

If product semantics deliberately prohibit reuse across proposals, return an explicit documented conflict; do not claim successful replay with inconsistent fields. Prefer the already stated same-key/same-operation replay contract unless verified policy requires otherwise.

Preserve current authorization rechecks even on replay. Different material payload/version/effective date/horizon under the same key conflicts without writes. Replays after newer commands, reversal, assignment changes, expiry, and restarted requests must not recalculate the old result or expose it to revoked users.

Prove concurrent replay through identical and duplicate preview IDs creates one clinical outcome. Include old actual-occurrence receipt/ledger interoperability. Do not rewrite historical ledger rows merely to retrofit a new result field; use a compatible result/receipt representation or explicit migration.

## 8. Complete a reload-safe projection contract

`patient_schedule.py::current_calendar` and `patients.py::CalendarEntry` currently expose protocol dates and actuals but discard operational booking/override layers. After booking imaging for **2 February**, the calendar returns protocol target **31 January** with no booking field.

Extend the selected versioned projection/DTO to expose separately:

- Protocol target and original planned date.
- Protocol permitted window, including unknown/disjoint states where supported.
- Operational appointment date and its within/outside-window status.
- Patient-specific override target/window, authority/reason where permitted, and exception flags.
- Actual visit date and actual treatment date.
- Track, relevant policy/basis, command/evaluation/version identity, status, provisional/pending state, and permitted explanation.

Preserve the original protocol window even when an exception authorizes another date. Distinguish an unknown window from an explicitly zero-width window. After reload, the API—not a frontend ledger interpretation—must still supply the correct values.

Keep command diffs, calendar/details, and outbox/reminder intents consistent. Check cancellation/completion/reversal against stale reminder intents; do not introduce push infrastructure or change the unrelated medication reminder system.

Use P3 privacy allowlists: patients see only their own permitted projection; Sponsor/CRO remain de-identified; staff notes, authority details, hashes/evidence, and denied resources must not leak through new fields. Test response bodies and diagnostic headers.

## 9. Complete live-store integration without an implicit cutover

The clinical patient schedule screen currently uses SQL routes; the command service/ledger exists on the Mongo plane. Verify current wiring rather than assuming the plan remains exact.

Trace a real patient from navigation through operational/canonical ID mapping, assigned schedule, projection, preview, confirmation, and reload. Configuration presence is not proof that the displayed patient exists in that plane or that it is authoritative.

Produce a concrete integration decision with source evidence:

- A compatible SQL persistence adapter for the shared command/application workflow preserves current patient authority, but needs correct SQL transaction/uniqueness/concurrency support and consistency with the repository's Mongo-pilot requirements.
- A supported Mongo cohort workflow needs existing verified enrolment/assignment mappings and explicit authority. Moving live SQL patients to Mongo or changing trial authority is not authorized by this prompt.
- A hidden workbench or a route unable to resolve the displayed patient's identifiers does not complete production integration.

Implement a supported reversible integration preserving existing authority when the repository/session permits it; do not require owner confirmation solely because a previous report listed options. If the repository's approved Mongo-only direction or a real authority change requires an essential decision, prepare the exact recommendation, affected routes/data, migration/rollback needs, and one concrete question. Complete all independent repairs and reviewable UI/backend contracts before stopping for that decision.

Do not end with only the same abstract “SQL or Mongo?” question. Do not claim P5 complete when the chosen production path remains unimplemented. Keep clinical writes within one store's transaction; a Mongo ledger and SQL evaluation are not one atomic operation. Avoid copying the clinical algorithm into a second service; reuse pure calculation and adapter contracts.

If adding SQL support, test its actual routes and transaction behavior, and report PostgreSQL locking limitations honestly. If retaining Mongo-only commands, provide a genuine reachable supported Mongo patient workflow without guessing enrolment or changing existing patients' authority.

## 10. Complete track review and production command UI

Add production-reachable track membership/policy review and patient command controls using existing layout/components and navigation.

Track review must allow an authorized reviewer to classify events/activities where needed, choose supported policies/anchors/cross-track links, review evidence, state rationale, and resolve uncertainty with expected revision. Extraction may propose evidence-supported tracks, but must not infer a fixed-imaging or moving-lab policy from a label. If track proposal is unavailable, provide an explicit manual review workflow; API-only editing is not sufficient.

Track/recurrence policy edits are material: update semantic fingerprint/editor provenance, invalidate stale approval previews, and preserve P3 maker-checker. Do not edit approved versions in place. Keep existing legacy schedules compatible and do not fabricate default tracks.

Patient UI must support the eleven current command types through appropriate authorized forms/actions:

- Actual visit/treatment, missed, and unscheduled visit.
- Appointment rescheduling, patient-specific override, and future cancellation.
- Track pause/resume/rebase/stop.
- Command history and supported linked reversal.

Use typed forms with explicit target occurrence/track, effective date, reason, required actual source/authority/policy, and usable validation. Show changed **and unchanged** events before confirmation. Display protocol target/window separately from appointment/override and visit/treatment actuals.

Handle pending future commands, missing policy/input, denied capability, no active schedule, unavailable plane, stale/expired preview, conflict, cancellation, reversal refusal, and replay. Stable idempotency keys must survive retry after lost responses. Reload after confirmation and verify persisted results; do not rely solely on the confirmation's temporary diff.

Integrate capability discovery as appropriate, while enforcing permissions server-side on preview/confirm/replay/history. No Sponsor/CRO write shortcut, unsafe any-of fallback, client actors/grants, or organization-admin clinical bypass. Keep high-impact grants explicitly protected and pending owner-default approval.

Preserve P1/P2/P3/P4 features and legacy clinical comments/tasks. No broad redesign or migration of legacy screens outside the verified supported integration.

## 11. Verification and acceptance checklist

Use fixed fixtures and injected clocks. Extend the existing suites rather than replacing substantive assertions.

Required new regressions:

1. NUMBER 1 requires index 1 actual, not index 0; equivalent selector tests for visit/treatment/manual/conditional inputs and missing/superseded history.
2. Rebase effective 1 Jan 2027 cannot shift targets inside a 2026 horizon. Test past/current/future boundaries, multiple rebases, and reversal effective periods.
3. Pause 20 Jan then resume 10 Jan is refused as ordinary resume without clinical writes; valid resume and supported backdated correction remain deterministic.
4. Duplicate-preview same-key replay returns original committed evaluation/diff/digest/summary, including concurrent confirmation and newer intervening commands.
5. Calendar GET/reload retains protocol target 31 Jan, appointment 2 Feb, override/window exception metadata, and separate actual visit/treatment values.
6. Track review can resolve an incomplete draft through UI, updates material provenance, and cannot approve unresolved/incompatible policies or foreign links.
7. Production navigation -> patient -> command form -> preview -> confirm -> reload works on the selected supported store; no raw API manipulation or fabricated identifiers.
8. All command types/reversals preserve immutable history, track isolation, explicit dependencies, and protocol-versus-booking semantics. Actual facts are corrected by supersession, not deleted by reversal.
9. Stale/version-changed/expired/unauthorized/cross-patient requests leave business state unchanged. Real-Mongo concurrent confirmation and injected failures preserve atomicity, uniqueness, pointers, ledger, audit, and outbox.
10. Existing treatment delay still moves labs 15 -> 22 Jan while baseline imaging stays 31 Jan. Existing P4 calendar/basis/termination/cap repairs and P1–P3 behavior remain intact.

Run focused P5/P4/P1/authz/review/projection tests, memory/Mongo contracts, and affected SQL adapter tests. Use a local replica set with `UCTSM_REQUIRE_MONGO=1`; verify every mutable Mongo test setting resolves to isolated local databases. Do not use the shared `.env` cluster or describe skipped real-Mongo checks as verification.

Then run the applicable curated handoff gate, checking file existence and omitting deleted extraction/footnote tests. `AGENTS.md` prohibits unrestricted whole-backend pytest; run affected TestClient modules separately where required. Report actual results rather than copying old totals.

Frontend: run `test:uctsm-patient-commands`, `test:uctsm-visit-schedule`, other affected scripts, and a viable scoped TypeScript check with project JSX/module/path settings. There is no plain `npm test`; Expo lint requires missing `yarnpkg`; whole-project typechecking exhausts memory here.

Run a browser/device acceptance walkthrough when tools/environment permit, capturing concrete routes and observed results. If unavailable, state it plainly and distinguish unit/typecheck success from interactive verification. Missing owner approval, data-authority integration, or device evidence must remain explicit; elapsed time is not approval.

## 12. Final handoff and honest completion

Run `git diff --check`, inspect final changes against the initial working tree, and update `docs/p5-tracks-commands-plan.md` and the audit outcome record. Preserve the original reproduced evidence and append resolution status instead of rewriting history to imply the failures never existed.

Your final response must provide:

- A five-finding table: reproduced, fixed, regression name, memory/Mongo/SQL verification.
- P5-01–P5-05 status with backend, API, projection, UI, and verification evidence.
- Actual integration decision and store, supported patient ID mappings, endpoints, screen/navigation, and transaction boundary.
- Demonstrations of selected-occurrence waiting, future-effective rebase isolation, valid transition chronology, stable replay, and reload-safe appointment/override data.
- Actual verification commands/results, concurrency/rollback proof, browser/device results or limitation, and remaining owner decisions.

Do not call P5 complete while a supported production screen cannot reach it, track review still requires manually crafted API edits, audit reproductions remain wrong, or pending policy/authority decisions are represented as approved. Complete every independently feasible authorized repair and make any remaining decision concrete and reviewable.

Start by inspecting the current tree, reproducing the five findings, and writing the repair/integration assessment. Then implement and verify the work.
