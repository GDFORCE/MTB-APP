# Adaptive Protocol Brain — implementation status

Tracks [requirements](adaptive-protocol-brain-requirements.md) and
[design](adaptive-protocol-brain-design.md) against the working tree.
Status values: IMPLEMENTED, VERIFIED, PARTIAL, BLOCKED, OUT_OF_SCOPE.
"VERIFIED" means a named automated test exercises the behaviour; it never means
verified on a real protocol or on a device unless stated.

Last updated: 1 Oct 2026. All work packages landed; **nothing is committed**.

## What works end to end

`backend/tests/test_journey_upload_to_patient.py` (14 tests, real HTTP endpoints,
scripted model client, synthetic protocol "SYN-CRT-7"):

1. **Upload → draft.** `/api/protocols/extract` returns a preview with source
   coverage and one disposition per statement; consuming it and building the
   canonical schedule creates one draft (retry does not duplicate).
2. **Review → approval.** Review view counts, Pending/Ready, one shared question for
   two rows, non-schedule statements labelled; blocked approval returns 409;
   resolving the question, stating a window, setting the holiday policy and
   attaching a holiday calendar make the draft approvable; stale revisions return
   409; exports carry every constraint and statement; approved versions are immutable.
3. **Enrolment → patient dates.** Planned treatment start is stored as planned;
   actual-only visits wait and name the missing input; recording actual starts dates
   6 weekly doses and 30 weekday radiotherapy sessions (site holiday skipped);
   stopping one drug cancels only its later doses; a two-anchor scan gets the
   intersected window, then becomes infeasible after a correction; no reminders for
   incomplete, infeasible or cancelled visits.
4. **Recovery.** A Mongo projection failure after the SQL commit, then two retries,
   creates no duplicates (AT-16).
5. **Amendment.** A second protocol version becomes a new draft; approving it leaves
   the enrolled patient on version 1; new patients go to the version in force.

## Verification (run 1 Oct 2026, from `backend/` unless noted)

Temp files redirected to D: (C: is full); `-p no:cacheprovider`; TestClient modules
run one file per process.

| Check | Result |
| --- | --- |
| Curated gate (handoff §E minus the 2 deleted files) | 375 passed (unchanged from baseline) |
| `tests/test_engine_*.py tests/test_scenario_*.py tests/test_extraction_*.py tests/test_native_import.py` (endpoints file excluded) | 501 passed |
| `test_extraction_endpoints` | 9 passed |
| `test_journey_upload_to_patient` | 14 passed, 0 xfail |
| `test_review_view` / `_edit_approval` / `_accountability` / `_constraints` / `_wp3` / `_bugfixes` / `_enrolment_sources` | 11 / 12 / 7 / 8 / 8 / 7 / 3 passed |
| `test_uctsm_enrolment_api`, `test_canonical_journey`, `test_uctsm_workflow`, `test_protocol_creation` | 3 / 9 / 1 / 4 passed |
| `test_uctsm_qualifier_resolution` | 2 failed, 8 passed — **pre-existing**, same on untouched HEAD |
| `alembic upgrade head` on a fresh SQLite database | clean, head `20260929_0017` |
| Frontend `npm run test:uctsm` (from `frontend/`) | 141 passed |
| Frontend scoped typecheck (temporary tsconfig over touched files) | 0 errors (agent-run) |

Not done: full-project `tsc` (out of memory here), `npm run lint` (needs `yarnpkg`),
any rendering on a device or browser, any screen-reader check.

## Real-document evaluation

- NCT01757288 was read twice with OpenAI `gpt-4.1` **before** WP1–WP4 (reader v2/v3).
  The pipeline ran end to end; the draft was not approvable (cycles unresolved, the
  follow-up plan missing in one run, non-schedule rows). No accuracy was measured.
- It has **not** been re-run on the current reader/compiler (v6). No real-protocol
  accuracy, latency or coverage figure is claimed. An answer key exists outside the
  repo (`…\pvs_poc\tests\fixtures\nct01757288_gold.json`) and has not been checked.

## Changed areas

| Area | Files |
| --- | --- |
| Extraction | `backend/app/extraction/{reader,facts,compiler,contracts,runner,llm_client}.py` (reader/compiler v6); new `ledger.py`, `artifacts.py`, `reconcile.py`, `statements.py`, `constraints.py`; `services/{extraction_service,native_import}.py`; `server.py` extraction endpoints |
| Engine | `backend/app/domain/schedule/{models,timing,condition,conditional,evaluator,validator,diff,projection,deviation,anchor_status}.py`; new `feasibility.py`, `capabilities.py`, `constraint_validation.py`, `calendar_math.py`, `calendar_recurrence.py`, `processes.py`, `facts.py`; `db/repositories.py` |
| Services | `schedule_service`, `impact_service`, `reminder_delivery`, `deviation_service`, `schedule_projection`, `anchor_status_service`, `operational_bridge`, `enrolment_preview`; new `review_view.py` |
| API | `backend/app/api/uctsm.py`: `review-view`, `accountability`, `PATCH/POST events`, `review-questions/{id}/resolve`, `holiday-calendars`, `approve-version`, `export`, `occurrence-facts`; impact preview accepts `process_transition`, `assignment_effective_from` |
| Frontend | `src/features/uctsm/{visitSchedule.ts,anchorEntry.ts,patientActions.ts,VisitScheduleReview.tsx,VisitScheduleSheets.tsx,PatientScheduleActions.tsx}`; `ProtocolScheduleScreen.tsx` (now the Visit Schedule screen), `PatientScheduleScreen.tsx`, `ScheduleTable.tsx`, `presentation.ts`, `types.ts`, `api.ts`; `sponsor/add-trial.tsx`, `clinical/add-patient.tsx` |

Migrations: none added by this work. New rule data lives in existing JSON columns
(reserved keys `_uctsm_constraints`, `_uctsm_processes`, `_uctsm_holiday_calendars`),
`ExtractionRun.trace`, Mongo fields, and the append-only `uctsm_patient_states` table
(reserved codes `uctsm:process_transition`, `uctsm:assignment_fact`,
`uctsm:site_holiday_calendar`). The earlier `20260929_0017_event_visit_window`
migration is still required.

Configuration: `UCTSM_EXTRACTION_PROVIDER` = `anthropic` | `gemini` | `openai` with the
matching key; optional `UCTSM_EXTRACTION_MODEL`, `UCTSM_EXTRACTION_MAX_PAGES`,
`_PAGES_PER_BATCH`, `_MAX_CALLS` (40), `_TIME_BUDGET_SECONDS` (1800),
`_REFERENCE_DEPTH` (2). `openai` added to `requirements.txt`.

## Operators (capability registry `uctsm-capabilities.v1`)

- Timing: ABSOLUTE, OFFSET, PROTOCOL_DAY, RANGE, NOMINAL_WITH_WINDOW, WITHIN,
  NO_LATER_THAN, TRIGGERED, CYCLE_DAY, CONSTRAINED. APPROXIMATE, PROTOCOL_DEFINED and
  UNRESOLVED are declared non-executable.
- Constraints (`uctsm-constraints.v1`): LOWER_BOUND, UPPER_BOUND, AT, ALL, ANY,
  UNSUPPORTED; values ANCHOR, EVENT (FIRST/LAST/CURRENT/PREVIOUS/NUMBER, START/END),
  ACTIVITY, LITERAL_DATE, SHIFT, PROCESS_BOUNDARY.
- Recurrence: INTERVAL, SEGMENTED (phased), cycle plans, CALENDAR (`WEEKDAY_GRID.v1`)
  with count basis, holiday policy and missed-occurrence policy.
- Calendar policies: `MONTH_END_CLAMP.v1`, `WEEKDAY_GRID.v1`.
- Processes (`uctsm-wp3.v1`): states ACTIVE/HELD/STOPPED/COMPLETED; hold/resume policies
  NOMINAL, SHIFT_BY_HOLD, RESTART_FROM_RESUME, WAIT_FOR_DECISION, NONE_STATED;
  actions HOLD/RESUME/STOP/COMPLETE_PROCESS plus the existing block and event actions.
- Not executable (reported as `UNSUPPORTED_OPERATOR`, blocks approval with dependents):
  business-day offsets; holiday rules on non-weekday series; anything the reader
  marks with an unknown operator.

## Traceability

| ID | Status | Code | Evidence | Limitation |
| --- | --- | --- | --- | --- |
| IN-01 | IMPLEMENTED | `ledger.CoverageLedger`, artifact manifest | `test_extraction_accountability` | separately supplied attachments not inventoried |
| IN-02 | VERIFIED | `reader._follow_references` | reference-follow test; journey | page ranking still keyword-based |
| IN-03 | VERIFIED | coverage states, `SOURCE_REFERENCE_MISSING` | AT-13 test | section headings found by regex |
| IN-04 | VERIFIED | `artifacts.py` | AT-20 resume tests | no artifact retention/cleanup |
| IN-05 | VERIFIED | `facts.unknown_fields` → blocking issue | IN-05 test | five annotation keys exempt |
| IN-06 | VERIFIED | prompt rules; verification recomputed | two injection tests | — |
| IR-01 | VERIFIED | citations with verification state, claim links | AT-14 tests | — |
| IR-02 | PARTIAL | occurrence-context splits, canonical anchors | reconcile tests | identity needs a shared name or code |
| IR-03 | VERIFIED | `reconcile.py` cross-batch pass | IR-03, AT-12 tests | — |
| IR-04 | PARTIAL | AND → ALL, stated alternatives → ANY, DUPLICATE, CONFLICTS_WITH | `test_extraction_constraints` | EXCEPTION_TO and SUPERSEDES not modelled |
| IR-05 | VERIFIED | blocking `UNRESOLVED_STATEMENT`, `UNCOMPILED_STATEMENT_FOR_ENTITY` | AT-04 tests; journey | — |
| IR-06 | PARTIAL | call/time budgets, one repair retry, reference depth | budget test | no model-driven targeted re-retrieval loop |
| RL-01, RL-12 | VERIFIED | `capabilities.py` | `test_engine_capabilities` | — |
| RL-02 | IMPLEMENTED | events, activities, occurrence identities | engine tests | — |
| RL-03 | VERIFIED | units, day numbering, precision issues | constraint precision tests | — |
| RL-04, RL-05 | VERIFIED | `feasibility.py` | AT-01, AT-18 tests; journey | persisted flat columns hold one interval; full set in `feasible_intervals` |
| RL-06 | VERIFIED | `condition.py` three-valued logic | engine tests | — |
| RL-07, RL-08, RL-09 | VERIFIED | `calendar_recurrence.py` | AT-05 tests; journey | no site store for holiday calendars |
| RL-10, RL-11 | VERIFIED | `processes.py`, `facts.assignment_periods` | AT-07, AT-11 tests; journey | — |
| RL-13 | VERIFIED | `constraint_validation.py` | reference/cycle/infeasible tests | static check same-reference only |
| RL-14 | VERIFIED | rolling horizon, stable identities | AT-09 test | — |
| PT-01 | VERIFIED | planned/actual anchors, Add Patient choice | planned-vs-actual tests; journey | — |
| PT-02, PT-03 | VERIFIED | occurrence facts, impact preview/confirm | AT-08 test | — |
| PT-04 | VERIFIED | booked vs target vs actual | `test_review_wp3` | no appointment-booking UI beyond recording |
| PT-05 | VERIFIED | version pinning, enrollable-version resolver | journey J4, `test_review_bugfixes` | — |
| PT-06 | VERIFIED | idempotent projection | journey AT-16 | — |
| PT-07 | VERIFIED | planned never becomes actual | AT-08 test | — |
| UI-01 | IMPLEMENTED | Add Trial → Visit Schedule | journey (API) | not rendered |
| UI-02..UI-10 | VERIFIED (logic) | `review_view.py`, `visitSchedule.ts`, `VisitScheduleReview.tsx` | review tests, frontend tests | timing editor only for OFFSET / PROTOCOL_DAY; no UI yet for calendar-policy edit or holiday-calendar attach (API exists) |
| UI-11 | IMPLEMENTED | accessibility props | pure tests | no device or screen-reader check |
| UI-12 | IMPLEMENTED | recovery cards, retry | pure tests | not rendered |
| RV-01 | VERIFIED | coverage/statement gates inside `ScheduleReviewService.approve()` | journey; `test_review_bugfixes` | a ledger with coverage NOT_ASSESSED does not block (legacy imports) |
| RV-02 | VERIFIED | reviewer-decision evidence, ledger `disposition_history` | review tests | — |
| RV-03 | VERIFIED | approval without patient data | review tests | — |
| RV-04 | VERIFIED | whole-version only | review tests | — |
| RV-05 | VERIFIED | content-hash revision, 409 on stale | AT-15 tests | — |
| NF-01, NF-03, NF-08 | VERIFIED | no runtime model calls; bound provenance; legacy unchanged | determinism tests; gate | — |
| NF-02 | IMPLEMENTED | versions + document hash in ledger and artifacts | accountability tests | — |
| NF-04 | VERIFIED | configurable limits, incomplete coverage | budget tests | — |
| NF-05 | VERIFIED | retry convergence | AT-16, AT-20 | — |
| NF-06 | VERIFIED | tenant 404 on new routes | review tests | — |
| NF-07 | IMPLEMENTED | raw replies in artifact storage, not logs | — | not audited end to end |
| NF-09 | PARTIAL | stage usage in ledger | — | no quality metrics by rule family; no thresholds set |
| AT-01, 02, 04..15, 16, 17, 18, 20 | VERIFIED (offline) | — | named tests | synthetic fixtures only |
| AT-03 | VERIFIED (offline) | CONFLICTING + blocking issue | compiler tests | — |
| AT-19 | PARTIAL | month-end policy explicit; DST in earlier time-zone tests | engine tests | no site time-zone source in live data |

## Out of scope (by the requirements)

Automated dose calculation; statistical/randomisation algorithms; push delivery;
flipping `UCTSM_AUTHORITATIVE` or any trial read-mode cutover; migrating the
protected legacy screens.

## Known limitations and risks

1. No real-protocol accuracy measured on the current reader/compiler.
2. Nothing rendered on a device or browser.
3. Two pre-existing failures in `test_uctsm_qualifier_resolution.py`.
4. No UI for calendar-policy edits or holiday-calendar attach; no site holiday store;
   no site time-zone source.
5. Business-day offsets unsupported; "every N cycles" on open-ended cycles unsupported.
6. Extraction artifacts have no retention policy.
7. Two formatting paths for timing text (backend review view and older `presentation.ts`).
8. The working tree mixes this work with an unrelated theme restyle; nothing committed.

## Decisions

- Agents owned disjoint files; no migration was added by this work.
- A ledger with coverage NOT_ASSESSED does not block approval, so legacy/demo
  schedules stay approvable; INCOMPLETE coverage or any uncompiled statement blocks.
- Resolving a hold condition does not resume treatment unless the block/process
  states `resume_trigger: CONDITION_RESOLUTION`.
- With a DELIVERED count basis an unstated missed-session policy is shown as implied,
  not as a review item; with SLOTS it stays a review item.
- Boundary inclusivity is taken only from stated fields or unambiguous wording.

## Next concrete actions

1. Re-run NCT01757288 on reader/compiler v6 and score against a checked answer key
   (paid API calls; needs approval).
2. Open the Visit Schedule and patient screens on a device; fix what rendering shows.
3. Add UI for calendar-policy edit and holiday-calendar attach; decide site holiday
   and time-zone storage.
4. Commit (separately from the theme restyle) and run `alembic upgrade head` on shared
   databases.
