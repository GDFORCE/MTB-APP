# P5 — schedule tracks and patient commands: design and verification record

Status 14 Oct 2026. **P5 is not complete.** Domain, ledger, preview/commit, Mongo-plane API and authorization are built and verified; the production clinical screen is not wired (see §7). Nothing here approves P3's proposed role matrix (`PROPOSED_PENDING_OWNER_APPROVAL`).

## 1. Audited P4 prerequisites

| # | Finding | Result | Regression (`tests/test_p4_dependency_recurrence.py`) |
|---|---|---|---|
| 1 | Conflicting recurrence start passed validation, failed at evaluation | **Fixed.** `RECURRENCE_START_UNVERIFIABLE` (reasons `DIFFERENT_REFERENCE`, `INCOMPATIBLE_UNITS`, path `…/start_reference`); a patient merely missing an anchor is still a wait | `…plus_one_month_start_offset_is_blocked_at_approval…`, `…different_anchor…`, `…missing_the_anchor_is_a_wait…` |
| 2 | COUNT 10,001 bypassed the 10,000 cap | **Fixed.** `RECURRENCE_SAFETY_CAP` separate from `protocol_count`; cap marker carries `protocol_count`, `incomplete_projection` | `…count_above_the_safety_cap…` |
| 3 | EVENT termination with undated end silently used the horizon | **Fixed (provisional).** Every occurrence carries `termination: {status: AWAITING_END_EVENT, provisional: true}`; no end date is guessed | `…undated_end_event_is_provisional…` |
| 4 | No app control to state a recurrence basis | **Fixed.** Edit sheet control (basis, actual source, rationale, expected revision; hidden without `schedule.correct`, server still decides). Frontend only verified by unit tests, not on a device | `frontend/tests/uctsm-visit-schedule.test.ts` (3 added) |
| 5 | Dependency satisfied by *any* occurrence | **Fixed, opt-in.** `Dependency.source_occurrence` ANY (default, event-level, unchanged) / FIRST / LAST / CURRENT / NUMBER | `…event_level_versus_occurrence_level`, `…one_completed_cycle_does_not_satisfy…` |

TRIGGER/PRECONDITION "must have actually happened" remains an **owner decision**; unchanged.

## 2. Requirement matrix

| Task | Built | Evidence | Not done |
|---|---|---|---|
| P5-01 tracks | `ScheduleTrack`, `TrackLink`, `Event.track_scope/track_id`, `TrackRebasePolicy`; validator `_tracks` (membership, policy, process match, composition with `RecurrenceBasis`, undeclared cross-track reference); canonical JSON + both stores; Mongo `schedule_tracks` projection + migration 0008 | `test_p5_tracks_domain.py` (26) | Extraction does not propose tracks; **no review UI** for membership/policy (approval of a track-aware draft needs an API edit) |
| P5-02 rebase/pause | Policies composed and validated; MANUAL_REBASE moves the selected track's anchor only; pause/resume/stop via the existing process model (`SHIFT_BY_HOLD` etc.); per-occurrence `track` metadata (changed and unchanged) | same | Weekday/cycle/phased models not track-aware |
| P5-03 commands | 11 typed commands, append-only `patient_commands` ledger, reversal as a linked command, one shared fact path (the old actual route now also writes the ledger) | `test_p5_patient_commands.py` | SQL plane has no ledger |
| P5-04 preview = commit | `PatientCommandService._apply` is the only calculation; preview runs it and rolls back; confirm re-checks the base fingerprint and the semantic digest | same, incl. stale/expired/cancel/replay | Legacy `/schedule-impact-proposals` is a separate service (not extended) |
| P5-05 UI | Request builders and result formatting only (`patientCommands.ts`, 5 tests) | — | **No screen, no navigation, no device check** |

## 3. Semantics

Command meaning (`domain/schedule/commands.py` docstring is authoritative):

| Command | Kind | Effect |
|---|---|---|
| RECORD_ACTUAL / MARK_MISSED / CREATE_UNSCHEDULED | clinical fact | feeds dependents through explicit bases; **not reversible** (correct by supersession) |
| RESCHEDULE_APPOINTMENT | operational booking | never moves a protocol target; outside the window it is flagged `BOOKED_OUTSIDE_PERMITTED` |
| OVERRIDE_OCCURRENCE | patient exception | shown beside the protocol target/window with named authority; window unchanged |
| CANCEL_OCCURRENCE | status | future, non-closed occurrence only; history kept |
| PAUSE / RESUME / STOP_TRACK | process transition | requires a confirmed track with a process and `stoppable`; resume policy never inferred |
| REBASE_TRACK | track input | only `MANUAL_REBASE` tracks, only the track's own anchor |
| REVERSE | compensating | refused for facts, reversals, already-reversed, or while a newer command touches the same track/occurrence; cross-patient/version → 404 |

Reversal recomputes through the evaluator from the remaining active commands; a reversed booking is replaced by the booking it superseded, else a `BOOKING_WITHDRAWN` marker.

Idempotency hash = patient, version, type, typed payload, effective date, horizon (reason and clock excluded). Uniqueness is storage-enforced: `(patient, sequence)`, `(org, patient, idempotency_key)`, `(patient, reverses_command_id)`.

## 4. Persistence and compatibility

* Mongo migration 0008 `tracks_and_patient_commands` (new; applied migrations untouched). `schedule_tracks` is a lookup projection of the version's immutable content, rewritten with it.
* Old schedules: no tracks and no classified events → validator skips tracks entirely; dates unchanged. A schedule that declares any track is *track-aware* and must classify every event (`TRACK_MEMBERSHIP_MISSING`).
* Fixed in passing (pre-existing): an on-demand definition and the first created unscheduled visit both held `(code, 0)`, which Mongo's unique projection index rejects. The definition placeholder is no longer written once a visit exists.
* Memory store: `PatientCommandRepository` mirrors the Mongo uniqueness rules.

## 5. API (Mongo patient plane, `/api/uctsm/mongo-plane/trials/{trial}/patients/{external}/…`)

| Route | Capability | Notes |
|---|---|---|
| `POST commands/preview` | narrowed by body `type`: facts → `patient.occurrence.record`, unscheduled → `patient.unscheduled.create`, **everything else → `patient.command.high_impact`** (`strict_unmapped`) | body has no actor/grant fields (422) |
| `POST commands/{proposal}/confirm` | any-of the three, then re-checked for the proposal's own type on every call (replay included) | `Idempotency-Key`; another patient's proposal → 404 |
| `POST commands/{proposal}/cancel` | as confirm | never applies |
| `GET commands` | `patient.command.preview` | ledger with reversal links |

`patient.command.high_impact` has **no role default** and is a clinical-decision capability: it must be granted. `patient.command.preview` defaults to pi/crc (proposal). Manifest count 58 → 62.

## 6. Sample diff (treatment 7 days late, imaging fixed)

```
LAB_AFTER_TX:0   target 2026-01-15 -> 2026-01-22  PROTOCOL_TARGET  track LABS (FOLLOWS_ACTUAL)
                 "counts from the actual treatment of C1D1"
IMAGING_BASELINE:0  2026-01-31 -> 2026-01-31      NONE             track IMAGING (FIXED_FROM_ANCHOR)
                 "unchanged: counts from BASELINE; no actual date of another visit moves it"
```

## 7. Not done / decisions needed

1. **Production UI and live-store reachability.** The clinical patient schedule screen uses the SQL plane (`/api/uctsm/patients/{id}/…`); the commands exist only on the Mongo plane, which the frontend does not call. Options: (a) port the ledger and command service to the SQL plane (second adapter; its own transaction), (b) move this screen's patients to the Mongo plane (a data-authority decision, forbidden here). Needs an owner choice. No cross-database writes were attempted.
2. Review UI for track membership/policy and extraction proposing tracks (approval of a track-aware draft currently needs an API edit — the exact pattern P5 forbids as a resolution path).
3. Legacy `impact_service` is not extended; reversal/override are not previewable through it.
4. High-impact capability mapping, default for `patient.command.preview`, and maker-checker for track-definition edits (they are material edits via the existing review path) await owner approval.
5. TRIGGER/PRECONDITION actual-occurrence rule; TRACK `stoppable`/rebase policies are never guessed.
6. Notification intents are returned and written to the outbox only; no delivery claimed.

## 8. Verification (real local replica set `rs0`, `UCTSM_REQUIRE_MONGO=1`, db `mtb_p5_test`)

| Suite | Result |
|---|---|
| New: `test_p5_tracks_domain` 26, `test_p5_patient_commands` 49 (+2 memory skips: single-threaded store; concurrency proven on Mongo), `test_p5_command_api` 12 (memory+Mongo) | passed |
| Curated gate (files present) + new modules | 517 passed, 2 skipped |
| contract, P1 api/recalculation/concurrency, authz manifest + http | 232 passed |
| journey 14, review suites 39, Mongo parity review 20, maker-checker 18+16, identity 22+2 | passed |
| Frontend `uctsm-visit-schedule` 27, `uctsm-patient-commands` 5; scoped `tsc` on touched files | passed |

Proven on real Mongo: two commands prepared on one base — exactly one commits, the other is `PROPOSAL_STALE`; three identical concurrent confirmations — one command, one evaluation; injected evaluation failure — no fact, ledger row or outbox row. Not verified: PostgreSQL locking, any browser/device, `npm run lint` (needs `yarnpkg`).


## 9. Completion pass (14 Oct 2026, second session)

### Semantics added
* **Effective period.** A command changes only what lies on or after its `effective_at`, decided on each occurrence's *stable* (unrebased) target so a rebase can never move the date that decides whether it applies. A reversal is a retraction when effective on/before the command's start; otherwise it ends the effect at its own date (earlier periods keep it). Pause/resume/stop are withdrawn whole; a later-dated reversal is refused (`REVERSAL_PERIOD_UNSUPPORTED`) and the opposite transition is the supported way.
* **Transition chronology.** State is evaluated in effective order (ties: recorded order). A resume must follow a pause it ends; inserting a transition may not invalidate a recorded one.
* **Selected occurrence.** `source_occurrence` ANY is event-level; FIRST/LAST/CURRENT/NUMBER resolve to occurrence indexes once and drive status, actual-visit, actual-treatment and the date used. CURRENT is still the latest generated occurrence (alias of LAST); it is not yet target-relative - that is open.
* **Replay.** Same key + same semantic hash returns the original committed outcome whichever preview carried the request.

### Integration decision (evidence-based)
* The SQL plane has **no port-based unit of work**: `PatientScheduleService` there is a separate SQLAlchemy service (`app/services/schedule_service.py`). A SQL command adapter would be a second implementation of ~10 repositories and the evaluation write path, against the repository's Mongo-port direction (commits `UCTSM Mongo port: …`). Not built.
* **Built instead (no authority change):** the existing patient schedule screen (`clinical/patient-schedule`) now receives `trial_id` and `external_patient_id` from `GET /api/uctsm/patients/{id}/schedule` and mounts `PatientCommandsPanel`, which probes `GET /mongo-plane/.../calendar`. If this patient exists in the MongoDB plane the panel offers the ten command forms, a before/after preview (changed and unchanged), confirm with a per-proposal idempotency key, history and linked reversal, re-reading the ledger after confirming. If not, it says so and changes nothing. Nobody is enrolled, pinned or moved.
* **Transaction boundary:** one Mongo transaction per command (fact + ledger + audit + evaluation + outbox). SQL data is never written by this path.
* **Consequence:** a patient who exists only in SQL cannot use commands. Whether such patients should be mirrored into the plane (a P1-style enrolment bridge already exists: `operational_bridge`) is the owner decision below.

### Track review
`POST /api/uctsm/schedule-versions/{id}/tracks` (capability `schedule.correct`, expected revision + rationale) replaces tracks, cross-track rules and membership as one reviewed material edit (provenance, revision change, revalidation). SQL persistence stores them in version/event metadata (no schema change). `TrackReviewSheet` ("Tracks and rules" on the visit schedule screen) edits them; nothing is defaulted and a track cannot be confirmed without a chosen policy. Extraction still proposes no tracks.

### Verification (this session)
| Suite | Result |
|---|---|
| Curated gate files present + P4/P5 modules | 540 passed, 2 skipped |
| contract, P1 api/recalculation/concurrency, authz manifest + http | 232 passed |
| journey 14, review 11+12+8+8, Mongo parity review 20, maker-checker 18+16, identity workflow 22 | passed |
| New: tracks domain 33, patient commands 55 (+2 memory skips), command API 13, track review 3 | passed |
| Frontend `uctsm-patient-commands` (10) + `uctsm-visit-schedule` (27) | 37 passed |
| scoped `tsc`: PatientCommandsPanel, PatientScheduleScreen, patientCommands, api, TrackReviewSheet | clean |

### Not verified / open
* `ProtocolScheduleScreen.tsx` (track button/sheet mount): `tsc` ran out of memory on this machine (whole-project limitation in AGENTS.md); the edit mirrors the existing sheet mounts. **No browser/device walkthrough** was possible, so the panels are unit/typecheck-verified only.
* No end-to-end SQL-to-plane patient exists in tests; the probe-and-hide behaviour is code-reviewed, not exercised in a UI.
* CURRENT selector semantics, per-occurrence activity commands, sponsor/CRO de-identification of the *history* route (it returns authority/reason to any caller holding `patient.command.preview`, which defaults to pi/crc only), and PostgreSQL locking.
* Owner decisions: mirror SQL-only patients into the Mongo plane vs. build a SQL adapter; P3 high-impact/default grants; TRIGGER/PRECONDITION actual rule.
