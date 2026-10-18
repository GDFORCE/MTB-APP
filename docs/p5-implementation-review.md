# Independent P5 implementation review

Reviewed 14 October 2026 against current source, `docs/p5-tracks-commands-plan.md`, and focused tests. Application code was not modified. All pre-existing uncommitted work was preserved.

## Verdict

The reported missing production screen, SQL-versus-Mongo integration boundary, track-review/extraction gaps, and pending authorization decisions are accurate. **P5 remains partial.** The backend has substantial working behavior and real-Mongo tests, but additional reproduced failures mean it should not be described as fully verified for acceptance.

| Task | Review assessment |
|---|---|
| P5-01 tracks | Domain/storage and membership validation exist; production membership/policy review and extraction integration remain missing. |
| P5-02 track policies | Track-scoped calculations exist; effective-date handling and transition chronology need repair. |
| P5-03 commands | Ledger, reversal links, and atomic writes exist; replay correctness and temporal validation need repair. |
| P5-04 preview/commit | Shared calculation and stale-base/digest checks exist; cross-proposal idempotent replay returns the wrong result metadata. |
| P5-05 UI | Request builders/formatters exist; no production command screen/navigation or device/browser proof. Calendar DTOs also omit operational appointment/override data. |

P3's proposed matrix and high-impact defaults remain pending owner approval. This review does not approve a move to Mongo authority or a SQL adapter strategy.

## Findings

### 1. High: future-effective rebase changes earlier targets

Source: `backend/app/domain/schedule/tracks.py::track_context` selects the latest rebase by `effective_at`, then replaces the track anchor for the event without restricting its application to the effective period. `command_inputs` supplies the rebase date, but the application/evaluator path does not enforce that boundary.

Reproduction using the existing P5 fixture:

- Track: TREATMENT; anchor: BASELINE.
- Command: REBASE_TRACK to anchor value 8 January 2026.
- Command effective date: **1 January 2027**.
- Evaluation horizon: **31 December 2026**.
- Preview and confirmation moved **five 2026 targets**. C1D1 moved from 8 January to 15 January; DOSE occurrences shifted from 15/22/29 January and 5 February to 22/29 January and 5/12 February.

Reproduced in memory **and through confirmation on real local Mongo**. A command effective outside the evaluated period must not silently change earlier targets. Establish explicit effective-period semantics for rebase, overrides, cancellations, and reversals; reject unsupported timing or apply it only to the intended period while preserving history.

### 2. High: resume is accepted before the pause it supposedly resumes

Source: `backend/app/domain/schedule/commands.py::track_state` folds active commands by ledger sequence. `validate_track_command` receives no proposed effective date, so it validates the latest recorded state rather than the state at the command's effective point.

Reproduction:

1. Commit PAUSE_TRACK effective **20 January 2026**.
2. Commit RESUME_TRACK effective **10 January 2026**.
3. The resume succeeds, but later DOSE occurrences remain PAUSED because the evaluator follows the effective timeline and the pause occurs later.

Reproduced in memory **and real local Mongo**. Validate transitions against the effective timeline, including backdated/future commands and equal-date tie rules. Do not claim a successful resume changed a hold that had not begun at its effective point.

### 3. High: numbered dependency uses another occurrence's actual

Source: `dependency.py::_select` narrows source statuses by occurrence, but the later `evaluator.py` actual-date requirement still calls `_actual_recorded` using only the source event code. The occurrence selector is not carried through that requirement.

Pure domain reproduction:

- SOURCE has two weekly occurrences, indexes 0 and 1.
- Only occurrence **0** has a recorded actual (1 January 2026).
- Occurrence **1** has a planned target (8 January) and no actual.
- TARGET requires `source_occurrence=NUMBER`, `source_occurrence_number=1`, and explicit `dependency_mode=ACTUAL_PREVIOUS_EVENT`.
- TARGET incorrectly evaluates as **RESOLVED**, with a satisfied dependency, instead of waiting for occurrence 1's actual.

This was reproduced through the current shared evaluator with the existing P4 fixtures. It is not a decision about whether TRIGGER/PRECONDITION types imply actuals: the fixture explicitly requires an actual already. Carry selected occurrence identity through status checks, actual-visit/treatment checks, manual inputs, and timing resolution. Test FIRST/LAST/CURRENT/NUMBER combined with explicit actual modes, not only status selection.

The reported P4 occurrence-specific repair is therefore **partial**, despite passing its current regression cases.

### 4. Medium: idempotent replay through another preview returns different metadata

Source: `backend/app/application/patient_commands.py::_confirm` finds an existing ledger command by idempotency key, but `_replayed` builds the result from the **newly requested proposal**, rather than the proposal/result that originally committed that command.

Reproduction:

1. Preview and confirm rescheduling IMAGING_BASELINE:0 to 2 February 2026, effective 20 January, with key `same`.
2. Create another preview of the same semantic command using the same effective date and horizon.
3. Confirm the second preview using key `same`.

The second call replays the original command ID and creates no second command, but:

- `evaluation_id` becomes **null** instead of the original committed evaluation ID.
- The digest differs from the original result.
- Original summary: **1 changed, 6 unchanged**. Replay summary: **0 changed, 7 unchanged**.

Reproduced in memory **and real local Mongo**. Replay must return the stored committed outcome for the matched command, not a fresh preview's outcome. Alternatively, define an explicit conflict for unsupported reuse across proposal identities; never return mixed original-command/new-preview metadata.

### 5. Integration: calendar response drops operational values

Source: `backend/app/application/patient_schedule.py::current_calendar` and `patients.py::CalendarEntry` serialize protocol dates, completion, and actual dates but not the booking/override layers stored in generated explanations.

After confirming an imaging booking for **2 February**, the calendar entry returns `planned_start=2026-01-31` and no appointment field. Preserving the protocol target is correct; omitting the operational booking makes it impossible for this calendar API alone to show both values after a reload. Overrides have the same DTO gap.

Expose the operational appointment/override, relevant deviation flags, track/policy information, and separate visit/treatment actuals through the selected role-safe projection contract. Do not make the frontend rebuild clinical scheduling semantics from the raw command ledger.

## Independent verification

Targeted backend modules were run separately with local-only Mongo settings and `UCTSM_REQUIRE_MONGO=1`:

| Module | Result |
|---|---|
| `test_p5_tracks_domain.py` | 26 passed |
| `test_p5_patient_commands.py` | 49 passed, 2 skipped |
| `test_p5_command_api.py` | 12 passed |
| `test_p4_dependency_recurrence.py` | 42 passed |

The two skips are the documented in-memory concurrency variants. The corresponding Mongo variants ran. The targeted total is **129 backend passes and 2 skips**; P5 alone is **87 passes and 2 skips**.

Frontend:

- `npm run test:uctsm-patient-commands`: **5 passed**.
- `npm run test:uctsm-visit-schedule`: **27 passed**.

The additional real-Mongo reproductions used a fresh local `uctsm_test_p5_audit_<random UUID>` database, applied repository migrations, and removed that test database afterwards. No shared database was used. The numbered-actual and calendar cases were exploratory checks, not added repository regression tests.

The full 517-test gate, wider 232-test run, journey, complete earlier suites, PostgreSQL locking, browser/device behavior, and scoped TypeScript check were **not independently rerun in this review**. Their previous results remain the engineer's report, not new audit evidence.

`git diff --check` returned no whitespace errors and existing line-ending notices.

## Fix order and completion gates

1. Add regressions for selected-occurrence actual requirements and effective-period/transition chronology, then repair the shared domain/application paths.
2. Repair cross-proposal idempotent replay and prove stable committed result identity/diff/digest on memory and real Mongo.
3. Complete role-safe projection fields for protocol targets/windows, appointments/overrides, and actuals.
4. Resolve the live persistence integration deliberately: a supported SQL adapter or an authorized Mongo cohort/enrolment route. Do not silently change authority or mix SQL and Mongo writes as one transaction.
5. Add production command forms, before/after previews, confirmation/reversal/history, track membership/policy review, and navigation; verify an actual user can finish the workflow.
6. Obtain owner decisions for P3/high-impact rights and any remaining clinical semantics. Re-run focused regressions and the applicable gate.

P6 design work can inspect the existing contracts, but amendment/adoption acceptance should not build on unresolved command timing, replay, or occurrence identity assumptions.

## Resolution record (appended 14 Oct 2026; original findings above are unchanged)

| # | Finding | Status | Regression | Verified on |
|---|---|---|---|---|
| 3 | numbered dependency used another occurrence's actual | **Fixed.** `selected_indexes` resolves the selector once; `_actual_recorded` and the actual-date substitution use only that occurrence's own state (never a code-wide fallback for an exact selector) | `test_p4_dependency_recurrence::test_numbered_source_with_actual_mode_waits_for_that_occurrences_own_actual` | pure domain |
| 1 | future-effective rebase changed earlier targets | **Fixed.** Rebase is evaluated per variant and chosen per occurrence by its STABLE (unrebased) target within `[effective_at, ended_at)`; overrides/cancellations use the same window; a reversal ends the effect at its own date | `test_p5_tracks_domain::test_a_rebase_effective_after_the_evaluated_period…`, `…inside_its_effective_period`, `…two_rebases…`, `…reversal_ends_the_effect…` | pure domain + memory + Mongo (existing command tests) |
| 2 | resume accepted before its pause | **Fixed.** State is read at the proposed effective date and the whole resulting timeline re-validated; a future-dated reversal of a transition is refused with the alternative | `…resume_dated_before_the_pause…`, `…backdated_pause_may_not_break…`, `…future_dated_reversal_of_a_transition…` | pure domain; confirm path via `test_p5_patient_commands` (memory + Mongo) |
| 4 | duplicate-preview replay returned mixed metadata | **Fixed.** Replay returns the canonical proposal's committed rows/digest/evaluation; the duplicate proposal is linked (`duplicate_of`) | `test_p5_patient_commands::test_same_key_through_a_second_preview…`, `…after_newer_commands…`, `…different_effective_date_conflicts` | memory + real Mongo |
| 5 | calendar dropped operational layers | **Fixed.** `CalendarEntry` carries appointment, override target/window, window_known, cancellation, track, provisional; authority/reason/command id withheld from non-staff roles | `…calendar_reload_keeps_protocol_target…`, `…unknown_window_is_not_a_zero_width…`, `test_p5_command_api::…withholds_authority_from_others` | memory + real Mongo + HTTP |
